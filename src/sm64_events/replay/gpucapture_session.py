"""One GPU request, helper and fragment run, owned by the recorder media worker."""

import logging
from pathlib import Path
import sys
import struct
import time

from sm64_events.core.paths import bundled_encoder_dll
from sm64_events.replay.gpucapture import GpuCleanupError
from sm64_events.replay import gpuchannel
from sm64_events.replay.gpuinput import OfferDecoder, ChannelSelection
from sm64_events.replay.gpumedia import GpuMedia
from sm64_events.replay.gpuaudio import GpuAudio
from sm64_events.replay.gpudiagnostics import CaptureTimings, failure_snapshot
from sm64_events.replay.gpuencoder import Result
from sm64_events.replay.gpuencoder_abi import CODEC_STREAM_NAMES
from sm64_events.replay.gpupublication import PublicationError, PublicationWriteError
from sm64_events.replay.gpumediaworker import MediaWorker
from sm64_events.replay.gpusettings import note_codec_unavailable, recording_codec
from sm64_events.replay.gpuprocess.retirement import close_encoder
from sm64_events.replay.gpuprocess.process_controller import Controller
from sm64_events.replay.media import MediaRun
from sm64_events.replay.packetmux import NativeFormat
from sm64_events.replay.ownedclose import CleanupPending

log = logging.getLogger("sm64.replay")

# Distinct from None (stop) and from a successful open tuple.
_RETRY_OTHER_CODEC = object()


