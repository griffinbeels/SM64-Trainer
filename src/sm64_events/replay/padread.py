"""The PAD READER: the frame map, READ off Usamune's own input display.

Round 32, 2026-09-01 -- after every timing route had been measured and
found wanting. The clip is a photograph of the window taken on a wall
clock; nothing in that photograph states which game frame it shows, and
the lag between the game finishing a frame and its picture landing in an
encoded slot wanders by whole frames inside one clip (attempt 5431: +5 to
-1 across 24 s). Every map built from stamps -- edges, feeds, presents,
the picture ledger -- inherits that wander, and the ink anchor on top of
them (`mapalign.py`) reads one number per slot, which is too weak to pin
a picture on its own. His ruling, verbatim: "we need to always capture the
same input display that's on screen, and display it as the input timeline
with 100% accuracy; anything less than 100% accuracy is failure."

So this module treats the display AS the ground truth, because he does.
Usamune paints the pad into every picture as six glyph cells -- two rows
("U71" over "R70": a direction letter and up to two digits each), and the
HUD font is rendered identically every time it draws a glyph, down to the
pixel, with only the world behind it changing. That makes each cell a
lookup rather than a recognition problem: a template per glyph, weighted
to the pixels the font itself paints, and a cell either matches one glyph
by a clear margin or is UNKNOWN. Never a guess.

Then the map is the alignment of two exact sequences: the track knows the
pad on every game frame, the cells say the pad on every video frame, and
a monotone path (dwell, advance, skip) through the frame numbers pays one
unit for every known cell it contradicts. Where the display is unreadable
-- a fade, a menu, a dark room -- the path is tethered to the map the
clocks built, so it can only ever be as wrong as before there, and it is
provably right everywhere the display can be checked. `score` says how
many slots that is, per clip, and names each disagreement.

MEASURED, 2026-09-01, on 7 minutes of his ring footage (title screen with
its scrolling "USAMUNE ROM" overlay as an adversarial occluder, plus
gameplay), 18,411 video frames against the input track: per-cell reads
on labelled frames 12,962 right / 8 wrong (d1), 3,958 / 6 (d2), 4,116 / 0
(letter); every fully-read slot agreed with the aligned frame -- 7,494 of
7,494 (Y row) and 6,079 of 6,080 (X row) -- and zero reads named a value
the track never held nearby. The per-field fingerprint reader this
replaces (`pixelmap.py`) read 84% / 49% on the same kind of footage,
because it needed a template per VALUE (hundreds) where this needs one
per GLYPH (fifteen).

The reference alphabet (`data/pad_glyphs_us.npz`) was learned from that
footage; it bootstraps the first read of any clip, and the clip's own
holds then re-learn every glyph at its own capture scale. A clip that
reads too little refuses, and the caller keeps the clocks' map.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import numpy as np

log = logging.getLogger("sm64.replay")

# Where Usamune's ONVAL readout sits, as fractions of the frame (PJ64
# stretches the 320x240 framebuffer linearly, so fractions hold at any
# window size). Measured on his 1600x1224 clips: x 96..306, y 928..1104.
REGION = (0.060, 0.7582, 0.19125, 0.9020)          # x0, y0, x1, y1
CELL_W, CELL_H = 105, 88                            # the region at 2.5 px per game px
ROWS = {"y": (0, 43), "x": (45, 88)}                # the U/D line, then the L/R line
COLS = {"letter": (0, 35), "d1": (35, 69), "d2": (68, 105)}
LETTERS = {"y": ("U", "D"), "x": ("R", "L")}        # (positive, negative)
KEYS = tuple((row, col) for row in ROWS for col in COLS)

# -- reading --------------------------------------------------------------
SPREAD_MAX = 14.0        # a template pixel: exemplars agree within this (MAD)
MIN_EXEMPLARS = 8        # fewer sightings than this teach nothing
MIN_WEIGHT = 40          # a template with fewer painted pixels is not a glyph
DIST_MAX = 40.0          # mean |diff| over painted pixels beyond this = no match
MARGIN_MIN = 6.0         # runner-up must trail by this, or the cell is UNKNOWN
MAX_EXEMPLARS = 400      # per glyph; evenly spread through the clip
HOLD = 1                 # a label is trusted only if the pad held +-HOLD frames

# -- aligning -------------------------------------------------------------
BAND = 12                # candidate frames either side of the prior map
MAXSTEP = 4              # frames one slot may advance past the previous
SKIP_COST = 0.35         # per frame skipped beyond +1
DWELL_ACROSS_COST = 3.0  # a NEW picture showing the same frame again
TETHER = 0.0002          # per frame away from the prior: a tie-break only
STEP_TETHER = 0.3        # per frame the step differs from the prior's step
HOLE_COST = 0.5          # per known cell, on a frame the track never captured
RESTART_COST = 5.0       # the prior jumped (a reset, a mis-stamped segment)
MIN_SURE_SLOTS = 30      # a clip that reads fewer whole rows refuses
REFERENCE = "pad_glyphs_us.npz"


def glyphs_of(value: int, row: str) -> tuple[str, str, str]:
    """(letter, d1, d2) as Usamune prints one axis: a bare '0' at rest,
    else the direction letter and the magnitude's digits, left-aligned."""
    if value == 0:
        return ("", "0", "")
    letter = LETTERS[row][0] if value > 0 else LETTERS[row][1]
    digits = str(abs(int(value)))
    return (letter, digits[0], digits[1] if len(digits) > 1 else "")


