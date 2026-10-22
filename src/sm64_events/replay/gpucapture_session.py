"""One GPU request, helper and fragment run, owned by the recorder media worker."""

from functools import lru_cache
import hashlib
import logging
from pathlib import Path
import sys
import struct
import time

from sm64_events.core.childproc import python_child
from sm64_events.core.paths import bundled_encoder_dll
from sm64_events.replay.gpucapture import GpuBackendUnsupported, GpuCleanupError, GpuStartupUnavailable
from sm64_events.replay import gpuchannel
from sm64_events.replay.gpuinput import OfferDecoder, ChannelSelection
from sm64_events.replay.ghostcopy import GhostCopyLane
from sm64_events.replay.gpumedia import GpuMedia
from sm64_events.replay.gpuaudio import GpuAudio
from sm64_events.replay.gpudiagnostics import CaptureTimings, failure_snapshot
from sm64_events.replay.encoderstage import staged_encoder_dll
from sm64_events.replay.gpuencoder import EncoderFailure, Result
from sm64_events.replay.gpuencoder_abi import CODEC_STREAM_NAMES
from sm64_events.replay.gpupublication import PublicationError, PublicationWriteError
from sm64_events.replay.gpumediaworker import MediaWorker
from sm64_events.replay.gpusettings import (
    backend_unsupported,
    note_backend_unsupported,
    note_codec_unavailable,
    recording_codec,
)
from sm64_events.replay.gpuprocess.retirement import close_encoder
from sm64_events.replay.gpuprocess.process_controller import Controller
from sm64_events.replay.gpuprocess import process_worker
from sm64_events.replay.media import MediaRun
from sm64_events.replay.packetmux import NativeFormat
from sm64_events.replay.ownedclose import CleanupPending

log = logging.getLogger("sm64.replay")

# Distinct from None (stop) and from a successful open tuple.
_RETRY_OTHER_CODEC = object()

# Lease supervisor reasons (gpudemand.py) that mean the Project64 process is gone.
_PRODUCER_GONE = frozenset({"producer_closed", "producer_pid_changed", "producer_identity_changed"})


def retirement_line(snapshot, cancelled: bool) -> str:
    """The one INFO line for a normal end of GPU recording, by its cause."""
    if cancelled:
        return "GPU recording stopped (recorder stop)"
    if snapshot.reason in _PRODUCER_GONE:
        return "Project64 closed; recording stopped"
    if snapshot.reason == "rom_closed":
        return "Project64 closed the ROM; recording stopped"
    return f"GPU capture ended with its producer ({snapshot.reason}); reconnecting"


