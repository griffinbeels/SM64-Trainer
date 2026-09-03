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
import itertools
import json
import subprocess
from bisect import bisect_right
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


# The per-picture anchor (round 32 items 41-43). One clip, two error
# shapes, measured on attempt 4518: a long shelf (the map a whole frame
# out for the last 7.6 s -- the pipeline lag drifts by whole frames WITHIN
# a clip and the rising fit smooths across the step) and short BLIPS (a
# few pictures wrong by one around a lag transition or a dropped frame --
# his panel frames 30-33, one frame behind the screen, healing at 34).
# A global offset can fix neither; a 4-second window fixes the shelf and
# is structurally blind to a 4-picture blip. So every PICTURE chooses its
# own offset by best path: the digits' evidence per picture, against a
# switching cost -- long shelves and short blips fall out of one rule,
# and a seam lands exactly where the evidence flips.
#
# All four knobs below are DIMENSIONLESS (multiples of the clip's own
# noise floor at the global anchor), so they transfer across clips and
# encoders where raw thresholds would not, and all sit on a wide plateau:
# the synthetic worlds (a shelf with its seam, a 4-picture and a 2-picture
# blip at physical seams, a 4 s noise stretch) resolve identically for
# switch 4-8 x cap 6-14 (swept 2026-08-28).
VITERBI_SWITCH = 4.0
# Each picture's evidence is CAPPED at this many noise-floors: a picture
# the model cannot explain at ANY offset (display obscured, a fade) then
# prefers nothing instead of dragging the path around, while a genuinely
# misaligned picture -- whose RIGHT offset still fits well -- keeps its
# full vote. The cap is what lets the switch cost stay small enough for a
# two-picture blip to flip.
EVIDENCE_CAP = 6.0
# ...and votes at all only when its best offset lands within this many
# noise-floors -- see the vote rule in measure_windows.
VOTE_FLOOR = 2.0
# ...and only when its slots actually SHOW digits: a picture with next to
# no lit ink (a menu, the lead-in, a star dance) would otherwise "vote"
# for whichever offset predicts the least ink -- chance preference with
# nothing on screen. A single digit lights ~30+ pixels.
MIN_VOTE_INK = 12.0
# Switch-cost scaling by WHERE: stepping at a physically expressible seam
# (the map's own advance is irregular there) is near-free; stepping in the
# middle of a regular stretch needs overwhelming sustained evidence.
SWITCH_AT_IRREGULAR = 0.25
SWITCH_AT_REGULAR = 3.0


def _slot_residuals(ink: np.ndarray, rows_by_slot, weights,
                    offset: int) -> list:
    """Squared prediction error per slot under one offset; None = unpaired."""
    out: list = []
    for slot in range(len(ink)):
        index = slot + offset
        row = rows_by_slot[index] if 0 <= index < len(rows_by_slot) else None
        out.append(None if row is None
                   else float((ink[slot] - row @ weights) ** 2))
    return out


def _paired(ink: np.ndarray, rows_by_slot, offset: int, lo: int, hi: int):
    rows, targets = [], []
    for slot in range(lo, hi):
        index = slot + offset
        row = rows_by_slot[index] if 0 <= index < len(rows_by_slot) else None
        if row is not None:
            rows.append(row)
            targets.append(ink[slot])
    return np.array(rows), np.array(targets)


