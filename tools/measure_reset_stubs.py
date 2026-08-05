"""Score the interrupted-action rule against the REAL journals (task 0084).

`detectors/anchors.py` used to swallow the action a reset interrupted for one
POLL. A 60 Hz poll reads the same 30 fps game frame twice, so the next poll
re-read the identical byte and marked the FRESH attempt as acted -- and the
Usamune menu warp that followed banked a second reset row for an attempt nobody
made ("warping to the beginning of a course inside a subarea results in an
extra reset"). The fix holds the action INSTANCE instead.

Three questions, in descending order of how much the answer can be trusted.

**1. How many `mario_acted` events are re-reads?** EXACT, no assumption:
`global_timer` is the game's own frame counter, so a second poll of the same
value cannot have seen a new action. Measured 2026-08-04: **1,325 in the repo
journal and 811 in the installed exe's**, out of 3,558 and 2,447 anchors.

**2. How long does the hold last?** The latch dies on the first differing byte,
and a reset's own reload supplies one. Measured over the anchors that see a
`spawned` at all: **1,657 of 1,686 land within 3 frames of the anchor** (repo
journal: 1536/113/7/1, then 29 stragglers; the exe: 1638/69/0/1 plus 2 at four
frames, then 19). So on any attempt that lasts longer than a blink, the fixed
detector IS the old detector -- which is the real bound on this change, and the
one question 3 is too blunt to state.

**3. What would the projector have recorded?** A STRICT UPPER BOUND and not a
blast radius -- read the caveat below before quoting the number.

WHY 3 CANNOT BE EXACT ON A PRE-2026-08-04 JOURNAL, stated so nobody re-derives
it by guess: the fix is about the action INSTANCE, and `mario_acted` recorded
only the frame. Mario running, resetting, and running again is the same action
id twice, which after the fact is indistinguishable from the anchor's own byte
lingering -- so this tool cannot tell whether a dropped emit would have been
re-emitted three frames later. It assumes the pessimistic answer wherever the
journal offers no other evidence, which over-removes. `mario_acted` carries its
action from 2026-08-04 on; against a journal recorded after that, replace the
frame test in `without_the_swallowed_action` with an action-identity test and
question 3 becomes exact.

The evidence it does use, so a dropped emit is MOVED rather than deleted where
it can be: a `jump`/`rollout`/`star_collected`/`key_grabbed`/`warp_entered` in
the period, a `spawned` (which proves the byte changed, so the latch is gone),
and the closing anchor's own `prev_action`. Both the event and the anchor's
payload flag are patched, and both matter: `_close_by_reset` reads the payload,
while `_close_by_death` and `_close` read the EVENT and nothing else.

Usage:
    uv run python tools/measure_reset_stubs.py [db]
"""
import dataclasses
import json
import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from sm64_events.memory.addresses import (DEATH_ACTIONS, LEVEL_EXIT_ACTIONS,
                                          PASSIVE_ACTIONS)
from sm64_events.storage.db import Database, EventRow
from sm64_events.tracking.projection import replay
from sm64_events.tracking.segments import SegmentDef

_ANCHORS = ("practice_reset", "state_loaded")
# Events Mario could not have produced without doing something, so a period
# holding one was acted whatever the swallowed byte said.
_ACTIVITY_EVIDENCE = ("jump", "rollout", "star_collected", "key_grabbed",
                      "warp_entered")


def _online_backup(live_path: Path, backup_path: Path) -> None:
    """sqlite3's OWN backup API -- the live db is WAL and a server may hold it."""
    src = sqlite3.connect(f"{live_path.resolve().as_uri()}?mode=ro", uri=True)
    dest = sqlite3.connect(str(backup_path))
    try:
        src.backup(dest)
    finally:
        src.close()
        dest.close()


def _segment_defs_from(db: Database) -> list[SegmentDef]:
    """The definitions that will SHIP: reconcile the bundled seed into the
    SNAPSHOT first, exactly as main.py does at startup."""
    from sm64_events.core.paths import bundled_defaults_seed
    from sm64_events.tracking.defaults import reconcile_defaults
    seed = json.loads(bundled_defaults_seed().read_bytes().decode("utf-8"))
    reconcile_defaults(db, seed)
    keys = [f.name for f in dataclasses.fields(SegmentDef)]
    return [SegmentDef(**{k: row[k] for k in keys}) for row in db.segment_defs()]


