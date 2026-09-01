# src/sm64_events/replay/pixelmap.py
"""SUPERSEDED 2026-09-01 by `replay/padread.py` (the pad reader): per-GLYPH
templates instead of the per-VALUE fingerprints below, which is what took
the reading from 84%/49% to 13,524 of 13,525 slots agreeing. Nothing wires
this module; it stays only until its deletion is his call.

The frame map, rebuilt from the FOOTAGE'S OWN PIXELS (map v3, round 32
item 26).

Why, measured: the chain is logic write -> render/present -> capture ->
feed -> encode. Map v1 stamped at the logic write (8/44 button changes
exact against Usamune's on-screen display); v2 stamped at capture and
recorded at feed (24/53). Each stamp point moved closer to the pixels and
the score rose -- but the game's logic->present wobble (render + vsync,
up to a frame, different every frame) is STRUCTURALLY invisible to
anything reading RAM, and his ruling is "100% or it can't be relied on".
The one source that cannot disagree with the footage is the footage:
Usamune's input display is IN the pixels.

HOW: read the display's VALUE per video slot, then fit the map to it.

1. LEARN. The display's two value fields ("U71" over "R70") sit at fixed
   screen positions -- PJ64 stretches the framebuffer linearly, so their
   boxes are fractions of the frame. Wherever the v2 prior shows one pad
   HELD for several slots, the field images there are labelled by the
   track's own pad (the prior cannot be a whole hold wrong -- its error
   is bounded at +-1 frame, measured). Values repeat constantly in play,
   so a clip teaches most of its own alphabet.
2. READ. Every slot's field images match against the learned templates:
   slot -> (stick_y string, stick_x string), unread where nothing is
   close. Buttons read by colour mass in the icon strip -- A is the blue
   blob, B the green, Z the bright grey -- with a dead band around the
   thresholds because Usamune FADES a released icon out, so release
   slots are honestly ambiguous rather than wrongly certain.
3. FIT. A banded DP walks the slots: each slot picks a game frame within
   +-BAND of the prior, non-decreasing, paying for every disagreement
   between what the slot READ and what the track says that frame's pad
   was. Unread components cost nothing. The result is the map that the
   pixels themselves vote for, with the prior only breaking ties.

Two earlier estimators are recorded here because their failure shaped
this one (both measured on his clip 778): snapping each pad boundary to
the nearest ink change moved 5/1109 slots -- while the stick moves there
is a change every other slot and "nearest" is a no-op; an offset curve
anchored on button icons reached 38-of-53 exact and regressed when
tuned, because a handful of anchors cannot see between themselves.
Reading VALUES is what removes the ambiguity both fell to.

If the display is off, covered, or the learned alphabet stays too small,
the fit REFUSES and the v2 map stands: a wrong map is worse than an
honest +-1.
"""
import logging
import subprocess
from dataclasses import dataclass

import numpy as np

log = logging.getLogger("sm64.replay")

# The two value fields (letter + digits) and the icon strip, as fractions
# of the frame (x0, y0, x1, y1). Measured on his 1600x1224 clips
# (2026-08-23); PJ64 scales the framebuffer linearly, so they hold.
FIELD_Y_REGION = (0.056, 0.756, 0.190, 0.826)      # "U71" line
FIELD_X_REGION = (0.053, 0.824, 0.187, 0.898)      # "R70" line
ICON_REGION = (0.238, 0.809, 0.444, 0.915)

# Fingerprint grid per field (the mask downsampled to this many cells).
GRID = (12, 32)
# A hold this long (slots) with a stable fingerprint labels a template.
LEARN_HOLD_SLOTS = 4
# A template must keep at least this many voting cells (see _learn).
MIN_VOTING_CELLS = 120
# A slot reads a value when no more than this share of the winning
# template's voting cells disagree.
READ_SHARE = 0.05
# The DP may move a slot at most this many game frames off the prior.
FIT_BAND_FRAMES = 2
# Refuse when fewer than this share of slots read at least one field.
MIN_READ_RATIO = 0.35

