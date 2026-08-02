"""Score the topological cancel rules against the REAL journal (spec
2026-08-01-topological-segment-validity).

`tracking/segments.py::can_run_from` refuses to consult the world-edge table
with a specific objection: "a check derived from that table could only ever be
tested against the table it came from." This tool is the answer to it. The
rules are not compared against the table that produced them; they are replayed
over thousands of real events and asked how many attempts the human ACTUALLY
COMPLETED they would have thrown away.

**Any non-zero killed count is a missing world edge or a bug, examined one at a
time -- never a number to accept.** The likely causes, in order: a missing row
in `memory/addresses.py::WORLD_EDGES_*`; a `step_node` branch that should
answer None; a transient shape the one-frame defer does not cover. Do not
weaken a rule to make the number go down.

What it does:
1. Takes a read-only ONLINE backup via sqlite3's own backup API -- never a file
   copy, which can catch a torn WAL mid-write (the live db is WAL-mode and a
   server may hold it open right now).
2. Replays the whole journal TWICE with the REAL stored definitions: once with
   `SegmentEngine._flush_move` monkeypatched to a no-op (baseline, i.e. the
   behaviour before this branch), once unpatched. A monkeypatch rather than a
   production feature flag, because a flag would outlive the measurement.
3. Reports every segment success present in the baseline and absent with the
   rules on, naming the definition and the settled move that killed it.
4. Reports the settled moves the rules judged, split by whether both endpoints
   are ordinary levels (a definite warp -- the real game has no course-to-course
   edge) or one touches the castle interior.

MEASURED 2026-08-01, both journals, with the rules as shipped:
  installed exe (17,424 events): 419 settled moves, 225 off-graph (160 of them
    course->course, which the real game cannot do at all). 82 segment
    successes, 82 survivors. CLEAN.
  repo checkout (20,542 events): 736 settled moves, 340 off-graph (235
    course->course). 112 successes, 110 survivors. The two killed were each
    read back against the raw journal and are the live report itself, banked
    as times:
    - `LLL -> HMC` (attempt 450000013672, 713f). Journal ids 13672-13687: he
      exited LLL, WARPED BACK INTO LLL, then warped LLL -> HMC (22 -> 7, not an
      edge either). The banked 23.8 s spans the whole round trip.
    - `Bowser 2 -> Upstairs` (attempt 570000018355, 2482f). Journal ids
      18355-18376: exited the arena into the basement, spent 30 s inside BitFS
      (with a target_set of its own), came out and went upstairs. The banked
      83 s is mostly detour.

  A THIRD kill in the first run was NOT a bug and is what produced the
  resurrection rule (`SegmentEngine._cancelled`). `Bowser 1 -> WF`, attempt
  350000017935, journal ids 17926-17940: armed by a Bowser 1 exit into the
  lobby, he warped to BitDW for 7 s, came back to the lobby, pressed reset AT
  THE ARM POSITION and ran lobby -> WF in 16 s. Redoing that start trigger
  means redoing the whole fight, so the reset IS how the movement is re-run --
  Griffin's ruling, with the forfeit half in the same breath: a reset SOMEWHERE
  ELSE ends it for good ("we've now gone out of order in a way that doesn't
  make sense for practicing... until I get back to Bowser 1 and trigger it from
  the beginning again"). It survives now.

Usage:
    uv run python tools/measure_topology_cancels.py [db]
"""
import dataclasses
import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from sm64_events.memory.addresses import CASTLE_LEVELS, node_label
from sm64_events.storage.db import Database
from sm64_events.tracking import topology
from sm64_events.tracking.projection import replay
from sm64_events.tracking.segments import (SEGMENT_ATTEMPT_OFFSET, SegmentDef,
                                           SegmentEngine)


def _online_backup(live_path: Path, backup_path: Path) -> None:
    """Read-only snapshot via sqlite3's OWN backup API -- same helper
    tools/measure_budget.py uses, and for the same reason."""
    src = sqlite3.connect(f"{live_path.resolve().as_uri()}?mode=ro", uri=True)
    dest = sqlite3.connect(str(backup_path))
    try:
        src.backup(dest)
    finally:
        src.close()
        dest.close()


def _segment_defs_from(db: Database) -> list[SegmentDef]:
    keys = [f.name for f in dataclasses.fields(SegmentDef)]
    return [SegmentDef(**{k: row[k] for k in keys}) for row in db.segment_defs()]


def _successes(attempts) -> dict:
    """Every SEGMENT success with a real time, by attempt id. Cleared rows are
    INCLUDED for the same reason measure_budget.py includes them: clearing is a
    retroactive curation decision, and says nothing about whether the arm was
    alive when it reached its end trigger."""
    return {a.id: a for a in attempts
            if a.outcome == "success" and a.segment_id is not None}


def _replay_baseline(events, defs):
    """The pre-branch behaviour: the position rules never run."""
    original = SegmentEngine._flush_move
    SegmentEngine._flush_move = lambda self, ev, notices: None
    try:
        return replay(events, segments=defs)[0]
    finally:
        SegmentEngine._flush_move = original