def measure_windows(ink: np.ndarray, rows_by_slot,
                    anchor: Alignment,
                    span: int = SEARCH_SLOTS,
                    frame_map: list | None = None,
                    runs: list[tuple[int, int]] | None = None) -> list[tuple]:
    """Per-stretch offsets: [(lo, hi, offset, fit, margin)] TILING the clip.

    Glyph weights are fitted ONCE at the global anchor's offset and held
    fixed, so every score is a pure prediction test -- a local refit would
    flatter every candidate. Each picture's evidence is its slots' summed
    squared residual under each EVEN candidate offset (odd = half a game
    frame, meaningless); the best path through those choices pays
    VITERBI_SWITCH noise-floors per frame of change, so a single noisy
    picture cannot flip the answer and a sustained disagreement -- shelf
    or blip -- always does. Pictures the pixels cannot answer carry no
    evidence and inherit their neighbours through the path. Empty list =
    the weights cannot be fitted at all; the caller falls back to the
    global offset."""
    design, target = _paired(ink, rows_by_slot, anchor.offset, 0, len(ink))
    if len(target) < MIN_PAIRED_SLOTS or float(np.var(target)) == 0.0:
        return []
    weights, *_ = np.linalg.lstsq(design, target, rcond=None)
    offsets = [offset for offset in range(-span, span + 1)
               if offset % 2 == 0]
    pictures, cheap = _pictures_and_seams(ink, rows_by_slot, frame_map, runs)
    costs = _picture_costs(ink, rows_by_slot, weights, offsets, pictures)
    ink_means = np.array([
        float(np.mean(ink[first:first + count])) if count else 0.0
        for first, count in pictures])
    path = _best_path(costs, offsets, anchor.offset, cheap, ink_means)
    return _stretches_of(path, pictures, offsets, ink, rows_by_slot, weights)


def _pictures_and_seams(ink, rows_by_slot, frame_map, runs):
    """The path's units and its cheap switch points."""
    if runs is not None and frame_map is not None:
        # The encoded pictures themselves (a map-value walk would merge a
        # DUP -- two pictures sharing a value, precisely the error being
        # corrected -- and hide its boundary).
        pictures = list(runs)
        # A lag step is only physically expressible where the map's
        # advance across a picture boundary is irregular (a skip or a dup
        # -- truth and stamp advance one per picture, so their difference
        # can move nowhere else). Entering such a picture switches
        # cheaply; everywhere else a switch costs a multiple, which is
        # what keeps a noisy stretch from wandering while a real step
        # lands exactly on its seam.
        cheap = [False]
        for before, entering in itertools.pairwise(pictures):
            left = frame_map[before[0]] if before[0] < len(frame_map) else None
            right = (frame_map[entering[0]]
                     if entering[0] < len(frame_map) else None)
            cheap.append(left is None or right is None or right - left != 1)
    else:
        pictures = []                             # (first_slot, slot_count)
        for slot in range(len(ink)):
            if pictures and slot == pictures[-1][0] + pictures[-1][1] \
                    and rows_by_slot[slot] is not None \
                    and rows_by_slot[slot - 1] is not None \
                    and np.array_equal(rows_by_slot[slot],
                                       rows_by_slot[slot - 1]):
                pictures[-1] = (pictures[-1][0], pictures[-1][1] + 1)
            else:
                pictures.append((slot, 1))
        cheap = [False] * len(pictures)
    return pictures, cheap


def _picture_costs(ink, rows_by_slot, weights, offsets, pictures):
    residuals = {offset: _slot_residuals(ink, rows_by_slot, weights, offset)
                 for offset in offsets}
    costs = np.zeros((len(pictures), len(offsets)))
    for row, (first, count) in enumerate(pictures):
        for column, offset in enumerate(offsets):
            total = 0.0
            for slot in range(first, first + count):
                value = residuals[offset][slot]
                if value is not None:
                    total += value
            costs[row, column] = total
    return costs