# Icon-strip colour masses (fraction of the strip) above which a button is
# SURELY drawn, and below which surely absent; between is the fade band.
ICON_SURE = {"A": 0.010, "B": 0.010, "Z": 0.012}
ICON_ABSENT = {"A": 0.003, "B": 0.003, "Z": 0.004}
BUTTON_BITS = {"A": 0x8000, "B": 0x4000, "Z": 0x2000}


def probe_dims(ffmpeg: str, clip_path) -> tuple[int, int]:
    probe = subprocess.run(
        [ffmpeg.replace("ffmpeg", "ffprobe"), "-v", "error",
         "-select_streams", "v", "-show_entries", "stream=width,height",
         "-of", "csv=p=0", str(clip_path)],
        capture_output=True, text=True)
    width, height = (int(value) for value in
                     probe.stdout.strip().split(",")[:2])
    return width, height


def decode_region(ffmpeg: str, clip_path, region,
                  dims: tuple[int, int] | None = None) -> np.ndarray:
    """Every video frame's crop of `region` (fractions), RGB, one array."""
    width, height = dims or probe_dims(ffmpeg, clip_path)
    x0, y0, x1, y1 = region
    x, y = round(width * x0), round(height * y0)
    w, h = round(width * (x1 - x0)), round(height * (y1 - y0))
    out = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(clip_path),
         "-vf", f"crop={w}:{h}:{x}:{y}", "-f", "rawvideo",
         "-pix_fmt", "rgb24", "pipe:1"], capture_output=True)
    frame_bytes = w * h * 3
    count = len(out.stdout) // frame_bytes
    frames = np.frombuffer(out.stdout[:count * frame_bytes], dtype=np.uint8)
    return frames.reshape(count, h, w, 3).astype(np.int16)


def field_fingerprints(frames: np.ndarray) -> np.ndarray:
    """Each slot's field as a coarse ink mask -- the digits are saturated
    orange/red with white cores and blue/silver letters, colours the world
    under them rarely wears."""
    red, green, blue = frames[..., 0], frames[..., 1], frames[..., 2]
    ink = ((red > 140) & (red - blue > 70)) | \
          ((blue > 130) & (blue - red > 50)) | \
          ((red > 170) & (green > 170) & (blue > 170))
    count, h, w = ink.shape
    rows, cols = GRID
    ys = np.linspace(0, h, rows + 1).astype(int)
    xs = np.linspace(0, w, cols + 1).astype(int)
    grid = np.zeros((count, rows, cols), dtype=bool)
    for gy in range(rows):
        for gx in range(cols):
            block = ink[:, ys[gy]:ys[gy + 1], xs[gx]:xs[gx + 1]]
            if block.shape[1] and block.shape[2]:
                grid[:, gy, gx] = block.mean(axis=(1, 2)) > 0.35
    return grid.reshape(count, -1)


def button_reads(frames: np.ndarray) -> list[int | None]:
    """Per slot: the buttons SURELY shown, or None when any icon sits in
    its fade band (release fades over several slots, so those slots are
    honestly ambiguous)."""
    red, green, blue = frames[..., 0], frames[..., 1], frames[..., 2]
    area = frames.shape[1] * frames.shape[2]
    masses = {
        "A": np.count_nonzero((blue > 130) & (blue - red > 50),
                              axis=(1, 2)) / area,
        "B": np.count_nonzero((green > 130) & (green - red > 40) &
                              (green - blue > 40), axis=(1, 2)) / area,
        "Z": np.count_nonzero((red > 170) & (green > 170) & (blue > 170),
                              axis=(1, 2)) / area,
    }
    out: list[int | None] = []
    for slot in range(frames.shape[0]):
        buttons = 0
        certain = True
        for name, series in masses.items():
            mass = series[slot]
            if mass >= ICON_SURE[name]:
                buttons |= BUTTON_BITS[name]
            elif mass > ICON_ABSENT[name]:
                certain = False
        out.append(buttons if certain else None)
    return out


