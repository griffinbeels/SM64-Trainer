# src/sm64_events/detectors/level.py
"""level_changed: gCurrLevelNum edge. Lets the tracking layer close
attempts abandoned by leaving the level (excluded from failure rates)
instead of letting the next timer reset miscount them as resets.
Death respawns reload the SAME level id -> no event (correct: a death
already closed the attempt).

The detector remembers the last level it EMITTED, not the last it saw:
the first processed pair always emits one establishing event (from may
equal to), and a level change that happens while detached (server
restart, emulator reattach — prev is re-seeded from a real read, so a
plain prev/curr edge would miss it) emits a corrective event with
`from` = the last emitted level. Projection-side level tracking
(castle rule, projection.py caveat 9) depends on the journal never
running stale, so the correction MUST land as a journal event — never
seed the live projector from a snapshot directly, or live and rebuild
diverge."""
from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot


class LevelChangeDetector:
    def __init__(self):
        self._last_emitted: int | None = None

    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        if curr.curr_level == self._last_emitted:
            return []
        prior = self._last_emitted if self._last_emitted is not None \
            else prev.curr_level
        self._last_emitted = curr.curr_level
        return [Event(type="level_changed", frame=curr.global_timer,
                      timestamp_utc=curr.wall_time_utc,
                      payload={"from": prior, "to": curr.curr_level})]
