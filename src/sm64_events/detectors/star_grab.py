# src/sm64_events/detectors/star_grab.py
"""Emits star_collected at the X-CAM moment — the leaderboard-legal time.

Why this works for re-collections: the game's interaction handler updates
gLastCompletedCourseNum/StarNum and Mario's numStars BEFORE setting the
star-dance action, on every grab. So at the grab edge, identity is already
current, and an unchanged numStars means the star was already owned.

## Why the emit is not on the grab edge

Usamune's `STOP` decides when Usamune's timer stops, and the manual defines
the choices: *Grab* = Mario touches the star, *Xcam* = Mario touches the
GROUND after the grab, *GrabX* = stop at the grab, then update at the x-cam.
Leaderboards accept `STOP` of GrabX or Xcam only, so the grab-frame number is
not a slightly-early time — it is an INVALID one, and it was our default.

Measured 2026-08-01 (`tools/derive_xcam.py`, scored automatically against
Usamune's own settled result store): **the x-cam moment is the star-DANCE
entry**, and Usamune's number is that frame's overall counter plus
`IgtClock.DISPLAY_TICK`. The arithmetic was never wrong; the FRAME was. Four
midair grabs came back within one frame of Usamune at the dance entry, against
-4, -11, -23 and -39 frames at the grab.

A GROUND grab enters the dance on the grab frame, so nothing is deferred there
and nothing about it changes. A MIDAIR grab passes through
`ACT_FALL_AFTER_STAR_GRAB` first, so this detector holds the grab and emits
when Mario lands — 0.1 s to 1.3 s later in his own play, and the emit lands on
the frame the star dance starts rather than the frame Mario touched the star.
Deriving it ourselves means the recorded time is legal whatever his TIMER
menu says, which is the point: the tool should not depend on the player having
configured Usamune correctly.

The fallback exists because a grab that never reaches a dance must still be
journaled: `igt_timed_at` says which of the two moments the number came from,
so a row can always explain what it is rather than being silently either.

IGT comes from the shared IgtClock (detectors/igt_clock.py) — result ->
counter -> reconstructed precedence; its docstring carries the rationale.
The same clock stamps the Bowser-3 grand star in detectors/key.py, at the
TOUCH: whether Usamune's STOP setting moves the grand star's number the way it
moves a collectable star's is unmeasured, and `ACT_JUMBO_STAR_CUTSCENE` has no
fall/dance pair to derive one from. Left alone deliberately rather than
changed by analogy.
"""
from dataclasses import dataclass

from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.core.timefmt import format_igt
from sm64_events.detectors.igt_clock import IgtClock
from sm64_events.memory.addresses import (KEY_GRAB_LEVELS, STAR_DANCE_ACTIONS,
                                          STAR_GRAB_ACTIONS, course_name,
                                          star_name)


@dataclass
class _PendingGrab:
    """A midair grab, identified, waiting for Mario to land.

    Everything the event needs is captured at the GRAB edge, including the
    grab-moment IGT: the identity fields are only current for a tick, and the
    fallback number has to be the one we would have emitted before deferring,
    not a back-computation over a window that may contain a pause."""
    course_id: int
    star_id: int
    already_collected: bool
    num_stars: int
    level: int
    grab_frame: int
    grab_igt: int
    grab_igt_source: str


