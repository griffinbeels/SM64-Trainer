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
def _clip_dims(ffmpeg: str, clip: Path) -> tuple[int, int]:
    """The clip's pixel size off the ffprobe that ships beside `ffmpeg` -- by
    FILE name, never a whole-path replace (that renamed the D:/ffmpeg install
    FOLDER and read nothing; the same catalogued hop-6 bug, and it reached
    here too)."""
    from sm64_events.replay.extract import ffprobe_beside
    ffprobe = ffprobe_beside(ffmpeg) or "ffprobe"
    probe = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(clip)],
        capture_output=True, text=True, check=False)
    first = probe.stdout.strip().splitlines()[0]        # a .ts lists a stream per line
    width, height = (int(v) for v in first.split(",")[:2])
    return width, height


def _decode_region(ffmpeg: str, clip: Path, region, out_w: int, out_h: int,
                   width: int | None, height: int | None) -> np.ndarray:
    """One scaled cell per STORED video frame -- `-fps_mode passthrough` so a
    VFR picture-feed clip (item 38) is NOT re-timed onto its r_frame_rate
    grid. Without it ffmpeg duplicated 8 frames of a 718-frame clip (726 out),
    the reader kept the first 718, and every cell past the first duplicate was
    shifted -- a uniform ~2-frame lag between the panel and the screen
    (diagnosed 2026-09-02: the reader's cell axis must BE the clip's frame
    axis, which is the browser's slot axis)."""
    if width is None or height is None:
        width, height = _clip_dims(ffmpeg, clip)
    x0, y0, x1, y1 = region
    x, y = round(width * x0), round(height * y0)
    w, h = round(width * (x1 - x0)), round(height * (y1 - y0))
    out = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(clip),
         "-vf", f"crop={w}:{h}:{x}:{y},scale={out_w}:{out_h}:flags=area",
         "-fps_mode", "passthrough",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
        capture_output=True).stdout
    stride = out_w * out_h * 3
    count = len(out) // stride
    return np.frombuffer(out[:count * stride], dtype=np.uint8).reshape(
        count, out_h, out_w, 3)


def decode_cells(ffmpeg: str, clip: Path, width: int | None = None,
                 height: int | None = None) -> np.ndarray:
    """Every stored video frame's readout region, scaled to CELL_W x CELL_H."""
    return _decode_region(ffmpeg, clip, REGION, CELL_W, CELL_H, width, height)


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
        # The button icons: every button read LIT on this picture must be
        # held on the candidate frame (`held[raw]` is its button bits).
        if icons is not None and held is not None and icons[slot]:
            bits = held.get(raw)
            for bit in icons[slot]:
                if bits is None:
                    cost += HOLE_COST
                elif not bits & bit:
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
    icons_checked: int = 0   # slots with at least one icon read lit
    icons_agree: int = 0     # ...and every lit button is held on the aligned frame
    icons_learned: list = field(default_factory=list)   # button bits the clip taught
    anchors: dict = field(default_factory=dict)   # white-flash slot -> [reset frame, frame chosen]
    frames_total: int = 0    # distinct game frames the clip shows
    frames_checked: int = 0  # ...on which the display could be checked
    frames_agree: int = 0    # ...and agreed on every checkable picture
    unpinned_longest: int = 0  # longest run of frames the display cannot tell apart

    @property
    def agreement(self) -> float:
        return self.agree / self.sure if self.sure else 0.0

    def as_dict(self) -> dict:
        return {"sure": self.sure, "agree": self.agree, "nowhere": self.nowhere,
                "known_cells": self.known_cells, "slots": self.slots,
                "icons_checked": self.icons_checked, "icons_agree": self.icons_agree,
                "icons_learned": list(self.icons_learned),
                "frames_total": self.frames_total, "frames_checked": self.frames_checked,
                "frames_agree": self.frames_agree,
                "unpinned_longest": self.unpinned_longest,
                "anchors": {str(k): v for k, v in self.anchors.items()},
                "disagreements": [list(d) for d in self.disagreements[:200]]}


