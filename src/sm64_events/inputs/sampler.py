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

from sm64_events.inputs.frame import (MARIO_BLOCK_OFF, MARIO_BLOCK_SIZE,
                                      InputFrame, decode)
from sm64_events.memory import addresses as A
from sm64_events.memory.base import MemoryReadError

log = logging.getLogger("sm64.inputs")


class InputSampler:
    def __init__(self, memory, layout, sink):
        self._memory = memory
        self._timer_at = layout.global_timer
        self._controller_at = layout.player1_controller
        # Mario's own state rides along inside the SAME coherent window as the
        # pad (round 32): what he was doing, which way he faced, how fast he
        # went. A layout with no Mario address (a version whose sync run has
        # not happened) captures the pad alone rather than nothing.
        self._mario_at = (layout.mario_struct + MARIO_BLOCK_OFF
                          if layout.mario_struct else None)
        self._sink = sink
        self._frame: int | None = None
        self._latest: InputFrame | None = None
        self._previous_buttons = 0
        self._counts = {"samples": 0, "straddles": 0, "frames": 0,
                        "edge_checks": 0, "edge_mismatches": 0}

    def health(self) -> dict:
        """Counters for the perf monitor.

        `edge_mismatches` is the one that matters. The game's own
        `buttonPressed` says which frame a button was NEWLY down on, so a
        non-zero count means we filed an input under the wrong frame number.
        It makes the sampling rate a thing we measure rather than a thing we
        assume is sufficient.
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
            return None
        self._counts["samples"] += 1
        if before != after:
            # The game advanced mid-read, so this pairing of counter and pad
            # is not one frame. Discarding is the only thing preventing frame
            # N's number from being stapled to frame N+1's input.
            self._counts["straddles"] += 1
            return None
        if self._frame is None:
            self._frame = before
        elif before != self._frame:
            backward = before < self._frame
            self._emit()
            if backward:
                # A console reset restarting the counter. Whatever was held
                # before it is gone, so the next frame's buttons are not a
                # down-edge against them.
                self._previous_buttons = 0
            self._frame = before
        self._latest = decode(block, mario)
        return before

    def flush(self) -> None:
        """Emit the frame in hand — for shutdown, and for a lost emulator."""
        self._emit()
        self._frame = None
        self._previous_buttons = 0

    def _emit(self) -> None:
        if self._frame is None or self._latest is None:
            return
        frame, latest = self._frame, self._latest
        self._latest = None
        self._counts["frames"] += 1
        newly = latest.buttons & ~self._previous_buttons
        if newly or latest.pressed:
            self._counts["edge_checks"] += 1
            if newly != latest.pressed:
                self._counts["edge_mismatches"] += 1
        self._previous_buttons = latest.buttons
        try:
            self._sink(frame, latest)
        except Exception:
            # The sink writes to disk. A failed write is not a reason to stop
            # reading the pad, and must not take the poll loop down with it.
            log.exception("input sink failed on frame %d", frame)