class StarGrabDetector:
    # Generous: the longest fall measured live was 39 frames (a Whomp's
    # caged-island grab), but a fall is as long as the drop under it. This is
    # only a backstop for a grab that never reaches a dance at all — a
    # savestate load and a level change both cut the wait short on their own.
    XCAM_TIMEOUT_FRAMES = 300
    # A Usamune reset while a grab is pending destroys the context the number
    # would describe, and the counter falling is how it shows. Distinguished
    # from noise by SIZE, not by direction: a snapshot is twelve separate reads
    # and can come back one frame stale (verify_death_clock.READ_SKEW_FRAMES),
    # while a real reset drops the counter by the whole attempt. A false abort
    # here would emit the grab time — the exact bug this file fixes — so the
    # threshold sits far above the skew and far below any real reset.
    IGT_RESET_DROP_FRAMES = 15

    def __init__(self):
        self._clock = IgtClock()
        self._pending: _PendingGrab | None = None

    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        if self._clock.empty():
            self._clock.observe(prev)
        events = self._detect(prev, curr)
        self._clock.observe(curr)
        return events

    def _detect(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        events: list[Event] = []
        if self._pending is not None:
            settled = self._settle(prev, curr)
            if settled is not None:
                events.append(settled)
        entered = (curr.mario_action in STAR_GRAB_ACTIONS
                   and prev.mario_action not in STAR_GRAB_ACTIONS)
        if not entered:
            return events
        grab = self._identify(prev, curr)
        if grab is None:
            return events
        if curr.mario_action in STAR_DANCE_ACTIONS:
            events.append(self._emit(grab, curr, grab.grab_frame, "xcam"))
        else:
            self._pending = grab  # midair: x-cam is the landing, still ahead
        return events

    def _identify(self, prev: GameSnapshot,
                  curr: GameSnapshot) -> _PendingGrab | None:
        if curr.curr_level in KEY_GRAB_LEVELS:
            return None  # Bowser key, not a star — detectors/key.py owns it
        star_id = curr.last_completed_star - 1  # game is 1-based, API 0-based
        if star_id < 0:
            return None
        grab_frame = max(0, curr.global_timer - curr.mario_action_timer)
        igt_frames, source = self._clock.igt_at_xcam(grab_frame, curr)
        # course 0 is valid here: castle secret stars (Toad/MIPS) report
        # course 0. The boot-time "never set" state has star == 0 too, so
        # the star_id guard above already excludes it.
        return _PendingGrab(
            course_id=curr.last_completed_course, star_id=star_id,
            already_collected=curr.num_stars == prev.num_stars,
            num_stars=curr.num_stars, level=curr.curr_level,
            grab_frame=grab_frame, grab_igt=igt_frames,
            grab_igt_source=source,
        )

    def _settle(self, prev: GameSnapshot, curr: GameSnapshot) -> Event | None:
        """The pending grab's x-cam has arrived, or can no longer arrive."""
        grab = self._pending
        if curr.mario_action in STAR_DANCE_ACTIONS:
            self._pending = None
            # Back-computed to the dance's ENTRY frame, so a poll that arrives
            # late still lands on the right one — but never before the grab it
            # belongs to, which a stale action timer could otherwise claim.
            xcam_frame = max(grab.grab_frame,
                             curr.global_timer - curr.mario_action_timer)
            return self._emit(grab, curr, xcam_frame, "xcam")
        abandoned = (curr.global_timer < prev.global_timer     # savestate load
                     or curr.curr_level != grab.level          # left the level
                     or prev.igt_overall - curr.igt_overall
                     > self.IGT_RESET_DROP_FRAMES              # Usamune reset
                     or curr.global_timer - grab.grab_frame
                     >= self.XCAM_TIMEOUT_FRAMES)
        if not abandoned:
            return None
        self._pending = None
        return self._emit(grab, curr, grab.grab_frame, "grab")

    def _emit(self, grab: _PendingGrab, curr: GameSnapshot, frame: int,
              timed_at: str) -> Event:
        if timed_at == "xcam":
            igt_frames, source = self._clock.igt_at_xcam(frame, curr)
        else:
            # The grab-moment reading, taken back at the grab edge rather than
            # back-computed from here: this snapshot may be hundreds of frames
            # and a pause away, and the counter does not count paused frames.
            igt_frames, source = grab.grab_igt, grab.grab_igt_source
        return Event(
            type="star_collected",
            frame=frame,
            timestamp_utc=curr.wall_time_utc,
            payload={
                "course_id": grab.course_id,
                "course_name": course_name(grab.course_id),
                "star_id": grab.star_id,
                "star_name": star_name(grab.course_id, grab.star_id),
                "already_collected": grab.already_collected,
                "igt_frames": igt_frames,
                "igt": format_igt(igt_frames),
                "igt_source": source,
                "igt_reconstructed": source == "reconstructed",
                "igt_timed_at": timed_at,
                "grab_frame": grab.grab_frame,
                "num_stars": grab.num_stars,
            },
        )