def score(reads: dict, path: list, pads) -> Verdict:
    """How many slots the display confirms, and which it contradicts."""
    verdict = Verdict(slots=len(path))
    verdict.known_cells = int(sum(cell.known.sum() for cell in reads.values()))
    # A picture is CHECKABLE on a row when its magnitude digit read; the
    # letter and the second digit are compared where they read and pass
    # where they did not (a bare '0' and a single digit have blank cells,
    # which no template can read -- and until 2026-09-01 those rows never
    # counted at all, so a screen showing R2 over a track holding 0 was
    # "checked" by omission; his Log Rolling report). Counted per VIDEO
    # frame, never per row: two rows on one picture are one check.
    frames_checked: set = set()
    frames_agreed: set = set()
    for slot, raw in enumerate(path):
        checked = False
        for row in ROWS:
            letter, d1, d2 = (reads[(row, col)] for col in COLS)
            if not d1.known[slot]:
                continue
            truth = truth_glyphs(pads.get(raw), row)
            seen = (letter.names[slot] if letter.known[slot] else None,
                    d1.names[slot],
                    d2.names[slot] if d2.known[slot] else None)
            checked = True
            matches = truth is not None and all(
                want is None or want == got for want, got in zip(seen, truth, strict=True))
            if matches:
                continue
            near = [truth_glyphs(pads.get(raw + k), row) for k in range(-10, 11)]
            anywhere = any(t is not None and all(w is None or w == g for w, g in zip(seen, t, strict=True))
                           for t in near)
            if not anywhere:
                verdict.nowhere += 1
            shown = "".join(g for g in seen if g)
            verdict.disagreements.append(
                (slot, row, shown, "".join(truth) if truth else "no capture"))
            frames_checked.add(slot)
            break
        else:
            if checked:
                frames_checked.add(slot)
                frames_agreed.add(slot)
    verdict.sure = len(frames_checked)
    verdict.agree = len(frames_agreed)
    # The same verdict in the TIMELINE's unit -- game frames -- so the chip
    # and the header count the same thing (his 2026-09-01 question: three
    # different frame numbers on one surface).
    shown = {raw for raw in path if raw is not None}
    verdict.frames_total = len(shown)
    verdict.frames_checked = len({path[slot] for slot in frames_checked})
    verdict.frames_agree = len({path[slot] for slot in frames_agreed}
                               - {path[slot] for slot in frames_checked - frames_agreed})
    # EXPOSURE (fresh-context review, 2026-09-01): a frame is PINNED only
    # when the display could be checked on it AND its pad differs from the
    # frame before -- inside a hold every neighbour reads the same, so the
    # display cannot say which of them a picture is, and a map shifted
    # inside the hold reads exactly as well as the right one (measured:
    # +2 across 120 slots, verdict unchanged). The longest unpinned run is
    # the number the chip owes him beside the agreement.
    checked_frames = {path[slot] for slot in frames_checked}
    longest = run = 0
    previous = None
    for raw in sorted(shown):
        pad = pads.get(raw)
        pinned = raw in checked_frames and pad is not None and pad != previous
        run = 0 if pinned else run + 1
        longest = max(longest, run)
        previous = pad
    verdict.unpinned_longest = longest
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
# Usamune lights one icon per HELD button in a strip right of the digits,
# packed left to right, and MEASURED on his clip 5534 (2026-09-01) the icons
# are on/off with the pad: full paint on the press frame, gone on the release
# frame, no fade. A lit icon is therefore evidence that its button is held on
# the picture's frame -- evidence that pins a press while the stick rests or
# holds still, which is exactly where the digits say nothing (his C-down on
# the frame after a reset; his A and B inside the pyramid at frames 746/771,
# the stick pinned at U84 the whole time).
#
# Read like the digits: one template per BUTTON, learned from the clip's own
# single-button frames at the first position (the icon is the same bitmap at
# every position), weighted where the exemplars agree and the colour is the
# icon's own; a position reads a button only by a clear margin, else nothing.
# Only LIT icons are evidence. An empty position is not read -- the first
# instrument tried that with a background-difference test and scored 83%
# (scenery lit every position; an orange C over sand read empty) -- so a
# release pins nothing and the next press or stick change does.
ICON_STRIP = (0.2475, 0.830, 0.485, 0.902)         # x0, y0, x1, y1 fractions of the frame
ICON_W, ICON_H = 190, 44                            # the strip at 2.5 px per game px
ICON_PITCH, ICON_WIDTH = 47, 40                     # packed positions, at that scale
ICON_POSITIONS = 4
ICON_DIST_MAX = 40.0
ICON_MARGIN_MIN = 8.0
ICON_MIN_EXEMPLARS = 24   # sightings a button needs before its icon is trusted...
ICON_MIN_HOLDS = 3        # ...spread over this many separate presses (backgrounds)
ICON_MIN_WEIGHT = 400     # painted pixels a template must own...
ICON_MIN_SATURATED = 200  # ...of which this many are the icon's own fill colour
ICON_REFERENCE = "pad_icons_us.npz"


def decode_icons(ffmpeg: str, clip: Path, width: int | None = None,
                 height: int | None = None) -> np.ndarray:
    """Every stored video frame's icon strip, scaled to ICON_W x ICON_H."""
    return _decode_region(ffmpeg, clip, ICON_STRIP, ICON_W, ICON_H, width, height)


def icon_cell(strip: np.ndarray, position: int) -> np.ndarray:
    left = position * ICON_PITCH
    return strip[..., :, left:left + ICON_WIDTH, :]


