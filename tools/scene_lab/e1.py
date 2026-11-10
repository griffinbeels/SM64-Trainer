"""E1: replay a scene-tap session from its closures and compare with its source pictures.

    uv run python tools/scene_lab/e1.py <session> <work> [--order direct|reverse|shuffle]
        [--pattern 0xA5] [--pair origin|next] [--limit N] [--every N]

Exports every task paired with a presented picture (tap.py frames: RDRAM holds
only the captured closure, every other byte --pattern), renders them through
the real renderer in one instance in the chosen order, warmed with the first
exported frame (the live renderer was never cold), and compares each render
with the ReadScreen picture the source presented. Any differing pixel fails;
mismatches get a difference picture. Work folders hold private RAM images.
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "tools/scene_lab"
sys.path.insert(0, str(LAB))
from gpu_gate import require_gpu  # noqa: E402


def run(*command: str) -> None:
    result = subprocess.run([sys.executable, *command], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(f"{Path(command[0]).name} failed: {result.stdout[-2000:]}{result.stderr[-2000:]}")


def main() -> int:
    require_gpu()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("session", type=Path)
    parser.add_argument("work", type=Path)
    parser.add_argument("--order", choices=("direct", "reverse", "shuffle"), default="direct")
    parser.add_argument("--pattern", default="0xA5")
    parser.add_argument("--pair", choices=("origin", "next"), default="next")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--every", type=int, default=0, help="export every N-th referenced frame")
    parser.add_argument("--seed", type=int, default=64)
    args = parser.parse_args()
    frames_dir = args.work / "frames"
    if not frames_dir.is_dir():
        run(str(LAB / "tap.py"), "frames", str(args.session), str(frames_dir), "--pattern", args.pattern,
            "--pair", args.pair, *(["--limit", str(args.limit)] if args.limit else []),
            *(["--every", str(args.every)] if args.every else []))
    frames = sorted(path for path in frames_dir.iterdir() if (path / "ref.bgr").is_file())
    if not frames:
        raise SystemExit("no frames with reference pictures")
    order = list(frames)
    if args.order == "reverse":
        order.reverse()
    elif args.order == "shuffle":
        random.Random(args.seed).shuffle(order)
    out = args.work / f"render-{args.order}"
    out.mkdir(parents=True, exist_ok=True)
    listing = out / "frames.txt"
    listing.write_text("".join(f"{frame}\n" for frame in order), encoding="utf-8")
    run(str(LAB / "replay.py"), "render", "--out", str(out), "--warmup", str(frames[0]), f"@{listing}")
    results = []
    for index, frame in enumerate(order):
        meta = json.loads((frame / "frame.json").read_text(encoding="utf-8"))
        width, height = meta["reference"]["width"], meta["reference"]["height"]
        source = np.frombuffer((frame / "ref.bgr").read_bytes(), np.uint8)
        replay = np.frombuffer((out / f"{index:04d}.raw").read_bytes(), np.uint8)
        entry = {"frame": frame.name, "identity": meta.get("identity"), "refusals": meta["tap"]["refusals"]}
        if source.size != replay.size or source.size != width * height * 3:
            entry["size_mismatch"] = [int(source.size), int(replay.size)]
        else:
            changed = np.any(source.reshape(height, width, 3) != replay.reshape(height, width, 3), axis=2)
            entry["differing_pixels"] = int(changed.sum())
            if changed.any():
                picture = replay.reshape(height, width, 3)[::-1, :, ::-1]
                mask = changed[::-1]
                Image.fromarray(np.where(mask[..., None], [255, 0, 255], picture // 3).astype(np.uint8)).save(
                    out / f"{index:04d}-diff.png")
        results.append(entry)
    failed = [entry for entry in results if entry.get("differing_pixels", 1)]
    summary = {"order": args.order, "frames": len(results), "identical": len(results) - len(failed),
               "failed": failed[:20]}
    (out / "e1.json").write_text(json.dumps({"summary": summary, "frames": results}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
