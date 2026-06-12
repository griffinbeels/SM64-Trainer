# src/sm64_events/detectors/star_grab.py
"""Emits star_collected on the edge into a star-grab action.

Why this works for re-collections: the game's interaction handler updates
gLastCompletedCourseNum/StarNum and Mario's numStars BEFORE setting the
star-dance action, on every grab. So at the edge, identity is already
current, and an unchanged numStars means the star was already owned.

IGT sources, in order of preference:
1. "result"  — Usamune writes the EXACT final displayed star time into a
   static global (USAMUNE_STAR_RESULT) at the grab. When that write is
   fresh (changed within a few frames of the touch), it is authoritative.
2. "counter" — the running overall counter (USAMUNE_OVERALL), back-computed
   to the touch frame via Mario's action timer, plus the display tick.
3. "reconstructed" — if the overall counter was RESET within a blink of the
   touch (reset-spamming between attempts), both sources above report a
   near-zero time for an attempt that took seconds. The detector keeps a
   short history of samples and reports the clock of the attempt that
   actually earned the star, extrapolated to the touch frame.
"""
from collections import deque

from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.core.timefmt import format_igt
from sm64_events.memory.addresses import (KEY_GRAB_LEVELS, STAR_GRAB_ACTIONS,
                                          course_name, star_name)


class StarGrabDetector:
    HISTORY_FRAMES = 150       # keep ~5 s of game time
    RESET_GRACE_FRAMES = 30    # a grab < 1 s after an IGT reset concluded the PRIOR attempt
    DISPLAY_TICK = 1           # counter path: Usamune's display is one tick ahead
    RESULT_FRESH_FRAMES = 15   # the result write lands within a few frames of the touch

    def __init__(self):
        # (global_timer, igt_overall, igt_result) samples
        self._history: deque[tuple[int, int, int]] = deque()

    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        if not self._history:
            self._observe(prev)
        events = self._detect(prev, curr)
        self._observe(curr)
        return events

    def _observe(self, snap: GameSnapshot) -> None:
        h = self._history
        if h and snap.global_timer < h[-1][0]:
            h.clear()  # time went backward (savestate / console reset)
        if not h or snap.global_timer > h[-1][0]:
            h.append((snap.global_timer, snap.igt_overall, snap.igt_result))
        cutoff = snap.global_timer - self.HISTORY_FRAMES
        while h and h[0][0] < cutoff:
            h.popleft()

    def _detect(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        entered = (curr.mario_action in STAR_GRAB_ACTIONS
                   and prev.mario_action not in STAR_GRAB_ACTIONS)
        if not entered:
            return []
        if curr.curr_level in KEY_GRAB_LEVELS:
            return []  # Bowser key, not a star — detectors/key.py owns it
        star_id = curr.last_completed_star - 1  # game is 1-based, API 0-based
        if star_id < 0:
            return []
        # course 0 is valid here: castle secret stars (Toad/MIPS) report
        # course 0. The boot-time "never set" state has star == 0 too, so
        # the star_id guard above already excludes it.
        course_id = curr.last_completed_course
        touch_frame = max(0, curr.global_timer - curr.mario_action_timer)
        igt_frames, source = self._igt_at(touch_frame, curr)
        return [Event(
            type="star_collected",
            frame=touch_frame,
            timestamp_utc=curr.wall_time_utc,
            payload={
                "course_id": course_id,
                "course_name": course_name(course_id),
                "star_id": star_id,
                "star_name": star_name(course_id, star_id),
                "already_collected": curr.num_stars == prev.num_stars,
                "igt_frames": igt_frames,
                "igt": format_igt(igt_frames),
                "igt_source": source,
                "igt_reconstructed": source == "reconstructed",
                "num_stars": curr.num_stars,
            },
        )]

    def _igt_at(self, touch_frame: int, curr: GameSnapshot) -> tuple[int, str]:
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
