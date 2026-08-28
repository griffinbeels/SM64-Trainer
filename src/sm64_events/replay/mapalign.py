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
import json
import subprocess
from collections import Counter
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
# The winner must beat the best offset OUTSIDE its own +-1 neighbourhood by
# this. Against the immediate neighbour it proves nothing: on a map already
# held to one answer per picture, +-1 slot differs only at picture
# boundaries, so the two are near-equivalent BY CONSTRUCTION -- his clip
# 4441 peaked cleanly at -2 (fit 0.469, next non-neighbour 0.452) and the
# old neighbour-inclusive gate refused it on a 0.007 margin to -1, leaving
# the whole clip running a frame ahead. The neighbour tie is the
# instrument's resolution, not evidence against the peak.
MIN_MARGIN = 0.010


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
    verdict = gate(scores)
    if verdict is None:
        return None
    best, margin = verdict
    return Alignment(offset=best, fit=scores[best], margin=margin,
                     paired=paired_counts[best])


def gate(scores: dict) -> tuple[int, float] | None:
    """(winning offset, margin) when the curve carries a real peak.

    The margin is measured against the best offset OUTSIDE the winner's own
    +-1 neighbourhood. On a map held to one answer per picture, offset k
    and its neighbour differ only at picture boundaries, so they are
    near-equivalent BY CONSTRUCTION and the neighbour tie is the
    instrument's resolution, not evidence against the peak -- his clip 4441
    peaked at -2 with 0.007 over -1 and 0.017 over the best non-neighbour,
    and the neighbour-inclusive gate this replaced refused it, leaving the
    whole clip a frame ahead.
    """
    if len(scores) < 4:
        return None
    best = max(scores, key=scores.get)
    rivals = [value for offset, value in scores.items()
              if abs(offset - best) > 1]
    if not rivals:
        return None
    margin = scores[best] - max(rivals)
    if scores[best] < MIN_FIT or margin < MIN_MARGIN:
        return None
    return best, margin


def frame_corrected(frame_map: list, slot_offset: int) -> list:
    """The measured slot offset applied in the FRAME domain.

    On a map already holding one answer per picture, shifting SLOTS by an
    odd amount would split pictures again -- the very invariant quantising
    exists for. The measurement means "the content is `slot_offset` slots
    older/newer than claimed", which in frames is round(offset / 2): every
    value moves by that constant and every picture keeps its one answer.
    """
    frames = round(slot_offset / 2)
    if not frames:
        return list(frame_map)
    return [None if raw is None else raw + frames for raw in frame_map]


class AnchorStats:
    """Per-clip measured anchors, remembered so display-off clips inherit.

    Every clip whose digits CAN be read appends its measured offset here;
    a clip whose digits cannot be read is corrected by the MEDIAN of the
    recent measurements instead of going uncorrected. The store is the
    "calibrate once" idea made continuous: he calibrates by playing, the
    constant converges, and a capture-pipeline change shows up as the
    median moving. Bounded to the newest KEEP entries; a corrupt or
    missing file reads as empty (never blocks a clip).
    """

    KEEP = 40
    MIN_MEASUREMENTS = 3

    def __init__(self, path: Path):
        self._path = path

    def _read(self) -> list:
        try:
            rows = json.loads(self._path.read_text())
            return rows if isinstance(rows, list) else []
        except (OSError, ValueError):
            return []

    def record(self, attempt_id: int, offset_slots: int, fit: float) -> None:
        rows = self._read()
        rows = [row for row in rows if row.get("attempt") != attempt_id]
        rows.append({"attempt": attempt_id, "offset": int(offset_slots),
                     "fit": round(float(fit), 4)})
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(rows[-self.KEEP:]))
        except OSError:
            pass                                  # stats must never block

    def fallback_offset(self) -> int | None:
        """The median measured offset, or None below MIN_MEASUREMENTS."""
        offsets = sorted(row["offset"] for row in self._read()
                         if isinstance(row.get("offset"), int))
        if len(offsets) < self.MIN_MEASUREMENTS:
            return None
        return offsets[len(offsets) // 2]


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


# -- the picture runs, and quantising a map onto them ------------------------
# A 30 fps game captured at 60 gives two video frames per picture, and the
# capture's own jitter makes it one or three often enough to see: measured
# on his clip 4374, 397 runs of 2 against 11 of one and 13 longer. His
# ruling, 2026-08-28: "if there's duplicated frames, input timeline should
# be identical for the sequential duplicated frames" -- a picture IS one
# game frame, however many video frames it happens to occupy, so the map
# must answer the same thing across all of them.
#
# Scaled small on purpose: the question is "did the picture change at all",
# and a 160x120 grey copy answers it for a fraction of the decode.
RUN_WIDTH, RUN_HEIGHT = 160, 120
# Mean per-pixel difference above which two video frames are DIFFERENT
# pictures. A re-encoded duplicate is not bit-identical, so this cannot be
# zero; measured on his clips, true duplicates sit under 0.1 and consecutive
# gameplay frames run 11 and up.
RUN_CHANGE_THRESHOLD = 0.35


def picture_runs(grey: np.ndarray) -> list[tuple[int, int]]:
    """(first slot, length) per distinct picture, in order."""
    if len(grey) == 0:
        return []
    changed = (np.abs(np.diff(grey.astype(np.int16), axis=0)).mean(axis=1)
               >= RUN_CHANGE_THRESHOLD)
    starts = [0] + [index + 1 for index, flag in enumerate(changed) if flag]
    return [(start, end - start)
            for start, end in zip(starts, starts[1:] + [len(grey)])]


def decode_grey(ffmpeg: str, clip: Path) -> np.ndarray:
    """Every video frame as a small grey image, for run detection."""
    out = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(clip),
         "-vf", f"scale={RUN_WIDTH}:{RUN_HEIGHT}", "-f", "rawvideo",
         "-pix_fmt", "gray", "pipe:1"], capture_output=True)
    stride = RUN_WIDTH * RUN_HEIGHT
    count = len(out.stdout) // stride
    return np.frombuffer(out.stdout[:count * stride],
                         dtype=np.uint8).reshape(count, stride)


