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

TWO INSTRUMENTS, and the VERDICT is the first one (`glyph_offset`): fit
Usamune's own STICK DIGITS. Its ink per slot is a rich per-frame value, so
predicting it from the track at each candidate offset and keeping the best
fit gives one signed number -- how many slots the map is out, 0 being the
goal. The button-icon histogram below it survives as per-event detail.

That is a REVERSAL, and the reason is worth keeping. Until 2026-08-26 this
docstring argued the opposite: stick digits saturate (892 changes over 979
slots) so digit CHANGES cannot align anything, while button icons are
sparse and sharp. The first half is still true and the conclusion was
still wrong -- saturation only rules out change-EVENTS, and the icons'
region mask cannot tell icon ink from moving scenery (700 "icon changes"
in a 28 s clip against the ~40 a clip holds), so the sparse instrument was
the noisy one. It manufactured two-sided wobble; every verdict it gave
before 2026-08-26 is suspect for that reason, including the "+-1 both
ways" reading of clip 741 that argued a constant could not fix v1.

Both regions are measured for a 1600x1224 capture and scaled by width;
--region overrides the icon strip when the scaling guess is wrong (LOOK at
a probe frame before trusting a verdict on a new capture size).
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
    """Slots where the icon strip's ink changed hard.

    Ink = the A icon's HUE-NEUTRAL saturated blue (red ~= green), the Z
    ball's neutral mid-grey, or bright white (outlines). The hue-neutral
    clauses are what make this level-proof: WF's water is teal (green-red
    ~= 31, measured on clip 1170's frames, 2026-08-25) and passed the
    original bare blue rule on 86% of the region's pixels, so camera
    motion over water buried the icons -- 195 "icon changes" in a 15 s
    clip against the ~40 a clip really holds. The icon blue measured
    (90, 89, 194): red and green within a few counts of each other."""
    red, green, blue = frames[..., 0], frames[..., 1], frames[..., 2]
    spread = frames.max(axis=-1) - frames.min(axis=-1)
    darkest = frames.min(axis=-1)
    ink = ((blue > 130) & (blue - red > 50) & (np.abs(red - green) < 25)) | \
          ((spread < 15) & (darkest > 85)) | \
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


# Usamune's STICK READOUT, measured on a 1600x1224 clip (2026-08-26) and
# scaled by width like the icon strip: two lines, "U84" over "R70", drawn in
# the same fiery HUD font. THE primary instrument -- see glyph_offset.
DIGIT_REGION_AT_1600 = (60, 900, 420, 260)
GLYPHS = list("0123456789UDLR")


def glyph_composition(frame) -> np.ndarray:
    """Which glyphs Usamune draws for this frame's stick, as counts.

    An axis at rest draws a bare "0"; otherwise a direction letter and the
    magnitude's digits -- so "U84" over "R70" is six glyphs and "U84" over
    "0" is four.
    """
    row = np.zeros(len(GLYPHS))
    for value, positive, negative in ((frame.stick_y, "U", "D"),
                                      (frame.stick_x, "R", "L")):
        value = int(value)
        text = ("0" if value == 0
                else (positive if value > 0 else negative) + str(abs(value)))
        for character in text:
            row[GLYPHS.index(character)] += 1
    return row


def glyph_offset(ffmpeg: str, clip: Path, region, frame_map, db: Database,
                 attempt_id: int, span: int = 8):
    """How many video slots the map is out, by fitting Usamune's own stick
    digits -- the sharpest instrument this project has.

    The stick moves nearly every frame, which makes digit CHANGES useless
    for alignment (they saturate) and makes digit INK ideal for it: each
    slot's lit-pixel count is a rich, per-frame value. Predict that count
    from the track (a per-glyph ink weight, least-squares) at every
    candidate offset and keep the best fit -- a wrong offset predicts the
    wrong digits and fits worse. Returns (offset_in_slots, r2_by_offset).

    Negative = the map runs AHEAD of the footage (it names a frame the
    screen has not reached); positive = behind. Zero is the goal.

    Why this replaced the button-icon histogram as the verdict: the icon
    method's region mask counted moving scenery as icon ink (372 "icon
    changes" in a 15 s clip against the ~40 a clip holds), which
    manufactured two-sided wobble and made a clean constant look like
    noise -- v2's historical "+-1 both ways" verdict is suspect for that
    reason.
    """
    attempt = next((a for a in db.attempts() if a.id == attempt_id), None)
    if attempt is None:
        raise SystemExit(f"no attempt {attempt_id} in this db")
    track = track_for_attempt(db.inputs, attempt)
    seams = stretches(track)
    by_axis = {axis: glyph_composition(frame)
               for axis, frame in capture_axis(track)}
    x, y, w, h = region
    pixels = decode_region(ffmpeg, clip, region)
    red, blue = pixels[..., 0], pixels[..., 2]
    # The digits are SATURATED fire; the walls and floors behind them are
    # not (a brown brick measured (121, 84, 42), a blue water tile (88,
    # 119, 189)) -- both fail `red > 200`.
    ink = ((red > 200) & (blue < 90) & (red - blue > 120)).sum(axis=(1, 2))
    ink = ink.astype(float)

    def row_at(slot):
        if slot < 0 or slot >= len(frame_map):
            return None
        raw = frame_map[slot]
        if raw is None:
            return None
        axis = axis_of(raw, seams)
        return by_axis.get(axis) if axis is not None else None

    scores = {}
    for offset in range(-span, span + 1):
        rows, targets = [], []
        for slot in range(len(ink)):
            row = row_at(slot + offset)
            if row is not None:
                rows.append(row)
                targets.append(ink[slot])
        if len(targets) < 200:
            continue
        design, target = np.array(rows), np.array(targets)
        weights, *_ = np.linalg.lstsq(design, target, rcond=None)
        residual = target - design @ weights
        scores[offset] = 1 - float(residual.var() / target.var())
    if not scores:
        return None, {}
    return max(scores, key=scores.get), scores


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

    frame_map = sidecar.get("frame_map")
    if not frame_map:
        raise SystemExit("this clip's sidecar carries no frame_map -- it was "
                         "cut before the frame clock existed (or coverage "
                         "was missing); there is nothing to score")
    digit_region = tuple(round(v * (region[2] / ICON_REGION_AT_1600[2]))
                         for v in DIGIT_REGION_AT_1600)
    best, curve = glyph_offset(ffmpeg, clip, digit_region, frame_map,
                               Database(Path(args.db)), args.attempt)
    print(f"map source: {sidecar.get('frame_map_source', 'unknown')}")
    if best is None:
        print("stick digits unreadable in this clip -- no verdict")
    else:
        ordered = sorted(curve.values())
        print(f"VERDICT: the map is {best:+d} slots "
              f"({best / 2:+.1f} game frames) out; 0 is the goal. "
              f"[negative = ahead of the footage]")
        print(f"  fit {curve[best]:.4f} vs next best {ordered[-2]:.4f}; "
              f"curve " + " ".join(f"{off:+d}:{curve[off]:.3f}"
                                   for off in sorted(curve)))
    print()
    frames = decode_region(ffmpeg, clip, region)
    display = icon_change_slots(frames)
    changes = map_button_changes(sidecar, Database(Path(args.db)),
                                 args.attempt)
    # Which series built this map: "presents" = v4 (the hunted host present
    # counter), "feeds" = v2 (capture tags through the feeder), "edges" = v1.
    # A verdict means nothing without this line -- the same clip scores a
    # different map depending on what the clock held at extraction.
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
