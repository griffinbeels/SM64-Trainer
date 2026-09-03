"""THE TIMER READER: which game frame a picture shows, read off Usamune's
own IGT clock.

Round 32, 2026-09-03. His question ended the previous approach: "have we even
FOR SURE confirmed that we can even accurately extract the numbers... out from
the bottom left corner of the screen?" The answer was no -- and worse, the
signal we had chosen is ambiguous BY NATURE. The stick readout repeats on
50-77% of a clip's frames, so half the pictures cannot be told from their
neighbour, and everything built on it (a banded search, a dynamic-programming
aligner, a learned-anchor store) existed to guess across those gaps.

The timer does not repeat. Usamune prints `floor(frames * 100 / 30)` since the
run began, so it advances 3 or 4 centiseconds on EVERY game frame and names
each one outright. His instruction: "We should leverage the actual, mechanical,
concrete data that we have access to."

TWO CHECKS THAT NEED NO CONTROLLER DATA AT ALL, which is what makes this
different from everything before it -- the reader can be proved right from the
screen alone:

  1. `floor(k * 100 / 30)` can only end in 0, 3 or 6. Measured over his clip
     and a window of raw ring footage: 3,503 reads, 3,503 legal.
  2. Every step between two readings must be a whole number of game frames,
     i.e. a sum of 3s and 4s. Measured: 3,077 of 3,079 and 420 of 422; the
     handful of exceptions were a seconds digit misread.

And the conversion back is EXACT, not fitted: `cs = floor(k * 10 / 3)` is
strictly increasing, so `k = ceil(cs * 3 / 10)` recovers the frame with no
rounding ambiguity. Measured on 3,080 reads carrying 2,533 distinct values:
every repeat was a duplicate picture, never two different frames.

WHERE IT DOES NOT ANSWER, and the caller must bridge (the picture ledger's
bookkeeping does): the pause menu and a star dance FREEZE the clock while the
game keeps running, fades and the reset flash hide it, and it resets to zero
each attempt. Measured on his clip: 92.5% of frames pinned outright, against
the 23-50% the stick display manages.

The digits are the SAME glyphs the stick readout uses, so this needs no
alphabet of its own -- `padread.load_alphabet()[("y", "d1")]` reads them.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

log = logging.getLogger("sm64.replay")

# Where Usamune's IGT clock sits, as fractions of the frame (PJ64 stretches the
# 320x240 framebuffer linearly, so fractions hold at any window size). Measured
# on his 1600x1224 clips: x 1120..1570, y 170..290.
REGION = (0.7000, 0.13889, 0.98125, 0.23693)
WIDTH, HEIGHT = 225, 60

# The five digit boxes inside that region: M ' S S " C C. The apostrophe and
# quote are separators and are never read.
BANDS = {"m": 13, "s1": 65, "s2": 96, "c1": 150, "c2": 182}
ORDER = ("m", "s1", "s2", "c1", "c2")
BOX_H, BOX_W = 43, 34
ROW0 = 11
# Registration search: the HUD sits a pixel or two differently per capture
# scale, so each box hunts its own best offset once per clip.
REGISTER_SPAN = 10
REGISTER_SAMPLE = 200

# A run of identical readings longer than this is the clock FROZEN (a pause
# menu, a star dance), not duplicate pictures. Measured on his clip: duplicate
# runs reach 3 pictures; the pause froze 1'16"03 across hundreds.
FREEZE_RUN = 4
# The epoch fit must win clearly or the clip keeps the bookkeeping's map.
EPOCH_MARGIN_MIN = 0.15
# Fewer pinned frames than this and the timer has not covered the clip.
MIN_PINNED = 30


def frames_of(centiseconds: int) -> int:
    """The game frame a reading names, counted from the clock's own zero.

    `cs = floor(k * 10 / 3)` is strictly increasing, so this inverts it
    exactly -- there is no rounding ambiguity and no two frames share a value
    (verified on 3,080 reads: every repeat was a duplicate picture).
    """
    return -(-centiseconds * 3 // 10)          # ceil(cs * 3 / 10)


@dataclass
class TimerReading:
    #: centiseconds per video frame, None where the clock could not be read
    values: list = field(default_factory=list)
    #: True where the clock is FROZEN (a pause, a dance) -- readable but not
    #: advancing, so it names no new frame
    frozen: list = field(default_factory=list)
    read: int = 0            # frames all five digits read on
    legal: int = 0           # ...whose last digit is a possible one (0, 3, 6)
    achievable: int = 0      # steps that are a whole number of game frames
    steps: int = 0           # steps scored
    pinned: int = 0          # frames the clock names outright

    @property
    def sound(self) -> bool:
        """Did the clock prove itself on the screen alone? Both checks need
        no controller data, so a failure here is the READER's, never the
        map's."""
        return (self.read >= MIN_PINNED
                and self.legal == self.read
                and self.achievable == self.steps)

    def as_dict(self) -> dict:
        return {"read": self.read, "legal": self.legal, "pinned": self.pinned,
                "achievable": self.achievable, "steps": self.steps,
                "sound": self.sound}