def _best_path(costs, offsets, anchor_offset, cheap, ink_means):
    at_anchor = costs[:, offsets.index(anchor_offset)]
    floor = float(np.median(at_anchor[at_anchor > 0])) \
        if np.any(at_anchor > 0) else 1.0
    costs = np.minimum(costs, EVIDENCE_CAP * floor)
    # A picture votes only when its best offset ACTUALLY FITS -- lands
    # near the clip's own noise floor. Merely being below the cap is not
    # enough: pure noise lands there by chance on ~half its pictures
    # (measured 54 of 120 on the synthetic obscured stretch), and a long
    # unexplained stretch would random-walk its way into a spurious
    # switch. No fit anywhere = no vote, and the path carries through.
    costs[costs.min(axis=1) >= VOTE_FLOOR * floor] = 0.0
    costs[ink_means < MIN_VOTE_INK] = 0.0
    switch = VITERBI_SWITCH * floor
    best = switch * np.abs((np.array(offsets) - anchor_offset)
                           // 2).astype(float)
    back = np.zeros((len(costs), len(offsets)), dtype=int)
    for row in range(len(costs)):
        scale = SWITCH_AT_IRREGULAR if cheap[row] else SWITCH_AT_REGULAR
        reached = np.full(len(offsets), np.inf)
        for column in range(len(offsets)):
            moves = best + switch * scale * np.abs(
                (np.array(offsets) - offsets[column]) // 2)
            source = int(np.argmin(moves))
            # STAY on a tie: an evidence-free stretch (a star dance, a
            # dark pause) otherwise lets argmin's first-index habit walk
            # the path across free seams (measured: his pyramid clip's
            # post-grab region marched -8..+8 across five such stretches).
            if moves[column] <= moves[source]:
                source = column
            reached[column] = moves[source] + costs[row, column]
            back[row, column] = source
        best = reached
    state = int(np.argmin(best))
    path = [0] * len(costs)
    for row in range(len(costs) - 1, -1, -1):
        path[row] = state
        state = back[row, state]
    return path


def _stretches_of(path, pictures, offsets, ink, rows_by_slot, weights):
    # Merge same-offset pictures into stretches; report each stretch's own
    # explained variance so the sidecar says how sure each one is.
    stretches: list[list] = []
    for (first, count), column in zip(pictures, path):
        offset = offsets[column]
        if stretches and stretches[-1][2] == offset:
            stretches[-1][1] = first + count
        else:
            stretches.append([first, first + count, offset, 0.0, 0.0])
    for stretch in stretches:
        rows, targets = _paired(ink, rows_by_slot, stretch[2],
                                stretch[0], stretch[1])
        if len(targets) and float(np.var(targets)) > 0.0:
            residual = targets - rows @ weights
            stretch[3] = round(
                1 - float(np.var(residual) / np.var(targets)), 4)
    return [tuple(stretch) for stretch in stretches]


def _value_runs(frame_map: list) -> list[tuple[int, int, int]]:
    """(first_slot, slot_count, value) per run of equal non-None values."""
    runs: list[tuple[int, int, int]] = []
    for slot, value in enumerate(frame_map):
        if value is None:
            continue
        if (runs and runs[-1][2] == value
                and slot == runs[-1][0] + runs[-1][1]):
            runs[-1] = (runs[-1][0], runs[-1][1] + 1, value)
        else:
            runs.append((slot, 1, value))
    return runs


def window_corrected(frame_map: list, windows: list[tuple]) -> list:
    """Apply each picture's shelf offset, in frames.

    Per PICTURE, not per slot -- a per-slot walk would split the very
    duplicates quantising unified. A downward step that would make a
    picture regress is clamped to previous+1: right at a seam the map had
    smoothed the step this reverses, so a picture of slack there is the
    honest floor."""
    if not windows:
        return list(frame_map)
    out = list(frame_map)
    previous = None
    active_delta = None
    at = 0
    for start, count, value in _value_runs(frame_map):
        middle = start + count / 2
        while at + 1 < len(windows) and windows[at][1] <= middle:
            at += 1
        wanted = round(windows[at][2] / 2)
        if active_delta is None:
            active_delta = wanted
        elif wanted != active_delta and (previous is None
                                         or value + wanted > previous):
            # An UPWARD step is a skip and always expressible. A DOWNWARD
            # one needs the original map to advance by 2+ here, or the
            # corrected series would regress -- and clamping previous+1
            # instead would CASCADE (+1 forever on a skipless stretch).
            # Hold the old shelf until the first boundary that absorbs it.
            active_delta = wanted
        corrected = value + active_delta
        if previous is not None and corrected <= previous:
            corrected = previous + 1           # safety net; deferral above
        previous = corrected
        for slot in range(start, start + count):
            out[slot] = corrected
    return out


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
    """Every STORED video frame as a small grey image, for run detection --
    `-fps_mode passthrough`, or a VFR picture-feed clip is re-timed onto its
    r_frame_rate grid and the runs no longer index the clip's frames (the
    reader's decode had the same fault, 2026-09-02)."""
    out = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(clip),
         "-vf", f"scale={RUN_WIDTH}:{RUN_HEIGHT}", "-fps_mode", "passthrough",
         "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"], capture_output=True)
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
    _paint_rising(out, numbered, values)
    return out