def truth_glyphs(pad, row: str) -> tuple[str, str, str] | None:
    """The glyphs the track implies, or None for a frame nobody captured."""
    if pad is None:
        return None
    return glyphs_of(pad[1] if row == "y" else pad[0], row)


# -- pixels -----------------------------------------------------------------
def decode_cells(ffmpeg: str, clip: Path, width: int | None = None,
                 height: int | None = None) -> np.ndarray:
    """Every video frame's readout region, scaled to CELL_W x CELL_H."""
    if width is None or height is None:
        probe = subprocess.run(
            [ffmpeg.replace("ffmpeg", "ffprobe"), "-v", "error",
             "-select_streams", "v", "-show_entries", "stream=width,height",
             "-of", "csv=p=0", str(clip)], capture_output=True, text=True)
        first = probe.stdout.strip().splitlines()[0]        # a .ts lists a stream per line
        width, height = (int(v) for v in first.split(",")[:2])
    x0, y0, x1, y1 = REGION
    x, y = round(width * x0), round(height * y0)
    w, h = round(width * (x1 - x0)), round(height * (y1 - y0))
    out = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(clip),
         "-vf", f"crop={w}:{h}:{x}:{y},scale={CELL_W}:{CELL_H}:flags=area",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
        capture_output=True).stdout
    stride = CELL_W * CELL_H * 3
    count = len(out) // stride
    return np.frombuffer(out[:count * stride], dtype=np.uint8).reshape(
        count, CELL_H, CELL_W, 3)


def cut(cells: np.ndarray, row: str, col: str) -> np.ndarray:
    r0, r1 = ROWS[row]
    c0, c1 = COLS[col]
    return cells[..., r0:r1, c0:c1, :]


def ink_mask(img: np.ndarray) -> np.ndarray:
    """Pixels the HUD font itself paints: fiery digits (orange, red,
    yellow), the silver U, the blue D and R, the gold L, white highlights,
    dark outlines. Measured off his clips; the world behind the glyphs
    wears these rarely, and a template only ever weights pixels the
    exemplars AGREE on, so a coincidence in one frame cannot enter."""
    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    fire = (r > 200) & (b < 90) & (r - b > 120)
    outline = (r > 110) & (g < 70) & (b < 70)
    bright = (r > 170) & (g > 170) & (b > 170)
    blue = (b > 170) & (b - r > 110)
    dark = (r < 60) & (g < 60) & (b < 60)
    return fire | outline | bright | blue | dark


# -- templates ----------------------------------------------------------------
@dataclass(frozen=True)
class Template:
    median: np.ndarray       # (h, w, 3) float32 -- the glyph's own pixels
    weight: np.ndarray       # (h, w) float32, 1 where the font paints
    exemplars: int


Alphabet = dict  # (row, col) -> {glyph: Template}


