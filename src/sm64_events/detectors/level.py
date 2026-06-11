# src/sm64_events/detectors/level.py
"""level_changed: gCurrLevelNum edge. Lets the tracking layer close
attempts abandoned by leaving the level (excluded from failure rates)
instead of letting the next timer reset miscount them as resets.
Death respawns reload the SAME level id -> no event (correct: a death
already closed the attempt).

`from` is 0 on the first read after attach (snapshot default), so one event
fires when the level is first observed — consumers treat it like any change."""
from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot


class LevelChangeDetector:
    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        if curr.curr_level == prev.curr_level:
            return []
        return [Event(type="level_changed", frame=curr.global_timer,
                      timestamp_utc=curr.wall_time_utc,
                      payload={"from": prev.curr_level, "to": curr.curr_level})]
