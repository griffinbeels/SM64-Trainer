# src/sm64_events/detectors/key.py
"""key_grabbed: claims all three fight-ending grabs — Bowser 1/2 keys and the
B3 grand star.

Bowser 1/2 keys enter the same star-dance actions as normal stars (STAR_GRAB_ACTIONS).
B3's grand star is NOT a collectable star — live-verified 2026-06-12: it enters
ACT_JUMBO_STAR_CUTSCENE (0x00001909) directly from a jump action; numStars unchanged
(stayed at 17); no star-dance action ever appeared; gLastCompleted* untouched.
Therefore star_collected cannot fire for the grand star and it must be claimed here.

star_grab.py needs NO guard for level 34: ACT_JUMBO_STAR_CUTSCENE is not in
STAR_GRAB_ACTIONS, so the grand-star grab never reaches star_grab's edge check.
KEY_GRAB_LEVELS (the star_grab guard set) and FIGHT_END_LEVELS (this detector's
claim set) are intentionally different — see the FIGHT_END_LEVELS comment in
addresses.py for why adding 34 to KEY_GRAB_LEVELS would be wrong.

frame is the touch frame (global_timer - action_timer), matching star_grab —
a poll stall must not shift segment end stamps."""
from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.memory.addresses import (ACT_JUMBO_STAR_CUTSCENE,
                                          FIGHT_END_LEVELS,
                                          STAR_GRAB_ACTIONS)

_CLAIM_ACTIONS = STAR_GRAB_ACTIONS | {ACT_JUMBO_STAR_CUTSCENE}


class KeyGrabDetector:
    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        entered = (curr.mario_action in _CLAIM_ACTIONS
                   and prev.mario_action not in _CLAIM_ACTIONS)
        if not entered or curr.curr_level not in FIGHT_END_LEVELS:
            return []
        which = FIGHT_END_LEVELS[curr.curr_level]
        touch_frame = max(0, curr.global_timer - curr.mario_action_timer)
        return [Event(type="key_grabbed", frame=touch_frame,
                      timestamp_utc=curr.wall_time_utc,
                      payload={"level": curr.curr_level, "which": which})]
