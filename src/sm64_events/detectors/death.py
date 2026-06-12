# src/sm64_events/detectors/death.py
"""death: edge into one of Mario's death actions, or a pending void-out warp.

Action-edge detection, same philosophy as star_grab: the death actions are
mutually exclusive, persist for many frames, and identify the CAUSE.
Payload carries the IGT at death so the tracking layer can record the
failed attempt's duration. Health/lives corroboration deliberately
omitted (would need new memory reads); the action set is sufficient.

Void-outs (death barriers — HMC pits, Bowser arenas, "death boxes") never
enter a death action: the game pends a delayed warp instead
(check_death_barrier -> WARP_OP_WARP_FLOOR) and the life is lost only
AFTER the warp, in the destination level's death-exit action. The pre-warp
pulse (PENDING_WARP_OP, ~20 game frames) is the only in-level signal — and
firing on it lands the death BEFORE level_changed in the journal, so the
projection closes the open attempt as death and the spit-out's level exit
closes nothing. Cause: "fall". Normal deaths pend WARP_OP_DEATH (0x12)
long after their action already fired — that op is deliberately ignored,
otherwise every death would double-count. The prev-action guard covers the
theoretical same-pulse overlap (already dying when a warp-floor pulse
appears): one event per death, always.

Stateless: an emulator reconnect mid-death re-fires once (prev resets to a
fresh pair) — same accepted behavior as star_grab. A reconnect mid-PULSE
cannot re-fire: the re-seeded prev already carries the pending op."""
from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.memory.addresses import DEATH_ACTIONS, WARP_OP_WARP_FLOOR


class DeathDetector:
    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        if curr.mario_action in DEATH_ACTIONS:
            if prev.mario_action in DEATH_ACTIONS:
                return []  # still dying; one event per death
            return [self._event(curr, cause=DEATH_ACTIONS[curr.mario_action])]
        if (curr.pending_warp_op == WARP_OP_WARP_FLOOR
                and prev.pending_warp_op != WARP_OP_WARP_FLOOR
                and prev.mario_action not in DEATH_ACTIONS):
            return [self._event(curr, cause="fall")]
        return []

    @staticmethod
    def _event(curr: GameSnapshot, cause: str) -> Event:
        return Event(type="death", frame=curr.global_timer,
                     timestamp_utc=curr.wall_time_utc,
                     payload={"cause": cause,
                              "igt_frames": curr.igt_overall,
                              "level": curr.curr_level})