def _rising(values: list[float]) -> list[float]:
    """Nearest non-decreasing series, least squares (pool adjacent violators).

    The one piece of machinery this repair needs: it finds the closest
    series to `values` that never goes down, so the correction moves each
    picture as little as the constraint allows instead of dragging the
    whole clip to fit its worst stretch.
    """
    stack: list[list[float]] = []           # [sum, count] per pooled block
    for value in values:
        block = [float(value), 1.0]
        while stack and stack[-1][0] / stack[-1][1] > block[0] / block[1]:
            previous = stack.pop()
            block = [previous[0] + block[0], previous[1] + block[1]]
        stack.append(block)
    out: list[float] = []
    for total, count in stack:
        out.extend([total / count] * int(count))
    return out


def quantised(frame_map: list, runs: list[tuple[int, int]]) -> list:
    """One answer per PICTURE, and a DIFFERENT one for the next picture.

    Two rules, and the second is what his 2026-08-28 session found. The
    first: the runs supply the boundaries, so every video frame showing one
    picture answers the same -- "if there's duplicated frames, input
    timeline should be identical for the sequential duplicated frames".

    The second: consecutive PICTURES must be consecutive FRAMES. Holding
    the boundaries alone left the map's own irregular advance untouched,
    and on his clip 4441 that meant 230 pictures sharing a frame with the
    next one and 225 skipping one -- three different pictures all labelled
    frame 7, then a jump straight to 9. Stepping forward then showed a new
    picture beside an unchanged panel, or a panel that moved two.

    So each picture is pushed to the nearest series that RISES by at least
    one per picture (`_rising` on value minus index), which is the smallest
    correction satisfying the rule. Rising by MORE is allowed and left
    alone where the map says so: the capture really does miss frames --
    1,230 pictures for ~1,326 game frames on that clip -- and forcing a
    step of exactly one would end it ninety-six frames adrift.
    """
    out = list(frame_map)
    numbered: list[tuple[int, int, int]] = []      # (run index, start, length)
    values: list[float] = []
    for index, (start, length) in enumerate(runs):
        window = [frame_map[slot] for slot in range(start, start + length)
                  if slot < len(frame_map) and frame_map[slot] is not None]
        if not window:
            continue
        counts = Counter(window)
        best = max(counts.items(), key=lambda pair: (pair[1], pair[0]))[0]
        numbered.append((index, start, length))
        values.append(float(best))
    if not numbered:
        return out
    # A series rising by >= 1 per picture is a NON-DECREASING series once
    # each picture's own index is subtracted; put it back afterwards.
    rising = _rising([value - order for order, value in enumerate(values)])
    previous: int | None = None
    for order, (_index, start, length) in enumerate(numbered):
        frame = int(round(rising[order] + order))
        # Rounding two neighbours a real frame apart can still land them on
        # the same integer; the rule is about the integers, so hold it here
        # as well (9 pictures of 1,163 on his clip 4441).
        if previous is not None and frame <= previous:
            frame = previous + 1
        previous = frame
        for slot in range(start, min(start + length, len(out))):
            out[slot] = frame
    return out


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
