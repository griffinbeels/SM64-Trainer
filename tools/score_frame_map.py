"""Score a clip's frame_map against Usamune's own pixels -- offline.

The clip IS the ground truth: with Usamune's input display on, every video
frame carries the game's own picture of the pad. This tool aligns two event
sequences on the clip's 60 fps slot axis -- "the button-icon strip's pixels
changed" vs "the frame_map says the buttons changed" -- and reports the
per-event residual. If the map is right, the histogram sits at 0.

    uv run python tools/score_frame_map.py --attempt 741

Reads the clip + sidecar from data/replay_buffer/clips/ (or --clip/--sidecar
for a saved file) and the track straight from data/tracker.db. No server, no
emulator.

WHY BUTTON ICONS, not the stick digits (measured on clip 741, 2026-08-23):
his stick moves nearly every frame, so digit changes saturate the axis (892
changes over 979 slots) and any alignment matches trivially. Button icons
appear and vanish ~40 times a clip -- sharp, sparse, unambiguous. The icon
strip's location is measured for a 1600x1224 capture and scaled by width for
other sizes; --region overrides it when the scaling guess is wrong (LOOK at
a probe frame before trusting a verdict on a new capture size).

THE VERDICT this tool produced on 2026-08-23 (clip 741, map v1 -- the
poller-edge stamp + constant lag): residuals spread over -3..+3 slots in
BOTH directions within one clip, no drift. That measurement is what forced
map v2 (capture-time tagging through the feeder, replay/frameclock.py) --
a constant cannot fix a two-sided error.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sm64_events.core.paths import bundled_ffmpeg  # noqa: E402
from sm64_events.inputs.runs import axis_of, capture_axis, stretches  # noqa: E402
from sm64_events.inputs.track import track_for_attempt  # noqa: E402
from sm64_events.storage.db import Database  # noqa: E402

# The button-icon strip, measured on a 1600x1224 clip (probe frames,
# 2026-08-23). Scaled linearly by the clip's own width.
ICON_REGION_AT_1600 = (380, 990, 330, 130)          # x, y, w, h
STEP_THRESHOLD = 150      # icon-ink pixels appearing/vanishing in one slot
SEARCH_SLOTS = 8


def decode_region(ffmpeg: str, clip: Path, region) -> np.ndarray:
    x, y, w, h = region
    out = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(clip),
         "-vf", f"crop={w}:{h}:{x}:{y}", "-f", "rawvideo",
         "-pix_fmt", "rgb24", "pipe:1"], capture_output=True)
    frame_bytes = w * h * 3
    count = len(out.stdout) // frame_bytes
    frames = np.frombuffer(out.stdout[:count * frame_bytes], dtype=np.uint8)
    return frames.reshape(count, h, w, 3).astype(np.int16)


def icon_change_slots(frames: np.ndarray) -> np.ndarray:
    """Slots where the icon strip's ink changed hard. Ink = strongly blue
    (the A icon) or bright grey/white (Z, outlines) -- colours the scenery
    under the strip rarely wears."""
    red, green, blue = frames[..., 0], frames[..., 1], frames[..., 2]
    ink = ((blue > 130) & (blue - red > 50)) | \
          ((red > 170) & (green > 170) & (blue > 170))
    signal = np.count_nonzero(ink, axis=(1, 2)).astype(int)
    return np.flatnonzero(np.abs(np.diff(signal)) > STEP_THRESHOLD) + 1


def map_button_changes(sidecar: dict, db: Database, attempt_id: int) -> list[int]:
    frame_map = sidecar.get("frame_map")
    if not frame_map:
        raise SystemExit("this clip's sidecar carries no frame_map -- it was "
                         "cut before the frame clock existed (or coverage "
                         "was missing); there is nothing to score")
    attempt = next((a for a in db.attempts() if a.id == attempt_id), None)
    if attempt is None:
        raise SystemExit(f"no attempt {attempt_id} in this db")
    track = track_for_attempt(db.inputs, attempt)
    seams = stretches(track)
    buttons_by_axis = {axis: frame.buttons
                       for axis, frame in capture_axis(track)}

    def at(k):
        raw = frame_map[k]
        if raw is None:
            return None
        axis = axis_of(raw, seams)
        return buttons_by_axis.get(axis) if axis is not None else None

    pads = [at(k) for k in range(len(frame_map))]
    return [k for k in range(1, len(pads))
            if pads[k] is not None and pads[k - 1] is not None
            and pads[k] != pads[k - 1]]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--clip", default=None, help="path to the .mp4 "
                        "(default: the scratch clip for --attempt)")
    parser.add_argument("--db", default="data/tracker.db")
    parser.add_argument("--region", default=None,
                        help="x,y,w,h of the button-icon strip, overriding "
                             "the width-scaled default")
    args = parser.parse_args()

    clip = Path(args.clip) if args.clip else Path(
        f"data/replay_buffer/clips/clip_attempt_{args.attempt}.mp4")
    sidecar_path = clip.with_suffix(".json")
    if not clip.exists() or not sidecar_path.exists():
        raise SystemExit(f"no clip+sidecar at {clip}")
    sidecar = json.loads(sidecar_path.read_text())

    ffmpeg = str(bundled_ffmpeg() or "ffmpeg")
    if args.region:
        region = tuple(int(v) for v in args.region.split(","))
    else:
        probe = subprocess.run(
            [ffmpeg.replace("ffmpeg", "ffprobe") if "ffmpeg" in ffmpeg
             else "ffprobe", "-v", "error", "-select_streams", "v",
             "-show_entries", "stream=width", "-of", "csv=p=0", str(clip)],
            capture_output=True, text=True)
        width = int(probe.stdout.strip() or 1600)
        scale = width / 1600
        region = tuple(round(v * scale) for v in ICON_REGION_AT_1600)

    frames = decode_region(ffmpeg, clip, region)
    display = icon_change_slots(frames)
    changes = map_button_changes(sidecar, Database(Path(args.db)),
                                 args.attempt)
    # Which series built this map: "presents" = v4 (the hunted host present
    # counter), "feeds" = v2 (capture tags through the feeder), "edges" = v1.
    # A verdict means nothing without this line -- the same clip scores a
    # different map depending on what the clock held at extraction.
    source = sidecar.get("frame_map_source", "unknown (pre-source sidecar)")
    print(f"map source: {source}")
    print(f"{len(frames)} slots; {len(display)} display icon-changes; "
          f"{len(changes)} map button-changes")
    if not changes or not len(display):
        print("not enough events to score -- play a busier attempt")
        return 1

    histogram: dict = {}
    rows = []
    for slot in changes:
        deltas = display - slot
        near = deltas[np.abs(deltas) <= SEARCH_SLOTS]
        residual = int(near[np.argmin(np.abs(near))]) if len(near) else None
        rows.append((slot, residual))
        histogram[residual] = histogram.get(residual, 0) + 1
    print("residual histogram (video slots; 2 slots = 1 game frame):")
    for key in sorted(histogram, key=lambda v: (v is None, v)):
        print(f"  {key!s:>5}: {histogram[key]}")
    exact = histogram.get(0, 0)
    print(f"\n{exact}/{len(changes)} map button-changes land exactly on a "
          "display change. The rest are the map's error, signed.")
    for slot, residual in rows:
        if residual not in (0, None):
            print(f"  slot {slot} ({slot / 60:.2f}s): off by {residual:+d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
