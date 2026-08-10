"""How often does the hand hold a target that CANNOT be run where he stands?

Round 29's measurement, and the number that sizes the defect before anything is
changed. His framing, which is the right one:

    "when we enter the Bowser arenas, there's LITERALLY nothing else that can
    be done there except the Bowser fights. We should be clearing out the hand
    in these cases, or we've defined things incorrectly."

Two halves of the system disagree about one fact. `tracking/practicable.py`
REFUSES a pick made from a place the entity cannot be run in — that is the
server's own rule, enforced on every `POST /api/target`. But the retirement
rule (projection caveat 12) compares COURSES and reads the castle and the
arenas as transit, so a target picked up elsewhere is HELD in exactly the
places a fresh pick of it would be rejected.

This replays every reachable journal and, at each place the player settles in,
asks `practicable_here` about whatever the projector is holding. It writes
nothing and changes nothing.

    uv run python tools/measure_unrunnable_holds.py [db ...]

Read the ARENA line first: an arena hosts exactly one practicable thing, so a
held foreign target there is the shape he reported, with no judgement call in
it. The CASTLE line is the softer half — a hub really is transit, and the count
there is the size of the argument rather than a bug on its own.
"""
from __future__ import annotations

import sqlite3
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from sm64_events.memory.addresses import (  # noqa: E402
    BOWSER_1_ARENA, BOWSER_2_ARENA, BOWSER_3_ARENA, LEVEL_CASTLE_INSIDE,
    course_for_level)
from sm64_events.storage.db import Database  # noqa: E402
from sm64_events.tracking import projection, segments as seg  # noqa: E402
from sm64_events.tracking.defaults import reconcile_defaults  # noqa: E402
from sm64_events.tracking.practicable import practicable_here  # noqa: E402

_ARENAS = {BOWSER_1_ARENA, BOWSER_2_ARENA, BOWSER_3_ARENA}


def _snapshot(live: Path, dest: Path) -> Path:
    """Read-only copy via sqlite's OWN backup API -- never a file copy, which
    can catch a torn WAL while a server holds the db open."""
    src = sqlite3.connect(f"{live.resolve().as_uri()}?mode=ro", uri=True)
    out = sqlite3.connect(str(dest))
    try:
        src.backup(out)
    finally:
        src.close()
        out.close()
    return dest


def _place_of(level: int | None, area: int | None) -> str:
    if level in _ARENAS:
        return "arena"
    if level == LEVEL_CASTLE_INSIDE:
        return "castle"
    course = course_for_level(level) if level is not None else None
    return "course" if course is not None else "elsewhere"


def measure(db_path: Path, scratch: Path) -> Counter:
    import dataclasses
    import json

    snap = _snapshot(db_path, scratch / (db_path.parent.parent.name + ".db"))
    defs_db = Database(_snapshot(snap, snap.with_name(snap.stem + "_defs.db")))
    reconcile_defaults(defs_db, json.loads(
        (_ROOT / "src/sm64_events/data/defaults.seed.json")
        .read_bytes().decode("utf-8")))
    keys = [f.name for f in dataclasses.fields(seg.SegmentDef)]
    defs = [seg.SegmentDef(**{k: row[k] for k in keys})
            for row in defs_db.segment_defs()]

    events = Database(snap).events()
    tally: Counter = Counter()
    original = projection.Projector.feed
    state = {"level": None, "area": None}

    def watching(self, ev):
        closed = original(self, ev)
        if ev.type in ("level_changed", "area_changed"):
            state["level"] = ev.payload.get("level") or ev.payload.get("to")
            if ev.type == "area_changed":
                state["area"] = ev.payload.get("to")
        if ev.type != "spawned":            # judge only where he SETTLES
            return closed
        state["area"] = ev.payload.get("area", state["area"])
        state["level"] = ev.payload.get("level", state["level"])
        target = self.target
        if target is None:
            return closed
        place = _place_of(state["level"], state["area"])
        node = projection.target_entity_key(target)
        stage = {"level": state["level"], "area": state["area"],
                 "course_id": course_for_level(state["level"]),
                 "mode": "stars"}
        armed = set(self.armed_segment_ids())
        running = (target[0] == "segment" and target[1] in armed)
        ok = practicable_here(stage, _node_for(target, defs), running)
        tally[f"{place}:{'runnable' if ok else 'UNRUNNABLE'}"] += 1
        if not ok:
            tally[f"  {place} holding {node}"] += 1
        return closed

    projection.Projector.feed = watching
    try:
        projection.replay(events, segments=defs)
    finally:
        projection.Projector.feed = original
    tally["events"] = len(events)
    return tally


def _node_for(target, defs):
    """The world node the held target lives at -- the same resolution
    `service.request_target` uses, so this measures the server's own rule
    rather than a second opinion about it."""
    if target[0] == "segment":
        d = next((x for x in defs if x.id == target[1]), None)
        return seg.start_origin(d.start_triggers) if d is not None else None
    return seg.star_origin(target[1])


def main() -> None:
    import tempfile
    paths = [Path(a) for a in sys.argv[1:]] or [_ROOT / "data" / "tracker.db"]
    with tempfile.TemporaryDirectory(
            ignore_cleanup_errors=True) as tmp:
        for path in paths:
            if not path.exists():
                print(f"(missing) {path}")
                continue
            print(f"\n=== {path}")
            for key, count in sorted(measure(path, Path(tmp)).items()):
                print(f"  {key:52} {count}")


if __name__ == "__main__":
    main()