def _paint_rising(out: list, numbered: list[tuple[int, int, int]],
                  values: list[float]) -> None:
    """Fit per-picture values to the rising law and paint their slots.

    A series rising by >= 1 per picture is a NON-DECREASING series once
    each picture's own index is subtracted; put it back afterwards."""
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


# Phase unwrapping (round 32 item 50) -- the per-row closed form behind
# "We MUST achieve 100% accuracy with each frame."
#
# composition = edge(stamp) + phase, and the present pipeline's delay is
# SMOOTH: it drifts by milliseconds per second and never jumps. So the
# whole-frame part of the delay -- the integer k in delay = phase +
# k*period -- is chosen per row to keep the implied delay continuous
# (plain unwrapping), and the displayed frame is stamp - 1 - k, exact up
# to ONE whole-clip constant that the global digit anchor already
# measures. Measured on his three clips (2026-08-28): the perfect one's
# phases sit mid-period (16-28 ms of 33); the two wrong ones sit ON the
# edge (24-36 ms), where the stamp flickers +-1 per row as jitter crosses
# it -- a per-row error no stretch-level instrument can see, and exactly
# what the unwrap resolves.
FRAME_PERIOD_S = 1 / 30.0
# Follow the delay's slow drift without chasing per-row jitter.
UNWRAP_DRIFT_GAIN = 0.2
# A row whose implied whole-frame correction is this large is not riding
# the smooth pipeline (an emulator stall's catch-up burst): no estimate,
# and the rising rule interpolates its run instead.
UNWRAP_MAX_WHOLE_FRAMES = 4