@dataclass(frozen=True)
class SnapResult:
    frame_map: list
    read_slots: int       # slots that read at least one field
    slots: int
    moved: int            # slots whose frame changed vs the prior
    source: str           # "pixels"


def _learn_templates(fingerprints, labels):
    """label (a string) -> (bits, weight): the mean fingerprint plus a
    per-cell vote mask. Only cells the exemplars AGREE on (almost always
    ink, or almost always empty) get a vote -- the field box unavoidably
    holds moving world behind the glyphs, and a boolean template let that
    background outvote the digits (measured: 14 of ~60 values survived a
    rigid spread cut, and reads covered a quarter of the clip)."""
    groups: dict = {}
    for print_, label in zip(fingerprints, labels):
        if label is None:
            continue
        groups.setdefault(label, []).append(print_)
    templates = {}
    for label, prints in groups.items():
        stack = np.stack(prints).astype(float)
        mean = stack.mean(axis=0)
        weight = (mean < 0.15) | (mean > 0.85)
        if np.count_nonzero(weight) < MIN_VOTING_CELLS:
            continue
        templates[label] = (mean > 0.5, weight)
    return templates


def _read(fingerprints, templates):
    """Per slot: the best-matching label, or None. Distance = share of a
    template's VOTING cells that disagree."""
    if not templates:
        return [None] * len(fingerprints)
    labels = list(templates)
    bits = np.stack([templates[label][0] for label in labels])
    weights = np.stack([templates[label][1] for label in labels])
    votes = weights.sum(axis=1)
    out = []
    for print_ in fingerprints:
        wrong = ((bits ^ print_) & weights).sum(axis=1)
        share = wrong / votes
        best = int(np.argmin(share))
        out.append(labels[best] if share[best] <= READ_SHARE else None)
    return out


def _value_string(value: int, letters: str) -> str:
    """How Usamune prints one axis: '0' bare, otherwise letter+magnitude."""
    if value == 0:
        return "0"
    letter = letters[0] if value > 0 else letters[1]
    return f"{letter}{abs(value)}"


def build_map(prior: list, pad_of_raw, raws_near,
              field_y, field_x, buttons) -> SnapResult | None:
    """Fit the map to the read values with a banded, monotone DP.

    `pad_of_raw(raw)` -> (buttons, stick_x, stick_y) or None;
    `raws_near(raw)` -> candidate raw frames within the band, ascending.
    `field_y`/`field_x`/`buttons`: per-slot reads (None = unread).
    """
    count = len(prior)
    read_slots = sum(1 for at in range(count)
                     if field_y[at] is not None or field_x[at] is not None)
    if count == 0 or read_slots / count < MIN_READ_RATIO:
        return None

    def mismatch(at, raw):
        pad = pad_of_raw(raw)
        if pad is None:
            return 0.5                      # a hole constrains nothing much
        cost = 0.0
        if field_y[at] is not None and \
                _value_string(pad[2], "UD") != field_y[at]:
            cost += 1.0
        if field_x[at] is not None and \
                _value_string(pad[1], "RL") != field_x[at]:
            cost += 1.0
        if buttons[at] is not None and \
                (pad[0] & 0xE000) != (buttons[at] & 0xE000):
            cost += 1.0
        return cost

    # DP over slots; states = candidate raw frames around the prior.
    previous_states: dict[int, tuple[float, int | None]] = {0: (0.0, None)}
    parents: list[dict] = []
    state_lists: list[list] = []
    for at in range(count):
        raw0 = prior[at]
        candidates = raws_near(raw0) if raw0 is not None else [None]
        states: dict = {}
        parent: dict = {}
        for candidate in candidates:
            best_cost, best_from = None, None
            for from_raw, (cost, _p) in previous_states.items():
                if candidate is not None and from_raw != 0 and \
                        from_raw is not None and candidate < from_raw:
                    continue                # monotone
                if best_cost is None or cost < best_cost:
                    best_cost, best_from = cost, from_raw
            if best_cost is None:
                continue
            step = mismatch(at, candidate) if candidate is not None else 0.25
            # a feather keeps the prior when the pixels do not object
            drift = 0.01 if candidate != raw0 else 0.0
            key = candidate if candidate is not None else 0
            total = best_cost + step + drift
            if key not in states or total < states[key][0]:
                states[key] = (total, best_from)
                parent[key] = best_from
        if not states:
            states = {0: (min(c for c, _p in previous_states.values()), None)}
            parent = {0: None}
        previous_states = states
        parents.append(parent)
        state_lists.append(list(states))
    # walk back the best path
    key = min(previous_states, key=lambda k: previous_states[k][0])
    path = [key]
    for at in range(count - 1, 0, -1):
        key = parents[at].get(key)
        path.append(key)
        if key is None:
            break
    path.reverse()
    refined: list = []
    moved = 0
    for at in range(count):
        value = path[at] if at < len(path) and path[at] not in (None, 0) \
            else prior[at]
        refined.append(value)
        if value != prior[at]:
            moved += 1
    return SnapResult(frame_map=refined, read_slots=read_slots, slots=count,
                      moved=moved, source="pixels")


