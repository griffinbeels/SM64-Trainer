# src/sm64_events/detectors/area.py
"""area_changed: (gCurrLevelNum, gCurrAreaIndex) edge. The segment matcher's
area_enter trigger (castle lobby/upstairs/basement are AREAS of level 6)
depends on journal-derived area state never running stale, so this detector
copies level.py's last-EMITTED discipline verbatim: establishing event on the
first pair (from may equal to), corrective event after attach gaps, keyed by
(level, area) so a level change re-establishes the new level's area."""
from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot


class AreaChangeDetector:
    def __init__(self):
        self._last_emitted: tuple[int, int] | None = None  # (level, area)

    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        key = (curr.curr_level, curr.curr_area)
        if key == self._last_emitted:
            return []
        prior = (self._last_emitted[1]
                 if self._last_emitted is not None
                 and self._last_emitted[0] == curr.curr_level
                 else prev.curr_area)
        self._last_emitted = key
        return [Event(type="area_changed", frame=curr.global_timer,
                      timestamp_utc=curr.wall_time_utc,
                      payload={"level": curr.curr_level,
                               "from": prior, "to": curr.curr_area})]
