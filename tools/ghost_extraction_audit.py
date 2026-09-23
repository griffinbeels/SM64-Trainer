"""Is Mario's position extracted on EVERY frame? Audit what was recorded.

His rule, 2026-09-22: "Do we make sure that for every frame we guarantee that
the position can be extracted? ... future features hinge on correct
extraction." This reads, never writes:

* every committed ghost clip under the replay save root (the manifest's
  `invalid_records` + `missing_occurrences`, recomputed from the bytes), and
* an online-backup snapshot of each live pose archive, grouping every record
  without a pose by what the game was doing, so a hole can be told apart from
  a transition the ghost never plays (a star exit, a level load).

    uv run python tools/ghost_extraction_audit.py [--root <checkout>]

Exit code 1 when any committed clip has a hole.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sm64_events.ghost import clip as C  # noqa: E402


def audit_clips(save_root: Path) -> int:
    holes = 0
    for path in sorted(save_root.rglob("*.ghost")):
        try:
            frames = C.decode_clip(path.read_bytes()).frames
        except C.OlderPoseFormat:
            print(f"  older format   {path.name}")
            continue
        except (C.ClipError, OSError) as error:
            print(f"  unreadable     {path.name}: {error}")
            continue
        refused = [index for index, frame in enumerate(frames) if not frame.playable]
        missing = frames[-1].sequence - frames[0].sequence + 1 - len(frames)
        holes += bool(refused or missing)
        state = "OK" if not (refused or missing) else f"HOLES refused={len(refused)} missing={missing}"
        reasons = Counter(frames[index].refusal_name or "unrecorded" for index in refused)
        print(f"  {state:<14} {path.name}: {len(frames)} frames"
              + (f", first refused {refused[:8]} ({', '.join(f'{name} {count}' for name, count in reasons.most_common())})"
                 if refused else ""))
    return holes


def audit_archive(path: Path) -> None:
    snapshot = Path(tempfile.gettempdir()) / f"audit-{path.name}"
    source = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    target = sqlite3.connect(snapshot)
    try:
        source.backup(target)
        rows = [row[0] for row in target.execute("SELECT data FROM poses ORDER BY id")]
    finally:
        source.close()
        target.close()
        snapshot.unlink(missing_ok=True)
    kinds, total, carried = Counter(), 0, 0
    for data in rows:
        try:
            frame = C.frame_from_bytes(data)
        except C.ClipError:
            kinds["older or unreadable record"] += 1
            continue
        total += 1
        if frame.carried:
            carried += 1
        if not frame.playable:
            kinds[f"level {frame.level} area {frame.area} action {frame.live_action:#x} "
                  f"refused {frame.refusal_name or 'unrecorded'} (status {frame.status}) "
                  f"world {frame.world_status} fields {frame.valid_fields}"] += 1
    print(f"  {path.name}: {total} records, {sum(kinds.values())} without a playable pose, "
          f"{carried} with Mario culled (root carried or composed)")
    for kind, count in kinds.most_common(12):
        print(f"    {count:>6}  {kind}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT, help="checkout whose replays/ and data/ to read")
    args = parser.parse_args()
    print("Committed ghost clips:")
    holes = audit_clips(args.root / "replays")
    print("Live pose archives (records outside attempts are expected to include transitions):")
    for archive in sorted((args.root / "data" / "replay_buffer").glob("ghost-poses-*.sqlite3")):
        audit_archive(archive)
    return 1 if holes else 0


if __name__ == "__main__":
    raise SystemExit(main())