class CaptureSession:
    """No method in this object is reachable from an emulator/audio callback."""

    def __init__(
        self,
        owner,
        demand,
        *,
        channel_factory=gpuchannel.Client,
        controller_factory=Controller,
    ):
        self.owner, self.demand = owner, demand
        self.channel_factory, self.controller_factory = (
            channel_factory,
            controller_factory,
        )
        self.channel = self.controller = self.media = self.adapter = None
        self.codec = None  # set by the Open that actually succeeded
        self.audio = self.archive = self.handoff = self.mux = None
        self.output = None
        self._output_joined = False
        self.frontier = None
        self.settings = owner.settings
        self.state = "preparing"
        self.error = None
        self.timings = CaptureTimings()

    def _continue(self):
        snapshot = self.demand.snapshot
        if snapshot.lifecycle:
            return False
        if snapshot.state == "fault":
            raise RuntimeError(snapshot.reason)
        return self.owner.want_capture() and snapshot.state != "stopped"

    def _wait(self, deadline):
        if not self._continue():
            return False
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("GPU recording startup deadline")
        self.owner.wait(self.settings.poll_s)
        return True

    def _open_channel(self):
        deadline = None
        while self._continue():
            identity = self.demand.identity
            if identity is not None:
                if deadline is None:
                    deadline = time.monotonic() + self.settings.startup_s
                try:
                    self.channel = self.channel_factory(
                        nonce=struct.unpack("<QQ", identity.nonce),
                        generation=identity.control_generation,
                        producer_pid=identity.producer_pid,
                        producer_birth=identity.producer_birth,
                    )
                    # The native worker signals this event on every offer,
                    # bridge and status publication: wake the tick on it
                    # instead of sleeping out the timer period.
                    watch = getattr(self.owner, "watch", None)
                    if watch is not None:
                        watch(getattr(self.channel, "result_event", None))
                    return True
                except CleanupPending as exc:
                    self.channel = exc.owner
                    raise  # run/finally and the media owner retain same-thread cleanup.
                except (FileNotFoundError, BlockingIOError):
                    pass  # Native preparation has not published its immutable header.
            if not self._wait(deadline):
                return False
        return False

    def _open_encoder(self):
        header = self.channel.header
        luid = (header.luid_high, header.luid_low)
        dll = bundled_encoder_dll()
        if dll is None:
            raise RuntimeError("this build carries no SM64GpuEncoderV1.dll; run tools/build_plugin.py")
        self.controller = self.controller_factory(
            executable=Path(sys.executable),
            helper=Path(__file__).resolve().parent / "gpuprocess/helper_bootstrap.py",
            limits=self.settings.helper(),
        )
        # ONE startup budget across both attempts. An adapter without an AV1
        # encoder refuses by codec before touching pixels, so the fallback
        # costs a round trip, not a second startup window -- and it is
        # remembered per adapter, so only the first capture pays it.
        deadline = time.monotonic() + self.settings.startup_s
        while True:
            codec = recording_codec(luid)
            opened = self._attempt_open(dll, header, luid, codec, deadline)
            if opened is not _RETRY_OTHER_CODEC:
                return opened

    def _attempt_open(self, dll, header, luid, codec, deadline):
        command = dict(
            op="Open",
            dll_path=str(dll),
            adapter_luid=[header.luid_high, header.luid_low],
            names=list(self.channel.texture_names),
            options=self.settings.encoder(
                header, self.owner.cfg,
                nominal_rate=self.owner.nominal_rate, codec=codec,
            ),
        )
        request_id = self.controller.enqueue(command)
        if type(request_id) is not int:
            raise RuntimeError("encoder bootstrap admission refused")
        while self._continue():
            reply = self.controller.take_result()
            if reply is not None:
                if (
                    reply.request_id == request_id
                    and reply.metadata["result"] == Result.CODEC
                    and not reply.metadata["worker_disposal_required"]
                    and note_codec_unavailable(luid, codec)
                ):
                    # This adapter has no encoder for that codec. A fact about
                    # the hardware, typed apart from every real fault, so the
                    # session asks for the other one instead of failing.
                    return _RETRY_OTHER_CODEC
                if (
                    reply.request_id != request_id
                    or reply.metadata["result"] != 0
                    or reply.metadata["error"]
                    or reply.metadata["worker_disposal_required"]
                ):
                    raise RuntimeError(
                        reply.metadata["error"] or "GPU encoder Open failed"
                    )
                self.codec = CODEC_STREAM_NAMES[codec]
                # Native has names/contexts ready but has captured no pixels yet.
                # Cold AAC/filter/format preparation must finish before source
                # admission. Its pool holds milliseconds, not a startup backlog.
                if not self.timings.measure("sink_prepare", self._prepare_sink):
                    return None
                self.handoff = self.owner.begin_audio()
                self.channel.encoder_ready()
                return command, request_id, reply
            status = self.controller.status()
            if status["fault"]:
                raise RuntimeError(status["fault"])
            if not self._wait(deadline):
                return None
        return None

    def _prepare_sink(self):
        h, cfg, limits = self.channel.header, self.owner.cfg, self.settings
        self.output = MediaWorker(
            NativeFormat(self.codec, h.even_width, h.even_height,
                         self.owner.nominal_rate),
            self.owner.ledger,
            lambda run: self.owner.publish(run, (h.even_width, h.even_height)),
            audio_rate=cfg.audio_rate, audio_bitrate=160000,
            packet_limit=limits.packet_bytes, pcm_limit=limits.pcm_bytes,
            max_bytes=limits.pending_bytes, max_blocks=limits.pcm_blocks,
            max_age=limits.max_age, timings=self.timings,
        )
        deadline = time.monotonic() + limits.startup_s
        while not self.output.ready:
            if not self._wait(deadline):
                return False
        self.output.check()
        return True

    def _first_offers(self):
        deadline = time.monotonic() + self.settings.startup_s
        while self._continue():
            offers = self.channel.offers()
            if offers:
                return offers
            if not self._wait(deadline):
                return ()
        return ()

    def _create_media(self, bootstrap, offers):
        from sm64_events.replay.channelencoder import ChannelEncoder

        h, limits = self.channel.header, self.settings
        decoder = OfferDecoder(h, self.owner.layout, self.owner.clock)
        run = MediaRun.starting_at(decoder.timestamp(offers[0].boundary_qpc))
        self.output.bind(run)
        self.mux = self.output
        self.media = GpuMedia(
            run,
            self.owner.ledger.selection_only(self.output.add_row),
            self.mux,
            source_namespace=decoder.namespace,
            encoder_duration=limits.request().encoder_duration,
            packet_limit=limits.packet_bytes,
            pending_count=limits.slots,
            pending_bytes=limits.pending_bytes,
            max_age=limits.max_age,
            pcm_bytes=limits.pcm_bytes,
            pcm_blocks=limits.pcm_blocks,
            lead_ticks=limits.lead_ticks,
            timings=self.timings,
            publish_picture=self.output.publish_picture,
        )
        self.audio = GpuAudio(
            self.media, self.handoff, monotonic=time.monotonic, wall_time=time.time
        )
        selection = ChannelSelection(self.channel, decoder, self.media)
        self.adapter = ChannelEncoder(
            selection, self.controller, max_pending=limits.slots, max_age=limits.max_age,
            defer_disposal=True,
        )
        command, request_id, reply = bootstrap
        self.adapter.attach_open(
            command, request_id, reply, initial_offers=offers, now=time.monotonic()
        )
        self.state = "recording"
        log.info(
            "GPU recording active: run=%s source=%s shape=%dx%d adapter=%s native=%s",
            run.id,
            decoder.namespace,
            h.width,
            h.height,
            (h.luid_high, h.luid_low),
            reply.metadata.get("identity"),
        )

    def run(self):
        try:
            if not self.timings.measure("channel", self._open_channel):
                return
            bootstrap = self.timings.measure("encoder", self._open_encoder)
            if bootstrap is None:
                return
            offers = self.timings.measure("first_offer", self._first_offers)
            if not offers:
                return
            self.timings.measure("media_setup", self._create_media, bootstrap, offers)
            while self._continue():
                self.timings.measure("tick", self.tick)
                self.owner.wait(self.settings.poll_s)
        except Exception as exc:
            self.error = str(exc)[:512]
            log.info("GPU capture failure before cleanup: %s", failure_snapshot(
                exc, frontier=self.frontier, adapter=self.adapter, output=self.output,
            ))
            retired = self.demand.reconcile_lifecycle()
            snapshot = self.demand.snapshot
            cancelled = (
                isinstance(exc, gpuchannel.ChannelStopped) and exc.cancelled
                and snapshot.cancelled and snapshot.state != "fault"
                and not snapshot.cleanup_error
            )
            if not isinstance(exc, GpuCleanupError) and (retired or cancelled):
                # Retain the actual error so this unfinished suffix is aborted.
                # Explicit producer/ROM retirement or recorder cancellation
                # avoids retry penalties; channel closure alone never does.
                # Cleanup below must still prove its own resource disposal.
                log.info("GPU capture retired during media operation: %s", self.error)
                return
            # GpuCapture logs once at the owning boundary. Preserve the lease
            # supervisor's reason as well as the media symptom; native channel
            # closure can precede that supervisor's next bounded poll.
            if snapshot.reason:
                self.error = f"{self.error}; demand={snapshot.state}:{snapshot.reason}"[:512]
                raise RuntimeError(self.error) from exc
            raise
        finally:
            try:
                self.close()
            except PublicationError:
                raise  # Finished writer failure is not unproved resource disposal.
            except Exception as exc:
                raise GpuCleanupError("GPU capture cleanup unproved") from exc
            finally:
                if self.error:
                    log.info("GPU capture stage timings at stop: %s", self.timings.summary())

    def tick(self):
        now = time.monotonic()
        self.output.check()
        self.timings.measure("audio_drain", self.audio.drain)
        self.timings.measure("adapter_pump", self.adapter.pump, now=now)
        self.frontier = self.adapter.frontier
        last = self.media.order.last_selected_pts
        if (
            self.frontier is not None
            and last is not None
            and self.media.run.ticks_at(self.frontier) - last
            >= round(self.settings.heartbeat_s * 90000)
        ):
            self.timings.measure("heartbeat", self.adapter.heartbeat, self.frontier, now=now)
        self.timings.measure("media_drain", self.media.drain)
        self.timings.measure("media_age", self.media.check_age, now)
        self.timings.measure("report", self.owner.report, self)

    def _finish_media(self):
        if self.media is None:
            if self.mux is not None:
                self.mux.abort()
            return
        if self.media.closed:
            return
        # Native disarm permanently cancels unpublished work. A final interval
        # is justified only by the last fully consumed source certificate.
        status = self.adapter.status() if self.adapter is not None else None
        if (
            self.error is None
            and status is not None
            and self.audio is not None
            and not status["pending"]
            and self.frontier is not None
            and self.media.seal(self.frontier)
            and self.audio.finish(self.frontier)
            and self.media.finish(audio_drained=True)
        ):
            return
        self.media.abort(
            self.error or "capture stopped with an uncompleted native suffix"
        )

    def _finish_helper(self):
        controller = self.controller
        if controller is None:
            return
        closed = close_encoder(controller, timeout=min(1.0, self.settings.close_s),
                               poll_s=self.settings.poll_s)
        if not closed:
            log.warning("GPU helper could not close cleanly; disposing owned process: %s",
                        controller.status())
            controller.stop("GPU capture request retired without native Close")
        deadline = time.monotonic() + self.settings.close_s
        while not controller.status()["done"] and time.monotonic() < deadline:
            time.sleep(self.settings.poll_s)
        status = controller.status()
        disposal = status["disposal"]
        if not (
            status["done"]
            and disposal
            and disposal["empty"]
            and disposal["handle_closed"]
            and disposal["active_processes"] == 0
        ):
            raise RuntimeError("GPU helper disposal is unproved; capture stays retired")

    def _close_channel(self):
        if self.channel is not None:
            self.channel.close()

    def _finish_output(self):
        error = self.error or (self.media.fault if self.media else "media setup failed")
        if self.output is not None:
            try:
                self.output.finish(error, timeout=self.settings.close_s)
                self._output_joined = True
            except PublicationError:
                self._output_joined = True
                raise
            finally:
                log.info("GPU publication at retirement: %s", self.output.status())
        elif self.archive is not None:
            self.archive.finish(error)

    def close(self):
        # Return actual native key custody before potentially slow publication.
        # Every owner still gets cleanup if an earlier step fails. A completed
        # publication error must never hide unproved channel/helper/writer exit.
        errors = []
        operations = (
            lambda: self.demand.request_stop("GPU capture run ended"),
            lambda: self.owner.end_audio(self.handoff),
            self._close_channel,
            self._finish_helper,
            self._finish_media,
            self._finish_output,
        )
        for operation in operations:
            try:
                operation()
            except Exception as exc:  # noqa: BLE001 - finish each independent owned resource before raising.
                errors.append(exc)
        if errors:
            if self._output_joined:
                errors = [
                    PublicationError(str(e))
                    if isinstance(e, PublicationWriteError)
                    or isinstance(e.__cause__, PublicationWriteError) else e
                    for e in errors
                ]
            raise next((e for e in errors if not isinstance(e, PublicationError)), errors[0])
        self.state = "stopped"