def register(cells: np.ndarray, table: dict) -> dict:
    """Each digit box's own (dy, dx) for this clip's capture scale, chosen by
    the offset whose pixels look most like SOME digit across the clip."""
    if len(cells) == 0:
        return {name: (0, 0) for name in BANDS}
    picks = np.linspace(0, len(cells) - 1,
                        min(REGISTER_SAMPLE, len(cells))).astype(int)
    sample = cells[picks].astype(np.float32)
    chosen = {}
    for name, x0 in BANDS.items():
        best = None
        for dy in range(-REGISTER_SPAN, REGISTER_SPAN + 1):
            for dx in range(-REGISTER_SPAN, REGISTER_SPAN + 1):
                top, left = ROW0 + dy, x0 + dx
                if (top < 0 or left < 0 or top + BOX_H > cells.shape[1]
                        or left + BOX_W > cells.shape[2]):
                    continue
                box = sample[:, top:top + BOX_H, left:left + BOX_W, :]
                dists = np.stack([
                    (np.abs(box - t.median).mean(axis=-1) * t.weight).sum(axis=(1, 2))
                    / max(float(t.weight.sum()), 1.0) for t in table.values()])
                score = float(np.median(dists.min(axis=0)))
                if best is None or score < best[0]:
                    best = (score, dy, dx)
        chosen[name] = (best[1], best[2]) if best else (0, 0)
    return chosen


def _box(cells: np.ndarray, offsets: dict, name: str) -> np.ndarray:
    dy, dx = offsets[name]
    top, left = ROW0 + dy, BANDS[name] + dx
    return cells[:, top:top + BOX_H, left:left + BOX_W, :]


def _read_digit(box: np.ndarray, table: dict, dist_max: float, margin_min: float):
    names = list(table)
    box = box.astype(np.float32)
    dists = np.stack([
        (np.abs(box - table[g].median).mean(axis=-1) * table[g].weight).sum(axis=(1, 2))
        / max(float(table[g].weight.sum()), 1.0) for g in names], axis=1)
    order = np.argsort(dists, axis=1)
    rows = np.arange(len(box))
    best, second = dists[rows, order[:, 0]], dists[rows, order[:, 1]]
    known = (best <= dist_max) & (second - best >= margin_min)
    return [names[i] for i in order[:, 0]], known


def learn(cells: np.ndarray, offsets: dict, digits: dict, template_of) -> dict:
    """Per digit box, re-learn each glyph from the frames that read it
    confidently -- at THIS clip's capture scale.

    The labels are the reader's own confident reads, never a frame map, so
    this cannot enter the feedback loop that made the stick reader teach
    itself to be worse (a map's labels are only as good as the map). It buys
    recall: the reference alphabet was learned off different footage, and on
    his clip the second pass took readings from 83% of frames to 95%.
    """
    tables = {}
    for name in ORDER:
        box = _box(cells, offsets, name)
        names, known = digits[name]
        groups: dict[str, list] = {}
        for slot in range(len(box)):
            if known[slot]:
                groups.setdefault(names[slot], []).append(slot)
        tables[name] = {}
        for glyph, members in groups.items():
            table = template_of(box, members)
            if table is not None:
                tables[name][glyph] = table
    return tables


