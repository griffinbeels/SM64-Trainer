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

A GROUND grab enters the dance on the grab frame; a MIDAIR grab passes through
`ACT_FALL_AFTER_STAR_GRAB` first, so this detector holds the grab and marks the
x-cam when Mario lands — 0.1 s to 1.3 s later in his own play.

## Why the emit then waits again

Deriving the moment is not enough, because **`USAMUNE_OVERALL` is
subarea-local**: it restarts at an area warp inside a level, so on a
multi-area star our counter measures the time since entering the subarea. Live
2026-08-01, his own gate run: nine single-area stars matched Usamune exactly
and the two subarea stars were 356 and 502 frames low — LLL "Hot-Foot-It into
the Volcano" 0'40"63 against 0'52"46, SSL "Inside the Ancient Pyramid" 0'02"43
against 0'19"13. Usamune's result store is the only thing that knows the whole
star, and it is written 0-2 frames after the dance on an ordinary star and
27-28 frames after it on those two. So the x-cam says WHICH MOMENT and
Usamune's own write says WHAT NUMBER, and the emit waits
`RESULT_SETTLE_FRAMES` for it.

A second subarea run the same day (five grabs, `tools/derive_xcam.py`) closed
the one question that could have removed that wait: **Usamune never writes the
answer once.** Every grab took 2-3 writes, the early ones echoing our own
counter, and the store is later CLEARED on level exit. Both ends of the wait
are therefore measured rather than chosen, and the ceiling is a real hazard
rather than headroom — the evidence sits on `RESULT_SETTLE_BRACKET` and is
pinned by `tests/test_star_grab.py`. All five of that run's journaled times
matched Usamune exactly.

Where no write comes — `STOP` of Grab or None, both already illegal — the
counter derivation stands in, and `igt_source` is `"counter"` rather than
`"result"`, which is the honest signal that Usamune was not stopping where a
leaderboard needs it to. That case keeps the subarea error; it cannot be fixed
from a counter that restarted.

The grab-moment fallback exists because a grab that never reaches a dance must
still be journaled: `igt_timed_at` says which of the two moments the number
came from, so a row can always explain what it is rather than being silently
either.

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
    # Filled in when Mario lands. The reading is taken THERE and kept, so the
    # settle wait that follows can never back-compute the counter across it.
    xcam_frame: int | None = None
    xcam_igt: int = 0
    xcam_igt_source: str = ""


