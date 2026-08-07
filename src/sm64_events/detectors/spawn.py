# src/sm64_events/detectors/spawn.py
"""spawned: Mario gained control at a spawn-in. Two observable shapes
(both VERIFY at the live gate — addresses.py):
- kind="intro": edge OUT of ACT_INTRO_CUTSCENE (file-select spawn; the
  Lakitu Skip start anchor — control begins when the cutscene action ends)
- kind="spawn": edge INTO a SPAWN_* action (non-intro spawn-ins)
Spurious grounds spawns (e.g. cannon exits) are harmless: segment starts
re-arm/disarm without recording rows. A savestate saved mid-intro and
loaded later also fires a spurious kind="intro" — same harmless re-arm.

A PLACE CHANGE CARRIES USAMUNE'S NUMBER, since 2026-08-06 -- his report:
*"It looks like some events have the timer next to them, most don't? I would
expect the timer for all of them."* The recorder surfaces a row's own
`igt_frames` and never computes one, so a type that does not stamp it draws a
blank cell. Read through the shared `detectors/igt_clock.py`, like
star_grab/key/warp/moment, so every time on screen comes from one derivation.
Forward-only: the raw counter at a historical edge was never journaled.

A spawn's number is 0'00"00, and that is the point rather than a degenerate
case: Usamune zeroes at the SPAWN, so this row is the zero the whole run is
measured from and the recorder can say so. It reads `IgtClock.igt_at_spawn`
rather than `igt_at` — this docstring claimed the zero for a day while the
shipped rows carried the PREVIOUS run's final time, because the shared clock's
reset-race guard reconstructs across exactly this edge and its premise is
inverted here. That method's docstring carries the measurement and the guard
that keeps a mid-run spawn honest.
"""
from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.core.timefmt import format_igt
from sm64_events.detectors.igt_clock import IgtClock
from sm64_events.memory.addresses import ACT_INTRO_CUTSCENE, SPAWN_ACTIONS


class SpawnDetector:
    def __init__(self):
        self._clock = IgtClock()

    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        self._clock.seed(prev)
        events = self._detect(prev, curr)
        self._clock.observe(curr)
        return events

    def _detect(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        if (prev.mario_action == ACT_INTRO_CUTSCENE
                and curr.mario_action != ACT_INTRO_CUTSCENE):
            kind = "intro"
        elif (curr.mario_action in SPAWN_ACTIONS
                and prev.mario_action not in SPAWN_ACTIONS):
            kind = "spawn"
        else:
            return []
        igt_frames, source = self._clock.igt_at_spawn(curr.global_timer, curr)
        return [Event(type="spawned", frame=curr.global_timer,
                      timestamp_utc=curr.wall_time_utc,
                      payload={"level": curr.curr_level, "kind": kind,
                               "igt_frames": igt_frames, "igt_source": source,
                               "igt": format_igt(igt_frames)})]