class PixelRefiner:
    """The extraction-time hook: rebuild a freshly cut clip's map from its
    own pixels. `pad_lookup(start_utc, duration_s)` returns the span's
    captured frames as {raw: (buttons, stick_x, stick_y)} (the inputs
    service's `pad_lookup`). Injected, so tests run without ffmpeg."""

    def __init__(self, ffmpeg: str, pad_lookup, decode=decode_region):
        self._ffmpeg = ffmpeg
        self._pad_lookup = pad_lookup
        self._decode = decode

    def refine(self, clip_path, meta: dict) -> SnapResult | None:
        prior = meta.get("frame_map")
        start = meta.get("start_utc")
        duration = meta.get("duration_s")
        if not prior or not start or not duration:
            return None
        try:
            pads = self._pad_lookup(start, duration)
            if not pads:
                return None
            dims = probe_dims(self._ffmpeg, clip_path)
            frames_y = self._decode(self._ffmpeg, clip_path, FIELD_Y_REGION,
                                    dims)
            frames_x = self._decode(self._ffmpeg, clip_path, FIELD_X_REGION,
                                    dims)
            if not len(frames_y):
                return None
            count = min(len(prior), len(frames_y), len(frames_x))
            prior = list(prior[:count])
            prints_y = field_fingerprints(frames_y[:count])
            prints_x = field_fingerprints(frames_x[:count])
            # Buttons are NOT read yet: the strip's colour masses cannot
            # tell the B icon's green from the grass behind it (measured --
            # a mass threshold reads B as held on most slots of a WF clip),
            # so button constraints would poison the fit. The stick fields
            # carry the alignment; buttons ride the fitted map.
            buttons: list = [None] * count

            def pad_of_raw(raw):
                return pads.get(raw)

            # LEARN from the prior's stable holds, then READ every slot:
            def hold_labels(axis_index, letters):
                labels = [None] * count
                begin = 0
                current = None
                for at in range(count + 1):
                    raw = prior[at] if at < count else None
                    pad = pads.get(raw) if raw is not None else None
                    value = pad[axis_index] if pad else None
                    if value != current or at == count:
                        if current is not None and \
                                at - begin >= LEARN_HOLD_SLOTS:
                            for slot in range(begin + 1, at - 1):
                                labels[slot] = _value_string(current,
                                                             letters)
                        begin, current = at, value
                return labels

            templates_y = _learn_templates(prints_y,
                                           hold_labels(2, "UD"))
            templates_x = _learn_templates(prints_x,
                                           hold_labels(1, "RL"))
            field_y = _read(prints_y, templates_y)
            field_x = _read(prints_x, templates_x)

            sorted_raws = sorted(pads)

            def raws_near(raw):
                lo, hi = raw - FIT_BAND_FRAMES, raw + FIT_BAND_FRAMES
                return [r for r in sorted_raws if lo <= r <= hi] or [raw]

            return build_map(prior, pad_of_raw, raws_near,
                             field_y, field_x, buttons)
        except Exception:
            log.exception("pixel refinement failed; keeping the prior map")
            return None
