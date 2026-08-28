# src/sm64_events/replay/mapalign.py
"""Align a clip's frame map to the game's OWN display, per clip.

Round 32, item 17, after four rounds of constants failed him. The map has
to say which game frame each video frame shows. Everything upstream of this
module produces that map from a chain of timing constants -- a display lag,
a counter's pipeline phase, a slot bias -- and each of those was set by
measuring one clip and then failed on the next: -2 slots, +1, -3, each time
verified live and each time landing somewhere else. His verdict on the last
round: "Totally desynced now, it's even worse than when we started trying
to improve this."

The chain cannot be fixed by picking better constants, because the thing
it estimates -- how long a picture takes to travel from the game's logic to
a captured video slot -- is not ours to know. It IS knowable from the clip:
with Usamune's input display on, every video frame carries the game's own
drawing of the pad on that frame, and the input track says what the pad
held on every frame. So the clip carries its own answer, and this module
reads it: measure the offset between what the map claims and what the
pixels show, and shift the map onto the pixels.

WHAT IT MEASURES, and why this signal and not another: the stick moves
nearly every frame, which makes digit CHANGES useless for alignment (they
saturate -- 892 changes over 979 slots) and makes digit INK ideal, because
each slot's lit-pixel count is then a rich per-frame value. Predict that
count from the track with a per-glyph ink weight (least squares, one fit
per candidate offset) and keep the best-fitting offset: a wrong offset
predicts the wrong digits and fits measurably worse. Measured on his own
clips, the winning offset is the same in every window of a clip -- a clean
constant per clip, which is exactly what a per-clip correction wants.

WHEN IT REFUSES: a clip recorded with the input display off carries no
digits, and a clip whose track has a hole carries too few paired slots. A
refusal keeps the constant-built map rather than shifting it by a number
nothing supports -- `measure_offset` returns None and the caller leaves
the map alone. The sidecar records which happened, so a wrong map is
always answerable after the fact rather than by eye.

The instrument is shared, not copied: `tools/score_frame_map.py` scores a
clip with this same code, so the tool cannot drift from what extraction
actually did.
"""
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# Usamune's stick readout on a 1600x1224 capture -- two lines, "U84" over
# "R70", in the HUD's fiery font. Scaled linearly for other capture sizes.
DIGIT_REGION_AT_1600 = (60, 900, 420, 260)      # x, y, w, h
REFERENCE_WIDTH = 1600
GLYPHS = "0123456789UDLR"

# The digits are SATURATED fire. Everything measured behind them fails the
# first clause: a brown brick (121, 84, 42), a blue water tile (88, 119,
# 189), a grey castle floor. Sampled from his clips, 2026-08-25/26.
INK_MIN_RED = 200
INK_MAX_BLUE = 90
INK_MIN_WARMTH = 120                            # red - blue

SEARCH_SLOTS = 8              # +-8 slots = +-4 game frames of search
MIN_PAIRED_SLOTS = 200        # below this the fit is not evidence
MIN_FIT = 0.35                # R2 floor -- an unlit region fits nothing
MIN_MARGIN = 0.010            # the winner must beat the runner-up by this


@dataclass(frozen=True)
class Alignment:
    """How far the map is out, and how strongly the pixels say so."""
    offset: int               # slots; negative = the map ran AHEAD
    fit: float                # R2 of the winning offset
    margin: float             # R2 over the runner-up
    paired: int               # slots that carried both ink and a track frame


def region_for_width(width: int) -> tuple[int, int, int, int]:
    scale = (width or REFERENCE_WIDTH) / REFERENCE_WIDTH
    return tuple(round(value * scale) for value in DIGIT_REGION_AT_1600)


def glyph_row(stick_x: int, stick_y: int) -> np.ndarray:
    """Which glyphs Usamune draws for this pad, as counts.

    An axis at rest draws a bare "0"; otherwise a direction letter and the
    magnitude's digits -- so "U84" over "R70" is six glyphs and "U84" over
    "0" is four.
    """
    row = np.zeros(len(GLYPHS))
    for value, positive, negative in ((stick_y, "U", "D"),
                                      (stick_x, "R", "L")):
        value = int(value)
        text = ("0" if value == 0
                else (positive if value > 0 else negative) + str(abs(value)))
        for character in text:
            row[GLYPHS.index(character)] += 1
    return row


def ink_per_slot(pixels: np.ndarray) -> np.ndarray:
    """Lit digit pixels per video slot, from a decoded (slots, h, w, 3)."""
    red, blue = pixels[..., 0], pixels[..., 2]
    lit = ((red > INK_MIN_RED) & (blue < INK_MAX_BLUE)
           & (red - blue > INK_MIN_WARMTH))
    return lit.sum(axis=(1, 2)).astype(float)


