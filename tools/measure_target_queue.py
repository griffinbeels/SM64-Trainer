"""Score the TARGET QUEUE (round 19) against the real journals.

His ruling made the held target a FIFO of detections: a deliberate arm takes
an empty hand, later detections wait their turn, a hooked head is held until
it completes / forfeits / expires, and an empty queue is neutral. The standing
pattern for a target-rule change (the 2026-08-03 armed-exemption deferral) is
to replay the real journals under the OLD rule and the NEW one and diff every
target reading before shipping — this tool is that measurement.

It replays each journal TWICE — once with the projection code as it shipped
at the branch base (from git, the artifact that actually ran, never a
reconstruction), once with the working tree — and diffs two things:

1. **Every target reading.** Each journal event after which the two sides
   hold a different target. These are the POINT of the change and are
   reported, grouped by transition shape, so each class can be read back
   against his ruling one at a time.
2. **Recorded rows.** Every attempt: identity, outcome, times. The queue can
   move these indirectly — the target feeds star attribution and the
   route/target arm guard — so every difference is printed with its ids.

Usage:
    uv run python tools/measure_target_queue.py [--before REV] [db ...]

With no db arguments it measures every journal it can find: this checkout's,
each sibling worktree's, and the installed exe's under %LOCALAPPDATA%.
Internally re-invokes itself as `--runner <srcdir> <db>` so each side imports
its own code in a clean process.
"""
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_DEFAULT_BEFORE = "2f19630"   # round 19's opening commit: the pre-queue rule


def _online_backup(live_path: Path, backup_path: Path) -> None:
    """Read-only snapshot via sqlite3's OWN backup API — never a file copy,
    which can catch a torn WAL mid-write while a server holds the db open."""
    src = sqlite3.connect(f"{live_path.resolve().as_uri()}?mode=ro", uri=True)
    dest = sqlite3.connect(str(backup_path))
    try:
        src.backup(dest)
    finally:
        src.close()
        dest.close()


def _journals() -> list[Path]:
    found = [_ROOT / "data" / "tracker.db"]
    parent = _ROOT.parent
    if parent.name == "worktrees":            # running from a worktree: add
        main = parent.parent.parent           # the primary checkout's journal
        found.append(main / "data" / "tracker.db")
        found += sorted(p for p in parent.glob("*/data/tracker.db")
                        if p != found[0])
    else:
        found += sorted((_ROOT / ".claude" / "worktrees")
                        .glob("*/data/tracker.db"))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        found.append(Path(local) / "SM64Trainer" / "tracker.db")
    seen, unique = set(), []
    for p in found:
        r = p.resolve()
        if r not in seen and p.exists():
            seen.add(r)
            unique.append(p)
    return unique


# -- runner side: runs inside ONE side's code ------------------------------

def _run_side(src_dir: str, db_path: str) -> None:
    sys.path.insert(0, src_dir)
    import dataclasses
    from sm64_events.storage.db import Database
    from sm64_events.tracking import projection
    from sm64_events.tracking.defaults import reconcile_defaults
    from sm64_events.tracking.segments import SegmentDef

    snapshot = Path(db_path)
    defs_copy = snapshot.with_name(snapshot.stem + "_defs.db")
    _online_backup(snapshot, defs_copy)
    db = Database(defs_copy)
    seed = json.loads((_ROOT / "src/sm64_events/data/defaults.seed.json")
                      .read_bytes().decode("utf-8"))
    reconcile_defaults(db, seed)
    keys = [f.name for f in dataclasses.fields(SegmentDef)]
    defs = [SegmentDef(**{k: row[k] for k in keys})
            for row in db.segment_defs()]

    events_db = Database(snapshot)
    events = events_db.events()

    trace = []
    original_feed = projection.Projector.feed

    def capturing(self, ev):
        closed = original_feed(self, ev)
        trace.append((ev.id, self.target))
        return closed

    projection.Projector.feed = capturing
    attempts, _ = projection.replay(events, segments=defs)

    changes = []
    last = None
    for event_id, target in trace:
        if target != last:
            changes.append([event_id,
                            list(target) if target is not None else None])
            last = target
    rows = {a.id: [a.segment_id, a.course_id, a.star_id, a.outcome,
                   a.igt_frames, a.rta_frames, a.cleared]
            for a in attempts}
    json.dump({"changes": changes, "rows": rows}, sys.stdout)


# -- parent side -----------------------------------------------------------

