# src/sm64_events/detectors/key.py
"""key_grabbed: the star-dance actions fire for keys too (addresses.py,
STAR_GRAB_ACTIONS comment) — in the Bowser 1/2 arenas the grab IS a key.
This detector claims those; star_grab.py carries the inverse guard so a key
is never journaled as a misattributed star_collected (gLastCompleted* may be
stale from the previous star at that moment — VERIFY note in addresses.py).
B3's grand star is a real star and stays with star_grab."""
from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.memory.addresses import (BOWSER_1_ARENA, KEY_GRAB_LEVELS,
                                          STAR_GRAB_ACTIONS)


class KeyGrabDetector:
    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        entered = (curr.mario_action in STAR_GRAB_ACTIONS
                   and prev.mario_action not in STAR_GRAB_ACTIONS)
        if not entered or curr.curr_level not in KEY_GRAB_LEVELS:
            return []
        which = "bitdw" if curr.curr_level == BOWSER_1_ARENA else "bitfs"
        return [Event(type="key_grabbed", frame=curr.global_timer,
                      timestamp_utc=curr.wall_time_utc,
                      payload={"level": curr.curr_level, "which": which})]
