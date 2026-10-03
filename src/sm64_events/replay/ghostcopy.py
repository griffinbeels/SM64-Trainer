"""The ghost copy lane: a second recording, with ghosts, beside the clean one.

One lane per switch-on edge inside one CaptureSession. It follows the clean
stream through GpuMedia's follower hook, runs its own encoder helper (its own
process, job and NVENC session) against the producer's two twin textures, and
writes exactly one twin packet per followed clean picture -- the same pts and
duration -- into a sink the owner provides:

- a twin bridge for that pts: Submit it (the ghost picture of that frame);
- no bridge, and the producer resolved that pts, or a clean repeat: Repeat the
  twin encoder's last picture, so the twin timeline never has a hole.

Coverage starts at the first twin bridge; earlier pictures are dropped, and
the first twin packet must be a keyframe. PCM follows its picture in clean
order, mixed with the ghost sound the owner's timeline heard.

Every call runs on the media worker thread and none waits: startup and stop
advance one step per tick, so the clean tick never stalls behind the lane.
CaptureSession turns any exception into stop(reason). The clean run never
sees a lane fault; the lane never retries itself.
"""

from collections import deque
from dataclasses import dataclass
import logging
import time
from uuid import uuid4

from sm64_events.replay import gpuchannel
from sm64_events.replay.gpudiagnostics import native_reason
from sm64_events.replay.gpuencoder import Result
from sm64_events.replay.gpupublication import PublicationBusyError, PublicationError
from sm64_events.replay.media import MediaRun
from sm64_events.replay.packetmux import EncodedPicture

log = logging.getLogger("sm64.replay")

# Stop waits this long for the producer's STOPPED before closing the helper.
RELEASE_S = 2.0
# Bookkeeping charged per followed event beside its PCM bytes.
EVENT_BYTES = 64


def twin_reason(reason):
    """The producer's twin reason, tagged by namespace or a bare GD code."""
    return native_reason(reason, channel=bool(reason & 0xF0000000))


def mix_ghost(game, ghost):
    # audio.py owns the saturating mix; imported here so the lane module
    # loads without the audio stack.
    from sm64_events.replay import audio

    return audio.mix_ghost(game, ghost)


@dataclass(slots=True)
class Followed:
    """One clean admission: a picture (pts set) or a PCM block (pcm set)."""

    created: float
    pts: int | None = None
    duration: int = 0
    repeat: bool = False
    pcm: bytes | None = None
    first_us: int = 0

    @property
    def size(self):
        return EVENT_BYTES + (len(self.pcm) if self.pcm is not None else 0)