class StarGrabDetector:
    # Generous: the longest fall measured live was 39 frames (a Whomp's
    # caged-island grab), but a fall is as long as the drop under it. This is
    # only a backstop for a grab that never reaches a dance at all — a
    # savestate load and a level change both cut the wait short on their own.
    XCAM_TIMEOUT_FRAMES = 300
    # How long to let Usamune's own result write settle after the x-cam. This
    # number is BRACKETED at both ends by live measurement, and both ends bite
    # — see RESULT_SETTLE_BRACKET below for the floor, the ceiling, and the
    # test that pins them. Under `STOP` of Grab or None no write ever comes and
    # this wait is paid in full; both are settings a leaderboard already
    # rejects.
    RESULT_SETTLE_FRAMES = 45
    # (floor, ceiling), exclusive of neither end by accident:
    #
    # FLOOR 28 — Usamune never writes the answer once. Live 2026-08-01, his
    # subarea run, five grabs, 2-3 writes each. The first writes are ECHOES of
    # our own counter (value = that sample's counter + 1) at the grab and again
    # at the dance entry; on a SUBAREA star a further write lands 26-28 frames
    # after the dance entry carrying the whole-star time, and only that one is
    # the star's number. SSL Pyramid +6=74 then +32=545; THI Tip Top +9=1362
    # then +35=1618; LLL Elevator Tour +1=465, +14=479, +41=777; CCM Slip
    # Slidin' +8=221 then +33=1524. Leaving on the first write would have
    # journaled the subarea-local number — the exact bug this wait exists for.
    #
    # CEILING 90 — the store does not merely settle, it is CLEARED. Same run,
    # WF "Shoot into the Wild Blue": +1=328, +3=330, then +92=0 as he left the
    # course. A window generous enough to catch that zero would journal
    # 0'00"00. So this is not a value to raise "for safety"; there is a wrong
    # answer waiting on the far side of it.
    #
    # A single-area star is corrected by nobody, and at the moment the echo
    # lands there is no way to tell "no correction is coming" from "not yet" —
    # which is why the wait is unconditional rather than subarea-only.
    RESULT_SETTLE_BRACKET = (28, 90)
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
        if self._pending is not None:
            # A second grab while one is still settling. Cannot happen in a
            # star dance long enough to cover RESULT_SETTLE_FRAMES, but the
            # alternative to closing the first one is dropping it silently.
            events.append(self._close_now(self._pending, curr))
        if curr.mario_action in STAR_DANCE_ACTIONS:
            self._mark_xcam(grab, grab.grab_frame, curr)  # ground: x-cam is now
        self._pending = grab
        return events

    def _close_now(self, grab: _PendingGrab, curr: GameSnapshot) -> Event:
        """Emit with the best reading already taken — the x-cam one if Mario
        has landed, the grab one if he has not."""
        self._pending = None
        if grab.xcam_frame is None:
            return self._emit(grab, curr, grab.grab_frame, "grab",
                              grab.grab_igt, grab.grab_igt_source)
        return self._emit(grab, curr, grab.xcam_frame, "xcam",
                          grab.xcam_igt, grab.xcam_igt_source)

    def _mark_xcam(self, grab: _PendingGrab, frame: int,
                   curr: GameSnapshot) -> None:
        grab.xcam_frame = frame
        grab.xcam_igt, grab.xcam_igt_source = self._clock.igt_at_xcam(frame, curr)

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
        """Two waits, in order: for Mario to LAND (which moment), then for
        Usamune to WRITE (which number). Either can end early and badly."""
        grab = self._pending
        broken = (curr.global_timer < prev.global_timer        # savestate load
                  or curr.curr_level != grab.level             # left the level
                  or prev.igt_overall - curr.igt_overall
                  > self.IGT_RESET_DROP_FRAMES)                # Usamune reset
        if grab.xcam_frame is None:
            if curr.mario_action in STAR_DANCE_ACTIONS:
                # Back-computed to the dance's ENTRY frame, so a poll that
                # arrives late still lands on the right one — but never before
                # the grab it belongs to, which a stale action timer could
                # otherwise claim.
                self._mark_xcam(grab, max(grab.grab_frame,
                                          curr.global_timer
                                          - curr.mario_action_timer), curr)
            elif broken or (curr.global_timer - grab.grab_frame
                            >= self.XCAM_TIMEOUT_FRAMES):
                self._pending = None
                return self._emit(grab, curr, grab.grab_frame, "grab",
                                  grab.grab_igt, grab.grab_igt_source)
            else:
                return None
        if not broken and (curr.global_timer - grab.xcam_frame
                           < self.RESULT_SETTLE_FRAMES):
            return None
        self._pending = None
        usamune = self._clock.settled_result_at_or_after(grab.xcam_frame, curr)
        if usamune is not None and grab.xcam_igt_source != "reconstructed":
            # Usamune's own number for this x-cam beats our derivation of it —
            # not for precision (they agreed to a frame on nine of eleven live
            # grabs) but because ours is subarea-local; see
            # IgtClock.settled_result_at_or_after. The reconstructed carve-out
            # keeps the reset-race guard: a grab that raced a reset has a
            # near-zero result written for it, which is the case that guard
            # exists to refuse.
            return self._emit(grab, curr, grab.xcam_frame, "xcam",
                              usamune, "result")
        return self._emit(grab, curr, grab.xcam_frame, "xcam",
                          grab.xcam_igt, grab.xcam_igt_source)

    def _emit(self, grab: _PendingGrab, curr: GameSnapshot, frame: int,
              timed_at: str, igt_frames: int, source: str) -> Event:
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
