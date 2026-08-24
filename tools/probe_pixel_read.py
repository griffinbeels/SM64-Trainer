"""The pixel reader's own gate: how well does map v3 read a clip today?

    uv run python tools/probe_pixel_read.py --attempt 778

Round 32 item 26. The v3 frame map (replay/pixelmap.py) reads Usamune's
input display out of the footage and fits the map to it -- but it only
EARNS being wired into extraction when the reading itself is reliable.
This probe prints the three numbers that decide, on any clip with a
sidecar map:

  1. TRAINING ACCURACY -- reads on the slots the templates were learned
     from. The reader's ceiling. WIRE-IN GATE: >= 0.99.
  2. AGREEMENT -- how many readable slots the prior map (and the fitted
     map) already agree with. The distance from 100%.
  3. NOWHERE READS -- reads naming a pad value the track never contained.
     Measured 0 on clip 778 (2026-08-23), which is the evidence that the
     display draws the same per-frame pad the sampler stores, and that
     100% is REACHABLE once reading is solid. If this ever goes non-zero,
     the display is drawing mid-frame state and no map can reach 100% --
     re-open the design before touching thresholds.

State when this was written (clip 778): training accuracy 485/578 (84%),
v2 agreement 497/807 field-readable slots, fitted 545/807. The reader --
not the map, not the band, not the DP -- is the bottleneck; improving it
(finer fingerprints, per-glyph cells, better ink masks) is the whole
remaining distance, and this probe is the loop: change the reader, run,
watch the three numbers.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sm64_events.inputs.service import InputsService  # noqa: E402
from sm64_events.replay import pixelmap as P  # noqa: E402
from sm64_events.storage.db import Database  # noqa: E402


def hold_labels(prior, pads, count, axis_index, letters):
    labels = [None] * count
    begin, current = 0, None
    for at in range(count + 1):
        raw = prior[at] if at < count else None
        pad = pads.get(raw) if raw is not None else None
        value = pad[axis_index] if pad else None
        if value != current or at == count:
            if current is not None and at - begin >= P.LEARN_HOLD_SLOTS:
                for slot in range(begin + 1, at - 1):
                    labels[slot] = P._value_string(current, letters)
            begin, current = at, value
    return labels


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--db", default="data/tracker.db")
    parser.add_argument("--clip", default=None)
    args = parser.parse_args()
    clip = Path(args.clip) if args.clip else Path(
        f"data/replay_buffer/clips/clip_attempt_{args.attempt}.mp4")
    sidecar = clip.with_suffix(".json")
    if not clip.exists() or not sidecar.exists():
        raise SystemExit(f"no clip+sidecar at {clip}")
    meta = json.loads(sidecar.read_text())
    if not meta.get("frame_map"):
        raise SystemExit("no frame_map in the sidecar -- cut the clip on a "
                         "server with the frame clock first")
    db = Database(Path(args.db))
    inputs = InputsService(db.inputs, db.input_templates, db.attempts)
    pads = inputs.pad_lookup(meta["start_utc"], meta["duration_s"])
    dims = P.probe_dims("ffmpeg", clip)
    frames_y = P.decode_region("ffmpeg", clip, P.FIELD_Y_REGION, dims)
    frames_x = P.decode_region("ffmpeg", clip, P.FIELD_X_REGION, dims)
    prior = meta["frame_map"]
    count = min(len(prior), len(frames_y), len(frames_x))
    prints = {"Y": P.field_fingerprints(frames_y[:count]),
              "X": P.field_fingerprints(frames_x[:count])}
    axes = {"Y": (2, "UD"), "X": (1, "RL")}
    reads = {}
    for name, (axis_index, letters) in axes.items():
        labels = hold_labels(prior, pads, count, axis_index, letters)
        templates = P._learn_templates(prints[name], labels)
        read = P._read(prints[name], templates)
        reads[name] = read
        train_total = train_right = 0
        for at in range(count):
            if labels[at] is not None and read[at] is not None:
                train_total += 1
                train_right += labels[at] == read[at]
        share = train_right / train_total if train_total else 0.0
        print(f"{name}: {len(templates)} templates; training accuracy "
              f"{train_right}/{train_total} ({share:.0%})"
              f"{'  << wire-in gate is 99%' if share < 0.99 else '  GATE MET'}")
        track_values = {P._value_string(p[axis_index], letters)
                        for p in pads.values()}
        nowhere = sum(1 for at in range(count)
                      if read[at] is not None
                      and read[at] not in track_values)
        print(f"   reads naming a value the track never held: {nowhere}"
              f"{'  (0 = 100% stays reachable)' if nowhere == 0 else '  << DESIGN ALARM: display shows mid-frame state'}")
    total = right = 0
    for at in range(count):
        raw = prior[at]
        pad = pads.get(raw) if raw is not None else None
        if pad is None:
            continue
        checks = []
        for name, (axis_index, letters) in axes.items():
            if reads[name][at] is not None:
                checks.append(P._value_string(pad[axis_index], letters)
                              == reads[name][at])
        if checks:
            total += 1
            right += all(checks)
    print(f"prior map agrees with the pixels on {right}/{total} "
          "readable slots")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