def read(cells: np.ndarray, table: dict, dist_max: float,
         margin_min: float, template_of=None) -> TimerReading:
    """Usamune's clock, per video frame, with its own two proofs.

    Everything here is checkable from the screen alone: a reading whose last
    digit is impossible, or a step that is not a whole number of game frames,
    is a MISREAD -- no controller data, no frame map, no alignment.
    """
    count = len(cells)
    if count == 0:
        return TimerReading(values=[], frozen=[])
    offsets = register(cells, table)
    digits = {name: _read_digit(_box(cells, offsets, name), table,
                                dist_max, margin_min)
              for name in ORDER}
    if template_of is not None:
        # A second pass at this clip's own scale. Labels are the first pass's
        # confident reads -- self-consistent, never map-derived.
        own = learn(cells, offsets, digits, template_of)
        digits = {name: _read_digit(_box(cells, offsets, name),
                                    {**table, **own[name]}, dist_max, margin_min)
                  for name in ORDER}
    values: list = []
    for slot in range(count):
        seen = [digits[name][0][slot] if digits[name][1][slot] else None
                for name in ORDER]
        if any(one is None for one in seen):
            values.append(None)
            continue
        values.append(int(seen[0]) * 6000 + int(seen[1]) * 1000
                      + int(seen[2]) * 100 + int(seen[3]) * 10 + int(seen[4]))

    out = TimerReading(values=values, frozen=[False] * count)
    out.read = sum(1 for one in values if one is not None)
    # CHECK 1: floor(k * 100 / 30) can only end in 0, 3 or 6.
    out.legal = sum(1 for one in values if one is not None and one % 10 in (0, 3, 6))
    # CHECK 2: every step is a whole number of game frames (a sum of 3s and 4s).
    previous = None
    for one in values:
        if one is None:
            continue
        if previous is not None:
            step = one - previous
            out.steps += 1
            if step >= 0 and any(3 * k <= step <= 4 * k for k in range(0, 60)):
                out.achievable += 1
        previous = one
    _mark_frozen(out)
    out.pinned = sum(1 for slot, one in enumerate(values)
                     if one is not None and not out.frozen[slot])
    return out


def _mark_frozen(reading: TimerReading) -> None:
    """A run of identical readings longer than a duplicate picture is the
    clock STOPPED -- the pause menu, a star dance -- while the game keeps
    running. Those frames are readable but name no new frame, so the caller
    must bridge them from the recorder's own bookkeeping."""
    values = reading.values
    start = 0
    while start < len(values):
        if values[start] is None:
            start += 1
            continue
        end = start
        while end + 1 < len(values) and values[end + 1] == values[start]:
            end += 1
        if end - start + 1 > FREEZE_RUN:
            for slot in range(start, end + 1):
                reading.frozen[slot] = True
        start = end + 1


def fit_epoch(reading: TimerReading, stick_agrees, prior: list,
              span: int = 240):
    """The ONE frame the clock counted from, fitted against the CONTROLLER
    DATA rather than against any map.

    `stick_agrees(slot, frame) -> bool | None` says whether the stick digits
    read from THAT picture match the pad the track holds on `frame` (None when
    the picture's readout says nothing). So the clock is checked against the
    other half of the same display plus what the player actually pressed --
    mechanical and concrete, and independent of every map. `prior` is used
    only to centre the search.

    Returns (epoch, agreeing frames, scored frames, margin). The margin is the
    winner minus the best epoch more than two frames away, as a fraction of
    the frames scored -- an epoch that only just wins has not been
    established, and the caller keeps the bookkeeping's map.
    """
    pinned = [(slot, frames_of(one))
              for slot, one in enumerate(reading.values)
              if one is not None and not reading.frozen[slot]]
    if len(pinned) < MIN_PINNED:
        return None, 0, 0, 0.0
    guesses: dict[int, int] = {}
    for slot, offset in pinned:
        if slot < len(prior) and prior[slot] is not None:
            guesses[prior[slot] - offset] = guesses.get(prior[slot] - offset, 0) + 1
    centre = max(guesses, key=lambda key: guesses[key]) if guesses else 0
    scores: dict[int, tuple[int, int]] = {}
    for epoch in range(centre - span, centre + span + 1):
        agree = checked = 0
        for slot, offset in pinned:
            verdict = stick_agrees(slot, epoch + offset)
            if verdict is None:
                continue
            checked += 1
            agree += bool(verdict)
        scores[epoch] = (agree, checked)
    best = max(scores, key=lambda key: scores[key][0])
    runner = max((hits for epoch, (hits, _seen) in scores.items()
                  if abs(epoch - best) > 2), default=0)
    agree, checked = scores[best]
    margin = (agree - runner) / checked if checked else 0.0
    return best, agree, checked, margin


def segments(reading: TimerReading) -> list[tuple[int, int]]:
    """Maximal runs of slots the clock names outright, as (first, last).

    The clock STOPS at a pause menu and a star dance while the game keeps
    running, so a single epoch cannot span one: everything after a pause sits
    later by however many frames the pause lasted. Each run therefore carries
    its own constant, and the clock supplies the exact shape within it.
    """
    runs: list[tuple[int, int]] = []
    start = None
    for slot, one in enumerate(reading.values):
        live = one is not None and not reading.frozen[slot]
        if live and start is None:
            start = slot
        elif not live and start is not None:
            runs.append((start, slot - 1))
            start = None
    if start is not None:
        runs.append((start, len(reading.values) - 1))
    return runs