@lru_cache(maxsize=4)
def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def helper_launch():
    """(command, identity, env) for the encoder helper.

    A direct child either way (core/childproc.py): the installed exe with its
    helper flag, or the base interpreter running the module, never the venv
    launcher that would drop the no-busy-cursor flag."""
    command, env = python_child(process_worker.__name__, process_worker.GPU_HELPER_FLAG)
    entry = sys.executable if getattr(sys, "frozen", False) else process_worker.__file__
    return command, {"entry": str(Path(entry).resolve()), "entry_sha256": _sha256(entry)}, env


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
        self.open_command = None  # that Open, which the ghost copy repeats
        # The ghost copy lane: started on a switch-on edge, never retried
        # within this session after it stops.
        self.copy_lane = None
        self._copy_wanted = self._copy_edge = False
        self._copy_lanes = 0
        self.audio = self.archive = self.handoff = self.mux = None
        self.output = None
        self._output_joined = False
        self.frontier = None
        self.settings = owner.settings
        self.state = "preparing"
        self.error = None
        self.timings = CaptureTimings()
        self._stage = "channel"
        self._report_failure = False
        self._attempted_options = None
        self._adapter_facts = None
        self._failure_snapshot = None
        self._helper_identity = None

    def _continue(self):
        snapshot = self.demand.snapshot
        if snapshot.lifecycle:
            return False
        if snapshot.state == "fault":
            from sm64_events.replay.gpudiagnostics import unsupported_capture_reason

            unsupported = unsupported_capture_reason(getattr(snapshot, "backend_reason", 0))
            if unsupported is not None and not getattr(snapshot, "cleanup_error", ""):
                raise GpuBackendUnsupported(unsupported)
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
        from sm64_events.replay.gpuadapter import adapter_facts

        self._adapter_facts = adapter_facts(luid)
        if self._adapter_facts is None:
            raise RuntimeError("capture adapter identity is unavailable; GPU recording will retry")
        # Before any helper starts: a positively incompatible adapter
        # (software or a known-too-old NVIDIA driver) is a verdict,
        # not a fault to retry every 10-60 s forever (gpuadapter.py).
        reason = backend_unsupported(luid)
        if reason is not None:
            raise GpuBackendUnsupported(reason)
        dll = bundled_encoder_dll()
        if dll is None:
            raise RuntimeError("this build carries no SM64GpuEncoderV1.dll; run tools/build_plugin.py")
        dll = staged_encoder_dll(dll)  # never hold the shipped file open
        command, identity, env = helper_launch()
        self.controller = self.controller_factory(
            command=command, identity=identity, env=env, limits=self.settings.helper(),
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
                nominal_rate=self.owner.nominal_rate, codec=codec, facts=self._adapter_facts,
            ),
        )
        self._attempted_options = command["options"]
        request_id = self.controller.enqueue(command)
        if type(request_id) is not int:
            raise RuntimeError("encoder bootstrap admission refused")
        while self._continue():
            reply = self.controller.take_result()
            if reply is None:
                status = self.controller.status()
                if status["fault"]:
                    # Terminal fault publication can race the first queue read.
                    # Consume its already-validated reply before discarding the
                    # encoder result/status; never enqueue another Open here.
                    reply = self.controller.take_result()
                    if reply is None:
                        failure = EncoderFailure(
                            f"{status['fault']}; encoder result=unknown; native status=unavailable",
                            worker_disposal_required=True,
                        )
                        failure.operation = "Open"
                        raise failure
            if reply is not None:
                if reply.request_id != request_id:
                    raise RuntimeError("GPU encoder Open reply request mismatch")
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
                    reply.request_id == request_id
                    and reply.metadata["result"] == Result.CODEC
                    and not reply.metadata["worker_disposal_required"]
                ):
                    # Refused by codec with nothing left to try: this adapter
                    # has no codec the recorder can write.
                    reason = "this GPU's video encoder offers neither AV1 nor H.264"
                    note_backend_unsupported(luid, reason)
                    raise GpuBackendUnsupported(reason)
                if (
                    reply.metadata["result"] != 0
                    or reply.metadata["error"]
                    or reply.metadata["worker_disposal_required"]
                ):
                    result = Result(reply.metadata["result"])
                    status = reply.metadata.get("status")
                    message = reply.metadata["error"] or "GPU encoder Open failed"
                    failure = EncoderFailure(
                        f"{message}; encoder result={int(result)}:{result.name}; "
                        f"native status={'available' if status is not None else 'unavailable'}",
                        result=result, status=status,
                        worker_disposal_required=reply.metadata["worker_disposal_required"],
                    )
                    failure.operation = "Open"
                    raise failure
                self.codec = CODEC_STREAM_NAMES[codec]
                self._helper_identity = reply.metadata.get("identity")
                # Native has names/contexts ready but has captured no pixels yet.
                # Cold AAC/filter/format preparation must finish before source
                # admission. Its pool holds milliseconds, not a startup backlog.
                if not self._measure_stage("sink_prepare", self._prepare_sink):
                    return None
                self.handoff = self.owner.begin_audio()
                self.channel.encoder_ready()
                return command, request_id, reply
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
        options = bootstrap[0]["options"]
        backend = "windows" if options.get("backend", 0) == 1 else "nvenc"
        run = MediaRun.starting_at(decoder.timestamp(offers[0].boundary_qpc),
                                  encoder_backend=backend)
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
        self.open_command = command
        self.adapter.attach_open(
            command, request_id, reply, initial_offers=offers, now=time.monotonic()
        )
        self.state = "recording"
        # The codec actually opened, and what startup cost stage by stage, on
        # EVERY run: the failure-only record made startup look slower than
        # it is (audit 2026-09-25).
        log.info(
            "GPU recording active: run=%s source=%s shape=%dx%d adapter=%s codec=%s "
            "native=%s stages=%s",
            run.id,
            decoder.namespace,
            h.width,
            h.height,
            (h.luid_high, h.luid_low),
            self.codec,
            reply.metadata.get("identity"),
            self.timings.summary(),
        )

    def _measure_stage(self, name, operation, *args):
        self._stage = name
        return self.timings.measure(name, operation, *args)

    def _record_failure(self, error):
        self.error = str(error)[:512]
        self._report_failure = True

    def _failure_exception(self, exc, snapshot):
        """Keep native cause and classify only the observed no-channel deadline."""
        if snapshot.reason:
            self.error = f"{self.error}; demand={snapshot.state}:{snapshot.reason}"[:512]
        if (self._stage == "channel" and self.channel is None and not snapshot.cancelled
                and self.owner.want_capture()
                and (snapshot.reason == "capture_setup_deadline"
                     or snapshot.backend_reason == 10012  # native GD_DEADLINE
                     or isinstance(exc, TimeoutError) and str(exc) == "GPU recording startup deadline")):
            return GpuStartupUnavailable(self.error)
        return RuntimeError(self.error) if snapshot.reason else None

    def run(self):
        try:
            if not self._measure_stage("channel", self._open_channel):
                return
            bootstrap = self._measure_stage("encoder", self._open_encoder)
            if bootstrap is None:
                return
            offers = self._measure_stage("first_offer", self._first_offers)
            if not offers:
                return
            self._measure_stage("media_setup", self._create_media, bootstrap, offers)
            self._stage = "recording"
            self._retain_diagnostic()
            while self._continue():
                self.timings.measure("tick", self.tick)
                self.owner.wait(self.settings.poll_s)
        except gpuchannel.LayerVersionMismatch as exc:
            # Project64 loaded a capture layer from another build (it was
            # running when this build's refresh came). No retry can succeed
            # until it restarts, which is when the refresh installs ours.
            self._record_failure(exc)
            raise GpuBackendUnsupported(str(exc)) from exc
        except GpuBackendUnsupported as exc:
            self._record_failure(exc)
            raise  # A verdict about the adapter; GpuCapture routes it, unwrapped.
        except Exception as exc:
            self.error = str(exc)[:512]
            # Taken before cleanup; logged only if this is not a normal end.
            before_cleanup = failure_snapshot(
                exc, frontier=self.frontier, adapter=self.adapter, output=self.output,
            )
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
                log.info(retirement_line(snapshot, cancelled and not retired))
                return
            log.info("GPU capture failure before cleanup: %s", before_cleanup)
            self._failure_snapshot = before_cleanup
            self._report_failure = True
            # GpuCapture logs once at the owning boundary. Preserve the lease
            # supervisor's reason as well as the media symptom; native channel
            # closure can precede that supervisor's next bounded poll.
            failure = self._failure_exception(exc, snapshot)
            if failure is not None:
                raise failure from exc
            raise
        finally:
            try:
                self.close()
            except PublicationError:
                raise  # Finished writer failure is not unproved resource disposal.
            except Exception as exc:
                self._report_failure = True
                self.error = self.error or str(exc)[:512]
                raise GpuCleanupError("GPU capture cleanup unproved") from exc
            finally:
                self._retain_diagnostic()
                # Every stop, clean ones included: a failure-only record
                # weights every startup and tick distribution toward failures.
                log.info("GPU capture stage timings at stop: codec=%s error=%s %s",
                         self.codec, self.error, self.timings.summary())

    def _retain_diagnostic(self):
        """After helper stderr drain, keep scalar evidence outside source lifetime."""
        from sm64_events.replay.gpufielddiagnostics import native_failure_receipt

        try:
            snapshot = self.demand.snapshot
            options = self._attempted_options or {}
            record = dict(observed_unix_s=time.time(), stage=self._stage,
                          reason=self.error, codec=self.codec,
                          requested_codec=CODEC_STREAM_NAMES.get(options.get("codec")),
                          encoder_backend="windows" if options.get("backend") == 1 else "nvenc" if options else "unknown",
                          capture_build=getattr(snapshot, "capture_build", ""),
                          native_reason=getattr(snapshot, "backend_reason", 0),
                          startup=getattr(snapshot, "native_startup", None),
                          stage_timings=self.timings.summary())
            facts = self._adapter_facts
            if self.channel is not None:
                h = getattr(self.channel, "header", None)
                if h is not None:
                    record["adapter_luid"] = [h.luid_high, h.luid_low]
            if facts is not None:
                record.update(gpu_name=facts.name, gpu_vendor_id=facts.vendor_id, gpu_driver=facts.driver)
            if self._failure_snapshot is not None:
                record["failure"] = self._failure_snapshot
                encoder = self._failure_snapshot.get("encoder_failure") or {}
                if encoder.get("result") is not None:
                    record["encoder_result"] = encoder["result"]
                status = encoder.get("native_status") or {}
                for name, key in (("hresult", "native_hresult"), ("encoder_error", "encoder_error"), ("nv_status", "driver_error")):
                    if isinstance(status.get(name), int):
                        record[key] = status[name]
            if self.controller is not None:
                try:
                    helper = self.controller.status()
                    receipt = native_failure_receipt(helper.get("stderr_tail")) if self._report_failure else {}
                    record.update(receipt)
                    record["native_receipt_available"] = bool(receipt)
                except Exception:  # noqa: BLE001 - retain other evidence when a diagnostic source fails.
                    record["native_receipt_available"] = False
            identity = self._helper_identity or {}
            if isinstance(identity.get("dll_sha256"), str):
                record["helper_dll_sha256"] = identity["dll_sha256"][:64]
            self.owner._last_diagnostic = record
            gate = getattr(self.owner, "retry_gate", None)
            if gate is not None:
                gate.note_diagnostic(self.owner.producer_identity, record, failed=self._report_failure)
        except Exception:  # noqa: BLE001 - diagnostics cannot replace the capture/cleanup outcome.
            log.debug("GPU diagnostic receipt unavailable", exc_info=True)

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
        self.timings.measure("copy_lane", self._tick_copy_lane, now)
        self.timings.measure("report", self.owner.report, self)

    def _tick_copy_lane(self, now):
        """The ghost copy's whole tick. Nothing raised here reaches the clean
        run: a lane exception becomes that lane's stop."""
        lane = self.copy_lane
        try:
            # P3's owner method; an owner without it never wants a copy.
            wanted = getattr(self.owner, "ghost_copy_wanted", None)
            wanted = bool(wanted()) if wanted is not None else False
            if wanted and not self._copy_wanted:
                self._copy_edge = True
            self._copy_wanted = wanted
            if lane is not None and not lane.stopped:
                if not wanted:
                    lane.stop("Ghosts in recording was switched off")
                lane.tick(now)
            elif self._copy_edge and wanted:
                self._copy_edge = False
                self._start_copy_lane(now)
        except Exception as exc:  # noqa: BLE001 - a lane fault stops only the lane.
            lane = self.copy_lane
            if lane is None or lane.stopped:
                log.exception("Ghost copy tick failed")
            else:
                lane.stop(str(exc) or type(exc).__name__)

    def _start_copy_lane(self, now):
        h = self.channel.header
        self._copy_lanes += 1
        self.copy_lane = GhostCopyLane(
            self.owner, self.channel, self.media, self.open_command,
            codec=self.codec, width=h.even_width, height=h.even_height,
            settings=self.settings, lane=self._copy_lanes,
            controller_factory=self.controller_factory, launch=helper_launch,
        )
        self.copy_lane.start(now)

    def _close_copy_lane(self):
        # Before the clean helper retires and while the channel is open: the
        # twin release and its key returns travel through this channel.
        lane = self.copy_lane
        if lane is not None and not lane.stopped:
            try:
                lane.close()
            except Exception:  # noqa: BLE001 - never a clean retirement error.
                log.exception("Ghost copy close failed")

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
            self._close_copy_lane,
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
