"""THE ORACLE READER: the frame number the game prints into its own picture.

Round 32, item 94 (2026-09-04). He switched on Usamune's HUD memory display
for ``0x8032D5D4`` -- ``layout.global_timer``, gGlobalTimer, the counter every
frame of the input track is filed under -- and the picture now carries its
own frame number, top-left under the lives counter, in the HUD font, +1 per
game frame, unique, never frozen, never restarting. His ruling in the same
message: the display is the ORACLE, never the shipped mechanism ("our users
will NOT want to enter this memory address every time they play"). So this
module is a dev gate: it certifies any frame map -- the timer join, the feed
log, the capture layer's stamps -- against what the game itself said, and it
ships nothing to a user.

What makes it an oracle rather than another OCR guess is the +1 RULE, which
needs no map: consecutive distinct pictures differ by exactly one (or by the
RAM stamps' own delta when a present was skipped), so a misread contradicts
its neighbours and is dropped, and a value is vouched for only when a
neighbour agrees. The glyphs are the SAME HUD digits the clock reader reads
(`timerread`), at the same capture scale, so its alphabet and its
distance/margin gate are reused rather than redefined; only the region, the
box row and the left-aligned variable length are this module's own.

Measured on clip 6650 (1600x1224): the counter's ink spans rows 16-55 of the
67-row cell and columns 20-168 for five digits -- a 30-pixel pitch, the
clock's own box size (43x34). Six boxes cover any count under a million
frames (nine hours of emulator uptime).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

log = logging.getLogger("sm64.replay")

# Where the HUD memory display draws, as fractions of the frame (PJ64 stretches
# the framebuffer linearly, so fractions hold at any window size). Measured on
# his 1600x1224 oracle clips: x 88..496, y 165..300.
REGION = (0.055, 0.135, 0.31, 0.245)
WIDTH, HEIGHT = 204, 67                   # the region at the clock reader's scale
BOX_COUNT = 6
BOX_X0 = 20                               # the first digit's left edge, measured
PITCH = 30                                # measured; the clock's is 31-32
ROW0 = 14                                 # the box top: ink rows 16-55 in a 43-row box
REGISTER_SPAN = 8                         # shared coarse search, every second pixel
REGISTER_FINE = 3                         # per-box refinement after the coarse pass
REGISTER_SAMPLE = 40


@dataclass
class OracleReading:
    #: the printed frame number per video frame, None where it could not be
    #: read or where the +1 rule dropped it
    values: list = field(default_factory=list)
    read: int = 0            # frames the reader produced a value on
    consistent: int = 0      # ...vouched for by a neighbour
    contradicted: int = 0    # ...contradicted by every readable neighbour (dropped)
    boxes: dict = field(default_factory=dict)   # box name -> (dy, dx) registered
    bridged: int = 0         # slots predicted between two vouched anchors
    verified: int = 0        # ...whose boxes agree with the prediction (kept)
    rejected: int = 0        # ...whose boxes do not (unknown)
    vouched_mask: list = field(default_factory=list)   # True where a neighbour agreed
    truncated: int = 0       # reads dropped for a digit count unlike the clip's
    lone: int = 0            # reads no neighbour could vouch for (not in values)
    learned: list = field(default_factory=list)        # glyphs the second pass learned

    def as_dict(self) -> dict:
        return {"read": self.read, "consistent": self.consistent,
                "contradicted": self.contradicted, "bridged": self.bridged,
                "verified": self.verified, "rejected": self.rejected,
                "truncated": self.truncated, "lone": self.lone,
                "known": sum(1 for one in self.values if one is not None),
                "slots": len(self.values)}


def assemble(glyphs: list, is_digit: list) -> int | None:
    """Left-aligned digits up to the first blank box; None if any digit box
    is unreadable or there is no digit at all."""
    digits = []
    for glyph, digit in zip(glyphs, is_digit, strict=True):
        if not digit:
            break
        if glyph is None:
            return None
        digits.append(glyph)
    if not digits:
        return None
    return int("".join(digits))


def enforce_plus_one(values: list, ram_deltas: list | None = None) -> OracleReading:
    """Keep a value only when a readable neighbour vouches for it.

    Consecutive pictures differ by exactly +1; when `ram_deltas` are given
    (0 for a duplicate picture, the RAM stamps' own step otherwise), by that
    delta instead -- a skipped present is then a witnessed jump, not a
    contradiction. A value with no readable neighbour is kept but not
    counted consistent: nothing vouches for it either way. Equal values on
    two DISTINCT pictures never vouch for each other: that is what a run of
    truncated reads looks like (a blank box mistaken for the end of the
    number), and it was self-consistent enough to pass a {0, 1} rule.
    """
    count = len(values)
    out = OracleReading(values=list(values))
    out.read = sum(1 for one in values if one is not None)

    def allowed(index: int) -> set:
        if ram_deltas is not None and ram_deltas[index] is not None:
            return {int(ram_deltas[index])}
        return {1}

    def agrees(index: int, neighbour: int) -> bool:
        """Does the value at `index` fit the readable value at `neighbour`?"""
        low, high = min(index, neighbour), max(index, neighbour)
        return values[high] - values[low] in allowed(high)

    readable = [one is not None for one in values]
    vouched = [False] * count
    for index in range(count):
        if not readable[index]:
            continue
        vouched[index] = ((index > 0 and readable[index - 1] and agrees(index, index - 1))
                          or (index + 1 < count and readable[index + 1]
                              and agrees(index, index + 1)))
    # A value nobody vouches for is DROPPED only when a credible witness -- a
    # neighbour that is itself vouched for -- disagrees with it. A lone value
    # whose only neighbour is an unvouched stray stays, unvouched: the stray
    # is the likelier misread, and bridging may still verify the lone one.
    keep = list(values)
    for index in range(count):
        if not readable[index] or vouched[index]:
            continue
        credible = ((index > 0 and vouched[index - 1])
                    or (index + 1 < count and vouched[index + 1]))
        if credible:
            out.contradicted += 1
            keep[index] = None
    out.consistent = sum(vouched)
    out.vouched_mask = vouched
    out.values = keep
    return out


def score_map(reading: OracleReading, frame_map: list) -> dict:
    """A frame map against the oracle: `off_by[k]` = slots where the map is
    k frames ahead of the printed number (negative = behind)."""
    off_by: dict[int, int] = {}
    exact = unreadable = 0
    slots = min(len(reading.values), len(frame_map))
    for slot in range(slots):
        oracle, mapped = reading.values[slot], frame_map[slot]
        if oracle is None or mapped is None:
            unreadable += 1
            continue
        delta = int(mapped) - int(oracle)
        off_by[delta] = off_by.get(delta, 0) + 1
        if delta == 0:
            exact += 1
    return {"slots": slots, "exact": exact, "unreadable": unreadable,
            "off_by": dict(sorted(off_by.items()))}


def disagreements(reading: OracleReading, frame_map: list) -> list:
    """[(slot, oracle, map)] where both read and differ."""
    return [(slot, reading.values[slot], frame_map[slot])
            for slot in range(min(len(reading.values), len(frame_map)))
            if reading.values[slot] is not None and frame_map[slot] is not None
            and reading.values[slot] != frame_map[slot]]


# --- the reading itself ----------------------------------------------------

def _boxes() -> dict:
    return {f"d{index}": BOX_X0 + PITCH * index for index in range(BOX_COUNT)}


def _fits(cells: np.ndarray, top: int, left: int) -> bool:
    from sm64_events.replay.timerread import BOX_H, BOX_W
    return (top >= 0 and left >= 0 and top + BOX_H <= cells.shape[1]
            and left + BOX_W <= cells.shape[2])


def register(cells: np.ndarray, table: dict) -> dict:
    """Each box's (dy, dx) for this clip: one coarse shift shared by the row
    (the HUD moves as one bitmap), then each box refines within a few pixels
    -- the pitch is measured, not exact, so the error grows along the row."""
    from sm64_events.replay.timerread import _box_score
    boxes = _boxes()
    if len(cells) == 0:
        return {name: (0, 0) for name in boxes}
    picks = np.linspace(0, len(cells) - 1,
                        min(REGISTER_SAMPLE, len(cells))).astype(int)
    sample = cells[picks].astype(np.float32)
    # The coarse pass scores the FIRST THREE boxes only: the last boxes are
    # blank sky on a five-digit counter and would pull the shift anywhere.
    coarse = None
    for dy in range(-REGISTER_SPAN, REGISTER_SPAN + 1, 2):
        for dx in range(-REGISTER_SPAN, REGISTER_SPAN + 1, 2):
            total = 0.0
            for x0 in list(boxes.values())[:3]:
                if not _fits(cells, ROW0 + dy, x0 + dx):
                    total = None
                    break
                total += _box_score(sample, table, ROW0 + dy, x0 + dx)
            if total is not None and (coarse is None or total < coarse[0]):
                coarse = (total, dy, dx)
    shared_dy, shared_dx = (coarse[1], coarse[2]) if coarse else (0, 0)
    chosen = {}
    for name, x0 in boxes.items():
        fine = None
        for dy in range(shared_dy - 1, shared_dy + 2):
            for dx in range(shared_dx - REGISTER_FINE, shared_dx + REGISTER_FINE + 1):
                if not _fits(cells, ROW0 + dy, x0 + dx):
                    continue
                score = _box_score(sample, table, ROW0 + dy, x0 + dx)
                if fine is None or score < fine[0]:
                    fine = (score, dy, dx)
        chosen[name] = (fine[1], fine[2]) if fine else (shared_dy, shared_dx)
    return chosen


def _box(cells: np.ndarray, offsets: dict, name: str) -> np.ndarray:
    from sm64_events.replay.timerread import BOX_H, BOX_W
    dy, dx = offsets[name]
    top, left = ROW0 + dy, _boxes()[name] + dx
    return cells[:, top:top + BOX_H, left:left + BOX_W, :]


def _distances(box: np.ndarray, table: dict) -> tuple[list, np.ndarray]:
    """(glyph names, (slots, glyphs) mean |diff| over each template's ink).

    A table may hold several templates for one glyph -- the reference and a
    learned one, keyed "3" and "3~learned" -- and a glyph's distance is the
    nearest of them, so a learned template can only ever bring a digit
    closer, never push the reference's answer away."""
    names = sorted({key.split("~")[0] for key in table})
    box = box.astype(np.float32)
    columns = []
    for glyph in names:
        variants = [table[key] for key in table if key.split("~")[0] == glyph]
        stack = np.stack([
            (np.abs(box - _fit(t.median, box.shape[1:3])).mean(axis=-1)
             * _fit(t.weight, box.shape[1:3])).sum(axis=(1, 2))
            / max(float(t.weight.sum()), 1.0) for t in variants], axis=1)
        columns.append(stack.min(axis=1))
    return names, np.stack(columns, axis=1)


def _fit(template: np.ndarray, shape: tuple) -> np.ndarray:
    """A template resampled (nearest) to the clip's own box shape. The
    reference alphabet was learned at one window size; a clip recorded at
    another cuts narrower or wider boxes (7141: 43x26 against 43x34 learned)
    and the read used to fail on the shape mismatch instead of reading."""
    height, width = int(shape[0]), int(shape[1])
    if template.shape[0] == height and template.shape[1] == width:
        return template
    rows = np.clip(np.round(np.linspace(0, template.shape[0] - 1, height)).astype(int),
                   0, template.shape[0] - 1)
    cols = np.clip(np.round(np.linspace(0, template.shape[1] - 1, width)).astype(int),
                   0, template.shape[1] - 1)
    return template[rows][:, cols]


# The counter is printed PROPORTIONALLY: a box's exact column depends on the
# digits before it (13233's third digit sat two pixels from 12632's on clip
# 6650, and one box read "3" for a "2" at a 22-vs-24 tie because of it). So a
# box is scored at every shift within this window of its registered offset
# and takes the nearest -- template matching with a little slack, per slot.
LOCAL_DX = 3
LOCAL_DY = 1


def _distances_local(cells: np.ndarray, offsets: dict, name: str,
                     table: dict) -> tuple[list, np.ndarray]:
    """`_distances` minimised over a small window of shifts around the
    box's registered offset, per slot and per glyph."""
    dy0, dx0 = offsets[name]
    best, glyphs = None, None
    for dy in range(dy0 - LOCAL_DY, dy0 + LOCAL_DY + 1):
        for dx in range(dx0 - LOCAL_DX, dx0 + LOCAL_DX + 1):
            if not _fits(cells, ROW0 + dy, _boxes()[name] + dx):
                continue
            glyphs, dists = _distances(_box(cells, {name: (dy, dx)}, name), table)
            best = dists if best is None else np.minimum(best, dists)
    if best is None:
        glyphs, best = _distances(_box(cells, offsets, name), table)
    return glyphs, best


# A duplicate picture -- the same present captured twice -- is not pixel-
# identical after encoding. Measured on clip 6650's 731 consecutive region
# pairs: 106 differ by a mean of under 1.5 per channel (compression noise),
# none between 1.5 and 2.0, and every real frame change by 2.0 or more
# (fades at 2-4; ordinary play 4-40). The duplicates come in a pattern the
# desktop grab makes: the same picture twice, then the next one skipped
# (12540, 12540, 12542 -- every fourth slot at that clip's start).
DUPLICATE_DIFF = 1.5


def duplicates(cells: np.ndarray) -> list:
    """True where a slot's region is the previous slot's picture again --
    the same present encoded twice, which the counter cannot tell apart
    because it did not change."""
    wide = cells.astype(np.int16)
    return [False] + [bool(np.abs(wide[i] - wide[i - 1]).mean() < DUPLICATE_DIFF)
                      for i in range(1, len(cells))]


def digit_count_guard(values: list) -> tuple[list, int]:
    """Drop values whose digit count differs from the clip's modal count: a
    blank box mistaken for the end of the number truncates a run of reads
    into small values that are +1-consistent with each other. The one legal
    change of count is the rollover (99999 -> 100000), kept when the longer
    value sits at or above the power-of-ten boundary."""
    counts: dict[int, int] = {}
    for one in values:
        if one is not None:
            counts[len(str(one))] = counts.get(len(str(one)), 0) + 1
    if not counts:
        return list(values), 0
    mode = max(counts, key=lambda k: (counts[k], k))

    def near_rollover(value: int) -> bool:
        return any(abs(value - 10 ** power) <= 200 for power in range(1, 8))

    kept, dropped = [], 0
    for one in values:
        if one is None or len(str(one)) == mode:
            kept.append(one)
        elif abs(len(str(one)) - mode) == 1 and near_rollover(one):
            kept.append(one)
        else:
            kept.append(None)
            dropped += 1
    return kept, dropped


def learn_table(cells: np.ndarray, offsets: dict, labels: list,
                template_of) -> dict:
    """One template per glyph from the labelled slots, POOLED across the
    boxes (one font, one size): a slot labelled 12632 teaches "1" from box
    d0, "2" from d1 and d4, and so on. Labels come from the +1 rule's
    arithmetic, never from the reader's own confident reads."""
    names = list(_boxes())
    count = len(cells)
    stacked = np.concatenate([_box(cells, offsets, name) for name in names],
                             axis=0)                       # boxes x slots
    members: dict[str, list] = {}
    for slot, value in enumerate(labels):
        if value is None:
            continue
        for index, digit in enumerate(str(value)):
            if index < len(names):
                members.setdefault(digit, []).append(index * count + slot)
    table = {}
    for glyph, rows in members.items():
        template = template_of(stacked, rows)
        if template is not None:
            table[glyph] = template
    return table


def read(cells: np.ndarray, table: dict, dist_max: float, margin_min: float,
         template_of=None, ram_deltas: list | None = None) -> OracleReading:
    """The printed counter per video frame: read, vouched, bridged, verified.

    1. FREE READ every box against the alphabet: a value where every digit
       box is confidently known and the box after them is blank; values of
       the wrong digit count are dropped (`digit_count_guard`).
    2. THE +1 RULE: a value is vouched for when a readable neighbour agrees
       (+1; 0 across a pixel-identical duplicate picture; the RAM delta
       when given).
    3. BRIDGE: between two vouched anchors whose values differ by exactly the
       number of distinct pictures between them, every slot's value is
       determined. Those are PREDICTIONS, and each is then
    4. VERIFIED box by box: the predicted digit must be the nearest template
       (within a small tie) in every digit box, and the box after the last
       digit must hold no digit. A prediction that fails stays unknown.
    5. With `template_of`, a SECOND PASS re-learns every glyph from the slots
       steps 2-4 named -- labels from the count's arithmetic, never from the
       reader's own confident reads (self-taught templates turned a run of
       0s into 6s on clip 6650 while staying consistent with themselves) --
       and runs 1-4 again with the learned glyphs over the reference ones.

    `values` holds only what a neighbour vouched for or a verification
    confirmed; a lone read anchors nothing and is reported as `lone`.
    """
    count = len(cells)
    if count == 0:
        return OracleReading()
    offsets = register(cells, table)
    names = list(_boxes())
    dup = duplicates(cells)
    deltas = [0 if dup[i] else (ram_deltas[i] if ram_deltas is not None else None)
              for i in range(count)]

    def one_pass(alphabet: dict) -> OracleReading:
        glyphs, dists = {}, {}
        for name in names:
            glyphs[name], dists[name] = _distances_local(cells, offsets, name, alphabet)
        free = []
        for slot in range(count):
            read_glyphs, digit_flags = [], []
            for name in names:
                row = dists[name][slot]
                order = np.argsort(row)
                best, second = row[order[0]], row[order[1]]
                known = best <= dist_max and second - best >= margin_min
                read_glyphs.append(glyphs[name][order[0]] if known else None)
                digit_flags.append(bool(best <= dist_max))
            free.append(assemble(read_glyphs, digit_flags))
        free, truncated = digit_count_guard(free)
        out = enforce_plus_one(free, deltas)
        out.truncated = truncated
        out.lone = sum(1 for slot, one in enumerate(out.values)
                       if one is not None and not out.vouched_mask[slot])
        values = [one if out.vouched_mask[slot] else None
                  for slot, one in enumerate(out.values)]
        anchors = [i for i, ok in enumerate(out.vouched_mask) if ok]
        bridged = verified = rejected = 0
        for left, right in zip(anchors, anchors[1:], strict=False):
            if right - left < 2:
                continue
            steps, expected = 0, {}
            for slot in range(left + 1, right + 1):
                steps += deltas[slot] if deltas[slot] is not None else 1
                expected[slot] = values[left] + steps
            if expected[right] != values[right]:
                continue                  # a skipped present nobody witnessed
            for slot in range(left + 1, right):
                bridged += 1
                if _verify(expected[slot], slot, names, glyphs, dists, dist_max, margin_min):
                    values[slot] = expected[slot]
                    verified += 1
                else:
                    rejected += 1
        out.values = values
        out.bridged, out.verified, out.rejected = bridged, verified, rejected
        out.boxes = offsets
        return out

    out = one_pass(table)
    if template_of is not None and any(one is not None for one in out.values):
        learned = learn_table(cells, offsets, out.values, template_of)
        if learned:
            second = one_pass({**table, **{f"{glyph}~learned": template
                                           for glyph, template in learned.items()}})
            second.learned = sorted(learned)
            out = second
    return out


def _verify(value: int, slot: int, names, glyphs, dists, dist_max, tie) -> bool:
    text = str(value)
    if len(text) > len(names):
        return False
    for index, name in enumerate(names):
        row = dists[name][slot]
        if index < len(text):
            want = glyphs[name].index(text[index])
            if row[want] > dist_max or row[want] - row.min() > tie:
                return False
        elif index == len(text) and row.min() <= dist_max:
            return False                  # a digit where the number has ended
    return True


def _nearest_distance(box: np.ndarray, table: dict) -> np.ndarray:
    box = box.astype(np.float32)
    dists = np.stack([
        (np.abs(box - t.median).mean(axis=-1) * t.weight).sum(axis=(1, 2))
        / max(float(t.weight.sum()), 1.0) for t in table.values()], axis=1)
    return dists.min(axis=1)


REFERENCE = "oracle_glyphs_us.npz"        # learned off his three oracle clips
REFERENCE_KEY = ("hud", "digit")


def load_reference(path: Path | None = None) -> dict:
    """The oracle's own alphabet (shipped in the package, or from `path`);
    the clock reader's digits when neither exists yet."""
    from importlib import resources
    from sm64_events.replay import padread
    if path is None:
        candidate = resources.files("sm64_events.data") / REFERENCE
        path = Path(str(candidate)) if candidate.is_file() else None
    if path is None:
        return padread.load_alphabet()[("y", "d1")]
    row, col = REFERENCE_KEY
    table = {}
    with np.load(path) as data:
        for name in data.files:
            parts = name.split("/")
            if parts[:2] != [row, col] or parts[3] != "median":
                continue
            glyph = parts[2]
            table[glyph] = padread.Template(
                data[name].astype(np.float32),
                data[f"{row}/{col}/{glyph}/weight"].astype(np.float32),
                int(data[f"{row}/{col}/{glyph}/n"]))
    return table


def save_reference(table: dict, path: Path) -> None:
    from sm64_events.replay import padread
    padread.save_alphabet({REFERENCE_KEY: table}, path)


def decode(clip: Path, ffmpeg: str, width: int | None = None,
           height: int | None = None) -> np.ndarray:
    from sm64_events.replay import padread
    return padread.decode_region(ffmpeg, clip, REGION, WIDTH, HEIGHT,
                                 width, height)


def read_clip(clip: Path, ffmpeg: str, reference: dict | None = None,
              ram_deltas: list | None = None,
              width: int | None = None, height: int | None = None) -> OracleReading:
    """Decode one clip's oracle region and read it."""
    from sm64_events.replay import padread
    cells = decode(clip, ffmpeg, width, height)
    table = reference if reference is not None else load_reference()
    return read(cells, table, padread.DIST_MAX, padread.MARGIN_MIN,
                template_of=padread.template_from, ram_deltas=ram_deltas)


def learn_reference(readings: list, ffmpeg: str) -> dict:
    """One alphabet pooled over several clips' vouched slots -- the varied
    backgrounds (sky, sand, brick, a dark corridor) are what make the
    spread gate drop every background pixel from a glyph's weight."""
    from sm64_events.replay import padread
    names = list(_boxes())
    stacks, members, base = [], {}, 0
    for clip, reading in readings:
        cells = decode(clip, ffmpeg)
        offsets = reading.boxes or register(cells, load_reference())
        count = len(cells)
        stacks.append(np.concatenate([_box(cells, offsets, name) for name in names], axis=0))
        for slot, value in enumerate(reading.values[:count]):
            if value is None:
                continue
            for index, digit in enumerate(str(value)):
                if index < len(names):
                    members.setdefault(digit, []).append(base + index * count + slot)
        base += len(names) * count
    stacked = np.concatenate(stacks, axis=0)
    table = {}
    for glyph, rows in members.items():
        template = padread.template_from(stacked, rows)
        if template is not None:
            table[glyph] = template
    return table