def learn(cells: np.ndarray, labels: dict) -> Alphabet:
    """One template per painted glyph per cell, from labelled frames.

    `labels[(row, col)]` is a per-frame glyph string or None. The median
    over exemplars survives a minority of wrong labels and occluded
    frames; a pixel is weighted only where the exemplars agree AND the
    median colour is one the font paints -- so a uniform background
    behind every sighting can never be mistaken for the glyph (the
    title screen's flat blue did exactly that to a mean-and-std draft).
    The blank glyph learns nothing: a cell is blank when no template
    matches, and that is called UNKNOWN, never read.
    """
    alphabet: Alphabet = {}
    for key in KEYS:
        row, col = key
        imgs = cut(cells, row, col)
        groups: dict[str, list[int]] = {}
        for index, glyph in enumerate(labels.get(key, ())):
            if glyph:
                groups.setdefault(glyph, []).append(index)
        table = {}
        for glyph, members in groups.items():
            if len(members) < MIN_EXEMPLARS:
                continue
            chosen = np.array(members)
            if len(chosen) > MAX_EXEMPLARS:
                chosen = chosen[np.linspace(0, len(chosen) - 1,
                                            MAX_EXEMPLARS).astype(int)]
            stack = imgs[chosen].astype(np.float32)
            median = np.median(stack, axis=0)
            spread = np.median(np.abs(stack - median), axis=0).mean(axis=-1) * 1.4826
            weight = ((spread < SPREAD_MAX) & ink_mask(median)).astype(np.float32)
            if weight.sum() < MIN_WEIGHT:
                continue
            table[glyph] = Template(median, weight, len(chosen))
        alphabet[key] = table
    return alphabet


def merge(base: Alphabet, learned: Alphabet) -> Alphabet:
    """The clip's own glyphs where it learned them, the reference elsewhere."""
    out: Alphabet = {}
    for key in KEYS:
        table = dict(base.get(key, {}))
        table.update(learned.get(key, {}))
        out[key] = table
    return out


def save_alphabet(alphabet: Alphabet, path: Path) -> None:
    arrays = {}
    for (row, col), table in alphabet.items():
        for glyph, t in table.items():
            arrays[f"{row}/{col}/{glyph}/median"] = t.median.astype(np.float16)
            arrays[f"{row}/{col}/{glyph}/weight"] = t.weight.astype(bool)
            arrays[f"{row}/{col}/{glyph}/n"] = np.array(t.exemplars)
    np.savez_compressed(path, **arrays)


def load_alphabet(path: Path | None = None) -> Alphabet:
    """The reference alphabet shipped with the package (or one from a file)."""
    if path is None:
        path = resources.files("sm64_events.data") / REFERENCE
    with np.load(path) as data:
        out: Alphabet = {key: {} for key in KEYS}
        for name in data.files:
            row, col, glyph, part = name.split("/")
            if part != "median":
                continue
            out[(row, col)][glyph] = Template(
                data[name].astype(np.float32),
                data[f"{row}/{col}/{glyph}/weight"].astype(np.float32),
                int(data[f"{row}/{col}/{glyph}/n"]))
    return out


# -- reading ------------------------------------------------------------------
@dataclass(frozen=True)
class CellReads:
    names: list            # best glyph per frame
    dist: np.ndarray       # its distance
    margin: np.ndarray     # runner-up minus best
    known: np.ndarray      # bool: read by a clear margin


def read(cells: np.ndarray, alphabet: Alphabet) -> dict:
    """Per (row, col): what every frame's cell says, and whether it is sure."""
    count = len(cells)
    out = {}
    for key in KEYS:
        table = alphabet.get(key, {})
        names = list(table)
        if not names:
            out[key] = CellReads([""] * count, np.full(count, np.inf),
                                 np.zeros(count), np.zeros(count, bool))
            continue
        imgs = cut(cells, *key).astype(np.float32)
        dists = np.zeros((count, len(names)), np.float32)
        for gi, glyph in enumerate(names):
            t = table[glyph]
            diff = np.abs(imgs - t.median).mean(axis=-1) * t.weight
            dists[:, gi] = diff.sum(axis=(1, 2)) / max(float(t.weight.sum()), 1.0)
        order = np.argsort(dists, axis=1)
        best = dists[np.arange(count), order[:, 0]]
        second = (dists[np.arange(count), order[:, 1]] if len(names) > 1
                  else np.full(count, np.inf))
        margin = second - best
        known = (best <= DIST_MAX) & (margin >= MARGIN_MIN)
        out[key] = CellReads([names[i] for i in order[:, 0]], best, margin, known)
    return out


