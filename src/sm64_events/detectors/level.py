# src/sm64_events/detectors/level.py
"""level_changed: gCurrLevelNum edge. Lets the tracking layer close
attempts abandoned by leaving the level (excluded from failure rates)
instead of letting the next timer reset miscount them as resets.
Death respawns reload the SAME level id -> no event (correct: a death
already closed the attempt). Void-out deaths DO leave the level, but
their death event fires pre-warp (death.py pending-warp pulse), so the
spit-out's level_changed finds nothing open — a close here is always a
true abandon.

The detector remembers the last level it EMITTED, not the last it saw:
the first processed pair always emits one establishing event (from may
equal to), and a level change that happens while detached (server
restart, emulator reattach — prev is re-seeded from a real read, so a
plain prev/curr edge would miss it) emits a corrective event with
`from` = the last emitted level. Projection-side level tracking
(castle rule, projection.py caveat 9) depends on the journal never
running stale, so the correction MUST land as a journal event — never
seed the live projector from a snapshot directly, or live and rebuild
diverge.

The payload also carries from_area (gCurrAreaIndex BEFORE the edge) so a
castle-subarea segment trigger can scope a crossing by the area Mario LEFT —
e.g. "exit Castle Inside Basement" (tracking/segments.py). from_area =
prev.curr_area is reliable: Mario was settled in that area the frame before
leaving (live journal 2026-06-13: leaving the basement reads from_area=3).
On a reattach-gap corrective event prev is re-seeded to the post-gap area,
so from_area is best-effort there (the from LEVEL stays the authoritative
correction).

There is deliberately NO to_area: the DESTINATION area is NOT known at the
level edge. The castle always loads area 1 (lobby) first, then warps Mario
to the real destination one poll LATER on the SAME game frame (live journal
2026-06-13: entering the basement from HMC reads gCurrAreaIndex=1 on the
level edge, then a co-frame area_changed settles 1->3). So curr.curr_area on
the edge is the transient lobby for EVERY castle entry — useless as a
destination. Destination-subarea triggers instead wait for the settled
co-frame area_changed (segments.py SegmentEngine._pending)."""
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
                      payload={"from": prior, "to": curr.curr_level,
                               "from_area": prev.curr_area})]
