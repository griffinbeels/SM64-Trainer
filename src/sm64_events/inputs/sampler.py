# src/sm64_events/inputs/sampler.py
"""Read the pad often enough to catch it AFTER the game writes it.

Two measured facts shape this file, both from `tools/probe_inputs.py` over
four live sessions (2026-08-20):

- No game frame goes unobserved at 60, 120, 250 or 500 Hz. Missing frames was
  never the problem.
- The game rewrites the controller struct ~62% of the way THROUGH a frame
  (`addresses.CONTROLLER_SETTLE_PHASE`). So the early samples of a frame's
  window still hold the PREVIOUS frame's input, and a loop that takes the
  first reading of each frame logs every input one frame late, on every frame,
  with nothing on screen to show for it. At 60 Hz the last look of a frame
  lands at 50% and reads fresh on 0% of frames; at 250 Hz it lands at 85% and
  reads fresh on 99%.

Hence: sample fast, and emit the LAST coherent reading of each frame. A frame
is therefore emitted when the counter first moves PAST it, which is the only
moment its last reading is known.

This class does no sleeping and owns no thread. `server/poller.py` drives it,
so pacing, Windows timer resolution and shutdown all live in one place.
"""
import logging
from datetime import datetime, timezone
from uuid import uuid4
import zlib

from sm64_events.inputs.frame import (MARIO_BLOCK_OFF, MARIO_BLOCK_SIZE,
                                      InputFrame, decode)
from sm64_events.inputs.observation import InputObservation
from sm64_events.inputs.readtimes import ReadTimes
from sm64_events.memory import addresses as A
from sm64_events.memory.base import MemoryReadError

