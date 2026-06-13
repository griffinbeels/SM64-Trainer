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
        self._last = _UNSET   # last EMITTED (course_id, level) key
        # Key is course_id for main courses (so in-course area switches are
        # silent); raw level for non-main-course levels (so castle -> Bowser
        # arena -> castle all emit distinct events even though all have
        # course_id=None).

    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        course = course_for_level(curr.curr_level)
        course_id = course if course is not None and 1 <= course <= 15 else None
        # Stable key: main-course entries key on course_id (ignoring area);
        # non-main-course entries key on the raw level so hub/Bowser/secret
        # transitions still emit an updated level in the payload.
        key = course_id if course_id is not None else curr.curr_level
        last_key = (self._last[0] if course_id is not None else self._last[1]) \
            if self._last is not _UNSET else _UNSET
        if self._last is not _UNSET and key == last_key:
            return []
        self._last = (course_id, curr.curr_level)
        return [Event(type="stage_changed", frame=curr.global_timer,
                      timestamp_utc=curr.wall_time_utc,
                      payload={"course_id": course_id,
                               "level": curr.curr_level,
                               "in_stage": course_id is not None})]