def decode_region(ffmpeg: str, clip: Path, region) -> np.ndarray:
    """Every video frame's pixels inside `region`, as (slots, h, w, 3)."""
    x, y, w, h = region
    out = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(clip),
         "-vf", f"crop={w}:{h}:{x}:{y}", "-f", "rawvideo",
         "-pix_fmt", "rgb24", "pipe:1"], capture_output=True)
    stride = w * h * 3
    count = len(out.stdout) // stride
    flat = np.frombuffer(out.stdout[:count * stride], dtype=np.uint8)
    return flat.reshape(count, h, w, 3).astype(np.int16)


def measure_offset(ink: np.ndarray, rows_by_slot,
                   span: int = SEARCH_SLOTS) -> Alignment | None:
    """The offset whose predicted digits best explain the measured ink.

    `rows_by_slot[k]` is the glyph composition the CURRENT map claims for
    slot k (or None where it claims nothing). The answer says where the
    map's claims really belong: `offset` of -3 means slot k shows what the
    map currently lists at slot k-3, so the map is running three slots
    ahead of the footage.

    None when the pixels cannot carry the question -- too few paired
    slots, an unlit region, or no offset clearly better than its
    neighbours.
    """
    if len(ink) == 0 or float(np.var(ink)) == 0.0:
        return None
    scores: dict[int, float] = {}
    paired_counts: dict[int, int] = {}
    for offset in range(-span, span + 1):
        rows, targets = [], []
        for slot in range(len(ink)):
            index = slot + offset
            row = rows_by_slot[index] if 0 <= index < len(rows_by_slot) else None
            if row is not None:
                rows.append(row)
                targets.append(ink[slot])
        if len(targets) < MIN_PAIRED_SLOTS:
            continue
        design, target = np.array(rows), np.array(targets)
        if float(np.var(target)) == 0.0:
            continue
        weights, *_ = np.linalg.lstsq(design, target, rcond=None)
        residual = target - design @ weights
        scores[offset] = 1 - float(np.var(residual) / np.var(target))
        paired_counts[offset] = len(target)
    if len(scores) < 2:
        return None
    best = max(scores, key=scores.get)
    runner_up = max(value for offset, value in scores.items() if offset != best)
    if scores[best] < MIN_FIT or scores[best] - runner_up < MIN_MARGIN:
        return None
    return Alignment(offset=best, fit=scores[best],
                     margin=scores[best] - runner_up,
                     paired=paired_counts[best])


def shifted(frame_map: list, offset: int) -> list:
    """The map moved onto the pixels: slot k answers with what the map
    listed at slot k + offset. Slots pushed past either end answer None --
    honestly unknown rather than clamped to a neighbour's frame."""
    if not offset:
        return list(frame_map)
    out = []
    for slot in range(len(frame_map)):
        index = slot + offset
        out.append(frame_map[index] if 0 <= index < len(frame_map) else None)
    return out


def rows_for_map(frame_map, stick_of):
    """Per slot, the glyphs the track says the display drew there.

    `stick_of(raw_game_frame)` answers (stick_x, stick_y) or None.
    """
    cache: dict[int, np.ndarray] = {}
    rows = []
    for raw in frame_map:
        if raw is None:
            rows.append(None)
            continue
        if raw not in cache:
            pad = stick_of(raw)
            cache[raw] = None if pad is None else glyph_row(pad[0], pad[1])
        rows.append(cache[raw])
    return rows


def probe_width(ffmpeg: str, clip: Path) -> int:
    """The clip's pixel width, for placing the readout region."""
    ffprobe = (ffmpeg.replace("ffmpeg", "ffprobe")
               if "ffmpeg" in ffmpeg else "ffprobe")
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v",
             "-show_entries", "stream=width", "-of", "csv=p=0", str(clip)],
            capture_output=True, text=True)
        return int((out.stdout.strip().split(",") or [""])[0])
    except (ValueError, OSError):
        return REFERENCE_WIDTH


def align_clip(clip: Path, frame_map, stick_of, ffmpeg: str,
               width: int | None = None) -> Alignment | None:
    """Measure one clip's map against its own footage. None = no verdict."""
    rows = rows_for_map(frame_map, stick_of)
    if sum(1 for row in rows if row is not None) < MIN_PAIRED_SLOTS:
        return None
    if width is None:
        width = probe_width(ffmpeg, clip)
    pixels = decode_region(ffmpeg, clip, region_for_width(width))
    if len(pixels) == 0:
        return None
    return measure_offset(ink_per_slot(pixels), rows)
