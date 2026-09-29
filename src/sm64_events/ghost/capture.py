"""Recorder-owned native pose pump; Python never paces ghost playback frames."""
from __future__ import annotations

import logging
import struct
import threading
import time
from dataclasses import replace
from typing import Callable

from sm64_events.ghost.clip import ClipError
from sm64_events.ghost.native_channel import NativeChannel, RejectedControl
from sm64_events.ghost.native_protocol import RECORD_BYTES, Producer
from sm64_events.memory.ghost_layout import GhostLayout
from sm64_events.replay.capturecontrol import CaptureControl
from sm64_events.replay.ownedclose import CleanupPending, retain

log = logging.getLogger("sm64.ghost.capture")

WARN_EVERY_S = 60.0  # one warning per distinct reason per minute, with the count since


class RateLimitedWarning:
    """Say every distinct failure once at once, then at most once a minute
    with how many times it happened since: a recurring fault stays visible
    without filling the log at the pump's 0.5 s retry rate."""

    def __init__(self, logger, clock=time.monotonic, every_s=WARN_EVERY_S):
        self._log, self._clock, self._every = logger, clock, every_s
        self._last = {}  # reason -> (logged at, repeats since)

    def __call__(self, message, reason):
        now = self._clock()
        logged, repeats = self._last.get(reason, (None, 0))
        if logged is not None and now - logged < self._every:
            self._last[reason] = (logged, repeats + 1)
            return False
        suffix = f" (x{repeats + 1} in the last {now - logged:.0f} s)" if logged is not None else ""
        self._log.warning("%s: %s%s", message, reason, suffix)
        self._last[reason] = (now, 0)
        return True


class HookCost:
    """What the renderer's ghost hooks cost the emulation thread, logged once
    a minute while capture runs. The plugin publishes the last completed
    list's numbers (gs_hook_cost: time inside the hooks and by stage, the
    whole list, driver queries); the pump polls at 60 Hz over the game's 30
    lists a second, so each list counts once, by its serial. Lists that drew a
    ghost and lists that did not are summed apart: the ghost's cost is their
    difference. What each number covers: docs/ghost-native-runtime.md, "What
    the hook numbers mean". A live session measures it without anyone asking him."""

    # Status fields summed per list (hook_<name>), in the order the line prints them.
    STAGES = ("flush_micros", "open_micros", "batch_micros", "validate_micros", "state_micros", "copy_micros",
              "draw_micros", "draw_max_micros", "restore_micros", "close_micros", "finalize_micros",
              "hud_micros", "hud_draws", "layer_passes", "query_micros", "error_reads")

    def __init__(self, logger, clock=time.monotonic, every_s=WARN_EVERY_S):
        self._log, self._clock, self._every = logger, clock, every_s
        self._since = None
        self._last_list = None
        self._reset()

    def _reset(self):
        self.polls = 0
        self._groups = {True: self._group(), False: self._group()}

    @staticmethod
    def _group():
        return {"lists": 0, "micros": 0, "max_us": 0, "queries": 0, "max_queries": 0,
                "list_micros": 0, "max_list_us": 0, **{stage: 0 for stage in HookCost.STAGES}}

    def observe(self, status) -> dict | None:
        """Add one poll of the status page; returns the window's summary when it logs one."""
        now = self._clock()
        if self._since is None:
            self._since = now
        self.polls += 1
        serial = status.hook_list
        if serial and serial != self._last_list:
            self._last_list = serial
            group = self._groups[status.hook_ghosts > 0]
            group["lists"] += 1
            group["micros"] += status.hook_micros
            group["max_us"] = max(group["max_us"], status.hook_micros)
            group["queries"] += status.hook_gl_queries
            group["max_queries"] = max(group["max_queries"], status.hook_gl_queries)
            group["list_micros"] += status.hook_list_micros
            group["max_list_us"] = max(group["max_list_us"], status.hook_list_micros)
            for stage in self.STAGES:
                value = getattr(status, f"hook_{stage}")
                group[stage] = max(group[stage], value) if stage == "draw_max_micros" else group[stage] + value
        if now - self._since < self._every:
            return None
        seconds = now - self._since
        summary = {"seconds": seconds, "polls": self.polls,
                   "ghost": self._summary(self._groups[True]), "plain": self._summary(self._groups[False])}
        for ghost, words in ((True, "with a ghost"), (False, "without a ghost")):
            part = summary["ghost" if ghost else "plain"]
            if part["lists"]:
                self._log.info("%s", self._line(seconds, self.polls, words, part))
        self._since = now
        self._reset()
        return summary

    @staticmethod
    def _summary(group):
        lists = group["lists"]
        mean = (lambda key: group[key] / lists) if lists else (lambda key: 0.0)
        return {"lists": lists, "mean_us": mean("micros"), "max_us": group["max_us"],
                "mean_queries": mean("queries"), "max_queries": group["max_queries"],
                "list_mean_us": mean("list_micros"), "list_max_us": group["max_list_us"],
                **{stage: group[stage] if stage == "draw_max_micros" else mean(stage)
                   for stage in HookCost.STAGES}}

    @staticmethod
    def _line(seconds, polls, words, s):
        return (f"native render hooks, last {seconds:.0f} s, {s['lists']} lists {words} ({polls} polls): "
                f"hooks {s['mean_us']:.1f} us mean, {s['max_us']} us max, of which renderer flush "
                f"{s['flush_micros']:.1f}, gateway open {s['open_micros']:.1f}, batch {s['batch_micros']:.1f} "
                f"(validate {s['validate_micros']:.1f}, state {s['state_micros']:.1f}, copy {s['copy_micros']:.1f}, "
                f"draw {s['draw_micros']:.1f}, "
                f"slowest ghost {s['draw_max_micros']} max, restore {s['restore_micros']:.1f}), gateway close "
                f"{s['close_micros']:.1f}, finalize {s['finalize_micros']:.1f}; whole list "
                f"{s['list_mean_us']:.1f} us mean, {s['list_max_us']} us max; HUD mirror {s['hud_micros']:.1f} us "
                f"over {s['hud_draws']:.1f} draws; {s['layer_passes']:.1f} layer passes; "
                f"{s['mean_queries']:.2f} driver queries mean, {s['max_queries']} max, "
                f"{s['query_micros']:.1f} us inside them; {s['error_reads']:.2f} gateway error reads")


