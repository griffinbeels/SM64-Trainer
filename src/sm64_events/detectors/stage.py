# src/sm64_events/detectors/stage.py
"""stage_changed: the quick-select CONTEXT for the practice-page banner — what
kind of one-click target the player can pick where they're standing. The payload
carries a single `mode` that the UI dispatches on:

  "stars"         a main COURSE 1-15 -> that course's stars (course_id set).
  "bowser_course" BitDW/BitFS/BitS (levels 17/19/21 -> course 16/17/18) -> two
                  targets: the "reds" 8-coin star (course_id set, star_id 0) AND
                  the level's "no reds" pipe-entry segment.
  "arena"         a Bowser 1/2/3 fight arena (levels 30/33/34) -> the single
                  fight segment (the UI auto-selects it). No course of its own.
  "castle"        Castle Inside (level 6) -> the segments whose start triggers
                  begin in this subarea (area 1/2/3 = lobby/upstairs/basement).
  None            everything else (caps, secret-star areas, hubs) -> no banner.

Resolves gCurrLevelNum via addresses.course_for_level (1-15 = star course) and
two named level sets (Bowser courses, Bowser arenas). Everything else with no
course and not Castle Inside is no context -> banner hides.

Broadcast-only (never journaled): stage is a live presentation signal, fully
recomputable from curr_level/curr_area, with no historical-query value --
service.publish caches it on current_stage and skips the journal (see
service.py). Mirrors level.py's last-EMITTED discipline so the first pair
establishes and a context change while detached still emits; keyed on the
RESOLVED context (("course", id, area) | ("bowser", level) | ("arena", level) |
("castle", area) | None), NOT the raw level. A castle lobby<->upstairs switch, a
BitDW->BitFS course swap, and a Bowser1->Bowser2 arena swap each ARE context
changes that re-emit (the offered targets differ). The context can legitimately
be None (no banner), so the 'never-emitted-yet' sentinel is a distinct object.

AN IN-COURSE AREA SWITCH RE-EMITS TOO, since 2026-08-08, and it did not until
then. The key was ("course", id) alone on the grounds that SSL area 1<->2 offers
the same seven stars — true when it was written, false since the selector
learned which stars a SUBAREA hosts (addresses.COURSE_SUBAREA_STARS, round 21).
Worse than a missed narrowing: `area` is stamped from the frame the LEVEL edge
fired, and a course load walks the area byte through a transient before it
settles, so the payload froze at the transient. Measured from his own journal
(2026-08-09 03:15): entering LLL stamped area 2 (the volcano), the settle to
area 1 landed 1.8s later and published nothing, and the selector filtered the
main area's row down to the volcano's two stars for as long as he stood there.
The load's own transient still emits, and is SUPERSEDED by the settled area
about 1.8s later — too far apart for the selector's card-set exchange to
coalesce (ui/exchange.js's window is ~210ms), so entering a course whose load
transits a subarea shows one fade from that subarea's stars to the course's.
Accepted rather than fixed: the alternative is holding the first emit until the
area settles, which is a 1.5s wait on the row he is walking towards, and a wait
he can see is a defect by his standing rule. The transient window is spent in
the level fade, and the correction lands before control returns."""
from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.counter_epoch import LEVEL_LOAD_TAIL_FRAMES
from sm64_events.memory.addresses import (
    BOWSER_1_ARENA, BOWSER_2_ARENA, BOWSER_3_ARENA,
    LEVEL_BITDW, LEVEL_BITFS, LEVEL_BITS, LEVEL_CASTLE_INSIDE,
    course_for_level)

_UNSET = object()
_BOWSER_COURSE_LEVELS = frozenset({LEVEL_BITDW, LEVEL_BITFS, LEVEL_BITS})  # 17/19/21
_BOWSER_ARENA_LEVELS = frozenset({BOWSER_1_ARENA, BOWSER_2_ARENA, BOWSER_3_ARENA})  # 30/33/34


class StageChangeDetector:
    def __init__(self):
        # last EMITTED context (see module docstring) | None | _UNSET
        self._last = _UNSET
        # frame of the last LEVEL change, for the settling window below
        self._entered_at: int | None = None

    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        level = curr.curr_level
        course = course_for_level(level)
        if course is not None and 1 <= course <= 15:   # 15 main courses
            mode, course_id, context = ("stars", course,
                                        ("course", course, curr.curr_area))
        elif level in _BOWSER_COURSE_LEVELS:
            mode, course_id, context = "bowser_course", course, ("bowser", level)
        elif level in _BOWSER_ARENA_LEVELS:
            mode, course_id, context = "arena", None, ("arena", level)
        elif level == LEVEL_CASTLE_INSIDE:
            mode, course_id, context = "castle", None, ("castle", curr.curr_area)
        else:
            mode, course_id, context = None, None, None
        # IS THIS AREA THE LOAD'S, OR HIS? A course load walks the area byte
        # through several values before it settles, and the course's own
        # star-select screen sits inside that walk -- so an area sampled there
        # is the LOAD's, not a place he has stood. Marking those emits lets the
        # selector decline to narrow on such an area, with nothing having to
        # WAIT for the settle (round 23; a wait he can see is a defect by his
        # own rule).
        #
        # THE WINDOW IS THE MEASURED LOAD TAIL, not one tick. Round 23 shipped
        # `prev.curr_level != curr.curr_level` -- true only on the frame the
        # level byte moved -- and it did not work, because the walk CONTINUES
        # after that frame: entering LLL from the basement emits (22, area 3)
        # on the edge, then (22, area 2) a beat later, and it was that second
        # emit the star-select screen showed. `LEVEL_LOAD_TAIL_FRAMES`
        # (counter_epoch.py) is the same 60 the epoch tracker and caused.py
        # already use, and it is measured rather than chosen: across 911 level
        # entries the load's LAST area edge lands at 44-47 frames every time,
        # and the earliest genuine warp deeper into a level appears at 60.
        # Sitting on the star-select screen past that costs nothing -- the area
        # has stopped changing by then, so no further emit fires and the row
        # keeps showing everything until he walks somewhere himself.
        if prev is not None and prev.curr_level != curr.curr_level:
            self._entered_at = curr.global_timer
        elif (self._entered_at is not None
              and curr.global_timer < self._entered_at):
            self._entered_at = None      # console reset: the clock went back
        settling = (self._entered_at is not None
                    and curr.global_timer - self._entered_at
                    < LEVEL_LOAD_TAIL_FRAMES)
        if self._last is not _UNSET and context == self._last:
            return []
        self._last = context
        return [Event(type="stage_changed", frame=curr.global_timer,
                      timestamp_utc=curr.wall_time_utc,
                      payload={"course_id": course_id,
                               "level": level,
                               "area": curr.curr_area,
                               "settling": settling,
                               "mode": mode})]