log = logging.getLogger("sm64.inputs")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class InputSampler:
    def __init__(self, memory, layout, sink, *, session_id=None, clock=_now,
                 on_activity=None):
        self._memory = memory
        self._timer_at = layout.global_timer
        # The RAM side of the screen CLOCK is Usamune's running leg counter,
        # not gGlobalTimer. Keep the two in the SAME sandwich as the pad so
        # capture can
        # later turn a displayed relative frame into an absolute frame
        # without asking either the pad OCR or an inferred frame map.
        self._controller_at = layout.player1_controller
        # Mario's own state rides along inside the SAME coherent window as the
        # pad (round 32): what he was doing, which way he faced, how fast he
        # went. A layout with no Mario address (a version whose sync run has
        # not happened) captures the pad alone rather than nothing.
        self._mario_at = (layout.mario_struct + MARIO_BLOCK_OFF
                          if layout.mario_struct else None)
        self._sink = sink
        self._session_id = session_id
        self._clock = clock
        self._on_activity = on_activity
        self._source_id = f"poll:{uuid4().hex}"
        self._sequence = 0
        self._observed_utc = None
        self._first_observed_utc = None
        self._read_times = None
        self._owner = None
        self._frame: int | None = None
        self._latest: InputFrame | None = None
        self._previous_buttons = 0
        self._counts = {"samples": 0, "straddles": 0, "frames": 0,
                        "history_failures": 0,
                        "edge_checks": 0, "edge_mismatches": 0,
                        "skips": 0, "skipped_frames": 0, "worst_skip": 0}

    def health(self) -> dict:
        """Counters for the perf monitor.

        `edge_mismatches` checks button-edge consistency, not freshness of
        every sampled state. A late poll can still precede the controller
        rewrite; stick-only changes can leave this audit green. Source-state
        identity requires an immutable game snapshot, not this counter check.

        `skips`/`skipped_frames`/`worst_skip` count the OTHER failure, the
        one he can see: the counter advancing by more than one between two
        observed samples, which means nobody read the frames in between and
        their input is gone -- the timeline says "No capture on this frame"
        for each. Measured over his whole journal (2026-08-31): 84 frames of
        93,958, 0.089%, in 39 of 245 attempts, almost all one or two frames
        wide. A hole needs the poll loop to stall past a whole game frame
        (33 ms) since eight samples land inside one at 250 Hz -- these
        counters are what turn "why did that happen" into a number instead
        of a theory.
        """
        return dict(self._counts)

    def sample(self) -> int | None:
        """One tick. Returns the frame counter read, or None if unusable."""
        try:
            before = self._memory.read_u32(self._timer_at)
            block = self._memory.read_block(self._controller_at,
                                            A.CONTROLLER_SIZE)
            mario = (self._memory.read_block(self._mario_at, MARIO_BLOCK_SIZE)
                     if self._mario_at is not None else None)
            after = self._memory.read_u32(self._timer_at)
        except MemoryReadError:
            self.flush()
            return None
        self._counts["samples"] += 1
        if before != after:
            # The game advanced mid-read, so this pairing of counter and pad
            # is not one frame. Discarding is the only thing preventing frame
            # N's number from being stapled to frame N+1's input. The frame
            # the read LANDED in is still worth knowing -- the caller paces
            # on it -- but nothing is held from a straddled read.
            self._counts["straddles"] += 1
            if after < before:
                # Although its state is unusable, this pair actually witnessed
                # the counter restart. Preserve that seam before filtering UTC.
                self.flush()
            return None
        # Latch the read before emitting the previous frame: its sink may
        # block on disk, but that cannot move this observation into the future.
        observed_utc = self._clock()
        latest = decode(block, mario)
        # Menu/reset input can leave Mario's action passive. Wake capture
        # from this already-read pad, before waiting to seal/store its frame.
        # A held input in a frozen game must not keep the recorder awake.
        active_pad_changed = (self._latest is None
            or (latest.buttons, latest.stick_x, latest.stick_y) !=
               (self._latest.buttons, self._latest.stick_x, self._latest.stick_y))
        if (self._on_activity is not None
                and (before != self._frame or active_pad_changed)
                and (latest.buttons or latest.stick_x or latest.stick_y)):
            try:
                self._on_activity()
            except Exception:
                # Replay availability cannot stop memory polling or lose
                # this coherent input sample.
                log.exception("input activity notification failed")
        owner = self._session_id() if self._session_id is not None else None
        unchanged = (before == self._frame and owner == self._owner
                     and latest == self._latest)
        if self._frame is None:
            self._frame = before
        elif before != self._frame or owner != self._owner:
            backward = before < self._frame
            if not backward and before - self._frame > 1:
                missed = before - self._frame - 1
                self._counts["skips"] += 1
                self._counts["skipped_frames"] += missed
                self._counts["worst_skip"] = max(self._counts["worst_skip"],
                                                 missed)
            self._emit()
            if backward or owner != self._owner:
                self._new_source()
            self._frame = before
        self._owner = owner
        self._latest = latest
        if not unchanged:
            self._first_observed_utc = observed_utc
            self._close_history()
        try:
            if not unchanged:
                self._read_times = ReadTimes()
            if self._read_times is not None:
                self._read_times.add(observed_utc)
        except (OSError, ValueError, zlib.error) as error:
            # The final state needs its complete observed history. A failed
            # spool stays unavailable until a new state/frame starts; retrying
            # every unchanged poll would hide lost instants and flood the log.
            self._history_failed(error)
            self._close_history()
        self._observed_utc = observed_utc
        return before

    def flush(self) -> None:
        """End this observed stretch, preserving its pending frame.

        A pause/lost source is a capture break, not proof of a game reset.
        The resumed stretch gets a new local ID even if its counter repeats.
        """
        if self._frame is None:
            return
        self._emit()
        self._frame = None
        self._new_source()

    def _new_source(self) -> None:
        # Persist the observed seam; a UTC query may omit the reset's chunk.
        self._previous_buttons = 0
        self._source_id = f"poll:{uuid4().hex}"
        self._sequence = 0

    def _emit(self) -> None:
        if self._frame is None or self._latest is None:
            return
        frame, latest = self._frame, self._latest
        sequence = self._sequence
        self._sequence += 1
        self._latest = None
        if self._read_times is None:
            return  # lost provenance is a capture hole, never a legacy sample
        try:
            history, lower, upper = self._read_times.finish()
            observation = InputObservation(self._source_id, sequence,
                                           self._observed_utc, self._first_observed_utc,
                                           lower, upper, history)
        except (OSError, ValueError, zlib.error) as error:
            self._history_failed(error)
            return
        finally:
            self._close_history()
        self._counts["frames"] += 1
        newly = latest.buttons & ~self._previous_buttons
        if newly or latest.pressed:
            self._counts["edge_checks"] += 1
            if newly != latest.pressed:
                self._counts["edge_mismatches"] += 1
        self._previous_buttons = latest.buttons
        try:
            if self._session_id is None:
                self._sink(frame, latest, observation=observation)
            else:
                # Emission may happen after a pause or session change. The
                # observation's owner remains the one that actually read it.
                self._sink(frame, latest, session_id=self._owner,
                           observation=observation)
        except Exception:
            # The sink writes to disk. A failed write is not a reason to stop
            # reading the pad, and must not take the poll loop down with it.
            log.exception("input sink failed on frame %d", frame)

    def _history_failed(self, error: Exception) -> None:
        self._counts["history_failures"] += 1
        log.error("input observation history failed on frame %s", self._frame, exc_info=error)

    def _close_history(self) -> None:
        history, self._read_times = self._read_times, None
        if history is not None:
            try:
                history.close()
            except OSError as error:
                self._history_failed(error)
