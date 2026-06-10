# src/sm64_events/detectors/star_grab.py
"""Emits star_collected on the edge into a star-grab action.

Why this works for re-collections: the game's interaction handler updates
gLastCompletedCourseNum/StarNum and Mario's numStars BEFORE setting the
star-dance action, on every grab. So at the edge, identity is already
current, and an unchanged numStars means the star was already owned.

IGT robustness: Usamune's practice timer can be reset by player input
within a frame or two of the star touch (reset-spamming between attempts),
clobbering the attempt time before any sampler — however fast — could read
it. The detector therefore keeps a short history of (global_timer,
igt_timer) samples; when a grab races a reset, it reports the clock of the
attempt that actually earned the star, extrapolated to the exact touch
frame, and flags the event with igt_reconstructed: true.
"""
from collections import deque

from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.memory.addresses import STAR_GRAB_ACTIONS, course_name, star_name


def format_igt(frames: int) -> str:
    """Usamune/HUD timer display: M'SS"CC (30 fps frames -> centiseconds)."""
    mins = frames // 1800
    secs = (frames % 1800) // 30
    cents = (frames % 30) * 100 // 30
    return f"{mins}'{secs:02d}\"{cents:02d}"


class StarGrabDetector:
    HISTORY_FRAMES = 150      # keep ~5 s of game time
    RESET_GRACE_FRAMES = 30   # a grab < 1 s after an IGT reset concluded the PRIOR attempt

    def __init__(self):
        self._igt_history: deque[tuple[int, int]] = deque()  # (global_timer, igt_timer)

    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        if not self._igt_history:
            self._observe(prev)
        events = self._detect(prev, curr)
        self._observe(curr)
        return events

    def _observe(self, snap: GameSnapshot) -> None:
        h = self._igt_history
        if h and snap.global_timer < h[-1][0]:
            h.clear()  # time went backward (savestate / console reset)
        if not h or snap.global_timer > h[-1][0]:
            h.append((snap.global_timer, snap.igt_timer))
        cutoff = snap.global_timer - self.HISTORY_FRAMES
        while h and h[0][0] < cutoff:
            h.popleft()

    def _detect(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        entered = (curr.mario_action in STAR_GRAB_ACTIONS
                   and prev.mario_action not in STAR_GRAB_ACTIONS)
        if not entered:
            return []
        star_id = curr.last_completed_star - 1  # game is 1-based, API 0-based
        if star_id < 0:
            return []
        # course 0 is valid here: castle secret stars (Toad/MIPS) report
        # course 0. The boot-time "never set" state has star == 0 too, so
        # the star_id guard above already excludes it.
        course_id = curr.last_completed_course
        touch_frame = max(0, curr.global_timer - curr.mario_action_timer)
        igt_frames, reconstructed = self._igt_at(touch_frame, curr)
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
                "igt_reconstructed": reconstructed,
            },
        )]

    def _igt_at(self, touch_frame: int, curr: GameSnapshot) -> tuple[int, bool]:
        """IGT at the touch frame, reset-race aware.

        Returns (frames, reconstructed). The plain reading back-computes from
        the current sample (the timer keeps running through the dance). If
        the history shows the IGT was RESET near the touch, the touch either
        provably preceded the reset, or followed it so closely that no human
        could have started a new attempt — in both cases the grab belongs to
        the attempt that was being played, so report that attempt's clock
        extrapolated to the touch frame.
        """
        post = max(0, curr.igt_timer - (curr.global_timer - touch_frame))
        samples = list(self._igt_history)
        samples.append((curr.global_timer, curr.igt_timer))
        for (g_a, igt_a), (g_b, igt_b) in zip(reversed(samples[:-1]),
                                              reversed(samples[1:])):
            if igt_b >= igt_a:
                continue  # running (or paused); not a reset
            # IGT was reset somewhere in the game-frame gap (g_a, g_b].
            if touch_frame <= g_a or post < self.RESET_GRACE_FRAMES:
                return max(0, igt_a + (touch_frame - g_a)), True
            return post, False
        return post, False
