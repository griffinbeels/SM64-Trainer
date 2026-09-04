"""THE TIMER READER: join Usamune's displayed IGT to a captured game frame.

Round 32, 2026-09-03. His question ended the previous approach: "have we even
FOR SURE confirmed that we can even accurately extract the numbers... out from
the bottom left corner of the screen?" The answer was no -- and worse, the
signal we had chosen is ambiguous BY NATURE. The stick readout repeats on
50-77% of a clip's frames, so half the pictures cannot be told from their
neighbour, and everything built on it (a banded search, a dynamic-programming
aligner, a learned-anchor store) existed to guess across those gaps.

The conversion from a correctly read CLOCK value to a relative IGT frame is
exact: Usamune prints ``floor(frame * 10 / 3)``, which is strictly increasing.
But that fact does NOT prove that five OCR glyphs were read correctly, and a
relative IGT frame is not an absolute ``gGlobalTimer`` frame.  The first draft
mistook both statements for one proof, then filled the missing constant from
controller OCR or the old frame map -- the authorities this reader was meant
to replace.

The real join is recorded at capture.  InputSampler reads
``(gGlobalTimer, usamune_overall)`` inside one counter sandwich and the picture
ledger stamps that pair on the distinct picture.  If the picture shows IGT
frame ``shown`` while RAM has reached ``current``, the render delay is
``current - shown`` and the displayed absolute frame is therefore
``gGlobalTimer - (current - shown)``.  No fitted epoch, controller pixels, or
timing constant participates.  This is deliberately local to one picture:
older comments claimed the RAM counter survived subarea loads, but the newer
live-journal epoch analysis proved it restarts there, so no run-wide epoch is
assumed.  If screen and RAM stop sharing a counter domain, their lag becomes
impossible and a sustained mismatch refuses the timer path.

The screen-only arithmetic remains a diagnostic, not a certificate.  A value
can end only in 0, 3, or 6 and consecutive readable values cannot go backward,
but a wrong higher digit can satisfy both.  A slot is called mechanical only
when it also has the coherent RAM pair and a physically possible display lag.
Frozen/unreadable/unpaired stretches are bridged from the prior map and are
reported as bridges, never described as exact.

The digits are the SAME glyphs the stick readout uses, so this needs no
alphabet of its own -- `padread.load_alphabet()[("y", "d1")]` reads them.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

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
# scale. It moves as ONE unit, so one coarse offset (every second pixel over
# +-REGISTER_SPAN) is searched for all five boxes together, then each box
# refines by a pixel. The exhaustive per-box search this replaced cost 77-82 s
# per clip (441 offsets x 5 boxes x 200 frames x every template, measured
# 2026-09-04 on 6592 / 6611) -- his "10x extraction time"; this reaches the
# same offsets in about a second.
REGISTER_SPAN = 10
REGISTER_SAMPLE = 40
REGISTER_COARSE_STEP = 2

# A run of identical readings longer than this is the clock FROZEN (a pause
# menu, a star dance), not duplicate pictures. Measured on his clip: duplicate
# runs reach 3 pictures; the pause froze 1'16"03 across hundreds.
FREEZE_RUN = 4
# The old end-to-end measurement put the screen one frame behind RAM and the
# prototype's healthy joins stayed in single digits.  Twelve is deliberately
# looser than either result, but narrower than one CLOCK seconds digit (30
# frames): a one-second OCR error must never pass merely because it is a legal
# timer value.  This is a refusal guard, not a calibrated correction.
MAX_DISPLAY_LAG_FRAMES = 12
# An isolated reset edge or glyph error can be bridged. A sustained rejection
# means the premise itself is wrong (wrong counter domain/address, or a whole
# glyph run misread), so replacing the rest of that stretch from the old map
# would call an inference mechanical. Refuse the timer path instead.
MAX_REJECTED_RUN = 3
# Fewer independently joined slots than this is not enough coverage to replace
# the existing map.  This is a refusal threshold, not a truth threshold.
MIN_MECHANICAL = 30


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
    monotonic: int = 0       # readable transitions that do not go backward
    backwards: int = 0       # reset boundary or OCR error; screen alone cannot tell
    transitions: int = 0     # readable transitions scored
    pinned: int = 0          # frames the clock names outright

    def as_dict(self) -> dict:
        return {"read": self.read, "legal": self.legal, "pinned": self.pinned,
                "monotonic": self.monotonic,
                "backwards": self.backwards,
                "transitions": self.transitions}


def _box_score(sample: np.ndarray, table: dict, top: int, left: int) -> float:
    """How little the box at (top, left) looks like ANY digit, over the
    sampled frames: the median per-frame distance to the nearest template."""
    box = sample[:, top:top + BOX_H, left:left + BOX_W, :]
    dists = np.stack([
        (np.abs(box - t.median).mean(axis=-1) * t.weight).sum(axis=(1, 2))
        / max(float(t.weight.sum()), 1.0) for t in table.values()])
    return float(np.median(dists.min(axis=0)))


def _box_fits(cells: np.ndarray, top: int, left: int) -> bool:
    return (top >= 0 and left >= 0 and top + BOX_H <= cells.shape[1]
            and left + BOX_W <= cells.shape[2])


def register(cells: np.ndarray, table: dict) -> dict:
    """Each digit box's own (dy, dx) for this clip's capture scale, chosen by
    the offset whose pixels look most like SOME digit across the clip.

    Coarse-to-fine: the five boxes share one shift (the HUD is one bitmap),
    so the coarse pass scores every second offset for all five together and
    the fine pass moves each box by at most a pixel from that answer."""
    if len(cells) == 0:
        return {name: (0, 0) for name in BANDS}
    picks = np.linspace(0, len(cells) - 1,
                        min(REGISTER_SAMPLE, len(cells))).astype(int)
    sample = cells[picks].astype(np.float32)
    coarse = None
    for dy in range(-REGISTER_SPAN, REGISTER_SPAN + 1, REGISTER_COARSE_STEP):
        for dx in range(-REGISTER_SPAN, REGISTER_SPAN + 1, REGISTER_COARSE_STEP):
            total = 0.0
            for x0 in BANDS.values():
                top, left = ROW0 + dy, x0 + dx
                if not _box_fits(cells, top, left):
                    total = None
                    break
                total += _box_score(sample, table, top, left)
            if total is not None and (coarse is None or total < coarse[0]):
                coarse = (total, dy, dx)
    shared_dy, shared_dx = (coarse[1], coarse[2]) if coarse else (0, 0)
    chosen = {}
    for name, x0 in BANDS.items():
        fine = None
        for dy in range(shared_dy - 1, shared_dy + 2):
            for dx in range(shared_dx - 1, shared_dx + 2):
                top, left = ROW0 + dy, x0 + dx
                if not _box_fits(cells, top, left):
                    continue
                score = _box_score(sample, table, top, left)
                if fine is None or score < fine[0]:
                    fine = (score, dy, dx)
        chosen[name] = (fine[1], fine[2]) if fine else (shared_dy, shared_dx)
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
    """Usamune's clock per video frame, plus necessary OCR diagnostics."""
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
    # CHECK 2: a running/reset-free clock never goes backward.  This catches
    # the observed seconds-digit spike, but it is not sufficient to prove all
    # five glyphs (a wrong larger value can still be monotone).
    previous = None
    for one in values:
        if one is None:
            continue
        if previous is not None:
            out.transitions += 1
            if one >= previous:
                out.monotonic += 1
            else:
                # Could be a legitimate retry/subarea epoch. The screen alone
                # cannot distinguish that from a misread, so report the fact
                # and leave the RAM join to decide; do not turn it into a
                # boolean certificate.
                out.backwards += 1
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


