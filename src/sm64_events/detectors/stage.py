# src/sm64_events/detectors/stage.py
"""stage_changed: the quick-select CONTEXT for the practice-page banner — the
main COURSE Mario is standing in (stars mode) OR the Castle Inside subarea he is
in (segments mode).

Resolves gCurrLevelNum -> course id via addresses.course_for_level and keeps
ONLY the 15 main courses (1-15) as STAR context; Bowser courses (16-18),
secret-star areas (19-24), the castle grounds/courtyard, and the Bowser arenas
are NOT star context (in_stage=False). Castle Inside (level 6) is the SEGMENT
context: its named subareas lobby/upstairs/basement (areas 1/2/3) each offer the
segments whose start triggers begin there (views.py derives the list), so while
in level 6 the detector keys on the AREA — a lobby->upstairs walk swaps the
offered segments and must re-emit. Everything else is no context -> banner hides.

Broadcast-only (never journaled): stage is a live presentation signal, fully
recomputable from curr_level/curr_area, with no historical-query value --
service.publish caches it on current_stage and skips the journal (see
service.py). Mirrors level.py's last-EMITTED discipline so the first pair
establishes and a context change while detached still emits; keyed on the
RESOLVED context (("course", id) | ("castle", area) | None), NOT the raw level,
so an in-course area switch (SSL area 1<->2, both course 8) is silent while a
castle lobby<->upstairs switch IS a context change. The context can legitimately
be None (no banner), so the 'never-emitted-yet' sentinel is a distinct object."""
from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.memory.addresses import LEVEL_CASTLE_INSIDE, course_for_level

_UNSET = object()


class StageChangeDetector:
    def __init__(self):
        # last EMITTED context: ("course", id) | ("castle", area) | None
        self._last = _UNSET

    # prev unused: the _UNSET sentinel covers the establishing case (level/area
    # siblings use prev for a 'from' field; stage has none).
    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        course = course_for_level(curr.curr_level)
        course_id = course if course is not None and 1 <= course <= 15 else None  # 15 main courses; see COURSE_BY_LEVEL in addresses.py
        if course_id is not None:
            context = ("course", course_id)
        elif curr.curr_level == LEVEL_CASTLE_INSIDE:
            context = ("castle", curr.curr_area)  # lobby/upstairs/basement swap the segments
        else:
            context = None
        if self._last is not _UNSET and context == self._last:
            return []
        self._last = context
        return [Event(type="stage_changed", frame=curr.global_timer,
                      timestamp_utc=curr.wall_time_utc,
                      payload={"course_id": course_id,
                               "level": curr.curr_level,
                               "area": curr.curr_area,
                               "in_stage": course_id is not None})]