def icon_paint(img: np.ndarray) -> np.ndarray:
    """Pixels an icon paints: a saturated fill (A blue, B green, the C
    buttons orange), a white core, or the dark outline every icon wears."""
    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    top, bottom = np.maximum(np.maximum(r, g), b), np.minimum(np.minimum(r, g), b)
    # No "dark outline" clause: a dark corridor is dark everywhere, and six
    # Z sightings from one such stretch agreed on the background, so the
    # template read the corridor as Z on 982 slots (measured on 5574).
    return (top - bottom > 70) | (bottom > 190)


def icon_labels_from(path: list, held) -> list:
    """Per slot: the ONE button bit held on the aligned frame (and its
    neighbours), else None -- the frames whose first position shows a
    known icon."""
    labels = [None] * len(path)
    for index, raw in enumerate(path):
        if raw is None:
            continue
        bits = held.get(raw)
        if not bits or bits & (bits - 1):                 # none, or more than one
            continue
        if any(held.get(raw + k) != bits for k in (-1, 1)):
            continue
        labels[index] = bits
    return labels


def learn_icons(strip: np.ndarray, labels: list) -> dict:
    """button bit -> Template, from single-button frames at position 0."""
    cells = icon_cell(strip, 0)
    groups: dict = {}
    for index, bit in enumerate(labels):
        if bit:
            groups.setdefault(bit, []).append(index)
    out: dict = {}
    for bit, members in groups.items():
        holds = 1 + sum(1 for a, b in zip(members, members[1:], strict=False) if b - a > 1)
        if len(members) < ICON_MIN_EXEMPLARS or holds < ICON_MIN_HOLDS:
            continue                  # one long press teaches its background, not the icon
        chosen = np.array(members)
        if len(chosen) > MAX_EXEMPLARS:
            chosen = chosen[np.linspace(0, len(chosen) - 1, MAX_EXEMPLARS).astype(int)]
        stack = cells[chosen].astype(np.float32)
        median = np.median(stack, axis=0)
        spread = np.median(np.abs(stack - median), axis=0).mean(axis=-1) * 1.4826
        weight = ((spread < SPREAD_MAX) & icon_paint(median)).astype(np.float32)
        # An icon template must be MOSTLY its saturated fill: the grey Z
        # left only its white core (327 px) and that matched sand and walls
        # on 452 slots of 5782 where Z was not held. A weak template is no
        # template -- the button stays unread rather than misread.
        top = np.maximum(np.maximum(median[..., 0], median[..., 1]), median[..., 2])
        bottom = np.minimum(np.minimum(median[..., 0], median[..., 1]), median[..., 2])
        saturated = float(((top - bottom > 70) & (weight > 0)).sum())
        if weight.sum() < ICON_MIN_WEIGHT or saturated < ICON_MIN_SATURATED:
            continue
        out[bit] = Template(median, weight, len(chosen))
    return out


def save_icons(templates: dict, path: Path) -> None:
    from sm64_events.memory import addresses as A
    names = {bit: name for bit, name in A.BUTTON_BITS}
    arrays = {}
    for bit, t in templates.items():
        arrays[f"{names[bit]}/median"] = t.median.astype(np.float16)
        arrays[f"{names[bit]}/weight"] = t.weight.astype(bool)
        arrays[f"{names[bit]}/n"] = np.array(t.exemplars)
    np.savez_compressed(path, **arrays)


def load_icons(path: Path | None = None) -> dict:
    """The reference icon templates shipped with the package: learned off
    his clips 5574 + 5782 (2026-09-01) and verified against their tracks."""
    if path is None:
        path = resources.files("sm64_events.data") / ICON_REFERENCE
    # Keys are button NAMES ("A/median"), resolved to bits through the one
    # table that names them -- a bit-keyed loader read "A" with int() and
    # the swallowed ValueError meant this channel had never once run
    # (fresh-context review, 2026-09-01, test T6).
    from sm64_events.memory import addresses as A
    bits = {name: bit for bit, name in A.BUTTON_BITS}
    out: dict = {}
    try:
        with np.load(path) as data:
            for key in data.files:
                name, part = key.split("/")
                if part != "median" or name not in bits:
                    continue
                out[bits[name]] = Template(data[key].astype(np.float32),
                                           data[f"{name}/weight"].astype(np.float32),
                                           int(data[f"{name}/n"]))
    except FileNotFoundError:
        return {}
    return out


