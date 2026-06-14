# src/sm64_events/detectors/stage.py
"""stage_changed: the COURSE the player is standing in, for the practice-page
quick-select banner. Resolves gCurrLevelNum -> course id via
addresses.course_for_level and keeps ONLY the 15 main courses (1-15): Bowser
courses (16-18), the secret-star areas (19-24), hub levels and Bowser arenas
all read as in_stage=False, so the banner shows for paintings/warps into a real
course and nothing else.

Broadcast-only (never journaled): stage is a live presentation signal, fully
recomputable from curr_level, with no historical-query value -- service.publish
caches it on current_stage and skips the journal (see service.py). Mirrors
level.py's last-EMITTED discipline so the first pair establishes and a course
change while detached still emits; keyed on the resolved course_id (NOT the raw
level) so an in-course area switch (SSL area 1<->2, both course 8) is silent.
course_id can legitimately be None (not in a main course), so the
'never-emitted-yet' sentinel is a distinct object, not None."""
from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.memory.addresses import course_for_level

_UNSET = object()


class StageChangeDetector:
    def __init__(self):
        self._last = _UNSET   # last EMITTED course_id (main-course int | None)

    # prev unused: the _UNSET sentinel covers the establishing case (level/area
    # siblings use prev for a 'from' field; stage has none).
    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        course = course_for_level(curr.curr_level)
        course_id = course if course is not None and 1 <= course <= 15 else None  # 15 main courses; see COURSE_BY_LEVEL in addresses.py
        if self._last is not _UNSET and course_id == self._last:
            return []
        self._last = course_id
        return [Event(type="stage_changed", frame=curr.global_timer,
                      timestamp_utc=curr.wall_time_utc,
                      payload={"course_id": course_id,
                               "level": curr.curr_level,
                               "in_stage": course_id is not None})]
