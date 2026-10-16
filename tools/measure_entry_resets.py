"""Count practice-log "reset" rows nobody made, per level and target kind.

His ruling, 2026-09-28, after Bowser 1, 2 and 3 and then BitDW's "8 Red
Coins (Pipe)" each logged "reset 00"06 in" on arrival: "These resets are
worthless. We need to fix this everywhere." Usamune zeroes its counter as
Mario arrives -- a menu warp or a pipe 1-6 frames after the level edge, a
painting 42-59 -- and that zero is an anchor, so a row it closes is a reset
nobody pressed.

A logged `reset` row is counted, read straight from the journal rather than
through the rules under test, when its closing anchor is one of:
- ENTRY: the first anchor after a level entry, within
  `counter_epoch.LEVEL_LOAD_TAIL_FRAMES` of it, with no `mario_acted`
  journaled in between -- the arrival's own zero. Level entries only: an L
  reset inside a course walks the area byte 1 -> 2 -> 1 before its zero (the
  reset's own reload, anchors.py), so an area edge before a zero is what a
  REAL reset looks like;
- SAME FRAME: on the frame its attempt started -- no game frame ran;
- UNPLAYED: within `EARLY_FRAMES` of an attempt that started at an anchor
  (where the detector's activity period starts, so the first act after it is
  journaled) with no `mario_acted` after its first frame. Sessions recorded
  since `mario_acted` carried its action (2026-08-04) only: before that the
  anchor's own action byte was re-read as an act and the one report per
  period was spent on it, so later play left no event (task 0084).
A row that is none of those is a reset he could have made.

Replays each journal twice -- the projection code at `--before` (from git,
the artifact that shipped) and the working tree -- and prints both counts per
(level, star|segment), then every row still counted in the working tree.
Same runner shape as `measure_target_queue.py`.

Usage:
    uv run python tools/measure_entry_resets.py [--before REV] db [db ...]
"""
import bisect
import dataclasses
import json
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "tools"))
from measure_target_queue import _old_src, _online_backup  # noqa: E402

#: How soon after its start an unplayed reset row is counted: three seconds,
#: past every measured arrival zero (the latest lands at 59 frames).
EARLY_FRAMES = 90
_ANCHORS = ("practice_reset", "state_loaded")


def _classify_anchors(events, tail):
    """{(session, frame): (level, why)} for every anchor that is an ENTRY
    zero, plus each session's sorted `mario_acted` frames and the level each
    anchor landed in."""
    entry_zero, level_at, acts = {}, {}, defaultdict(list)
    entry, level = None, None
    for ev in events:
        if ev.type == "session_started":
            entry, level = None, None
        elif ev.type == "level_changed":
            level = ev.payload.get("to")
            entry = [ev.session_id, ev.frame, False]
        elif ev.type == "mario_acted":
            if "action" in ev.payload:
                acts[ev.session_id].append(ev.frame)
            if entry is not None:
                entry[2] = True
        elif ev.type in _ANCHORS:
            level_at[(ev.session_id, ev.frame)] = level
            if (entry is not None and ev.session_id == entry[0] and not entry[2]
                    and 0 <= ev.frame - entry[1] <= tail):
                entry_zero[(ev.session_id, ev.frame)] = "entry"
            entry = None
    for frames in acts.values():
        frames.sort()
    return entry_zero, level_at, acts


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
    entry_zero, level_at, acts = _classify_anchors(events, LEVEL_LOAD_TAIL_FRAMES)

    counted = []
    for a in attempts:
        close = (a.session_id, a.closed_frame)
        if a.outcome != "reset" or a.cleared or close not in level_at:
            continue
        why = entry_zero.get(close)
        if why is None and a.anchor_frame == a.closed_frame:
            why = "same frame"
        if (why is None and a.anchor_type in _ANCHORS
                and 0 <= a.closed_frame - a.anchor_frame <= EARLY_FRAMES):
            frames = acts.get(a.session_id)
            i = bisect.bisect_right(frames or [], a.anchor_frame + 1)
            if frames and (i == len(frames) or frames[i] > a.closed_frame):
                why = "unplayed"
        if why is not None:
            counted.append({"id": a.id, "level": level_at[close], "why": why,
                            "kind": "segment" if a.segment_id is not None else "star",
                            "rta": a.rta_frames, "anchor": a.anchor_type})
    json.dump(counted, sys.stdout)


def _side(src_dir: Path, db: Path) -> list:
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

    def tally(rows):
        return Counter((r["level"], r["kind"]) for r in rows)
    before, after = tally(old), tally(new)
    print(f"  resets nobody made: {len(old)} before, {len(new)} after")
    for level, kind in sorted(set(before) | set(after), key=lambda k: (k[0] or 0, k[1])):
        name = LEVEL_NAMES.get(level, f"level {level}")
        print(f"     {name:30} {kind:8} {before.get((level, kind), 0):4} -> "
              f"{after.get((level, kind), 0)}")
    for row in new:
        print(f"     STILL  {row}")


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