def _replay_with_capture(events, defs):
    """Rules ON, recording every settled move judged and every disarm the
    judgement caused. Reads the ENGINE's own state before and after the real
    `_flush_move` rather than re-deriving the rules here -- a second copy of
    the precedence would be exactly the kind of restatement that drifts."""
    moves, kills = [], []
    original = SegmentEngine._flush_move

    def capturing(self, ev, notices):
        previous = self._settled_node
        pending = self._pending_move
        armed_before = set(self._armed)
        original(self, ev, notices)
        node = self._settled_node
        if previous is None or node is None or node == previous:
            return
        moves.append((previous, node))
        gone = armed_before - set(self._armed)
        if gone:
            kills.append({"frame": pending[0] if pending else ev.frame,
                          "event_id": ev.id, "from": previous, "to": node,
                          "segment_ids": sorted(gone)})

    SegmentEngine._flush_move = capturing
    try:
        return replay(events, segments=defs)[0], moves, kills
    finally:
        SegmentEngine._flush_move = original


def _is_course_to_course(from_key: str, to_key: str) -> bool:
    """Both endpoints ordinary levels -- the real game has no direct
    course-to-course edge, so such a move can only be the warp menu."""
    return (int(from_key.partition(":")[0]) not in CASTLE_LEVELS
            and int(to_key.partition(":")[0]) not in CASTLE_LEVELS)


def main() -> None:
    db_arg = sys.argv[1] if len(sys.argv) > 1 else "data/tracker.db"
    live_path = Path(db_arg)
    if not live_path.exists():
        print(f"ERROR: database not found: {live_path}", file=sys.stderr)
        sys.exit(1)

    with tempfile.TemporaryDirectory(prefix="measure_topology_") as scratch:
        backup_path = Path(scratch) / "tracker_snapshot.db"
        _online_backup(live_path, backup_path)
        db = Database(backup_path)
        try:
            events = db.events()
            defs = _segment_defs_from(db)
        finally:
            db.close()

    print("=== Topological cancel measurement "
          "(tools/measure_topology_cancels.py) ===")
    if events:
        print(f"Journal: {live_path} -- {len(events)} events, "
              f"{events[0].wall_time_utc} .. {events[-1].wall_time_utc}")
    else:
        print(f"Journal: {live_path} -- 0 events")
    print(f"Definitions: {len(defs)}")
    print()

    baseline = _successes(_replay_baseline(events, defs))
    after_attempts, moves, kills = _replay_with_capture(events, defs)
    after = _successes(after_attempts)

    illegal = [(a, b) for a, b in moves if not topology.is_legal_move(a, b)]
    warps = [m for m in illegal if _is_course_to_course(*m)]
    print(f"Settled moves judged: {len(moves)}")
    print(f"  not an edge: {len(illegal)}  "
          f"({len(warps)} course->course, i.e. definitely the warp menu; "
          f"{len(illegal) - len(warps)} touching the castle)")
    print(f"Segment successes -- baseline: {len(baseline)}, "
          f"with the rules on: {len(after)}")
    print()

    killed = [baseline[i] for i in sorted(set(baseline) - set(after))]
    if not killed:
        print("VERDICT: CLEAN -- the rules killed no recorded success.")
    else:
        print(f"VERDICT: {len(killed)} REAL SUCCESSES KILLED "
              f"-- investigate each. Do NOT weaken a rule to move this number.")
        names = {d.id: d.name for d in defs}
        for attempt in killed:
            # The attempt id encodes the ARM's journal id (id %
            # SEGMENT_ATTEMPT_OFFSET, tracking/segments.py), so the kill that
            # actually ended this attempt is the first one for that definition
            # AFTER its arm. Matching on definition alone picks a kill from a
            # different episode entirely -- it named an event 2 BEFORE the arm
            # on the first run of this tool.
            arm_jid = attempt.id % SEGMENT_ATTEMPT_OFFSET
            culprit = next((k for k in kills
                            if attempt.segment_id in k["segment_ids"]
                            and k["event_id"] >= arm_jid), None)
            where = ("no recorded kill -- the arm never happened at all"
                     if culprit is None else
                     f"{node_label(culprit['from'])} -> "
                     f"{node_label(culprit['to'])} "
                     f"({culprit['from']} -> {culprit['to']}) "
                     f"at frame {culprit['frame']}, event {culprit['event_id']}")
            print(f"  attempt {attempt.id}  "
                  f"{names.get(attempt.segment_id, attempt.segment_id)!r}  "
                  f"rta={attempt.rta_frames}  killed by: {where}")

    if illegal:
        print()
        print("--- off-graph moves seen, most common first ---")
        for (a, b), n in Counter(illegal).most_common(20):
            tag = "warp" if _is_course_to_course(a, b) else "castle"
            print(f"{n:5d}  [{tag:<6}] {node_label(a):<22} -> "
                  f"{node_label(b):<22} ({a} -> {b})")


if __name__ == "__main__":
    main()