@dataclass(frozen=True)
class TimerMapping:
    frame_map: list
    reading: TimerReading
    mechanical: int
    bridged: int
    rejected: int
    display_lags: tuple[int, ...]

    def as_dict(self) -> dict:
        lags = sorted(self.display_lags)
        lag = (None if not lags else {
            "min": lags[0], "median": lags[len(lags) // 2], "max": lags[-1]})
        return {**self.reading.as_dict(),
                "mechanical": self.mechanical,
                "bridged": self.bridged,
                "rejected": self.rejected,
                "display_lag_frames": lag}


def _transition_holds(left, right) -> bool:
    """Can two mechanically joined pictures occur in this order?

    A displayed frame never goes backward. It MAY advance farther than live
    RAM did between the two capture stamps: the display lag is a per-picture
    measurement, not a constant, and when the emulator catches up (lag 1 ->
    lag 0) the screen skips a frame. Measured on his pyramid clip (6592,
    2026-09-04): lag 0 on 587 pictures and 1 on 17, and the pad display,
    scored with no alignment, agreed with the timer's own lag on 18 of those
    19 odd pictures. The old upper bound (mapped delta <= raw delta) rejected
    every lag drop, and the two-deep spike stack then refused 49 of the 50
    pictures after two consecutive lag-1 ones -- and with them the whole
    clip, which fell to the legacy aligner and shipped 2 frames off. A
    backward raw counter is a reset and starts a new epoch rather than being
    compared across it; how far a forward jump may go is bounded by the lag
    range in map_from_clock.
    """
    _lslot, lraw, lmapped, _llag = left
    _rslot, rraw, rmapped, _rlag = right
    if rraw < lraw:
        return True
    return rmapped >= lmapped


def _without_spikes(candidates: list[tuple]) -> tuple[list[tuple], set[int]]:
    """Drop an isolated OCR value that violates both mechanical neighbours.

    This is a refusal, not a repair: the prior map bridges the rejected slot.
    The small stack rule is the linear-time form of the counterfactual -- if
    removing the last value makes the surrounding two agree, the last value
    was the first divergence; otherwise the new value is the one we cannot
    establish.
    """
    accepted: list[tuple] = []
    rejected: set[int] = set()
    for candidate in candidates:
        if not accepted or _transition_holds(accepted[-1], candidate):
            accepted.append(candidate)
            continue
        if len(accepted) >= 2 and _transition_holds(accepted[-2], candidate):
            rejected.add(accepted[-1][0])
            accepted[-1] = candidate
        else:
            rejected.add(candidate[0])
    return accepted, rejected


def _longest_consecutive(slots: set[int]) -> int:
    longest = run = 0
    previous = None
    for slot in sorted(slots):
        run = run + 1 if previous is not None and slot == previous + 1 else 1
        longest = max(longest, run)
        previous = slot
    return longest


def _bridge_from_nearest(exact: list, prior: list) -> list:
    """Shift prior bookkeeping by the nearest mechanical slot across holes."""
    out = list(exact)
    anchors = [slot for slot, value in enumerate(exact)
               if value is not None and slot < len(prior)
               and prior[slot] is not None]
    if not anchors:
        return out
    left: list[int | None] = [None] * len(out)
    right: list[int | None] = [None] * len(out)
    anchor_set = set(anchors)
    nearest = None
    for slot in range(len(out)):
        if slot in anchor_set:
            nearest = slot
        left[slot] = nearest
    nearest = None
    for slot in range(len(out) - 1, -1, -1):
        if slot in anchor_set:
            nearest = slot
        right[slot] = nearest
    for slot in range(len(out)):
        if out[slot] is not None or slot >= len(prior) or prior[slot] is None:
            continue
        before, after = left[slot], right[slot]
        anchor = after if before is None else before
        if after is not None and before is not None \
                and after - slot < slot - before:
            anchor = after
        if anchor is not None:
            out[slot] = prior[slot] + exact[anchor] - prior[anchor]
    return out


def map_from_clock(reading: TimerReading, clock_pairs: list,
                   prior: list) -> TimerMapping | None:
    """Mechanically join each displayed CLOCK value to ``gGlobalTimer``.

    ``clock_pairs[slot]`` is the ledger row's coherently sampled
    ``(gGlobalTimer, usamune_overall)``.  For an advancing clock the delta
    between the RAM and screen IGT values is exactly how far the rendered
    picture trails capture, so subtracting it from the RAM frame names the
    displayed frame directly.
    """
    count = min(len(reading.values), len(clock_pairs), len(prior))
    candidates = []
    impossible: set[int] = set()
    for slot in range(count):
        value = reading.values[slot]
        pair = clock_pairs[slot]
        if value is None or reading.frozen[slot] or pair is None:
            continue
        if value % 10 not in (0, 3, 6):
            impossible.add(slot)
            continue
        raw, current_igt = pair
        shown_igt = frames_of(value)
        lag = current_igt - shown_igt
        if not 0 <= lag <= MAX_DISPLAY_LAG_FRAMES:
            impossible.add(slot)
            continue
        candidates.append((slot, int(raw), int(raw) - lag, lag))
    accepted, spikes = _without_spikes(candidates)
    rejected = impossible | spikes
    if (len(accepted) < MIN_MECHANICAL
            or _longest_consecutive(rejected) > MAX_REJECTED_RUN):
        return None
    exact: list = [None] * len(prior)
    lags = []
    for slot, _raw, mapped, lag in accepted:
        exact[slot] = mapped
        lags.append(lag)
    built = _bridge_from_nearest(exact, prior)
    bridged = sum(1 for slot, value in enumerate(built)
                  if value is not None and exact[slot] is None)
    return TimerMapping(frame_map=built, reading=reading,
                        mechanical=len(accepted), bridged=bridged,
                        rejected=len(rejected),
                        display_lags=tuple(lags))


def read_clip(clip: Path, prior: list, clock_pairs: list,
              ffmpeg: str, reference: dict | None = None
              ) -> TimerMapping | None:
    """Decode one clip and return a map only when the mechanical join covers it."""
    from sm64_events.replay import padread

    if not prior or not clock_pairs:
        return None
    cells = padread.decode_region(ffmpeg, clip, REGION, WIDTH, HEIGHT)
    if len(cells) == 0:
        return None
    count = min(len(cells), len(prior), len(clock_pairs))
    table = (reference if reference is not None
             else padread.load_alphabet()[("y", "d1")])
    reading = read(cells[:count], table, padread.DIST_MAX,
                   padread.MARGIN_MIN, template_of=padread.template_from)
    return map_from_clock(reading, list(clock_pairs), list(prior))
