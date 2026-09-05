"""RE-RUN the stamp join on a clip already cut, and rewrite its sidecar.

A cut clip keeps its baked-in frame map forever, so a change to the join
never reaches the clip that showed the problem. This re-derives the map from
the sidecar's own `picture_ledger` -- the rows the capture layer stamped --
so the open replay shows the corrected panel.

The join is one line: a picture shows the frame of the stamp BEFORE its own
(`PLUGIN_PICTURE_LAG`, measured; see
`.claude/rules/chain-input-timeline-frame.md`). `--lag N` re-runs it under a
different constant without editing the code, which is what round 33's still-
open item needs: clip 7141 peaks one further back than the three clips the
oracle certified, and the way to test a candidate constant is to apply it to
a real clip and score it.

    uv run python tools/remap_clip.py --attempt N [--lag 1] [--dry-run]

Read-only with `--dry-run`. Offline: no emulator, no ffmpeg, no server.
Score the result with `tools/score_oracle.py --attempt N`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sm64_events.core.paths import replay_scratch_dir           # noqa: E402
from sm64_events.replay.service import PLUGIN_PICTURE_LAG       # noqa: E402


def sidecar_for(attempt_id: int) -> Path:
    clip = replay_scratch_dir() / "clips" / f"clip_attempt_{attempt_id}.mp4"
    side = clip.with_suffix(".json")
    if not side.exists():
        raise SystemExit(f"no cached clip for attempt {attempt_id}: {side}")
    return side


def remap(meta: dict, lag: int) -> list:
    """The map the stamps name under `lag`: one entry per video slot, taken
    from the row the CURRENT map already matched to that slot, so the
    picture-to-row join is preserved and only the constant moves."""
    rows = meta.get("picture_ledger") or []
    if not rows:
        raise SystemExit("this clip carries no picture ledger; nothing to remap")
    by_frame = {row["frame"]: row for row in rows if row.get("frame") is not None}
    old_lag = PLUGIN_PICTURE_LAG
    out = []
    for shown in meta.get("frame_map") or []:
        if shown is None:
            out.append(None)
            continue
        row = by_frame.get(shown + old_lag)
        out.append(None if row is None or not row.get("exact")
                   else row["frame"] - lag)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--attempt", type=int, required=True)
    ap.add_argument("--lag", type=int, default=PLUGIN_PICTURE_LAG,
                    help=f"pictures behind the stamp (default {PLUGIN_PICTURE_LAG})")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    side = sidecar_for(args.attempt)
    meta = json.loads(side.read_text())
    if meta.get("frame_map_source") != "plugin":
        raise SystemExit("this clip's map did not come from the capture layer; "
                         "there is nothing to re-join")
    before = meta.get("frame_map") or []
    after = remap(meta, args.lag)
    moved = sum(1 for a, b in zip(before, after, strict=True) if a != b)
    print(f"attempt {args.attempt}: {len(after)} slots, lag "
          f"{PLUGIN_PICTURE_LAG} -> {args.lag}, {moved} slots move")
    if args.dry_run:
        print("(dry run: nothing written)")
        return 0
    meta["frame_map"] = after
    side.write_text(json.dumps(meta))
    print(f"wrote {side}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
