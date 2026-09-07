"""THE ORACLE: score a clip's frame map against the frame number the game
printed into every picture (round 32, item 94). Offline, read-only.

    uv run python tools/score_oracle.py --attempt N [--disagreements]
    uv run python tools/score_oracle.py --clip replays/oracle/clip_attempt_6650.mp4
    uv run python tools/score_oracle.py --learn src/sm64_events/data/oracle_glyphs_us.npz --clip a.mp4 --clip b.mp4

Needs a clip recorded with Usamune's HUD memory display showing
``0x8032D5D4`` (gGlobalTimer) -- top-left, under the lives. Prints the
reader's own bookkeeping first (read / vouched / bridged / verified /
rejected: every value it reports is one a neighbour vouched for or a
prediction its boxes confirmed, never a lone read), then the shipped
``frame_map``'s verdict: ``exact`` and the ``off_by`` histogram (map minus
oracle; negative = the map names an EARLIER frame than the picture shows).
``--learn`` pools every clip's vouched slots into one alphabet and writes it
-- the reference the reader ships with was learned this way off his three
oracle clips (SSL: sky, sand, brick, a dark corridor and two white fades).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from sm64_events.replay import oracleread as O          # noqa: E402


def find_clip(attempt: int) -> Path | None:
    from sm64_events.core.paths import replay_scratch_dir, replays_root
    name = f"clip_attempt_{attempt}.mp4"
    for root in (replay_scratch_dir() / "clips", replays_root(), REPO / "replays"):
        for hit in sorted(root.rglob(name)) if root.exists() else []:
            return hit
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--attempt", type=int, action="append", default=[])
    ap.add_argument("--clip", type=Path, action="append", default=[])
    ap.add_argument("--disagreements", action="store_true",
                    help="list every slot where the map and the oracle differ")
    ap.add_argument("--learn", type=Path,
                    help="pool the given clips' vouched slots into one alphabet and save it")
    ap.add_argument("--reference", type=Path,
                    help="read with this alphabet instead of the shipped one")
    args = ap.parse_args()
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        print("ffmpeg not on PATH")
        return 2
    clips = list(args.clip)
    for attempt in args.attempt:
        hit = find_clip(attempt)
        if hit is None:
            print(f"attempt {attempt}: no clip_attempt_{attempt}.mp4 in the cache or replays/")
            return 2
        clips.append(hit)
    if not clips:
        ap.error("give --attempt N or --clip PATH")
    reference = O.load_reference(args.reference)
    readings = []
    for clip in clips:
        reading = O.read_clip(clip, ffmpeg, reference=reference)
        readings.append((clip, reading))
        print(f"{clip.name}: {reading.as_dict()}")
        if reading.learned:
            print(f"  second pass learned {''.join(reading.learned)} at this clip's scale")
        sidecar = clip.with_suffix(".json")
        if not sidecar.exists():
            print("  no sidecar beside the clip: nothing to score")
            continue
        meta = json.loads(sidecar.read_text())
        frame_map = meta.get("frame_map")
        if not frame_map:
            print("  sidecar carries no frame_map")
            continue
        verdict = O.score_map(reading, frame_map)
        source = meta.get("frame_map_source")
        print(f"  frame_map ({source}): exact {verdict['exact']} of {verdict['slots'] - verdict['unreadable']} "
              f"oracle-known slots, unreadable {verdict['unreadable']}, off_by {verdict['off_by']}")
        if args.disagreements:
            for slot, oracle, mapped in O.disagreements(reading, frame_map):
                print(f"    slot {slot}: screen {oracle}  map {mapped}  ({mapped - oracle:+d})")
    if args.learn:
        alphabet = O.learn_reference([(clip, reading) for clip, reading in readings], ffmpeg)
        O.save_reference(alphabet, args.learn)
        print(f"wrote {args.learn}: {''.join(sorted(alphabet))} "
              f"({sum(t.exemplars for t in alphabet.values())} exemplars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