def unwrapped_display(rows: list[dict]) -> dict[int, int]:
    """row index -> displayed-frame estimate (stamp - 1 - k), CANONICAL:
    the whole-frame split is chosen so the clip's median implied delay
    lands inside one frame period. That makes the one remaining constant
    the session's true pipeline delay -- the same number on every clip --
    so the digit anchor can confirm it where the digits are strong and
    the learned store can supply it where they are flat (his island clip:
    full-up held for seconds, a 0.03-wide score curve). Rows without a
    stamp or a phase, or off the smooth pipeline, are simply absent."""
    held: list[tuple[int, int, int, float]] = []   # (index, stamp, k, lag)
    target_lag: float | None = None
    for index, row in enumerate(rows):
        stamp, phase = row.get("frame"), row.get("phase")
        if stamp is None or phase is None:
            continue
        if target_lag is None:
            whole = 0
            lag = float(phase)
        else:
            whole = round((target_lag - phase) / FRAME_PERIOD_S)
            lag = phase + whole * FRAME_PERIOD_S
        if abs(whole) > UNWRAP_MAX_WHOLE_FRAMES:
            continue                       # a stall's burst, not the pipeline
        target_lag = (lag if target_lag is None
                      else (1 - UNWRAP_DRIFT_GAIN) * target_lag
                      + UNWRAP_DRIFT_GAIN * lag)
        held.append((index, stamp, whole, lag))
    if not held:
        return {}
    lags = sorted(lag for _index, _stamp, _whole, lag in held)
    canonical = int(lags[len(lags) // 2] // FRAME_PERIOD_S)
    return {index: stamp - 1 - whole - canonical
            for index, stamp, whole, _lag in held}


# Beyond this a run has NO ledger row: half a picture period (pictures are
# ~33 ms apart), which also guarantees no row can match two runs.
LEDGER_MATCH_TOLERANCE_S = 0.017
# Fewer matched runs than this and the ledger does not cover the clip.
LEDGER_MIN_MATCHED = 3


def _inject_orphans(entries, stamped, used, bias, start_ts, fps,
                    display_of) -> None:
    """Item 50: a row no run matched is a picture the encoded-side
    boundary detector merged away -- his pyramid's dark corridor fused
    three near-identical pictures into one 4-slot run and frames 276-277
    fell out of the map entirely. The ledger saw them (one row per
    present, exact dedup), so each orphaned row carves its slots out of
    the over-merged run at its own composition time. Only a run long
    enough to PROVE a merge takes an injection (two pictures of slots,
    split a full picture inside): jitter makes tie-losing rows look
    orphaned, and splitting an honest run shreds the map. The pixels may
    REFINE boundaries; they may never delete a picture."""
    slots_per_picture = max(1, round(fps / 30.0))
    for order, (ts, row_index) in enumerate(stamped):
        if order in used:
            continue
        split = round((ts - bias - start_ts) * fps - 0.5)
        for entry in entries:
            start, length, _value = entry
            if (length >= 2 * slots_per_picture
                    and start + slots_per_picture <= split
                    <= start + length - slots_per_picture):
                tail = start + length - split
                entry[1] = split - start
                entries.append([split, tail, display_of(row_index)])
                break


def ledger_map(slot_count: int, runs: list[tuple[int, int]],
               rows: list[dict], start_ts: float, fps: float,
               lag_frames: int = 0, frame_times: list | None = None) -> list | None:
    """The frame map built from the picture ledger: capture's own record
    of when each distinct picture appeared and what frame the game was on.

    Each encoded picture run is matched to the ledger row nearest its
    first slot's wall time. The two clocks (composition time vs the feed
    wall the slots live on) disagree by a small systematic amount that
    four hand-set constants each got wrong -- so the bias is MEASURED per
    clip as the median of the nearest-row deltas, never tuned. A run with
    no row inside half a picture period was a change the capture-side
    sample missed; it takes its left neighbour's value plus its picture
    distance (the consecutive-pictures law as the default belief), and
    the shared rising fit then holds the whole series to that law.
    `lag_frames` is the same display constant the feed series subtracts,
    so ledger maps and series maps land in one domain and the anchor
    store's medians stay comparable. Too few matches: None, and the
    series path answers instead.
    """
    displays = unwrapped_display(rows)

    def display_of(index: int) -> float:
        held = displays.get(index)
        return float(held if held is not None
                     else rows[index]["frame"] - lag_frames)

    stamped = sorted((row["ts"], index) for index, row in enumerate(rows)
                     if row.get("frame") is not None)
    if not stamped or not runs:
        return None
    times = [ts for ts, _index in stamped]
    # A picture-feed clip is VFR: a run's first slot sits at its own
    # timestamp, never on the 60 Hz grid (reading a VFR clip on the grid is
    # the same blindness that faked a 55-frame gap in inspect_timeline).
    walls = [start_ts + (frame_times[start] if frame_times
                         and start < len(frame_times)
                         else (start + 0.5) / fps)
             for start, _length in runs]

    def nearest(wall: float) -> int | None:
        at = bisect_right(times, wall)
        best = None
        for candidate in (at - 1, at):
            if 0 <= candidate < len(times) and (
                    best is None
                    or abs(times[candidate] - wall) < abs(times[best] - wall)):
                best = candidate
        return best

    deltas = [times[found] - wall for wall in walls
              if (found := nearest(wall)) is not None]
    if not deltas:
        return None
    bias = sorted(deltas)[len(deltas) // 2]

    matched: dict[int, float] = {}
    used: set[int] = set()
    for index, wall in enumerate(walls):
        found = nearest(wall + bias)
        if found is not None and (
                abs(times[found] - (wall + bias))
                <= LEDGER_MATCH_TOLERANCE_S):
            matched[index] = display_of(stamped[found][1])
            used.add(found)
    if len(matched) < max(LEDGER_MIN_MATCHED, len(runs) // 2):
        return None

    entries: list[list] = []                    # [start, length, value]
    previous_match: tuple[int, float] | None = None
    for index, (start, length) in enumerate(runs):
        value = matched.get(index)
        if value is None:
            if previous_match is None:
                first = min(matched)
                value = matched[first] - (first - index)
            else:
                value = previous_match[1] + (index - previous_match[0])
        else:
            previous_match = (index, value)
        entries.append([start, length, value])

    _inject_orphans(entries, stamped, used, bias, start_ts, fps, display_of)
    entries.sort(key=lambda entry: entry[0])
    numbered = [(order, start, length)
                for order, (start, length, _value) in enumerate(entries)]
    values = [float(value) for _start, _length, value in entries]
    out: list = [None] * slot_count
    _paint_rising(out, numbered, values)
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


# --- the digit fit: every picture assigned its own game frame -------------
# Round 32 item 55. Offsets could not express what his BBH clip does: the
# map was EXACT at frames 87-89 (the pixels show L3/L4/L5 exactly where it
# said) and 1-2 frames early at 129-141, in one clip, with ten stretches
# already fitted. A per-stretch offset assumes the error is piecewise
# constant; what actually varies is which game frames the capture DROPPED,
# and a drop shifts everything after it by one until the next drop.
#
# So the assignment is fitted directly: each encoded PICTURE takes the game
# frame whose drawn digits best explain its ink, subject to the only two
# laws the physics gives -- pictures advance monotonically, and consecutive
# pictures advance by at least one frame. That is a shortest-path problem
# over (picture, frame), which is exact rather than iterative, and it
# expresses a dropped frame as a step of two without needing to detect one.
MAX_PICTURE_STEP = 6          # a stall's catch-up; beyond this the band ends
FIT_BAND = 24                 # frames either side of the prior map's answer
# What an extra frame of step COSTS, in multiples of the clip's own ink
# noise. Without it the path is free wherever the picture cannot answer --
# a white fade-in, a dark room -- and it takes maximum steps for a
# rounding's worth of fit: his BBH clip jumped six frames per press through
# exactly such a stretch (2026-08-31), skipping the R and C-down he had
# just pressed. Most pictures really do advance by one, so a skip has to
# earn itself against the pixels.
STEP_PENALTY = 4.0
# The refit is only accepted when it explains the digits BETTER than the
# map it replaces, by this much of the noise floor per picture. A fit that
# cannot beat the prior has no business moving anything.
MIN_FIT_GAIN = 0.02


def _predicted_ink(rows_by_frame, weights):
    return {frame: float(row @ weights)
            for frame, row in rows_by_frame.items()}


def fit_pictures_to_frames(ink, runs, prior_map, rows_by_frame,
                           weights, band: int = FIT_BAND) -> list | None:
    """A frame map built by assigning each picture its best-fitting frame.

    `ink` is per video slot, `runs` the encoded pictures, `prior_map` the
    map to band the search around, `rows_by_frame` the glyph composition
    the track says each game frame drew, and `weights` the per-glyph ink
    already fitted.

    TWO guards, both paid for by his BBH clip on 2026-08-31, where the
    first version of this stepped six frames per press through a washed-out
    fade-in and skipped inputs he had just made. A step of more than one
    frame COSTS (`STEP_PENALTY` noise floors per extra frame), because most
    pictures really do advance by one and a skip has to earn itself; and
    the result is only returned when it explains the digits better than the
    map it replaces (`MIN_FIT_GAIN`), so a refit can never be worse than
    doing nothing. Returns None when the pictures or the track cannot carry
    the question, or when the fit has not earned its change.
    """
    if not runs or not rows_by_frame:
        return None
    predicted = _predicted_ink(rows_by_frame, weights)
    frames = sorted(predicted)
    index_of = {frame: at for at, frame in enumerate(frames)}
    # Each picture's measured ink (the median over its own slots -- one
    # slot can catch a fade or a torn present).
    measured, anchors = [], []
    for start, length in runs:
        window = [ink[slot] for slot in range(start, min(start + length,
                                                         len(ink)))]
        if not window:
            return None
        measured.append(float(np.median(window)))
        prior = next((prior_map[slot]
                      for slot in range(start, min(start + length,
                                                   len(prior_map)))
                      if prior_map[slot] is not None), None)
        anchors.append(prior)
    if any(anchor is None for anchor in anchors):
        return None
    # Candidate frames per picture: a band around the prior's answer.
    candidates = []
    for anchor in anchors:
        centre = index_of.get(anchor)
        if centre is None:
            centre = min(range(len(frames)),
                         key=lambda at: abs(frames[at] - anchor))
        low = max(0, centre - band)
        high = min(len(frames), centre + band + 1)
        candidates.append(range(low, high))
    def error(order, at):
        return (measured[order] - predicted[frames[at]]) ** 2

    # The clip's own noise floor: the typical error the PRIOR map already
    # carries. Every penalty below is a multiple of it, so nothing here is
    # tuned to one clip's brightness or one encoder's contrast.
    prior_errors = []
    for order, anchor in enumerate(anchors):
        at = index_of.get(anchor)
        if at is not None:
            prior_errors.append(error(order, at))
    if not prior_errors:
        return None
    floor = sorted(prior_errors)[len(prior_errors) // 2] or 1.0

    # Shortest path: a picture's cost is its ink error, and a move must
    # advance by 1..MAX_PICTURE_STEP frames -- paying for every frame past
    # the first, so a skip is a claim the pixels have to support.
    best = {at: error(0, at) for at in candidates[0]}
    back = []
    for order in range(1, len(measured)):
        step_back = {}
        reached = {}
        for at in candidates[order]:
            source, source_cost = None, None
            for step in range(1, MAX_PICTURE_STEP + 1):
                prior_at = at - step
                if prior_at not in best:
                    continue
                moved = best[prior_at] + STEP_PENALTY * floor * (step - 1)
                if source_cost is None or moved < source_cost:
                    source, source_cost = prior_at, moved
            if source is None:
                continue
            reached[at] = source_cost + error(order, at)
            step_back[at] = source
        if not reached:
            return None
        best = reached
        back.append(step_back)
    at = min(best, key=best.get)
    path = [at]
    for step_back in reversed(back):
        at = step_back[at]
        path.append(at)
    path.reverse()

    # EARNED, or nothing moves: the refit must explain the digits better
    # than the map it replaces, per picture.
    fitted_error = sum(error(order, at) for order, at in enumerate(path))
    prior_error = sum(prior_errors) * len(path) / len(prior_errors)
    if fitted_error > prior_error - MIN_FIT_GAIN * floor * len(path):
        return None

    out = [None] * len(prior_map)
    for (start, length), at in zip(runs, path):
        for slot in range(start, min(start + length, len(out))):
            out[slot] = frames[at]
    return out


def digit_fitted(clip: Path, frame_map, stick_of, ffmpeg: str,
                 width: int | None = None) -> list | None:
    """The clip's map, refitted picture by picture against its own digits.

    BUILT AND NOT WIRED (round 32 item 57, 2026-08-31). It fixed one of his
    clips and broke another: where a picture is washed out -- a bright
    fade-in, a dark room -- the ink says nothing, so the path buys a
    marginal fit with six-frame skips and drops inputs he had just
    pressed. A step penalty and a gate requiring it to explain the digits
    BETTER than the map it replaces were both added, and the wrong path
    still won. That is the finding, and it is about the SIGNAL: total ink
    is one number per slot, too weak to overrule the ledger's own answer.
    Wire it when the digits are READ rather than summed
    (`replay/pixelmap.py`) and gate it on the same 99% that reader owes.

    The offset instruments (global, then per stretch) assume the error is
    piecewise constant. His BBH clip is not: the map was EXACT at frames
    87-89 -- the pixels there really do draw L3, L4, L5 where it said --
    and one to two frames early at 129-141, in one clip. What varies is
    which frames the capture DROPPED, and a drop shifts everything after
    it until the next one, which no offset can express. So each picture
    takes the frame whose digits best explain its ink, monotonically.
    None when the pixels or the track cannot carry the question.
    """
    ink, _rows = _ink_and_rows(clip, frame_map, stick_of, ffmpeg, width)
    if ink is None:
        return None
    runs = picture_runs(decode_grey(ffmpeg, clip))
    rows_by_frame = {}
    for raw in {value for value in frame_map if value is not None}:
        pad = stick_of(raw)
        if pad is not None:
            rows_by_frame[raw] = glyph_row(*pad)
    # Every frame the clip could be showing, not only the ones it claims:
    # the fit has to be able to move a picture onto a neighbour.
    if rows_by_frame:
        low, high = min(rows_by_frame), max(rows_by_frame)
        for raw in range(low - SEARCH_SLOTS, high + SEARCH_SLOTS + 1):
            if raw in rows_by_frame:
                continue
            pad = stick_of(raw)
            if pad is not None:
                rows_by_frame[raw] = glyph_row(*pad)
    design, target = [], []
    for slot, raw in enumerate(frame_map):
        if raw in rows_by_frame and slot < len(ink):
            design.append(rows_by_frame[raw])
            target.append(ink[slot])
    if len(target) < MIN_PAIRED_SLOTS:
        return None
    weights, *_ = np.linalg.lstsq(np.array(design), np.array(target),
                                  rcond=None)
    return fit_pictures_to_frames(ink, runs, frame_map, rows_by_frame,
                                  weights)


def _ink_and_rows(clip: Path, frame_map, stick_of, ffmpeg: str,
                  width: int | None):
    rows = rows_for_map(frame_map, stick_of)
    if sum(1 for row in rows if row is not None) < MIN_PAIRED_SLOTS:
        return None, None
    if width is None:
        width = probe_width(ffmpeg, clip)
    pixels = decode_region(ffmpeg, clip, region_for_width(width))
    if len(pixels) == 0:
        return None, None
    return ink_per_slot(pixels), rows


def align_clip(clip: Path, frame_map, stick_of, ffmpeg: str,
               width: int | None = None) -> Alignment | None:
    """Measure one clip's map against its own footage. None = no verdict."""
    ink, rows = _ink_and_rows(clip, frame_map, stick_of, ffmpeg, width)
    if ink is None:
        return None
    return measure_offset(ink, rows)


def windowed_alignment(clip: Path, frame_map, stick_of, ffmpeg: str,
                       width: int | None = None
                       ) -> tuple[Alignment | None, list[tuple]]:
    """The global verdict AND the per-window ones, from one decode.

    (None, []) = the pixels cannot answer at all; (anchor, []) = a global
    verdict but no window passed its own gate, so the caller applies the
    global offset exactly as before."""
    ink, rows = _ink_and_rows(clip, frame_map, stick_of, ffmpeg, width)
    if ink is None:
        return None, []
    anchor = measure_offset(ink, rows)
    if anchor is None:
        return None, []
    runs = picture_runs(decode_grey(ffmpeg, clip))
    return anchor, measure_windows(ink, rows, anchor, frame_map=frame_map,
                                   runs=runs or None)