def _side(src_dir: Path, db: Path) -> dict:
    out = subprocess.run(
        [sys.executable, __file__, "--runner", str(src_dir), str(db)],
        capture_output=True, cwd=_ROOT)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.decode("utf-8", "replace")[-2000:])
    return json.loads(out.stdout.decode("utf-8"))


def _old_src(before_rev: str, scratch: Path) -> Path:
    """The src tree as it shipped at `before_rev`, extracted from git."""
    tar_path = scratch / "old_src.tar"
    with open(tar_path, "wb") as fh:
        subprocess.run(["git", "archive", before_rev, "src"], cwd=_ROOT,
                       stdout=fh, check=True)
    dest = scratch / "old"
    with tarfile.open(tar_path) as tar:
        tar.extractall(dest)
    return dest / "src"


def _target_by_event(changes: list) -> dict:
    return {event_id: tuple(t) if t is not None else None
            for event_id, t in changes}


def measure(live_path: Path, old_src: Path) -> None:
    print(f"\n=== {live_path} ===")
    with tempfile.TemporaryDirectory(prefix="target_queue_",
                                     ignore_cleanup_errors=True) as scratch:
        snapshot = Path(scratch) / "snapshot.db"
        _online_backup(live_path, snapshot)
        old = _side(old_src, snapshot)
        new = _side(_ROOT / "src", snapshot)

    # Reconstruct the full per-event target sequence from the change lists so
    # the two sides can be compared at EVERY event, not only where one moved.
    def full(changes, ids):
        by_event = _target_by_event(changes)
        current, out = None, {}
        for event_id in ids:
            if event_id in by_event:
                current = by_event[event_id]
            out[event_id] = current
        return out

    all_ids = sorted({event_id for event_id, _t in old["changes"]}
                     | {event_id for event_id, _t in new["changes"]})
    # The change lists only carry moved readings; walking their union of ids
    # in order is enough, because between those ids both sides are constant.
    old_seq, new_seq = full(old["changes"], all_ids), full(new["changes"], all_ids)
    diffs = [(event_id, old_seq[event_id], new_seq[event_id])
             for event_id in all_ids if old_seq[event_id] != new_seq[event_id]]

    def label(t):
        if t is None:
            return "none"
        return f"{t[0]}:{':'.join(str(x) for x in t[1:])}"

    shapes = Counter((("none" if o is None else o[0]),
                      ("none" if n is None else n[0]))
                     for _e, o, n in diffs)
    print(f"  target readings that differ: {len(diffs)} "
          f"(of {len(all_ids)} readings where either side moved)")
    for (old_kind, new_kind), count in shapes.most_common():
        print(f"     {old_kind:8} -> {new_kind:8}  x{count}")
    for event_id, old_t, new_t in diffs[:30]:
        print(f"     event {event_id}: {label(old_t)} -> {label(new_t)}")
    if len(diffs) > 30:
        print(f"     ... {len(diffs) - 30} more")

    old_rows = {int(k): tuple(v) for k, v in old["rows"].items()}
    new_rows = {int(k): tuple(v) for k, v in new["rows"].items()}
    lost = sorted(set(old_rows) - set(new_rows))
    gained = sorted(set(new_rows) - set(old_rows))
    changed = sorted(i for i in set(old_rows) & set(new_rows)
                     if old_rows[i] != new_rows[i])
    print(f"  rows: {len(old_rows)} before, {len(new_rows)} after "
          f"({len(lost)} lost, {len(gained)} gained, {len(changed)} changed)")
    for i in lost[:20]:
        print(f"     LOST    {i}: {old_rows[i]}")
    for i in gained[:20]:
        print(f"     GAINED  {i}: {new_rows[i]}")
    for i in changed[:20]:
        print(f"     CHANGED {i}: {old_rows[i]} -> {new_rows[i]}")


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "--runner":
        _run_side(sys.argv[2], sys.argv[3])
        return
    args = sys.argv[1:]
    before = _DEFAULT_BEFORE
    if args and args[0] == "--before":
        before = args[1]
        args = args[2:]
    journals = [Path(a) for a in args] or _journals()
    with tempfile.TemporaryDirectory(prefix="target_queue_src_",
                                     ignore_cleanup_errors=True) as scratch:
        old_src = _old_src(before, Path(scratch))
        for journal in journals:
            measure(journal, old_src)


if __name__ == "__main__":
    main()