class GhostCopyLane:
    def __init__(self, owner, channel, media, open_command, *, codec, width, height,
                 settings, lane, controller_factory, launch, mix=mix_ghost,
                 clock=time.monotonic):
        self.owner, self.channel, self.media = owner, channel, media
        # The clean Open exactly, codec included, but with the twin names.
        self.open_command = dict(open_command, names=list(channel.twin_texture_names))
        self.codec, self.size = codec, (width, height)
        self.settings, self.lane = settings, lane
        self.controller_factory, self.launch, self.mix = controller_factory, launch, mix
        self.clock = clock
        self.phase = "new"
        self.reason = self._fault = None
        self.controller = self.sink = self.run = None
        self._events = deque()
        self._bytes = 0
        self._start_pts = None
        self._covering = False
        self._last_pts = None
        self._bridges = {}   # pts -> twin Bridge published and not yet submitted
        self._admitted = {}  # slot -> (Bridge, serial) whose key the helper holds
        self._inflight = None
        self._retained = None
        self._serial = self._last_submitted = 0
        self._status = None
        self._requested = False
        self._deadline = None
        self._close_id = None
        self._sink_ended = False
        self._sink_error = None
        self.counts = dict(submitted=0, repeated=0, missed=0, published=0, dropped=0)

    @property
    def stopped(self):
        return self.phase == "stopped"

    # ---- follower: called by GpuMedia after each clean admission ----------

    def video(self, pts, duration, repeat):
        if self.reason is not None or self.phase not in ("requested", "running"):
            return
        if self._start_pts is not None and not self._covering:
            if pts < self._start_pts:
                return self._drop()
            if pts > self._start_pts:
                # The tick stops on it, with this reason rather than a detach.
                self._fault = "the first ghost copy picture was never followed"
                return
            self._covering = True
        self._append(Followed(self.clock(), pts=pts, duration=duration, repeat=repeat))

    def pcm(self, data, first_us):
        if self.reason is not None or self.phase not in ("requested", "running"):
            return
        if self._start_pts is not None and not self._covering:
            return self._drop()
        self._append(Followed(self.clock(), pcm=bytes(data), first_us=first_us))

    def _append(self, event):
        self._events.append(event)
        self._bytes += event.size
        if not self._covering:
            # Before coverage only a first bridge's own picture can matter.
            while self._events and self._bytes > self.settings.pending_bytes:
                self._pop()
                self._drop()

    def _pop(self):
        event = self._events.popleft()
        self._bytes -= event.size
        return event

    def _drop(self):
        self.counts["dropped"] += 1

    # ---- start --------------------------------------------------------

    def start(self, now):
        command, identity, env = self.launch()
        self.controller = self.controller_factory(
            command=command, identity=identity, env=env, limits=self.settings.helper(),
        )
        request = self.controller.enqueue(self.open_command)
        if type(request) is not int:
            raise RuntimeError("the ghost copy helper refused its Open")
        self._inflight = (request, "Open", None, None, self.open_command)
        self.phase = "opening"
        self._deadline = now + self.settings.startup_s
        log.info("Ghost copy %d starting: codec=%s shape=%dx%d", self.lane, self.codec,
                 *self.size)

    def tick(self, now):
        if self.reason is not None:
            self._advance_stop(now)
        elif self.phase == "opening":
            self._opening(now)
        elif self.phase == "sink":
            self._sink_ready(now)
        elif self.phase in ("requested", "running"):
            self._running(now)

    def _helper_fault(self):
        fault = self.controller.status()["fault"]
        if fault:
            raise RuntimeError(f"ghost copy helper: {fault}")

    def _opening(self, now):
        self._helper_fault()
        reply = self.controller.take_result()
        if reply is None:
            if now > self._deadline:
                raise TimeoutError("the ghost copy encoder did not open in time")
            return
        request = self._inflight[0]
        self._inflight = None
        metadata = reply.metadata
        if reply.request_id != request:
            raise RuntimeError("ghost copy helper reply does not match its Open")
        if (metadata["result"] == Result.CODEC
                and not metadata["worker_disposal_required"]):
            raise RuntimeError(f"this GPU refused a second {self.codec} encoder")
        if (metadata["result"] != 0 or metadata["error"]
                or metadata["worker_disposal_required"]):
            raise RuntimeError(metadata["error"] or "ghost copy encoder Open failed")
        self._retained = metadata["retained"]
        self.sink = self.owner.ghost_copy_sink(self.codec, *self.size)
        self.phase = "sink"
        self._deadline = now + self.settings.startup_s

    def _sink_ready(self, now):
        self._helper_fault()
        if not self.sink.ready:
            if now > self._deadline:
                raise TimeoutError("the ghost copy sink did not prepare in time")
            return
        self.sink.check()
        self._requested = True  # a failed wake can follow a written READY
        self.channel.twin_request(self.lane)
        self.media.follower = self
        self.phase = "requested"
        self._deadline = now + self.settings.startup_s

    # ---- running ------------------------------------------------------

    def _running(self, now):
        if self._fault:
            raise RuntimeError(self._fault)
        self._helper_fault()
        self.sink.check()
        if self.media.follower is not self:
            raise RuntimeError("the clean recording stopped feeding the ghost copy")
        self._take_reply()
        self._observe(now)
        self._dispatch()
        self._bound(now)

    def _observe(self, now):
        # Status before bridges: the producer publishes a bridge before it
        # resolves that pts, so this order never mistakes a bridge for a miss.
        try:
            status = self.channel.twin_status()
            bridges = self.channel.twin_bridges()
        except BlockingIOError:
            self._status = None  # torn: no news, and no miss decided this tick
            return
        self._status = status if status.lane == self.lane else None
        if self._status is not None:
            if status.state == gpuchannel.TWIN_STOPPED:
                raise RuntimeError("the capture layer stopped the ghost copy: "
                                   + twin_reason(status.reason))
            if status.state == gpuchannel.TWIN_ACTIVE and self.phase == "requested":
                self.phase = "running"
                log.info("Ghost copy %d active", self.lane)
        if self.phase == "requested" and now > self._deadline:
            raise TimeoutError("the capture layer did not start the ghost copy in time")
        for bridge in bridges:
            if bridge.pts in self._bridges or (
                self._last_pts is not None and bridge.pts <= self._last_pts
            ):
                raise RuntimeError("twin bridge for a picture already resolved")
            self._bridges[bridge.pts] = bridge
            if self._start_pts is None:
                self._begin(bridge.pts)

    def _begin(self, pts):
        """Coverage starts at the first bridge: drop everything before its picture."""
        self._start_pts = pts
        while self._events:
            head = self._events[0]
            if head.pts is not None and head.pts >= pts:
                if head.pts > pts:
                    raise RuntimeError("the first ghost copy picture was never followed")
                self._covering = True
                break
            self._pop()
            self._drop()
        log.info("Ghost copy %d covers from pts %d", self.lane, pts)

    def _dispatch(self):
        while self._inflight is None and self._covering and self._events:
            head = self._events[0]
            if head.pcm is not None:
                # PCM that followed a published picture follows it here too.
                self._write_pcm(self._pop())
                continue
            if head.repeat:
                self.counts["repeated"] += 1
                return self._send("Repeat", head)
            bridge = self._bridges.get(head.pts)
            if bridge is not None:
                return self._send("Submit", head, bridge)
            if self._status is not None and self._status.resolved_pts >= head.pts:
                self.counts["missed"] += 1
                return self._send("Repeat", head)
            break
        if self._inflight is None and self._admitted:
            self._enqueue(dict(op="Poll"), "Poll")

    def _write_pcm(self, event):
        data = event.pcm
        getter = getattr(self.owner, "ghost_timeline", None)
        timeline = getter() if getter is not None else None
        if timeline is not None:
            data = self.mix(data, timeline.take(event.first_us, len(data) // 4))
        self.sink.write_pcm(data, event.first_us)

    def _enqueue(self, command, op, head=None, bridge=None):
        request = self.controller.enqueue(command)
        if type(request) is not int:
            return False  # bounded helper capacity: the next tick asks again
        self._inflight = (request, op, head, bridge, command)
        return True

    def _send(self, op, head, bridge=None):
        serial = self._serial + 1
        if head is None:  # a release-time Submit: it only returns its key
            pts, duration = bridge.pts, bridge.nominal_duration
        else:
            pts, duration = head.pts, head.duration
        common = dict(serial=serial, pts=pts, encoder_duration=duration)
        if op == "Repeat":
            retained = self._retained
            if not retained or not retained["valid"]:
                raise RuntimeError("the ghost copy encoder holds no picture to repeat")
            command = dict(op="Repeat", generation=retained["generation"],
                           force_idr=False, **common)
        else:
            command = dict(op="Submit", slot=bridge.texture_index,
                           bridge_token=bridge.token, force_idr=serial == 1, **common)
        return self._enqueue(command, op, head, bridge)

    def _take_reply(self):
        reply = self.controller.take_result()
        if reply is None:
            return False
        if self._inflight is None or reply.request_id != self._inflight[0]:
            raise RuntimeError("ghost copy helper reply does not match its command")
        _, op, head, bridge, command = self._inflight
        self._inflight = None
        metadata = reply.metadata
        code = metadata["result"]
        if metadata["error"] or metadata["worker_disposal_required"]:
            raise RuntimeError(metadata["error"] or "ghost copy encoder custody uncertain")
        if (code not in (0, 9, 10) or (op == "Repeat" and code != 0)
                or (op == "Poll" and code == 9)):
            raise RuntimeError("unexpected ghost copy helper result")
        self._retained = metadata["retained"]
        if op in ("Submit", "Repeat") and code == 0:
            serial = command["serial"]
            expected = serial if op == "Submit" else self._last_submitted
            if (not self._retained or not self._retained["valid"]
                    or self._retained["selected_serial"] != expected):
                raise RuntimeError("ghost copy encoder retained a different picture")
            self._serial = serial
            if op == "Submit":
                self._last_submitted = serial
                del self._bridges[bridge.pts]
                self._admitted[bridge.texture_index] = (bridge, serial)
                self.counts["submitted"] += 1
            self._packet(op, head, command, metadata["packet"], reply.payload)
        self._custody(metadata["key_returns"])
        return True

    def _packet(self, op, head, command, packet, payload):
        if (not isinstance(packet, dict) or not payload
                or type(packet.get("keyframe")) is not bool):
            raise RuntimeError("ghost copy helper packet is malformed")
        if command["serial"] == 1 and not packet["keyframe"]:
            raise RuntimeError("the ghost copy's first picture was not a keyframe")
        if head is None or self.reason is not None:
            return  # a stopping lane only returns keys; its packets are dropped
        if not self._events or self._events[0] is not head:
            raise RuntimeError("ghost copy packet passed its picture")
        if self.run is None:
            # Same origin as clean, so twin pts are clean pts: the twin run may
            # start at pts > 0, and a viewer pairs the two copies by UTC.
            self.run = MediaRun(uuid4().hex, self.media.run.origin_ts)
            self.sink.bind(self.run)
        self.sink.publish_picture(
            EncodedPicture(command["serial"], head.pts, head.duration,
                           packet["keyframe"], payload),
            None,
            self.run.origin_ts + head.pts / 90000,
            media_run=self.run,
            pts=head.pts,
            source_id=f"ghostcopy:{head.pts}",
            repeat=op == "Repeat",
        )
        self._pop()
        self._last_pts = head.pts
        self.counts["published"] += 1

    def _custody(self, returns):
        for receipt in returns:
            held = self._admitted.get(receipt.get("slot"))
            if held is None:
                raise RuntimeError("ghost copy key return lacks a submitted bridge")
            bridge, serial = held
            if receipt != {"slot": bridge.texture_index, "bridge_token": bridge.token,
                           "serial": serial}:
                raise RuntimeError("ghost copy key return differs from its bridge")
            self.channel.acknowledge_twin_metadata(bridge)
            del self._admitted[bridge.texture_index]

    def _bound(self, now):
        limit, budget = self.settings.max_age, self.settings.pending_bytes
        if not self._covering:
            while self._events and now - self._events[0].created > limit:
                self._pop()
                self._drop()
            return
        if self._events and now - self._events[0].created > limit:
            raise RuntimeError(f"the ghost copy fell more than {limit:g} s behind")
        if self._bytes > budget:
            raise RuntimeError("the ghost copy's pending pictures exceeded their budget")

    # ---- stop -----------------------------------------------------------

    def stop(self, reason):
        """Begin the stop; never raises. tick() then advances it one step at
        a time: release, drain keys until STOPPED (or RELEASE_S), close the
        helper through the clean retirement steps, finish the sink."""
        if self.reason is not None:
            return
        try:
            self.reason = str(reason)[:512] or "ghost copy stopped"
            if self.media.follower is self:
                self.media.follower = None
            self._events.clear()
            self._bytes = 0
            log.info("Ghost copy %d stopping: %s %s", self.lane, self.reason, self.counts)
            self.phase = "releasing" if self._requested else "closing"
            self._deadline = self.clock() + RELEASE_S
            if self._requested:
                self.channel.twin_release()
        except Exception:  # noqa: BLE001 - the clean run must never see a lane fault.
            log.exception("Ghost copy %d release failed", self.lane)
            self._closing(self.clock())

    def close(self, reason="the recording stopped"):
        """CaptureSession.close: the same stop, driven to its end here."""
        self.stop(reason)
        limit = self.clock() + RELEASE_S + 3 * self.settings.close_s + 1.0
        while not self.stopped and self.clock() < limit:
            self._advance_stop(self.clock())
            if not self.stopped:
                time.sleep(self.settings.poll_s)
        if not self.stopped:
            log.warning("Ghost copy %d did not finish stopping: phase=%s",
                        self.lane, self.phase)

    def _advance_stop(self, now):
        step = getattr(self, "_stop_" + self.phase, None)
        if step is None:
            return
        try:
            step(now)
        except Exception:  # noqa: BLE001 - every stop step ends in a later one.
            log.exception("Ghost copy %d stop step %s failed", self.lane, self.phase)
            following = dict(releasing="closing", closing="disposing",
                             disposing="finishing", finishing="stopped")
            if self.phase == "closing" and self.controller is not None:
                self.controller.stop("ghost copy helper close failed")
            self._enter(following[self.phase], now)

    def _enter(self, phase, now):
        self.phase = phase
        if phase == "closing":
            self._deadline = now + min(1.0, self.settings.close_s)
        elif phase in ("disposing", "finishing"):
            self._deadline = now + self.settings.close_s
        elif phase == "stopped":
            log.info("Ghost copy %d stopped: %s %s", self.lane, self.reason, self.counts)

    def _closing(self, now):
        self._enter("closing", now)

    def _stop_releasing(self, now):
        if self.controller.status()["fault"]:
            return self._enter("closing", now)
        # Keys still move: every twin credit the helper holds is returned, and
        # a bridge never submitted is submitted now, its packet discarded, so
        # the producer can retire it instead of abandoning it.
        self._take_reply()
        try:
            for bridge in self.channel.twin_bridges():
                self._bridges[bridge.pts] = bridge
        except BlockingIOError:
            pass
        if self._inflight is None:
            if self._bridges:
                self._send("Submit", None, self._bridges[min(self._bridges)])
            elif self._admitted:
                self._enqueue(dict(op="Poll"), "Poll")
        done = now > self._deadline
        try:
            self.channel.status()  # the clean channel ended: nothing more will move
            status = self.channel.twin_status()
            done = done or status.lane != self.lane or status.state in (
                gpuchannel.TWIN_OFF, gpuchannel.TWIN_STOPPED)
        except BlockingIOError:
            pass
        except (gpuchannel.ChannelStopped, ProcessLookupError):
            done = True
        if done:
            self._enter("closing", now)

    def _stop_closing(self, now):
        controller = self.controller
        if controller is None:
            return self._enter("finishing", now)
        status = controller.status()
        if status["fault"] or status["done"]:
            return self._enter("disposing", now)
        if self._inflight is not None:
            # The last command's reply first: it may return keys. An Open
            # still in flight settles here too (its reply has none).
            if self._inflight[1] == "Open":
                if controller.take_result() is not None:
                    self._inflight = None
            else:
                self._take_reply()
        else:
            reply = controller.take_result()
            if reply is not None:
                metadata = reply.metadata
                if (reply.request_id != self._close_id or metadata["error"]
                        or metadata["worker_disposal_required"]
                        or metadata["result"] not in (0, 10)):
                    controller.stop("ghost copy helper failed to close")
                    return self._enter("disposing", now)
                if metadata["result"] == 0:
                    return self._enter("disposing", now)
                self._close_id = None  # native Close pending: ask again
        if self._inflight is None and self._close_id is None and not status["pending_count"]:
            request = controller.enqueue({"op": "Close"})
            if type(request) is int:
                self._close_id = request
        if now > self._deadline:
            controller.stop("ghost copy helper did not close in time")
            self._enter("disposing", now)

    def _stop_disposing(self, now):
        status = self.controller.status()
        disposal = status["disposal"]
        if (status["done"] and disposal and disposal["empty"]
                and disposal["handle_closed"] and disposal["active_processes"] == 0):
            return self._enter("finishing", now)
        if now > self._deadline:
            self.controller.stop("ghost copy helper disposal deadline")
            log.warning("Ghost copy %d helper disposal is unproved: %s", self.lane, status)
            self._enter("finishing", now)

    def _stop_finishing(self, now):
        sink = self.sink
        if sink is None:
            return self._enter("stopped", now)
        if not self._sink_ended:
            self._sink_ended = True
            # Keep what was published: a bound, healthy sink closes complete.
            if self.run is not None and sink.status()["error"] is None:
                sink.close()
            else:
                self._sink_error = self.reason
                sink.abort()
        try:
            sink.finish(self._sink_error, timeout=0)
        except PublicationBusyError:
            if now <= self._deadline:
                return
            log.warning("Ghost copy %d sink did not finish in time", self.lane)
        except PublicationError as exc:
            log.warning("Ghost copy %d sink failed: %s", self.lane, exc)
        self._enter("stopped", now)
