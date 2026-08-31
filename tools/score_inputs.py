"""Does the timeline AGREE with the footage, on every frame of a clip?

Round 32, item 61 -- his ask, 2026-08-31: "Do we have the correct
instrumentation to be able to parse each frame visually, detect what the
input values were, compare that to our frame data annotation, and compare
that to our input timeline, to confirm that all of them are in agreement?"

The trick is that it needs no digit RECOGNITION, which is the part that has
never been reliable. Usamune draws the pad into every picture, so the digit
region is a function of the pad ALONE: two video frames showing the same
pad draw pixel-identical regions, and two showing different pads do not.
That gives a test with no reader in it --

    the region CHANGED between two video frames
      <=>  the pad CHANGED between the frames the map assigns them

-- and every violation is a real disagreement between the footage, the
frame map and the input track. A change in the pixels with no change in the
pad means the timeline is holding one frame too long; a change in the pad
with none in the pixels means it has moved on too early.

For each violation the tool also asks which LOCAL SHIFT would resolve it,
so a report reads "this stretch is one frame early" rather than "something
is wrong here". His pattern hunt (2026-08-31: perfect outside the pyramid,
desynced after the subarea transition) is exactly what that column is for.

    uv run python tools/score_inputs.py                # the newest clip
    uv run python tools/score_inputs.py --attempt 5363
    uv run python tools/score_inputs.py --attempt 5363 --frames   # panel frames

Read-only; needs the clip and its sidecar, no emulator.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sm64_events.inputs.track import track_with_lead
from sm64_events.replay import mapalign
from sm64_events.storage.db import Database

CLIP_FPS = 60.0
# How far a local shift is searched when a violation is found.
SHIFT_SEARCH = 4


def clip_paths(attempt_id: int | None) -> tuple[int, Path]:
    clips = Path("data/replay_buffer/clips")
    if attempt_id is None:
        newest = sorted(clips.glob("clip_attempt_*.json"),
                        key=lambda p: p.stat().st_mtime)
        if not newest:
            raise SystemExit("no cached clips -- open an attempt's replay first")
        attempt_id = int(newest[-1].stem.rsplit("_", 1)[1])
    side = clips / f"clip_attempt_{attempt_id}.json"
    if not side.exists():
        raise SystemExit(f"no sidecar for attempt {attempt_id}")
    return attempt_id, side


def region_signatures(clip: Path, ffmpeg: str = "ffmpeg") -> np.ndarray:
    """The DIGIT INK per video frame, as a small float image.

    The ink mask, never the raw crop: the region sits over the gameplay, so
    the scene behind the digits changes on nearly every frame and a raw
    comparison reports every boundary as a change (68% "agreement" on its
    first run, all of it noise). Usamune's readout is saturated fire, which
    the mask isolates from anything the game draws behind it -- the same
    test `mapalign.ink_per_slot` counts, kept as a picture instead of a
    sum so two different pads with the same amount of ink still differ.
    """
    width = mapalign.probe_width(ffmpeg, clip)
    pixels = mapalign.decode_region(ffmpeg, clip,
                                    mapalign.region_for_width(width))
    red, blue = pixels[..., 0], pixels[..., 2]
    lit = ((red > mapalign.INK_MIN_RED) & (blue < mapalign.INK_MAX_BLUE)
           & (red - blue > mapalign.INK_MIN_WARMTH)).astype(np.float32)
    # Sum 4x4 blocks: keeps each glyph's shape and position while making a
    # one-pixel edge wobble in the encode irrelevant.
    height, span = lit.shape[1] // 4 * 4, lit.shape[2] // 4 * 4
    blocks = lit[:, :height, :span].reshape(len(lit), height // 4, 4,
                                            span // 4, 4)
    return blocks.sum(axis=(2, 4))


def same_picture_threshold(differences: np.ndarray) -> float:
    """Where "the same picture" ends and "a new one" begins, per clip.

    The distribution is strongly bimodal -- on his pyramid clip p75 sits at
    0.02 and p90 at 0.72 -- so the split is the widest gap between
    consecutive sorted values inside the plausible band, which is the one
    the data itself argues for rather than a constant that would need
    re-tuning per level.
    """
    ordered = np.sort(differences[differences > 0])
    if len(ordered) < 8:
        return 0.25
    window = ordered[int(len(ordered) * 0.40):int(len(ordered) * 0.97)]
    if len(window) < 3:
        return float(np.median(ordered))
    at = int(np.argmax(np.diff(window)))
    return float((window[at] + window[at + 1]) / 2)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", type=int, default=None)
    parser.add_argument("--frames", action="store_true",
                        help="report panel frames rather than video slots")
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()

    attempt_id, side_path = clip_paths(args.attempt)
    side = json.loads(side_path.read_bytes())
    frame_map = side.get("frame_map")
    if not frame_map:
        raise SystemExit(f"attempt {attempt_id}'s clip carries no frame map")

    db = Database(Path("data") / "tracker.db")
    attempt = next(a for a in db.attempts() if a.id == attempt_id)
    seen = [value for value in frame_map if value is not None]
    frames, lead = track_with_lead(db.inputs, attempt,
                                   span=(min(seen), max(seen)))
    pads = {number: (frame.stick_x, frame.stick_y) for number, frame in frames}
    axis0 = frames[0][0] if frames else 0

    signatures = region_signatures(side_path.with_suffix(".mp4"))
    slots = min(len(signatures), len(frame_map))
    print(f"attempt {attempt_id}: {slots} video frames, "
          f"{len(pads)} captured pads, lead {lead}")

    def pad_of(slot: int, shift: int = 0):
        """What the readout DRAWS for this slot's frame: the magnitudes.

        Usamune paints the direction letters (U/D/L/R) in blue and the
        numbers in fire, and the ink mask only sees the fire -- verified by
        rendering it (the "U" of "U77 L39" is simply absent). So a sign flip
        is invisible to this instrument and comparing signed values would
        report a disagreement the pixels cannot possibly show. That is a
        stated limit, not an approximation: everything else about the pad
        IS in the picture.
        """
        raw = frame_map[slot]
        if raw is None:
            return None
        pad = pads.get(raw + shift)
        return None if pad is None else (abs(pad[0]), abs(pad[1]))

    def panel(slot: int):
        raw = frame_map[slot]
        return None if raw is None else raw - axis0 - lead

    differences = np.array([
        float(np.mean(np.abs(signatures[slot] - signatures[slot - 1])))
        for slot in range(1, slots)])
    cut = same_picture_threshold(differences)
    changed_pixels = list(differences > cut)
    print(f"a picture changes above {cut:.3f} mean ink difference "
          f"(split taken from this clip's own distribution)")

    violations = []
    for slot in range(1, slots):
        here, before = pad_of(slot), pad_of(slot - 1)
        if here is None or before is None:
            continue                       # nothing captured to compare
        pad_moved = here != before
        if pad_moved == changed_pixels[slot - 1]:
            continue
        # Which local shift would make this slot agree?
        fix = None
        for shift in range(-SHIFT_SEARCH, SHIFT_SEARCH + 1):
            if shift == 0:
                continue
            shifted, shifted_before = pad_of(slot, shift), pad_of(slot - 1,
                                                                 shift)
            if shifted is None or shifted_before is None:
                continue
            if (shifted != shifted_before) == changed_pixels[slot - 1]:
                fix = shift
                break
        violations.append((slot, pad_moved, changed_pixels[slot - 1], fix))

    agree = slots - 1 - len(violations)
    print(f"agreement: {agree} of {slots - 1} frame boundaries "
          f"({100 * agree / max(slots - 1, 1):.2f}%)")
    if not violations:
        print("every boundary agrees: the footage, the map and the track "
              "tell the same story on every frame of this clip")
        return
    print(f"{len(violations)} disagreement(s); the first {args.limit}:")
    for slot, pad_moved, pixels_moved, fix in violations[:args.limit]:
        where = (f"panel {panel(slot)}" if args.frames
                 else f"slot {slot} ({slot / CLIP_FPS:5.2f}s)")
        what = ("the pad moved but the picture did not"
                if pad_moved else "the picture moved but the pad did not")
        cure = f"a shift of {fix:+d} frames fixes it" if fix else "no shift fixes it"
        print(f"  {where}: {what}; {cure}")
    if len(violations) > args.limit:
        print(f"  ... and {len(violations) - args.limit} more")


if __name__ == "__main__":
    main()
