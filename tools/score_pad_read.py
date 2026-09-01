"""DOES THE TIMELINE SHOW WHAT THE SCREEN SHOWS -- the pad reader's verdict.

    uv run python tools/score_pad_read.py --attempt 5431
    uv run python tools/score_pad_read.py --attempt 5431 --disagreements

Round 32, 2026-09-01. His acceptance test, verbatim: "we need to always
capture the same input display that's on screen, and display it as the
input timeline with 100% accuracy; anything less than 100% accuracy is
failure." This is that test as a number. It reads Usamune's input display
out of every video frame of a cached clip (`replay/padread.py` -- the same
code extraction runs), aligns the input track to what it read, and prints:

  SURE      video frames where a whole row of the display read (letter and
            both digits, each by a clear margin)
  AGREE     ...and the timeline's pad on that frame is exactly what the
            screen shows -- the number he is grading
  NOWHERE   ...and no frame of the track within +-10 shows it: a misread
            of the display, never a map error (0 is the reader's own gate)
  MOVED     slots whose frame the reading changed from the sidecar's map

Every disagreement is listed with --disagreements: the video slot, its
time, the row, what the screen reads and what the map claims. A clip
recorded with the display OFF refuses, and says so. Read-only; needs no
emulator and no server.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sm64_events.core.paths import bundled_ffmpeg  # noqa: E402
from sm64_events.inputs.track import track_with_lead  # noqa: E402
from sm64_events.replay import padread  # noqa: E402
from sm64_events.storage.db import Database  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--clip", default=None, help="path to the .mp4 "
                        "(default data/replay_buffer/clips/clip_attempt_N.mp4)")
    parser.add_argument("--db", default="data/tracker.db")
    parser.add_argument("--disagreements", action="store_true",
                        help="list every slot the screen contradicts")
    args = parser.parse_args()
    clip = Path(args.clip or f"data/replay_buffer/clips/clip_attempt_{args.attempt}.mp4")
    sidecar = clip.with_suffix(".json")
    if not clip.exists() or not sidecar.exists():
        print(f"no cached clip + sidecar at {clip} -- open the attempt's replay first")
        return 2
    meta = json.loads(sidecar.read_text())
    frame_map = meta.get("frame_map")
    if not frame_map:
        print("the sidecar carries no frame map; nothing to read against")
        return 2
    db = Database(Path(args.db))
    attempt = next((a for a in db.attempts() if a.id == args.attempt), None)
    if attempt is None:
        print(f"attempt {args.attempt} is not in {args.db}")
        return 2
    seen = [raw for raw in frame_map if raw is not None]
    frames, _lead = track_with_lead(
        db.inputs, attempt, span=(min(seen) - padread.BAND, max(seen) + padread.BAND))
    pads = {number: (frame.stick_x, frame.stick_y) for number, frame in frames}
    fps = float(meta.get("fps") or 60.0)
    print(f"attempt {args.attempt}: {len(frame_map)} video slots, map built by "
          f"{meta.get('frame_map_source') or 'the fixed offset'}"
          + (", already READ at extraction" if meta.get("frame_map_read") else ""))
    reading = padread.read_clip(clip, frame_map, pads, str(bundled_ffmpeg() or "ffmpeg"))
    if reading is None:
        print("REFUSED: the display could not be read on enough frames "
              "(input display off, or covered) -- the clocks' map stands")
        return 1
    v = reading.verdict
    moved = sum(1 for a, b in zip(frame_map, reading.frame_map) if a is not None and a != b)
    print(f"  SURE     {v.sure:6d} of {v.slots} slots read a whole row of the display")
    print(f"  AGREE    {v.agree:6d}  ({100 * v.agreement:.2f}%) show exactly the pad the timeline holds")
    print(f"  NOWHERE  {v.nowhere:6d}  reads matching no frame within +-10 (misreads)")
    print(f"  MOVED    {moved:6d}  slots the reading moved off the sidecar's map")
    print(f"  cells read {v.known_cells} of {v.slots * 6}; glyphs the clip taught itself: "
          + ", ".join(f"{row}/{col}:{''.join(glyphs)}" for (row, col), glyphs in reading.learned.items()))
    if v.disagreements and args.disagreements:
        print("  disagreements (slot, time, row, screen reads, map says):")
        for slot, row, seen_text, says in v.disagreements:
            print(f"    slot {slot:5d}  {slot / fps:7.3f}s  {row}  screen {seen_text:5s}  map {says}")
    elif v.disagreements:
        print(f"  {len(v.disagreements)} disagreements -- --disagreements lists them")
    print("VERDICT: " + ("100% -- every frame the display can be checked on agrees"
                         if v.agree == v.sure else
                         f"{v.sure - v.agree} of {v.sure} checkable frames disagree"))
    return 0 if v.agree == v.sure else 1


if __name__ == "__main__":
    raise SystemExit(main())
