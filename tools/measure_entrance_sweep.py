"""Score the ENTRANCE-TOUCH sweep against the real journals (task 0081,
spec 2026-08-04-course-entrance-touch-design).

56 definitions moved their end from the course LOAD to the ENTRANCE TOUCH --
the frame Mario collides with the painting, portal, hole or pipe, 77 frames
earlier (23 at a pipe). Griffin's constraint on that change, 2026-08-04:

    "our topological logic is already working as expected and fine -- this
    should NOT break that. We need to be careful to maintain the same
    functionality regarding legitimacy of segments / going between steps of
    a segment / etc."

That is an acceptance bar, and it is checkable rather than arguable. This tool
replays each journal TWICE -- once with the corpus as it shipped before the
sweep, once with the corpus as it ships now -- and diffs three things:

1. **Cancels.** Which definition the topological rules disarmed, on which
   journal event, and on which move. Expected difference: NONE. A difference
   here means the sweep changed what the wrong-turn rule considers a wrong
   turn, which is the thing that must not move.
2. **`declared_nodes`.** The world nodes each definition names as steps of its
   own route -- what exempts a deliberate re-entry from Rule 2. Read straight
   off the definitions, no replay needed. Expected difference: NONE.
3. **Recorded rows.** Every closed attempt: outcome, time, timing source and
   closing event. Differences here are the POINT of the change, so they are
   reported rather than gated -- times drop by ~77 frames (23 at a pipe), and
   a success DISAPPEARS wherever the destination was reached by a Usamune menu
   warp, which fabricates the edge and so produces no touch to end on. Every
   such row is printed with its journal ids so it can be read back one at a
   time.

**A non-zero count on (1) or (2) is a finding, not a number to accept, and it
exits non-zero.** Do not weaken the comparison to make it go down.

The OLD corpus comes from git (`git show <rev>:src/sm64_events/data/
defaults.seed.json`), so the baseline is the artifact that actually shipped
rather than a reconstruction of it.

Usage:
    uv run python tools/measure_entrance_sweep.py [--before REV] [db ...]

With no db arguments it measures every journal it can find: this checkout's,
each worktree's, and the installed exe's under %LOCALAPPDATA%.
"""
import dataclasses
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from sm64_events.storage.db import Database                       # noqa: E402
from sm64_events.tracking.defaults import reconcile_defaults      # noqa: E402
from sm64_events.tracking.projection import replay                # noqa: E402
from sm64_events.tracking.segments import (SegmentDef,            # noqa: E402
                                           SegmentEngine,
                                           declared_nodes)


def _online_backup(live_path: Path, backup_path: Path) -> None:
    """Read-only snapshot via sqlite3's OWN backup API -- never a file copy,
    which can catch a torn WAL mid-write while a server holds the db open."""
    src = sqlite3.connect(f"{live_path.resolve().as_uri()}?mode=ro", uri=True)
    dest = sqlite3.connect(str(backup_path))
    try:
        src.backup(dest)
    finally:
        src.close()
        dest.close()


def _seed_at(rev: str | None) -> dict:
    """The bundled seed as of `rev`, or the working tree's if rev is None."""
    path = "src/sm64_events/data/defaults.seed.json"
    if rev is None:
        return json.loads((_ROOT / path).read_bytes().decode("utf-8"))
    blob = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=_ROOT,
                          capture_output=True, check=True).stdout
    return json.loads(blob.decode("utf-8"))


def _defs_for(snapshot: Path, seed: dict) -> list[SegmentDef]:
    """Reconcile `seed` into a FRESH copy of the snapshot and read back the
    definitions that would ship. Reconciling into the snapshot (never the live
    file) is what main.py does at every startup; without it the tool would
    score whatever corpus the db was last reconciled against and a corpus
    change would measure as no change at all."""
    scratch = snapshot.with_name(snapshot.stem + "_defs.db")
    _online_backup(snapshot, scratch)
    db = Database(scratch)
    problems = reconcile_defaults(db, seed)
    if problems:
        print(f"  WARNING: {len(problems)} seed rows would not reconcile",
              file=sys.stderr)
    keys = [f.name for f in dataclasses.fields(SegmentDef)]
    return [SegmentDef(**{k: row[k] for k in keys}) for row in db.segment_defs()]


def _replay_capturing_cancels(events, defs):
    """Replay with the real rules, recording every disarm the POSITION
    judgement caused. Reads the engine's own armed set either side of the real
    `_flush_move` rather than restating the precedence here -- a second copy of
    the rules is exactly what would drift."""
    cancels = []
    original = SegmentEngine._flush_move

    def capturing(self, ev, notices):
        before = set(self._armed)
        previous = self._settled_node
        original(self, ev, notices)
        node = self._settled_node
        if previous is None or node is None or node == previous:
            return
        for segment_id in sorted(before - set(self._armed)):
            cancels.append((segment_id, ev.id, previous, node))

    SegmentEngine._flush_move = capturing
    try:
        return replay(events, segments=defs)[0], cancels
    finally:
        SegmentEngine._flush_move = original


