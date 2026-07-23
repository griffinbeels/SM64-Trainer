# src/sm64_events/detectors/area.py
"""area_changed: (gCurrLevelNum, gCurrAreaIndex) edge. The segment matcher's
area_enter trigger (castle lobby/upstairs/basement are AREAS of level 6)
depends on journal-derived area state never running stale, so this detector
copies level.py's last-EMITTED discipline verbatim: establishing event on the
first pair (from may equal to), corrective event after attach gaps, keyed by
(level, area) so a level change re-establishes the new level's area.

from_transient: True unless Mario demonstrably DWELT in `from` within this
level — i.e. unless the prior emission was for the SAME level on an EARLIER
game frame. Every castle entry loads the lobby (area 1) transiently, then
warps Mario to the real area a poll later on the SAME game frame
(detectors/level.py), so a course exit into the basement emits from=1
exactly like a genuine lobby walk; same-frame priors catch that. A fast
load can even hide the transient-lobby poll, making the first castle read
the settled destination — then `from` is the OTHER level's area index, so a
cross-level prior is transient too. A genuine walk always has a same-level
prior on an earlier frame (the castle establishes its area on entry, and
reaching a loading zone takes frames). The area_enter "coming from" trigger
(tracking/segments.py) rejects transient sources; legacy events without the
key conservatively match."""
from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot


class AreaChangeDetector:
    def __init__(self):
        self._last_emitted: tuple[int, int] | None = None  # (level, area)
        self._last_emit_frame: int | None = None

    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        key = (curr.curr_level, curr.curr_area)
        if key == self._last_emitted:
            return []
        same_level = (self._last_emitted is not None
                      and self._last_emitted[0] == curr.curr_level)
        prior = (self._last_emitted[1] if same_level else prev.curr_area)
        settled_from = (same_level
                        and self._last_emit_frame != curr.global_timer)
        self._last_emitted = key
        self._last_emit_frame = curr.global_timer
        return [Event(type="area_changed", frame=curr.global_timer,
                      timestamp_utc=curr.wall_time_utc,
                      payload={"level": curr.curr_level,
                               "from": prior, "to": curr.curr_area,
                               "from_transient": not settled_from})]
