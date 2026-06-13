# src/sm64_events/detectors/igt_clock.py
"""Usamune IGT at a touch frame — the SHARED clock behind every grab time.

Extracted from star_grab.py 2026-06-12 (logic unchanged) so the Bowser-3
grand star reports the SAME authoritative time as a collectable star. The
grand star enters ACT_JUMBO_STAR_CUTSCENE and fires key_grabbed, never
star_collected (detectors/key.py), so key.py composes this clock to stamp
the grab with Usamune's exact displayed time — otherwise a grand-star segment
falls back to the wall-frame delta, which is one display-tick short of
Usamune and counts paused frames (live report 2026-06-12: 0'46"23 vs 0'46"26).

Source precedence (see the original rationale in star_grab.py's docstring):
1. "result"  — USAMUNE_STAR_RESULT, the exact number Usamune displays, used
   when its write is fresh (changed within a few frames of the touch).
2. "counter" — USAMUNE_OVERALL back-computed to the touch frame, + the
   one-frame display tick. Pause-safe: the overall counter is Usamune's own
   IGT, so it never counts paused frames (unlike a wall-frame delta).
3. "reconstructed" — when the overall counter was RESET within a blink of the
   touch (reset-spamming between attempts), report the clock of the attempt
   that actually earned the grab, extrapolated to the touch frame.
"""
from collections import deque

from sm64_events.core.snapshot import GameSnapshot


class IgtClock:
    HISTORY_FRAMES = 150       # keep ~5 s of game time
    RESET_GRACE_FRAMES = 30    # a grab < 1 s after an IGT reset concluded the PRIOR attempt
    DISPLAY_TICK = 1           # counter path: Usamune's display is one tick ahead
    RESULT_FRESH_FRAMES = 15   # the result write lands within a few frames of the touch

    def __init__(self):
        # (global_timer, igt_overall, igt_result) samples
        self._history: deque[tuple[int, int, int]] = deque()

    def empty(self) -> bool:
        return not self._history

    def observe(self, snap: GameSnapshot) -> None:
        h = self._history
        if h and snap.global_timer < h[-1][0]:
            h.clear()  # time went backward (savestate / console reset)
        if not h or snap.global_timer > h[-1][0]:
            h.append((snap.global_timer, snap.igt_overall, snap.igt_result))
        cutoff = snap.global_timer - self.HISTORY_FRAMES
        while h and h[0][0] < cutoff:
            h.popleft()

    def igt_at(self, touch_frame: int, curr: GameSnapshot) -> tuple[int, str]:
        samples = list(self._history)
        samples.append((curr.global_timer, curr.igt_overall, curr.igt_result))
        candidate, source = self._primary(touch_frame, curr, samples)
        # Reset-race guard, regardless of source: when the overall counter
        # was reset within a blink of the touch, even Usamune's own result
        # write holds the post-reset near-zero time — but the grab concluded
        # the attempt that was being played.
        for (g_a, ov_a, _), (_, ov_b, _) in zip(reversed(samples[:-1]),
                                                reversed(samples[1:])):
            if ov_b >= ov_a:
                continue  # running (or paused); not a reset
            # overall counter was reset in the game-frame gap after g_a
            if touch_frame <= g_a or candidate < self.RESET_GRACE_FRAMES:
                prior = max(0, ov_a + (touch_frame - g_a))
                return prior + self.DISPLAY_TICK, "reconstructed"
            break
        return candidate, source

    def _primary(self, touch_frame: int, curr: GameSnapshot,
                 samples: list[tuple[int, int, int]]) -> tuple[int, str]:
        if curr.igt_result and self._result_is_fresh(touch_frame, samples):
            return curr.igt_result, "result"  # the exact displayed number
        post = max(0, curr.igt_overall - (curr.global_timer - touch_frame))
        return post + self.DISPLAY_TICK, "counter"

    def _result_is_fresh(self, touch_frame: int,
                         samples: list[tuple[int, int, int]]) -> bool:
        """True when the result store changed within a few frames of the
        touch — i.e., it was written for THIS grab, not a previous star."""
        latest = samples[-1][2]
        for g, _, res in reversed(samples[:-1]):
            if res != latest:
                return g >= touch_frame - self.RESULT_FRESH_FRAMES
        return False  # never observed changing: may be a stale prior result