def read_icons(strip: np.ndarray, templates: dict) -> list:
    """Per slot: the set of button bits whose icon is lit, read by a clear
    margin at any position. An empty set is no evidence."""
    count = len(strip)
    lit = [set() for _ in range(count)]
    if not templates:
        return lit
    bits = list(templates)
    for position in range(ICON_POSITIONS):
        cells = icon_cell(strip, position).astype(np.float32)
        if cells.shape[2] < ICON_WIDTH:
            break
        dists = np.zeros((count, len(bits)), np.float32)
        for gi, bit in enumerate(bits):
            t = templates[bit]
            diff = np.abs(cells - t.median).mean(axis=-1) * t.weight
            dists[:, gi] = diff.sum(axis=(1, 2)) / max(float(t.weight.sum()), 1.0)
        order = np.argsort(dists, axis=1)
        best = dists[np.arange(count), order[:, 0]]
        second = (dists[np.arange(count), order[:, 1]] if len(bits) > 1
                  else np.full(count, np.inf))
        known = (best <= ICON_DIST_MAX) & (second - best >= ICON_MARGIN_MIN)
        for index in np.where(known)[0]:
            lit[index].add(bits[order[index, 0]])
    return lit


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
    # picture_runs yields (first slot, LENGTH), not (start, end). Unpacking it
    # as an end made this `same[start + 1 : end + 1]`, which for a run of two
    # at slot 100 is `same[101:3]` -- EMPTY. So the "this picture is held, the
    # map may not advance" flag reached almost nothing, and the aligner walked
    # the map straight across duplicated pictures: his pyramid clip stepped +1
    # over 110 of its 115 held frames, so the panel's pad changed while the
    # screen did not (2026-09-02). Every slot after a run's first IS the same
    # picture.
    for start, length in runs:
        same[start + 1:min(start + length, count)] = True
    diff = np.abs(cells[1:].astype(np.int16) - cells[:-1].astype(np.int16)).mean(axis=(1, 2, 3))
    changed = np.concatenate([[False], diff > 1.0])
    same &= ~changed
    return same, changed


def read_clip(clip: Path, frame_map: list, pads, ffmpeg: str,
              reference: Alphabet | None = None,
              held=None, resets=None, repeats=None) -> PadReading | None:
    """Read the clip's display and pin its map to it. None = refused.

    `repeats` (per video frame, True = the sink re-fed the previous
    picture unchanged) comes from the picture feed's log: every other
    frame IS a new picture, so the picture flags are known rather than
    guessed from pixels, and the alignment may dwell only on a repeat.

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
    # The PICTURES decide what is the same picture -- never the feed log
    # alone. A picture-feed clip still holds DUPLICATE pictures: the game's
    # RAM frame advanced but the emulator re-presented the old render (a lag
    # frame), the two grabs differed by a few dithered pixels, so the ledger
    # filed two rows with advancing stamps for ONE picture. Measured on his
    # pyramid clip (5946, 2026-09-02): 115 of 775 stored frames pixel-identical
    # to their predecessor, 110 of which the map stepped +1 across -- the panel
    # advanced while the screen held. With the pixel flag, the alignment may
    # only dwell on such a slot, and the stepper (which walks to the next
    # DISTINCT map value) never lands on it. The feed log's heartbeat repeats
    # OR into the same flag.
    same, changed = picture_flags(ffmpeg, clip, cells)
    if repeats is not None:
        known = min(count, len(repeats))
        same[:known] |= np.asarray(list(repeats)[:known], bool)
        changed &= ~same
    anchors = flash_anchors(cells, frame_map, resets)
    alphabet = reference if reference is not None else load_alphabet()
    icons = None                      # the digits align first; icons join the final pass
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
    icons = None
    icon_alphabet: dict = {}
    if held is not None:
        # The digits' own path labels the single-button frames; the clip
        # teaches its icons from them and the final pass reads every
        # position -- a press inside a held-stick stretch then pins.
        try:
            strip = decode_icons(ffmpeg, clip)[:count]
            icon_alphabet = dict(load_icons())
            icon_alphabet.update(learn_icons(strip, icon_labels_from(path, held)))
            icons = read_icons(strip, icon_alphabet)
        except Exception:
            log.exception("icon read failed; aligning on the digits alone")
            icons = None
    path = align(reads, path, pads, same, changed, held, icons, anchors)
    verdict = score(reads, path, pads)
    verdict.anchors = {int(slot): [int(raw), int(path[slot])] for slot, raw in anchors.items()}
    if icons is not None and held is not None:
        checked = [(slot, raw) for slot, raw in enumerate(path) if icons[slot]]
        verdict.icons_checked = len(checked)
        verdict.icons_agree = sum(
            1 for slot, raw in checked
            if held.get(raw) is not None and all(held[raw] & bit for bit in icons[slot]))
        verdict.icons_learned = sorted(icon_alphabet)
    if verdict.sure < MIN_SURE_SLOTS:
        log.info("pad reader refused: %d sure slots of %d", verdict.sure, count)
        return None
    return PadReading(path, verdict, learned)
