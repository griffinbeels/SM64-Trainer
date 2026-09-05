"""WHICH FRAME'S PAD DOES EACH PICTURE SHOW -- the whole-clip offset sweep on
two independent channels, scored against the sidecar's map AS HANDED.

    uv run python tools/score_picture_offset.py --attempt 7015

Round 33 item 15 (2026-09-05). The picture-vs-panel offset had two constants
written on it, each right on the clip that set it and wrong on the next;
what ended it was a fresh-context review measuring the offset directly, on
two channels, and THIS is that measurement kept as a tool. For each offset
o in -6..+6 it asks: on how many video slots does the screen show the pad
of frame `map[k] + o`?

  STICK DIGITS   the display's letter + two digits per row, read with the
                 clip's own learned glyphs; a slot counts when a whole row
                 read (85.8% at -1 on 7015, 66.8% at 0)
  BUTTON ICONS   the lit button icons, templates learned under the map's
                 OWN labels (offset 0), so this channel leans AGAINST any
                 nonzero answer it gives (352 of 352 at -1 on 7015, 311 at 0)
  DP MOVES       how far the pad reader's per-slot alignment moved each
                 slot off the map (926 of 1076 by exactly -1 on 7015)

On a clip cut with the right convention every channel peaks at 0. A peak
elsewhere on TWO clips is the bar for touching `PLUGIN_PICTURE_LAG` or
`DISPLAY_LAG_FRAMES` (replay/service.py); one clip never is. What no pad
channel can say is whether the picture ITSELF is one list old or only the
ROM's input display draws the previous pad -- that is the oracle clip's
(tools/score_oracle.py). Offline, read-only; needs no emulator and no server.
"""
import argparse
import collections
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sm64_events.core.paths import bundled_ffmpeg  # noqa: E402
from sm64_events.inputs.track import track_with_lead  # noqa: E402
from sm64_events.memory import addresses as A  # noqa: E402
from sm64_events.replay import padread  # noqa: E402
from sm64_events.storage.db import Database  # noqa: E402

SPAN = 6


def digit_sweep(cells, frame_map, pads, held, same, changed) -> tuple[dict, list]:
    """{offset: (agreeing slots, slots scored)} over the stick digits, and
    the reader's own aligned path for the move histogram."""
    alphabet = padread.load_alphabet()
    path = list(frame_map)
    for _ in range(2):
        reads = padread.read(cells, alphabet)
        padread.enforce_grammar(reads)
        path = padread.align(reads, path, pads, same, changed, held, None, {})
        own = padread.learn(cells, padread.labels_from(path, pads))
        alphabet = padread.merge(alphabet, own)
    reads = padread.read(cells, alphabet)
    padread.enforce_grammar(reads)
    scores = {}
    for offset in range(-SPAN, SPAN + 1):
        agree = scored = 0
        for slot, raw in enumerate(frame_map):
            if raw is None:
                continue
            truth = (padread.truth_glyphs(pads.get(raw + offset), "y"),
                     padread.truth_glyphs(pads.get(raw + offset), "x"))
            matched = True
            checked = False
            for row, want in zip(("y", "x"), truth, strict=True):
                if want is None:
                    continue
                for index, col in enumerate(("letter", "d1", "d2")):
                    cell = reads.get((row, col))
                    if cell is None or not cell.known[slot]:
                        continue
                    checked = True
                    if want[index] != cell.names[slot]:
                        matched = False
            if checked:
                scored += 1
                agree += matched
        scores[offset] = (agree, scored)
    return scores, path


def icon_sweep(ffmpeg: str, clip: Path, frame_map, held) -> dict:
    """{offset: (lit slots holding that button, lit slots checked)}."""
    strip = padread.decode_icons(ffmpeg, clip)
    count = min(len(strip), len(frame_map))
    strip = strip[:count]
    frame_map = list(frame_map[:count])
    alphabet = dict(padread.load_icons())
    alphabet.update(padread.learn_icons(strip, padread.icon_labels_from(frame_map, held)))
    icons = padread.read_icons(strip, alphabet)
    lit = [(slot, tuple(icons[slot])) for slot in range(count) if icons[slot]]
    scores = {}
    for offset in range(-SPAN, SPAN + 1):
        agree = checked = 0
        for slot, bits in lit:
            raw = frame_map[slot]
            if raw is None:
                continue
            buttons = held.get(raw + offset)
            if buttons is None:
                continue
            checked += 1
            agree += all(buttons & bit for bit in bits)
        scores[offset] = (agree, checked)
    return scores


def print_table(title: str, scores: dict, unit: str) -> int:
    print(f"  {title}")
    best = max(scores, key=lambda key: scores[key][0])
    for offset in sorted(scores):
        agree, scored = scores[offset]
        share = 100 * agree / scored if scored else 0.0
        mark = "  <-- peak" if offset == best else ""
        print(f"    offset {offset:+d}: {agree:5d} / {scored:5d} {unit}  ({share:5.1f}%){mark}")
    return best


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--clip", default=None)
    parser.add_argument("--db", default="data/tracker.db")
    args = parser.parse_args()
    clip = Path(args.clip or f"data/replay_buffer/clips/clip_attempt_{args.attempt}.mp4")
    sidecar = clip.with_suffix(".json")
    if not clip.exists() or not sidecar.exists():
        print(f"no cached clip + sidecar at {clip} -- open the attempt's replay first")
        return 2
    meta = json.loads(sidecar.read_text())
    frame_map = meta.get("frame_map")
    if not frame_map:
        print("the sidecar carries no frame map; nothing to sweep against")
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
    held = {number: frame.buttons & A.BUTTON_VALID_MASK for number, frame in frames}
    ffmpeg = str(bundled_ffmpeg() or "ffmpeg")

    cells = padread.decode_cells(ffmpeg, clip)
    count = min(len(cells), len(frame_map))
    cells = cells[:count]
    frame_map = list(frame_map[:count])
    same, changed = padread.picture_flags(ffmpeg, clip, cells)
    repeats = meta.get("repeats")
    if repeats is not None:
        known = min(count, len(repeats))
        same[:known] |= np.asarray(list(repeats)[:known], bool)
        changed &= ~same

    print(f"attempt {args.attempt}: {count} video slots, map built by "
          f"{meta.get('frame_map_source') or 'the fixed offset'}; "
          "offset o = the screen at slot k shows the pad of frame map[k] + o")
    digits, path = digit_sweep(cells, frame_map, pads, held, same, changed)
    best_digits = print_table("STICK DIGITS (a slot counts when a whole row read)", digits, "slots")
    icons = icon_sweep(ffmpeg, clip, frame_map, held)
    best_icons = print_table("BUTTON ICONS (templates learned under the map's own labels)",
                             icons, "lit slots")
    moves = collections.Counter(p - r for p, r in zip(path, frame_map, strict=True)
                                if r is not None and p is not None)
    print("  DP MOVES (the reader's aligned slot minus the map's):",
          dict(sorted(moves.items())))
    if best_digits == best_icons == 0:
        print("VERDICT: 0 -- the map's convention matches the pictures on both channels")
    elif best_digits == best_icons:
        print(f"VERDICT: {best_digits:+d} on both channels -- the pictures show the pad "
              f"of map[k]{best_digits:+d}; a second clip agreeing is the bar for a constant")
    else:
        print(f"VERDICT: the channels disagree (digits {best_digits:+d}, icons "
              f"{best_icons:+d}) -- no constant on this clip")
    return 0


if __name__ == "__main__":
    sys.exit(main())