def rereads_and_spawn_latency(events: list[EventRow]) -> tuple[int, Counter]:
    """Questions 1 and 2 -- the halves that need no assumption."""
    rereads, latency, anchor_frame = 0, Counter(), None
    for row in events:
        if row.type == "mario_acted" and row.frame == anchor_frame:
            rereads += 1
        elif row.type == "spawned" and anchor_frame is not None:
            latency[min(row.frame - anchor_frame, 99)] += 1
            anchor_frame = None
        elif row.type in _ANCHORS:
            anchor_frame = row.frame
    return rereads, latency


def without_the_swallowed_action(events: list[EventRow]) -> list[EventRow]:
    """Question 3's input: the journal as the fixed detector would have written
    it, pessimistic wherever the evidence runs out (module docstring)."""
    next_id = max((row.id for row in events), default=0) + 1
    out = []
    last_anchor_frame, latched, cleared, acted = None, None, True, False

    def emit_acted(frame, session_id):
        nonlocal next_id, acted
        out.append(EventRow(next_id, session_id, 0, "mario_acted", frame, "", {}))
        next_id += 1
        acted = True

    for row in events:
        if row.type == "mario_acted" and row.frame == last_anchor_frame:
            continue                # the byte the anchor already swallowed
        if row.type == "mario_acted":
            acted = True
        if row.type == "spawned":
            cleared = True          # a different byte: the latch is gone
        if row.type in _ACTIVITY_EVIDENCE and not acted:
            emit_acted(row.frame, row.session_id)
        if row.type in _ANCHORS:
            previous = row.payload.get("prev_action")
            if (not acted and previous is not None
                    and (cleared or previous != latched)
                    and previous not in PASSIVE_ACTIONS
                    and previous not in DEATH_ACTIONS
                    and previous not in LEVEL_EXIT_ACTIONS):
                emit_acted(row.frame, row.session_id)
            if row.payload.get("acted_tracking"):
                row = EventRow(row.id, row.session_id, row.seq, row.type,
                               row.frame, row.wall_time_utc,
                               {**row.payload, "mario_acted": acted})
            last_anchor_frame = row.frame
            acted, latched, cleared = False, row.payload.get("action"), False
        elif row.type == "game_reset":
            acted, latched, cleared = False, None, True
        out.append(row)
    return out


def main() -> None:
    live_path = Path(sys.argv[1] if len(sys.argv) > 1 else "data/tracker.db")
    if not live_path.exists():
        print(f"ERROR: database not found: {live_path}", file=sys.stderr)
        sys.exit(1)

    with tempfile.TemporaryDirectory(prefix="measure_reset_stubs_") as scratch:
        backup_path = Path(scratch) / "tracker_snapshot.db"
        _online_backup(live_path, backup_path)
        db = Database(backup_path)
        try:
            events = db.events()
            defs = _segment_defs_from(db)
        finally:
            db.close()

    anchors = sum(1 for row in events if row.type in _ANCHORS)
    rereads, latency = rereads_and_spawn_latency(events)
    print(f"=== {live_path} ===")
    print(f"{len(events)} events, {anchors} anchors")
    print(f"1. re-reads of the swallowed byte (exact): {rereads}")
    print("2. frames from an anchor to the reload's spawn: "
          + ", ".join(f"{frames}f x{count}"
                      for frames, count in sorted(latency.items())))

    before = {a.id: a for a in replay(events, segments=defs)[0]}
    after = {a.id: a for a in replay(without_the_swallowed_action(events),
                                     segments=defs)[0]}
    removed = [before[i] for i in before.keys() - after.keys()]
    added = [after[i] for i in after.keys() - before.keys()]
    changed = [i for i in before.keys() & after.keys() if before[i] != after[i]]
    print(f"3. UPPER BOUND ONLY (docstring): attempts {len(before)} -> "
          f"{len(after)}  (-{len(removed)} +{len(added)}, {len(changed)} changed)")
    by_outcome = Counter(a.outcome for a in removed)
    for outcome, count in sorted(by_outcome.items()):
        print(f"     {count:5} x {outcome}")
    if by_outcome.keys() - {"reset"} or added or changed:
        print("     a removed success, or anything ADDED or CHANGED, is worth "
              "reading back against the raw journal")


if __name__ == "__main__":
    main()