def _rows(attempts) -> dict:
    """Every SEGMENT attempt, keyed by id, reduced to what a comparison cares
    about. Cleared rows are included: clearing is a retroactive curation
    decision and says nothing about whether the arm reached its end."""
    return {a.id: (a.segment_id, a.outcome, a.igt_frames, a.rta_frames,
                   a.timed_by, a.closed_by)
            for a in attempts if a.segment_id is not None}


def _journals() -> list[Path]:
    found = [_ROOT / "data" / "tracker.db"]
    found += sorted((_ROOT / ".claude" / "worktrees").glob("*/data/tracker.db"))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        found.append(Path(local) / "SM64Trainer" / "tracker.db")
    return [p for p in found if p.exists()]


def measure(live_path: Path, before_rev: str) -> bool:
    """True when the topological behaviour is byte-identical."""
    print(f"\n=== {live_path} ===")
    # ignore_cleanup_errors: Database keeps its sqlite connection open for the
    # process lifetime, and Windows refuses to unlink an open file.
    with tempfile.TemporaryDirectory(prefix="entrance_sweep_",
                                     ignore_cleanup_errors=True) as scratch:
        snapshot = Path(scratch) / "snapshot.db"
        _online_backup(live_path, snapshot)
        db = Database(snapshot)
        events = db.events()
        old_defs = _defs_for(snapshot, _seed_at(before_rev))
        new_defs = _defs_for(snapshot, _seed_at(None))

        old_by_id = {d.id: d for d in old_defs}
        node_diffs = [
            (d.name, sorted(declared_nodes(old_by_id[d.id])),
             sorted(declared_nodes(d)))
            for d in new_defs if d.id in old_by_id
            and declared_nodes(old_by_id[d.id]) != declared_nodes(d)]

        old_attempts, old_cancels = _replay_capturing_cancels(events, old_defs)
        new_attempts, new_cancels = _replay_capturing_cancels(events, new_defs)

        names = {d.id: d.name for d in new_defs}
        cancel_diffs = sorted(set(old_cancels) ^ set(new_cancels))
        old_rows, new_rows = _rows(old_attempts), _rows(new_attempts)

        print(f"  {len(events):,} events, {len(new_defs)} definitions")
        print(f"  cancels: {len(old_cancels)} before, {len(new_cancels)} after,"
              f" {len(cancel_diffs)} differing")
        print(f"  declared_nodes differing: {len(node_diffs)}")
        for name, was, now in node_diffs:
            print(f"     {name}: {was} -> {now}")
        for segment_id, event_id, source, target in cancel_diffs[:40]:
            side = "before-only" if (segment_id, event_id, source, target) \
                in set(old_cancels) else "after-only"
            print(f"     {side}: {names.get(segment_id, segment_id)} "
                  f"at event {event_id} ({source} -> {target})")

        old_wins = {i for i, r in old_rows.items() if r[1] == "success"}
        new_wins = {i for i, r in new_rows.items() if r[1] == "success"}
        moved = [(i, old_rows[i], new_rows[i]) for i in old_wins & new_wins
                 if old_rows[i] != new_rows[i]]
        print(f"  successes: {len(old_wins)} before, {len(new_wins)} after "
              f"({len(old_wins - new_wins)} lost, {len(new_wins - old_wins)} "
              f"gained, {len(moved)} re-timed)")
        for attempt_id in sorted(old_wins - new_wins):
            segment_id, _o, igt, rta, *_ = old_rows[attempt_id]
            print(f"     LOST  {names.get(segment_id, segment_id):28} "
                  f"attempt {attempt_id} (igt={igt} rta={rta}) -- read the "
                  f"journal back: a menu warp has no touch to end on")
        for attempt_id in sorted(new_wins - old_wins):
            segment_id, _o, igt, rta, *_ = new_rows[attempt_id]
            print(f"     GAINED {names.get(segment_id, segment_id):27} "
                  f"attempt {attempt_id} (igt={igt} rta={rta})")
        deltas = [(old[3] or 0) - (new[3] or 0) for _i, old, new in moved
                  if old[3] and new[3]]
        if deltas:
            deltas.sort()
            print(f"     re-timed by {deltas[0]}..{deltas[-1]} frames "
                  f"(median {deltas[len(deltas) // 2]})")
        return not cancel_diffs and not node_diffs


def main() -> None:
    argv = sys.argv[1:]
    before_rev = "HEAD"
    if "--before" in argv:
        index = argv.index("--before")
        before_rev = argv[index + 1]
        del argv[index:index + 2]
    targets = [Path(a) for a in argv] or _journals()
    if not targets:
        print("ERROR: no journal found", file=sys.stderr)
        sys.exit(1)
    print(f"baseline corpus: {before_rev}")
    clean = [measure(path, before_rev) for path in targets]
    print("\n" + ("TOPOLOGY UNCHANGED across all journals" if all(clean)
                  else "TOPOLOGY MOVED -- read the diffs above, one at a time"))
    sys.exit(0 if all(clean) else 1)


if __name__ == "__main__":
    main()
