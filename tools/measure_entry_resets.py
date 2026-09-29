"""Count practice-log "reset" rows that are really a level ENTRY, per level.

His ruling, 2026-09-28, after Bowser 1, 2 and 3 each logged "reset 00"06 in"
on arrival: "Need to make sure that this doesn't happen for ANY stage or any
course / star." Usamune zeroes its counter as Mario arrives somewhere -- a
menu warp 1-6 frames after the level edge, a course entry 42-59 -- and that
zero is an anchor. A row it closes is a reset nobody made.

A row is counted when, read straight from the journal rather than through the
rule under test:
- it is a `reset`,
- the anchor that closed it is the FIRST anchor after the latest level entry,
- it lands within `counter_epoch.LEVEL_LOAD_TAIL_FRAMES` of that entry, and
- no `mario_acted` reached the journal between the entry and it.

Replays each journal twice -- the projection code at `--before` (from git,
the artifact that shipped) and the working tree -- and prints the count per
entered level for both. Same runner shape as `measure_target_queue.py`.

Usage:
    uv run python tools/measure_entry_resets.py [--before REV] db [db ...]
"""
import dataclasses
import json
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "tools"))
from measure_target_queue import _old_src, _online_backup  # noqa: E402


def _run_side(src_dir: str, db_path: str) -> None:
    sys.path.insert(0, src_dir)
    from sm64_events.detectors.counter_epoch import LEVEL_LOAD_TAIL_FRAMES
    from sm64_events.storage.db import Database
    from sm64_events.tracking import projection
    from sm64_events.tracking.segments import SegmentDef

    db = Database(Path(db_path))
    keys = [f.name for f in dataclasses.fields(SegmentDef)]
    defs = [SegmentDef(**{k: row[k] for k in keys}) for row in db.segment_defs()]
    events = db.events()
    attempts, _ = projection.replay(events, segments=defs)

    # For every anchor: the level entry it follows, if it is that entry's
    # first anchor, in its window, with no act journaled in between.
    entry_zero: dict[tuple, int] = {}
    entry = None   # (session, frame, level, acted)
    for ev in events:
        if ev.type == "session_started":
            entry = None
        elif ev.type == "level_changed":
            entry = [ev.session_id, ev.frame, ev.payload.get("to"), False]
        elif ev.type == "mario_acted" and entry is not None:
            entry[3] = True
        elif ev.type in ("practice_reset", "state_loaded") and entry is not None:
            if (ev.session_id == entry[0] and not entry[3]
                    and 0 <= ev.frame - entry[1] <= LEVEL_LOAD_TAIL_FRAMES):
                entry_zero[(ev.session_id, ev.frame)] = entry[2]
            entry = None
    per_level = Counter(
        entry_zero[(a.session_id, a.closed_frame)] for a in attempts
        if a.outcome == "reset" and (a.session_id, a.closed_frame) in entry_zero)
    json.dump({str(k): v for k, v in per_level.items()}, sys.stdout)


def _side(src_dir: Path, db: Path) -> dict:
    out = subprocess.run([sys.executable, __file__, "--runner", str(src_dir), str(db)],
                         capture_output=True, cwd=_ROOT)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.decode("utf-8", "replace")[-2000:])
    return json.loads(out.stdout.decode("utf-8"))


def measure(live_path: Path, old_src: Path) -> None:
    sys.path.insert(0, str(_ROOT / "src"))
    from sm64_events.memory.addresses import LEVEL_NAMES
    print(f"\n=== {live_path} ===")
    with tempfile.TemporaryDirectory(prefix="entry_resets_",
                                     ignore_cleanup_errors=True) as scratch:
        snapshot = Path(scratch) / "snapshot.db"
        _online_backup(live_path, snapshot)
        old, new = _side(old_src, snapshot), _side(_ROOT / "src", snapshot)
    levels = sorted(set(old) | set(new), key=int)
    print(f"  entry resets: {sum(old.values())} before, {sum(new.values())} after")
    for level in levels:
        name = LEVEL_NAMES.get(int(level), f"level {level}")
        print(f"     {name:32} {old.get(level, 0):4} -> {new.get(level, 0)}")


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "--runner":
        _run_side(sys.argv[2], sys.argv[3])
        return
    args = sys.argv[1:]
    before = "HEAD"
    if args and args[0] == "--before":
        before, args = args[1], args[2:]
    if not args:
        sys.exit(__doc__)
    with tempfile.TemporaryDirectory(prefix="entry_resets_src_",
                                     ignore_cleanup_errors=True) as scratch:
        old_src = _old_src(before, Path(scratch))
        for journal in args:
            measure(Path(journal), old_src)


if __name__ == "__main__":
    main()