def grouped(producer: Producer, frames: tuple[bytes, ...]):
    """Queue batches can straddle rewind; attach each record's own identity."""
    group, identity = [], None
    for frame in frames:
        if len(frame) != RECORD_BYTES:
            raise ValueError("native ghost record size changed")
        key = (struct.unpack_from("<I", frame, 8)[0],
               struct.unpack_from("<I", frame, 24)[0])
        if identity is not None and key != identity:
            yield replace(producer, configuration=identity[0], epoch=identity[1]), tuple(group)
            group = []
        identity = key
        group.append(frame)
    if identity is not None:
        yield replace(producer, configuration=identity[0], epoch=identity[1]), tuple(group)


class NativeCapture:
    """Start only after acquiring the machine-wide ReplayRecorder lock.

    A timeout from stop retains the live thread and channel. A caller must keep
    this owner until a later stop succeeds; timeout is never release evidence.
    """

    def __init__(self, layout, on_batch: Callable[[Producer, tuple[bytes, ...]], None],
                 *, producer_pid: int | None = None):
        config = layout if isinstance(layout, GhostLayout) else GhostLayout.from_layout(layout)
        self._layout = config.to_bytes()
        self._on_batch, self._pid = on_batch, producer_pid
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = None
        self._playback = {"enabled": False}
        self._revision = 1
        self._status = {"available": False, "reason": "not_started"}
        self._channel = None
        self._pending_cleanup = None
        self._warn = RateLimitedWarning(log)
        self._absent = False  # one INFO line per absence of the channel
        self._refused, self._refused_reason = 0, None
        self._hooks, self._hook_summary = HookCost(log), None

    def start(self):
        if self._thread is not None:
            raise RuntimeError("native ghost capture already started")
        self._thread = threading.Thread(target=self._run, name="ghost-pose-capture", daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 3.0) -> bool:
        self._stop.set()
        if self._thread is None:
            return True
        self._thread.join(timeout_s)
        return not self._thread.is_alive()

    def set_playback(self, *, blocking=True, **controls) -> bool:
        """Control changes only; poll callbacks skip a contended publication."""
        if not self._lock.acquire(blocking=blocking):
            return False
        try:
            if controls == self._playback:
                return True
            self._playback = dict(controls)
            self._revision += 1
            return True
        finally:
            self._lock.release()

    def snapshot_status(self, *, blocking=True) -> dict | None:
        if not self._lock.acquire(blocking=blocking):
            return None
        try:
            return dict(self._status)
        finally:
            self._lock.release()

    def _note_absent(self, error):
        """No channel because Project64 is closed, not rendering a ROM yet, or
        just closed it (native_channel.py raises FileNotFoundError and
        ProcessLookupError for exactly those). Normal, so one INFO line per
        absence and DEBUG while it lasts, instead of a WARNING a minute."""
        if self._absent:
            log.debug("native ghost channel still absent: %s", error)
            return
        self._absent = True
        log.info("native ghost channel absent (Project64 closed or no ROM rendering yet); "
                 "waiting for it: %s", error)

    def _report(self, **status):
        with self._lock:
            self._status = status

    def _discover(self):
        if self._pid is not None:
            return self._pid
        with CaptureControl() as control:
            return control.status().producer_pid

    def _close_channel(self) -> bool:
        try:
            if self._pending_cleanup is not None:
                self._pending_cleanup.close()
                self._pending_cleanup = None
            if self._channel is not None:
                self._channel.close()
        except CleanupPending as error:
            self._pending_cleanup = (retain(self._pending_cleanup, error)
                                     if self._pending_cleanup is not None else error.owner)
            return False
        except (OSError, RuntimeError):
            log.exception("Native ghost channel retains ownership during close")
            return False
        self._channel = None
        return True

    def _run(self):
        try:
            self._pump()
        finally:
            while not self._close_channel():
                threading.Event().wait(0.25)
            self._report(available=False, reason="stopped")

    def _pump(self):
        sent_revision = 0
        while not self._stop.is_set():
            try:
                if self._channel is None:
                    self._channel = NativeChannel(self._discover(), self._layout)
                    sent_revision = 0
                with self._lock:
                    revision = self._revision
                    control = dict(self._playback) if revision != sent_revision else None
                status, frames = self._channel.poll(control)
                sent_revision = revision
                producer = Producer.from_status(status)
                for identity, batch in grouped(producer, frames):
                    if self._stop.is_set():
                        break
                    try:
                        self._on_batch(identity, batch)
                    except ClipError as error:
                        # The archive refused this batch's data; the channel
                        # is fine. Reconnecting (the old path, silently) cannot
                        # fix data, only drop the queued poses with it and
                        # revoke the ghost's display: a blink every 0.5 s for
                        # a recurring refusal. Drop this batch, keep the
                        # channel, and say why.
                        self._refused += 1
                        self._refused_reason = str(error)
                        self._warn("Mario poses refused by the archive; batch dropped", str(error))
                self._hook_summary = self._hooks.observe(status) or self._hook_summary
                self._absent = False
                self._report(available=True, reason=status.reason, state=status.state,
                             dropped=status.dropped, producer=producer,
                             playback_state=status.playback_state,
                             playback_reason=status.playback_reason,
                             displayed=bool(status.displayed),
                             refused_batches=self._refused, refused_reason=self._refused_reason,
                             hook_gl_queries=status.hook_gl_queries, hook_micros=status.hook_micros,
                             hook_cost=self._hook_summary)
                self._stop.wait(0.016)
            except BlockingIOError:
                self._stop.wait(0.016)
            except RejectedControl:
                # 2026-09-22: a command the channel could not encode escaped as
                # a KeyError and ended this thread, which silently stopped Mario
                # POSE RECORDING too. Drop that one command, keep the channel
                # and the recording, and say so.
                log.exception("ghost command rejected; Mario poses keep recording")
                sent_revision = revision
                self._stop.wait(0.016)
            except CleanupPending as error:
                self._pending_cleanup = error.owner
                self._report(available=False, reason=str(error))
                while not self._close_channel():
                    threading.Event().wait(0.25)
            except Exception as error:  # noqa: BLE001 - this thread also records every attempt's poses
                if isinstance(error, (FileNotFoundError, ProcessLookupError)):
                    self._note_absent(error)
                elif not isinstance(error, (OSError, RuntimeError, ValueError)):
                    log.exception("native ghost channel failed; reconnecting")
                else:  # a channel that exists and misbehaves: never silent
                    self._warn("native ghost channel unavailable; reconnecting",
                               f"{type(error).__name__}: {error}")
                self._report(available=False, reason=str(error))
                if not self._close_channel():
                    # A close failure may retain handles. Never acquire a new
                    # channel until the preceding owner has actually released.
                    while not self._close_channel():
                        threading.Event().wait(0.25)
                self._stop.wait(0.5)
