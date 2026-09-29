"""death: edge into one of Mario's death actions, or a pending death warp
that no death action announced. Also THE definition of "Mario is alive".

Action-edge detection, same philosophy as star_grab: the death actions are
mutually exclusive, persist for many frames, and identify the CAUSE.
Payload carries the IGT at death so the tracking layer can record the
failed attempt's duration.

Void-outs (death barriers — HMC pits, Bowser arenas, "death boxes") never
enter a death action: the game pends a delayed warp instead
(check_death_barrier -> WARP_OP_WARP_FLOOR) and the life is lost only
AFTER the warp, in the destination level's death-exit action. The pre-warp
pulse (PENDING_WARP_OP, ~20 game frames) is the only in-level signal — and
firing on it lands the death BEFORE level_changed in the journal, so the
projection closes the open attempt as death and the spit-out's level exit
closes nothing. Cause: "fall".

Four more deaths never enter a death action either, and pend WARP_OP_DEATH
(0x12) directly: a lava boost that runs out of health, a squish, a
whirlpool, and a NULL floor (out of bounds). Until 2026-09-28 the op was
ignored outright, because a NORMAL death pends it ~48 frames after its
action already fired — so these deaths journaled nothing, and a BitFS lava
death that flew on into the pipe recorded as a pipe SUCCESS (his row #2,
0'48"26: ACT_LAVA_BOOST from frame 76906, op 0x12 at 76972, the pipe touch
at 77012, the castle at 77022 — read off the ghost pose archive). The op
now fires when no death action is running on either side of its edge;
inside one it is still ignored, so a normal death still counts once. Cause:
`DEATH_WARP_CAUSES` by action, else "out_of_bounds". The prev-action guards
cover the same-pulse overlaps: one event per death, always.

Stateless: an emulator reconnect mid-death re-fires once (prev resets to a
fresh pair) — same accepted behavior as star_grab. A reconnect mid-PULSE
cannot re-fire: the re-seeded prev already carries the pending op.

`mario_alive` is the one-boolean check every SUCCESS is gated on (his words,
2026-09-28: "when we grab the star / complete a segment, we need to confirm
that mario is alive"). The success detectors (star_grab, key, warp) stamp it
on their event at the success frame as `ALIVE_KEY`; the tracking layer reads
it back through `finished_dead` and records the finish as a death. That gate
catches what the death event above cannot yet have said: health already
under a wedge while Mario is still airborne or in knockback, where the game
has not entered a death action and the pipe still takes him (interact_warp
has no health check)."""
from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.memory.addresses import (DEATH_ACTIONS, DEATH_WARP_CAUSES,
                                          DEATH_WARP_OPS,
                                          MARIO_HEALTH_DEAD_BELOW,
                                          WARP_OP_DEATH, WARP_OP_WARP_FLOOR)

# The payload key a success event carries: was Mario alive at its frame.
ALIVE_KEY = "mario_alive"
# outcome_detail of a success recorded as a death because Mario was dead.
FINISHED_DEAD = "finished_dead"


def mario_alive(snap: GameSnapshot) -> bool:
    """False when the game already counts Mario as dead, by any of its three
    marks: health under one wedge (the game's own test — no star or key is
    granted below it), a death action (a quicksand death keeps full health),
    or a pending death warp (void-outs and out-of-bounds keep full health,
    and the pending warp overrides whatever exit comes next). Health 0 is
    the struct before a file has loaded, never a death."""
    return not (0 < snap.mario_health < MARIO_HEALTH_DEAD_BELOW
                or snap.mario_action in DEATH_ACTIONS
                or snap.pending_warp_op in DEATH_WARP_OPS)


def finished_dead(payload: dict) -> bool:
    """Did this success event happen with Mario already dead? Journals from
    before 2026-09-28 lack the key and read as alive — forward-only."""
    return payload.get(ALIVE_KEY) is False


class DeathDetector:
    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        if curr.mario_action in DEATH_ACTIONS:
            if prev.mario_action in DEATH_ACTIONS:
                return []  # still dying; one event per death
            return [self._event(curr, cause=DEATH_ACTIONS[curr.mario_action])]
        if prev.mario_action in DEATH_ACTIONS:
            return []  # the death this op belongs to already fired
        if (curr.pending_warp_op == WARP_OP_WARP_FLOOR
                and prev.pending_warp_op != WARP_OP_WARP_FLOOR):
            return [self._event(curr, cause="fall")]
        if (curr.pending_warp_op == WARP_OP_DEATH
                and prev.pending_warp_op != WARP_OP_DEATH):
            cause = (DEATH_WARP_CAUSES.get(curr.mario_action)
                     or DEATH_WARP_CAUSES.get(prev.mario_action)
                     or "out_of_bounds")
            return [self._event(curr, cause=cause)]
        return []

    @staticmethod
    def _event(curr: GameSnapshot, cause: str) -> Event:
        return Event(type="death", frame=curr.global_timer,
                     timestamp_utc=curr.wall_time_utc,
                     payload={"cause": cause,
                              "igt_frames": curr.igt_overall,
                              "level": curr.curr_level})
