"""warp_entered: edge into a warp-entry action (pipe touch, teleporter).
The community-comparable moment for 'entered the pipe' segments — the level
edge that follows adds constant fade time, so the matcher anchors on this.
Stateless edge on the already-sampled mario_action; level/area context rides
in the payload so triggers can scope it."""
from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.memory.addresses import WARP_ENTRY_ACTIONS


class WarpDetector:
    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        entered = (curr.mario_action in WARP_ENTRY_ACTIONS
                   and prev.mario_action not in WARP_ENTRY_ACTIONS)
        if not entered:
            return []
        return [Event(type="warp_entered", frame=curr.global_timer,
                      timestamp_utc=curr.wall_time_utc,
                      payload={"level": curr.curr_level,
                               "area": curr.curr_area,
                               "action": curr.mario_action})]
