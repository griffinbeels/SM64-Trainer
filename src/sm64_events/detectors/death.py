# src/sm64_events/detectors/death.py
"""death: edge into one of Mario's death actions (cause from the action id).

Action-edge detection, same philosophy as star_grab: the death actions are
mutually exclusive, persist for many frames, and identify the CAUSE.
Payload carries the IGT at death so the tracking layer can record the
failed attempt's duration. Health/lives corroboration deliberately
omitted (would need new memory reads); the action set is sufficient.

Stateless: an emulator reconnect mid-death re-fires once (prev resets to a
fresh pair) — same accepted behavior as star_grab."""
from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.memory.addresses import DEATH_ACTIONS


class DeathDetector:
    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        if curr.mario_action not in DEATH_ACTIONS:
            return []
        if prev.mario_action in DEATH_ACTIONS:
            return []  # still dying; one event per death
        return [Event(type="death", frame=curr.global_timer,
                      timestamp_utc=curr.wall_time_utc,
                      payload={"cause": DEATH_ACTIONS[curr.mario_action],
                               "igt_frames": curr.igt_overall,
                               "level": curr.curr_level})]