# -- labels -----------------------------------------------------------------
def labels_from(frame_map: list, pads, hold: int = HOLD) -> dict:
    """Per cell, the glyph each frame SHOULD show under `frame_map`, or None
    where the pad was not held `hold` frames either side (a small map
    error there would teach the wrong glyph)."""
    labels: dict = {key: [None] * len(frame_map) for key in KEYS}
    for index, raw in enumerate(frame_map):
        if raw is None:
            continue
        pad = pads.get(raw)
        if pad is None or any(pads.get(raw + k) != pad
                              for k in range(-hold, hold + 1)):
            continue
        for row in ROWS:
            glyphs = glyphs_of(pad[1] if row == "y" else pad[0], row)
            for ci, col in enumerate(COLS):
                labels[(row, col)][index] = glyphs[ci]
    return labels


# -- aligning -----------------------------------------------------------------
def _filled(frame_map: list) -> list:
    """The prior with its holes closed: an interior hole is interpolated
    between its answered neighbours (so the prior's STEPS survive it), and
    a hole at either end carries the nearest answer."""
    out = list(frame_map)
    answered = [i for i, v in enumerate(out) if v is not None]
    if not answered:
        return out
    for i in range(answered[0]):
        out[i] = out[answered[0]]
    for i in range(answered[-1] + 1, len(out)):
        out[i] = out[answered[-1]]
    for left, right in zip(answered, answered[1:]):
        for i in range(left + 1, right):
            out[i] = round(out[left] + (out[right] - out[left])
                           * (i - left) / (right - left))
    return out


def align(reads: dict, frame_map: list, pads, same_picture=None,
          changed=None, held=None, icons=None, anchors=None) -> list | None:
    """The monotone path of game frames the read cells vote for.

    States per slot: raw frames within BAND of the prior. Moving from one
    slot to the next may dwell (+0), advance (+1) or skip (+2..MAXSTEP at
    SKIP_COST per extra frame); a slot known to show the SAME picture as
    the last (`same_picture[k]`) may only dwell, and one that is a NEW
    picture (`changed[k]`) pays DWELL_ACROSS_COST to dwell -- the game
    advanced one frame per picture, so a prior that is frames out is
    corrected by the whole path shifting, never by stalling the frame
    counter until the display catches up. Emission: one unit per known
    cell whose glyph the candidate frame's pad contradicts, HOLE_COST per
    known cell on a frame the track never captured. Where the pictures
    say nothing, STEP_TETHER prefers the step the prior took between the
    same two slots (the clocks know where capture dropped a frame even
    when their absolute answer has wandered), and TETHER is a tie-break
    toward the prior's position. A prior that jumps (a reset, a bad
    stamp) restarts the chain at RESTART_COST rather than breaking it.
    """
    prior = _filled(frame_map)
    count = len(prior)
    if count == 0 or prior[0] is None:
        return None
    prior_step = [0] + [min(max(prior[k] - prior[k - 1], 0), MAXSTEP)
                        for k in range(1, count)]
    truth_cache: dict = {}

    def truths(raw):
        if raw not in truth_cache:
            pad = pads.get(raw)
            truth_cache[raw] = None if pad is None else {
                (row, col): glyphs_of(pad[1] if row == "y" else pad[0], row)[ci]
                for row in ROWS for ci, col in enumerate(COLS)}
        return truth_cache[raw]

    def emission(slot, raw):
        cost = TETHER * abs(raw - prior[slot])
        truth = truths(raw)
        for key, cell in reads.items():
            if not cell.known[slot]:
                continue
            if truth is None:
                cost += HOLE_COST
            elif truth[key] != cell.names[slot]:
                cost += 1.0
        # The button icons: how many are lit on this picture must be how
        # many buttons the candidate frame holds (`held[raw]`).
        if icons is not None and held is not None and icons[slot] is not None:
            count = held.get(raw)
            if count is None:
                cost += HOLE_COST
            elif count != icons[slot]:
                cost += 1.0
        # The reset's white flash: its first picture shows the reset frame.
        if anchors and slot in anchors and anchors[slot] != raw:
            cost += ANCHOR_COST
        return cost

    best_prev = states_prev = None
    back = []
    for slot in range(count):
        states = np.arange(prior[slot] - BAND, prior[slot] + BAND + 1)
        em = np.array([emission(slot, int(raw)) for raw in states])
        if best_prev is None:
            cost, parent = em, np.full(len(states), -1)
        else:
            cost = np.full(len(states), np.inf)
            parent = np.full(len(states), -1)
            dwell_only = bool(same_picture is not None and same_picture[slot])
            new_picture = bool(changed is not None and changed[slot])
            restart = int(np.argmin(best_prev))
            for si, raw in enumerate(states):
                steps = raw - states_prev
                hi = 0 if dwell_only else MAXSTEP
                cands = np.where((steps >= 0) & (steps <= hi))[0]
                if len(cands) == 0:
                    cost[si] = best_prev[restart] + RESTART_COST + em[si]
                    parent[si] = restart
                    continue
                # Across a picture boundary the game advanced exactly one
                # frame unless the capture dropped some (+2.. at SKIP_COST
                # each); staying on the same frame for a NEW picture is
                # not physical and priced accordingly. Inside a picture
                # only dwelling exists. Where nothing says which, the
                # path prefers the STEP the prior took here -- the clocks
                # know where capture dropped a frame even when their
                # absolute answer has wandered.
                moved = steps[cands]
                extra = np.where(moved >= 2, SKIP_COST * (moved - 1), 0.0)
                if new_picture:
                    extra = extra + np.where(moved == 0, DWELL_ACROSS_COST, 0.0)
                elif not dwell_only:
                    extra = extra + STEP_TETHER * np.abs(moved - prior_step[slot])
                total = best_prev[cands] + extra
                k = int(np.argmin(total))
                cost[si] = total[k] + em[si]
                parent[si] = cands[k]
        back.append((states, parent))
        best_prev, states_prev = cost, states
    path = [0] * count
    si = int(np.argmin(best_prev))
    for slot in range(count - 1, -1, -1):
        states, parent = back[slot]
        path[slot] = int(states[si])
        si = int(parent[si])
    return path


