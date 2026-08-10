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

A SPAWN NAMES ITS SUBAREA AND ITS SPAWN POINT (round 20 item 3): "When I
reset the level INSIDE OF A SUBAREA, we should actually have a special
'Spawned into Lethal Lava Land [Subarea Name]' event... ideally we would be
able to identify *which* spawn we came through in each subarea" (SSL's
pyramid has a top and a bottom entry). `area` is the settled gCurrAreaIndex.
`spawn_node` is read out of `sWarpDest` — the game performs EVERY spawn
through that struct, its nodeId says which warp node placed Mario, and the
snapshot already samples all four bytes for the warp detector (probe
2026-08-05: the struct SURVIVES a completed warp, which is exactly what
makes it readable here, after the fact). Taken only when the struct's own
level AND area match the spawn's — a savestate load or a stale struct
degrades to None, never to a foreign node. VERIFY (live): entering the
pyramid by the top and then the bottom must journal two different
spawn_node values.
"""
from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.core.timefmt import format_igt
from sm64_events.detectors.igt_clock import IgtClock
from sm64_events.memory.addresses import ACT_INTRO_CUTSCENE, SPAWN_ACTIONS


def spawn_node_from(curr: GameSnapshot) -> int | None:
    """WHICH spawn point this spawn came through, or None when the warp
    struct cannot vouch for it (type 0 = never warped; a level/area
    mismatch = the struct describes some OTHER warp than this spawn)."""
    if (curr.warp_dest_type != 0
            and curr.warp_dest_level == curr.curr_level
            and curr.warp_dest_area == curr.curr_area):
        return curr.warp_dest_node
    return None


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
                               "area": curr.curr_area,
                               "spawn_node": spawn_node_from(curr),
                               "igt_frames": igt_frames, "igt_source": source,
                               "igt": format_igt(igt_frames)})]