def fit_runs(reading: TimerReading, prior: list, agrees, span: int = 8,
             margin_min: float = 0.10) -> list:
    """Each clock run's own constant: fitted against the CONTROLLER DATA where
    the display can determine it, and taken from the recorder's bookkeeping
    where it cannot.

    Returns one entry per run: (first, last, constant, agreeing, scored,
    margin, fitted). The clock supplies every frame's position within a run;
    this supplies only the run's one number, which is all a pause can cost.

    Measured on his clip 6510 (2026-09-03), eight runs: the four the display
    could determine agreed with the controller data on 2269 of 2283 frames,
    and the four it could not tied across every candidate -- there the
    bookkeeping is the better answer than an arbitrary winner, so it is used
    and the run says it was not fitted.
    """
    out = []
    for first, last in segments(reading):
        modes: dict[int, int] = {}
        for slot in range(first, last + 1):
            if slot < len(prior) and prior[slot] is not None:
                guess = prior[slot] - frames_of(reading.values[slot])
                modes[guess] = modes.get(guess, 0) + 1
        centre = max(modes, key=lambda key: modes[key]) if modes else 0
        scores: dict[int, tuple[int, int]] = {}
        for candidate in range(centre - span, centre + span + 1):
            agree = checked = 0
            for slot in range(first, last + 1):
                verdict = agrees(slot, candidate + frames_of(reading.values[slot]))
                if verdict is None:
                    continue
                checked += 1
                agree += bool(verdict)
            scores[candidate] = (agree, checked)
        best = max(scores, key=lambda key: scores[key][0])
        agree, checked = scores[best]
        runner = max((hits for cand, (hits, _n) in scores.items() if cand != best),
                     default=0)
        margin = (agree - runner) / checked if checked else 0.0
        fitted = margin >= margin_min
        out.append((first, last, best if fitted else centre,
                    agree, checked, margin, fitted))
    return out


def frame_map(reading: TimerReading, runs: list, prior: list) -> list:
    """The map: every frame the clock names outright, and the recorder's own
    bookkeeping across the stretches it cannot (a pause, a dance, a fade).

    `runs` comes from `fit_runs`. The bridge is SHIFTED to meet the clock, so
    a gap cannot introduce a step change -- the bookkeeping is trusted for how
    many frames passed, never for which frame it was.
    """
    out: list = [None] * len(reading.values)
    for first, last, here, _agree, _checked, _margin, _fitted in runs:
        # THE CLOCK GIVES THE SHAPE, ONE CONSTANT GIVES THE RUN ITS PLACE.
        # Within a run the clock names every frame exactly and corrects all
        # the per-frame wander; only the run's single number comes from
        # elsewhere, which is why a pause -- where the clock stops but the
        # game does not -- costs one number rather than the clip.
        #
        # Carrying the constant across a gap from its two edge frames instead
        # was tried and is far worse (measured 2026-09-03: 0.58% against
        # 96.57%), because a gap's edges are exactly where a degraded
        # bookkeeping is least reliable, and eight runs compound it.
        for slot in range(first, last + 1):
            out[slot] = here + frames_of(reading.values[slot])
    # The stretches the clock cannot name -- a pause, a dance, a fade -- take
    # the bookkeeping, shifted to meet the NEAREST run so a gap can never
    # introduce a step change of its own. Carrying one clip-wide shift instead
    # put every gap on the wrong side of a run that had refused its fit
    # (measured 2026-09-03).
    shifts: list = [None] * len(out)
    for first, last, *_rest in runs:
        for slot in (first, last):
            if slot < len(prior) and prior[slot] is not None:
                shifts[slot] = out[slot] - prior[slot]
    nearest = None
    for slot in range(len(out)):                    # carry forward
        if shifts[slot] is not None:
            nearest = shifts[slot]
        elif out[slot] is None:
            shifts[slot] = nearest
    nearest = None
    for slot in range(len(out) - 1, -1, -1):        # and back, for a leading gap
        if out[slot] is not None and slot < len(prior) and prior[slot] is not None:
            nearest = out[slot] - prior[slot]
        elif out[slot] is None and shifts[slot] is None:
            shifts[slot] = nearest
    for slot in range(len(out)):
        if out[slot] is None and slot < len(prior) and prior[slot] is not None:
            out[slot] = prior[slot] + (shifts[slot] or 0)
    return out