# -- the verdict ----------------------------------------------------------------
@dataclass
class Verdict:
    sure: int = 0            # slots where a whole row read (letter, d1, d2)
    agree: int = 0           # ...and the aligned frame's pad matches it
    nowhere: int = 0         # ...and NO frame within +-10 matches (a misread)
    known_cells: int = 0     # cells read across all slots
    slots: int = 0
    disagreements: list = field(default_factory=list)   # (slot, row, read, map says)
    icons_checked: int = 0   # slots where the lit-icon count was clear
    icons_agree: int = 0     # ...and equals the held-button count on the aligned frame
    anchors: dict = field(default_factory=dict)   # white-flash slot -> [reset frame, frame chosen]

    @property
    def agreement(self) -> float:
        return self.agree / self.sure if self.sure else 0.0

    def as_dict(self) -> dict:
        return {"sure": self.sure, "agree": self.agree, "nowhere": self.nowhere,
                "known_cells": self.known_cells, "slots": self.slots,
                "icons_checked": self.icons_checked, "icons_agree": self.icons_agree,
                "anchors": {str(k): v for k, v in self.anchors.items()},
                "disagreements": [list(d) for d in self.disagreements[:200]]}


def score(reads: dict, path: list, pads) -> Verdict:
    """How many slots the display confirms, and which it contradicts."""
    verdict = Verdict(slots=len(path))
    verdict.known_cells = int(sum(cell.known.sum() for cell in reads.values()))
    for row in ROWS:
        letter, d1, d2 = (reads[(row, col)] for col in COLS)
        for slot, raw in enumerate(path):
            if not (letter.known[slot] and d1.known[slot] and d2.known[slot]):
                continue
            seen = (letter.names[slot], d1.names[slot], d2.names[slot])
            verdict.sure += 1
            if truth_glyphs(pads.get(raw), row) == seen:
                verdict.agree += 1
                continue
            near = {truth_glyphs(pads.get(raw + k), row) for k in range(-10, 11)}
            if seen not in near:
                verdict.nowhere += 1
            says = truth_glyphs(pads.get(raw), row)
            verdict.disagreements.append(
                (slot, row, "".join(seen), "".join(says) if says else "no capture"))
    return verdict


