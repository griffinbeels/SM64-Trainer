# src/sm64_events/detectors/spawn.py
"""spawned: Mario gained control at a spawn-in. Two observable shapes
(both VERIFY at the live gate — addresses.py):
- kind="intro": edge OUT of ACT_INTRO_CUTSCENE (file-select spawn; the
  Lakitu Skip start anchor — control begins when the cutscene action ends)
- kind="spawn": edge INTO a SPAWN_* action (non-intro spawn-ins)
Spurious grounds spawns (e.g. cannon exits) are harmless: segment starts
re-arm/disarm without recording rows. A savestate saved mid-intro and
loaded later also fires a spurious kind="intro" — same harmless re-arm."""
from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.memory.addresses import ACT_INTRO_CUTSCENE, SPAWN_ACTIONS


class SpawnDetector:
    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        if (prev.mario_action == ACT_INTRO_CUTSCENE
                and curr.mario_action != ACT_INTRO_CUTSCENE):
            kind = "intro"
        elif (curr.mario_action in SPAWN_ACTIONS
                and prev.mario_action not in SPAWN_ACTIONS):
            kind = "spawn"
        else:
            return []
        return [Event(type="spawned", frame=curr.global_timer,
                      timestamp_utc=curr.wall_time_utc,
                      payload={"level": curr.curr_level, "kind": kind})]
