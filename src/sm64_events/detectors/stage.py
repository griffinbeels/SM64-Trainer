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
RESOLVED context (("course", id) | ("bowser", level) | ("arena", level) |
("castle", area) | None), NOT the raw level. So an in-course area switch (SSL
area 1<->2, both course 8) is silent, while a castle lobby<->upstairs switch, a
BitDW->BitFS course swap, and a Bowser1->Bowser2 arena swap each ARE context
changes that re-emit (the offered targets differ). The context can legitimately
be None (no banner), so the 'never-emitted-yet' sentinel is a distinct object."""
from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot
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

    # prev unused: the _UNSET sentinel covers the establishing case (level/area
    # siblings use prev for a 'from' field; stage has none).
    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        level = curr.curr_level
        course = course_for_level(level)
        if course is not None and 1 <= course <= 15:   # 15 main courses
            mode, course_id, context = "stars", course, ("course", course)
        elif level in _BOWSER_COURSE_LEVELS:
            mode, course_id, context = "bowser_course", course, ("bowser", level)
        elif level in _BOWSER_ARENA_LEVELS:
            mode, course_id, context = "arena", None, ("arena", level)
        elif level == LEVEL_CASTLE_INSIDE:
            mode, course_id, context = "castle", None, ("castle", curr.curr_area)
        else:
            mode, course_id, context = None, None, None
        if self._last is not _UNSET and context == self._last:
            return []
        self._last = context
        return [Event(type="stage_changed", frame=curr.global_timer,
                      timestamp_utc=curr.wall_time_utc,
                      payload={"course_id": course_id,
                               "level": level,
                               "area": curr.curr_area,
                               "mode": mode})]