# -- the reset's white flash --------------------------------------------------------
# A Usamune reset reloads the level behind a white flash, and MEASURED on
# his clip 5534 (2026-09-01) the first white picture IS the spawn frame --
# the attempt's anchor: three white frames from the spawn, then the fade-in,
# with his C-down on the first faded-in picture pinning the count. Nothing
# else in a reset's neighbourhood is readable (the digits are washed out,
# the stick rests through the fall), which is exactly where the clocks'
# answer drifted a frame (his frames 6/7). So each white run's first slot
# is evidence that it shows the reset frame the journal recorded.
WHITE_MIN = 235            # every channel of the readout region above this: the flash
ANCHOR_COST = 2.0          # a candidate frame that is not the reset's, on its white slot


def white_slots(cells: np.ndarray) -> np.ndarray:
    """Per frame: is the readout region a wash of white (the reset flash)."""
    return cells.min(axis=(1, 2, 3)) > WHITE_MIN


def flash_anchors(cells: np.ndarray, frame_map: list, resets) -> dict:
    """slot -> reset frame, for the first slot of each white run whose prior
    lies within BAND of a reset frame the journal recorded."""
    anchors: dict = {}
    if not resets:
        return anchors
    prior = _filled(list(frame_map))
    white = white_slots(cells)
    for slot in range(len(white)):
        if not white[slot] or (slot > 0 and white[slot - 1]):
            continue
        guess = prior[slot] if slot < len(prior) else None
        if guess is None:
            continue
        nearest = min(resets, key=lambda raw: abs(raw - guess))
        if abs(nearest - guess) <= BAND:
            anchors[slot] = nearest
    return anchors


# -- the button icons ---------------------------------------------------------------
# Usamune draws one icon per HELD button, packed left to right in a strip
# to the right of the digits, and MEASURED on his clip 5534 (2026-09-01) the
# icons are on/off with the pad -- a press paints the icon in full on the
# press frame and a release removes it on the release frame, no fade. So
# the number of lit icons on a picture is the number of buttons held on
# its frame: evidence that would pin a press or a release even while the
# stick rests. `align` accepts that evidence (`held`, `icons`) and it is
# tested; `icon_counts` below is the first INSTRUMENT for it and it is NOT
# wired: measured on clip 5534 it agreed with the track on 858 of 1,034
# clear slots (83%) -- scenery behind the strip lit every position (a
# bob-omb, a wall: held none, "4 lit" x35), and an orange C icon over
# orange sand read as empty (held C-right, "0 lit" x15). A background-
# difference test cannot tell an icon from the world; the next instrument
# needs the icons' own templates, learned like the digits, and the wire-in
# gate is the same 99% the digits met.
ICON_STRIP = (0.2475, 0.830, 0.4975, 0.902)       # x0, y0, x1, y1 fractions
ICON_PITCH = 95 / 1600                             # one packed position to the next
ICON_WIDTH = 80 / 1600
ICON_POSITIONS = 4
ICON_REF_BAND = (0.2475, 0.820, 0.4975, 0.828)     # the strip's own background, same frame
ICON_LIT_MIN = 0.30       # share of a position differing from the background: lit
ICON_EMPTY_MAX = 0.04     # ...below this: empty; between = unknown
ICON_DIFF = 40            # per-pixel mean |RGB diff| that counts as icon paint


def icon_counts(ffmpeg: str, clip: Path, width: int | None = None,
                height: int | None = None) -> list:
    """Per video frame: how many button icons are lit, or None when a
    position is neither clearly lit nor clearly empty (a wash-out, a fade
    from white). Counted left to right until the first empty position."""
    if width is None or height is None:
        probe = subprocess.run(
            [ffmpeg.replace("ffmpeg", "ffprobe"), "-v", "error",
             "-select_streams", "v", "-show_entries", "stream=width,height",
             "-of", "csv=p=0", str(clip)], capture_output=True, text=True)
        first = probe.stdout.strip().splitlines()[0]
        width, height = (int(v) for v in first.split(",")[:2])
    x0, y0, x1, y1 = ICON_STRIP
    rx0, ry0, rx1, ry1 = ICON_REF_BAND
    x, y = round(width * x0), round(height * ry0)
    w, h = round(width * (x1 - x0)), round(height * (y1 - ry0))
    out = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(clip),
         "-vf", f"crop={w}:{h}:{x}:{y}", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "pipe:1"], capture_output=True).stdout
    stride = w * h * 3
    count = len(out) // stride
    if count == 0:
        return []
    strip = np.frombuffer(out[:count * stride], dtype=np.uint8).reshape(
        count, h, w, 3).astype(np.int16)
    band_h = round(height * (ry1 - ry0))
    reference = np.median(strip[:, :band_h].reshape(count, -1, 3), axis=1)   # (N, 3)
    icons_y0 = round(height * (y0 - ry0))
    pitch, wide = round(width * ICON_PITCH), round(width * ICON_WIDTH)
    counts: list = []
    for k in range(count):
        lit = 0
        verdict: int | None = None
        for position in range(ICON_POSITIONS):
            cx = position * pitch
            cell = strip[k, icons_y0:, cx:cx + wide]
            if cell.shape[1] < wide // 2:
                break
            share = float((np.abs(cell - reference[k]).mean(axis=-1) > ICON_DIFF).mean())
            if share >= ICON_LIT_MIN:
                lit += 1
                continue
            if share <= ICON_EMPTY_MAX:
                verdict = lit
            break
        else:
            verdict = lit
        counts.append(verdict)
    return counts


# -- one clip, end to end -----------------------------------------------------------
@dataclass(frozen=True)
class PadReading:
    frame_map: list
    verdict: Verdict
    learned: dict            # (row, col) -> glyphs the clip taught itself


def picture_flags(ffmpeg: str, clip: Path, cells: np.ndarray):
    """(same_picture, changed) per slot. Same = the whole frame repeats the
    previous one (the quantiser's own picture runs); changed = the readout
    region itself differs, which is a new frame however still the world."""
    from sm64_events.replay.mapalign import decode_grey, picture_runs
    count = len(cells)
    same = np.zeros(count, bool)
    try:
        runs = picture_runs(decode_grey(ffmpeg, clip))
    except Exception:
        runs = []
    for start, end in runs:
        same[start + 1:min(end + 1, count)] = True
    diff = np.abs(cells[1:].astype(np.int16) - cells[:-1].astype(np.int16)).mean(axis=(1, 2, 3))
    changed = np.concatenate([[False], diff > 1.0])
    same &= ~changed
    return same, changed


def read_clip(clip: Path, frame_map: list, pads, ffmpeg: str,
              reference: Alphabet | None = None,
              held=None, icons=None, resets=None) -> PadReading | None:
    """Read the clip's display and pin its map to it. None = refused.

    Two passes: the reference alphabet reads first (no labels needed, so
    a prior that is frames out cannot mislead the first templates), the
    path it yields labels every frame, and the clip re-learns its own
    glyphs at its own scale before the final read and alignment.
    """
    if not frame_map or all(v is None for v in frame_map):
        return None
    cells = decode_cells(ffmpeg, clip)
    if len(cells) == 0:
        return None
    count = min(len(cells), len(frame_map))
    cells, frame_map = cells[:count], list(frame_map[:count])
    same, changed = picture_flags(ffmpeg, clip, cells)
    if icons is not None:
        icons = list(icons[:count]) + [None] * max(0, count - len(icons))
    anchors = flash_anchors(cells, frame_map, resets)
    alphabet = reference if reference is not None else load_alphabet()
    path = frame_map
    learned: dict = {}
    for _ in range(2):
        reads = read(cells, alphabet)
        path = align(reads, path, pads, same, changed, held, icons, anchors)
        if path is None:
            return None
        own = learn(cells, labels_from(path, pads))
        learned = {key: sorted(table) for key, table in own.items() if table}
        alphabet = merge(alphabet, own)
    reads = read(cells, alphabet)
    path = align(reads, path, pads, same, changed, held, icons, anchors)
    verdict = score(reads, path, pads)
    verdict.anchors = {int(slot): [int(raw), int(path[slot])] for slot, raw in anchors.items()}
    if icons is not None and held is not None:
        checked = [(slot, raw) for slot, raw in enumerate(path)
                   if icons[slot] is not None and held.get(raw) is not None]
        verdict.icons_checked = len(checked)
        verdict.icons_agree = sum(1 for slot, raw in checked if icons[slot] == held[raw])
    if verdict.sure < MIN_SURE_SLOTS:
        log.info("pad reader refused: %d sure slots of %d", verdict.sure, count)
        return None
    return PadReading(path, verdict, learned)
