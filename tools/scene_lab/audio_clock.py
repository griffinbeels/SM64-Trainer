"""Map each presented picture to a sample of the source's own audio (M4 E4-audio).

Two clocks meet here. The scene tap stamps every present with Project64's
QueryPerformanceCounter (tap.py Present.qpc). The recorder's process tap
(replay/audio.py ProcessAudioSource) counts samples on the audio device's
crystal and hands them over in chunks; a fanout stamps each chunk with the
same QPC when it arrives (Python's perf_counter is QPC on Windows). Nothing
else relates the two: the device clock runs some ppm off QPC (docs/
architecture.md measured audio ~101 ppm slow against the wall clock, ~147 ppm
net against the old video clock, 2026-06-17), so a fixed offset drifts by
seconds over a session.

A chunk cannot arrive before its last sample exists, and callback jitter only
ever delays it. So the samples' true clock is the LOWER ENVELOPE of (sample
count, arrival): `fit_envelope` is the linear-programming fit of Moon, Skelly
and Towsley ("Estimation and removal of clock skew from network delay
measurements", INFOCOM 1999) -- the line under every point that minimises the
summed distance to them, which is the lower convex hull edge spanning the
mean sample index. Its slope is the device rate against QPC (drift); its
residuals are the delivery jitter.

The stream is split where its count stops telling time: a new tap epoch (a
reopened tap counts from 0 again), or a lasting step of the windowed minimum
(a HOLE: time passed with no samples, e.g. a packet the pump dropped; an
OVERRUN: more samples than time). A step that does not last -- a stall whose
backlog arrives later -- moves no minimum and splits nothing. A chunk the
fanout could not keep (`lost`) still occupies its counted samples: its time is
known, its content is not, and it is marked as a loss span, never hidden.
Long runs fit piecewise wherever the envelope bends away from one line.

`build_clock` returns integer segment parameters (anchor tick, QPC ticks per
sample as a 32.32 fixed-point value) and maps every present with integer
arithmetic alone, so any reader re-derives exactly the same sample from the
stored parameters (audio_layer.py checks this on decode).
"""
from __future__ import annotations

import bisect
import math
from collections import deque
from dataclasses import dataclass, field

import numpy as np

COVERED, LOSS, GAP, BEFORE, AFTER = 0, 1, 2, 3, 4
STATUS_NAMES = ("covered", "loss", "gap", "before", "after")
MEASURED, JOINED = 1, 2                 # segment flags
Q = 32                                  # fixed-point bits of period_q32

MAX_CHUNKS = 1 << 24
MAX_CHUNK_SAMPLES = 1 << 20
MAX_PRESENTS = 1 << 24
MAX_EPOCHS = 1 << 12
MAX_PPM = 2000.0                        # a device rate further off QPC than this is not trusted


class ClockError(ValueError):
    """The stamps are malformed: out of order, overlapping, or out of bounds."""


@dataclass(frozen=True, slots=True)
class Chunk:
    epoch: int          # tap stream epoch: a reopened tap starts a new one, counting from 0
    first: int          # its first sample's index in the epoch (lost samples are counted too)
    samples: int
    arrival: int        # QPC tick at which the fanout received it (after its last sample existed)
    lost: bool = False  # the bounded fanout dropped its bytes: time known, content not


@dataclass(frozen=True, slots=True)
class Present:
    sequence: int       # the tap's record sequence (tap.py Present.sequence)
    qpc: int


@dataclass(frozen=True, slots=True)
class Segment:
    epoch: int
    first: int          # first sample, as the epoch counts it
    samples: int
    stream_first: int   # where its samples start in the stored stream
    anchor: int         # tick of its first sample, relative to the origin (the first present)
    period_q32: int     # ticks per sample * 2**32
    flags: int          # MEASURED: rate fitted from the stamps; JOINED: no gap before it

    def time_of(self, offset: int) -> int:
        """Tick (relative) at which sample `offset` of this segment sounds."""
        return self.anchor + ((offset * self.period_q32) >> Q)

    @property
    def end(self) -> int:
        return self.time_of(self.samples)

    def sample_at(self, ticks: int) -> int:
        """The segment's sample sounding at `ticks` (clamped into the segment)."""
        if ticks <= self.anchor:
            return 0
        return min(self.samples - 1, ((ticks - self.anchor) << Q) // self.period_q32)


@dataclass(frozen=True, slots=True)
class Picture:
    sequence: int
    ticks: int          # presentation tick relative to the origin
    status: int
    sample: int         # stored-stream index sounding at the picture, -1 without one


@dataclass
class AudioClock:
    rate: int
    frequency: int
    origin_qpc: int                 # host-private: absolute ticks never leave this object
    offset_ticks: int
    total_samples: int
    segments: list[Segment]
    losses: list[tuple[int, int]]   # (stream first, samples) whose content is unknown
    pictures: list[Picture]
    stats: dict = field(default_factory=dict)


def _validate(chunks: list[Chunk], presents: list[Present], rate: int, frequency: int) -> None:
    if type(rate) is not int or not 8000 <= rate <= 192000:
        raise ClockError("sample rate out of range")
    if type(frequency) is not int or not 1000 <= frequency <= 10**10:
        raise ClockError("clock frequency out of range")
    if len(chunks) > MAX_CHUNKS or len(presents) > MAX_PRESENTS:
        raise ClockError("too many stamps")
    epochs, arrival, expect = 0, None, None
    for i, c in enumerate(chunks):
        if not all(type(v) is int for v in (c.epoch, c.first, c.samples, c.arrival)):
            raise ClockError(f"chunk {i}: integer stamps required")
        if not 0 < c.samples <= MAX_CHUNK_SAMPLES or c.first < 0 or c.arrival < 0:
            raise ClockError(f"chunk {i}: sample count or stamp out of range")
        if i and c.epoch < chunks[i - 1].epoch:
            raise ClockError(f"chunk {i}: epoch moved backward")
        if not i or c.epoch != chunks[i - 1].epoch:
            epochs += 1
            expect = c.first
        if c.first != expect:
            raise ClockError(f"chunk {i}: sample count is not contiguous within its epoch")
        expect = c.first + c.samples
        if arrival is not None and c.arrival < arrival:
            raise ClockError(f"chunk {i}: arrival moved backward")
        arrival = c.arrival
    if epochs > MAX_EPOCHS:
        raise ClockError("too many tap epochs")
    for i, p in enumerate(presents):
        if type(p.sequence) is not int or type(p.qpc) is not int or p.qpc < 0 or p.sequence < 0:
            raise ClockError(f"present {i}: integer stamps required")
        if i and (p.sequence <= presents[i - 1].sequence or p.qpc < presents[i - 1].qpc):
            raise ClockError(f"present {i}: presents out of order")


def _sliding_min(values: list[int], times: list[int], lo: int, hi: int, window: int, forward: bool) -> list[int]:
    """Per index k in [lo, hi): min of values over the chunks of [lo, hi)
    arriving within `window` after (forward) or before (backward) times[k],
    k itself included."""
    out = [0] * (hi - lo)
    q: deque = deque()
    if forward:
        j = lo
        for k in range(lo, hi):
            while j < hi and times[j] <= times[k] + window:
                while q and values[q[-1]] >= values[j]:
                    q.pop()
                q.append(j)
                j += 1
            while q[0] < k:
                q.popleft()
            out[k - lo] = values[q[0]]
    else:
        start = lo
        for k in range(lo, hi):
            while q and values[q[-1]] >= values[k]:
                q.pop()
            q.append(k)
            while times[start] < times[k] - window:
                start += 1
            while q[0] < start:
                q.popleft()
            out[k - lo] = values[q[0]]
    return out


def split_runs(ends: list[int], arrivals: list[int], period: float, window: int, hole: int):
    """Split one epoch's chunks where the arrival-minus-count floor steps.

    The floor before chunk k is the minimum offset of the window behind it; the
    floor after is the minimum of the window ahead. A hole (time passed with no
    samples) lifts every later offset, an overrun (more samples than time)
    lowers them. Only full windows are compared, so one jittered chunk at a
    run's start or the stream's end cannot pose as a step; a step inside the
    stream's first or last window is not split (its pictures keep one line).

    Which chunk opens the new run is decided by level, and on the side that
    cannot bend a fit: a chunk on the wrong side of a cut lies ABOVE its run's
    lower envelope (ignored by it), never below. Returns [(start, stop,
    step_ticks)]: chunk ranges and the step that opened each run after the
    first (positive: a hole, negative: an overrun)."""
    offsets = [a - round(e * period) for a, e in zip(arrivals, ends, strict=True)]
    tol = hole // 3
    runs, start, step, n = [], 0, 0, len(ends)
    while start < n:
        fwd = _sliding_min(offsets, arrivals, start, n, window, True)
        back = _sliding_min(offsets, arrivals, start, n, window, False)
        cut = None
        for k in range(start + 1, n):
            if arrivals[k - 1] - arrivals[start] < window or arrivals[-1] - arrivals[k] < window:
                continue
            before = back[k - 1 - start]
            if abs(fwd[k - start] - before) <= hole:
                continue
            reach = bisect.bisect_right(arrivals, arrivals[k] + window, k, n)
            if fwd[k - start] > before:
                # Hole: the new floor is what settles a little later; the cut is the
                # first chunk from which nothing falls back below it.
                settled = bisect.bisect_left(arrivals, arrivals[k] + window // 4, k, reach)
                floor = min(offsets[settled:reach]) if settled < reach else fwd[k - start]
                cut, low = reach - 1, offsets[reach - 1]
                for j in range(reach - 1, k - 1, -1):
                    low = min(low, offsets[j])
                    if low >= floor - tol:
                        cut = j
                    else:
                        break
                found = floor - before
            else:
                # Overrun: the forward window sees it early; the cut is the first chunk
                # clearly below the old floor.
                cut = next(j for j in range(k, reach) if offsets[j] < before - tol)
                found = min(offsets[cut:reach]) - before
            break
        runs.append((start, n if cut is None else cut, step))
        if cut is None:
            break
        step, start = found, cut
    return runs


def fit_envelope(xs: list[int], ys: list[int]) -> tuple[float, float] | None:
    """(a, b) of the line y = a + b*x under every point, minimising the summed
    distance to them (Moon et al. 1999): the lower convex hull edge whose x
    range holds the mean x. Integer cross products keep it exact at QPC scale.
    None for fewer than two distinct x."""
    hull: list[tuple[int, int]] = []
    for point in zip(xs, ys, strict=True):
        while len(hull) >= 2:
            (x1, y1), (x2, y2) = hull[-2], hull[-1]
            if (x2 - x1) * (point[1] - y1) - (y2 - y1) * (point[0] - x1) <= 0:
                hull.pop()
            else:
                break
        hull.append(point)
    if len(hull) < 2:
        return None
    mean = sum(xs) / len(xs)
    for (x1, y1), (x2, y2) in zip(hull, hull[1:], strict=False):
        if x2 >= mean:
            slope = (y2 - y1) / (x2 - x1)
            return y1 - slope * x1, slope
    (x1, y1), (x2, y2) = hull[-2], hull[-1]
    slope = (y2 - y1) / (x2 - x1)
    return y1 - slope * x1, slope


def _envelope_bend(xs: list[int], ys: list[int], a: float, b: float, bin_ticks: int) -> float:
    """Largest per-bin minimum residual: how far the envelope leaves the line."""
    worst, low, edge = 0.0, None, None
    for x, y in zip(xs, ys, strict=True):
        r = y - (a + b * x)
        if edge is None or y >= edge:
            if low is not None:
                worst = max(worst, low)
            low, edge = r, y + bin_ticks
        else:
            low = min(low, r)
    return max(worst, low or 0.0)


def _fit_pieces(xs, ys, period, frequency, min_fit, tolerance, depth=0):
    """[(first index, stop index, a, b, measured)] covering the run, split in
    two (by time) while one line leaves the envelope by more than tolerance."""
    span = ys[-1] - ys[0]
    fit = fit_envelope(xs, ys) if span >= min_fit else None
    measured = fit is not None and abs(fit[1] / period - 1) * 1e6 <= MAX_PPM
    if not measured:
        b = period
        a = min(y - b * x for x, y in zip(xs, ys, strict=True))
        return [(0, len(xs), a, b, False)]
    a, b = fit
    if depth < 8 and span >= 4 * min_fit and _envelope_bend(xs, ys, a, b, 5 * frequency) > tolerance:
        mid = next(i for i, y in enumerate(ys) if y >= ys[0] + span // 2)
        if 0 < mid < len(xs) - 1:
            left = _fit_pieces(xs[:mid + 1], ys[:mid + 1], period, frequency, min_fit, tolerance, depth + 1)
            right = _fit_pieces(xs[mid:], ys[mid:], period, frequency, min_fit, tolerance, depth + 1)
            # The shared point ends the left run and anchors the right; pieces never overlap in samples.
            return ([(s, min(e, mid), pa, pb, m) for s, e, pa, pb, m in left if s < mid]
                    + [(s + mid, e + mid, pa, pb, m) for s, e, pa, pb, m in right])
    return [(0, len(xs), a, b, True)]


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(p * (len(ordered) - 1) + 0.5))]


def build_clock(chunks: list[Chunk], presents: list[Present], *, rate: int = 48000,
                frequency: int = 10_000_000, offset_ticks: int = 0, window_s: float = 1.0,
                hole_s: float = 0.003, min_fit_s: float = 2.0, tolerance_s: float = 0.001) -> AudioClock:
    """Fit the audio clock from chunk stamps and map every present to a sample.

    `offset_ticks` shifts picture time before mapping (the measured difference
    between when a picture shows and when its audio sounds; 0 until a live
    witness measures it). A picture's sample is the one sounding at its
    presentation; its status says whether that time has audio (covered), only
    lost content (loss), no audio at all (gap) or lies outside the recording."""
    chunks, presents = list(chunks), list(presents)
    _validate(chunks, presents, rate, frequency)
    if type(offset_ticks) is not int or abs(offset_ticks) > frequency * 10:
        raise ClockError("audio offset out of range")
    origin = presents[0].qpc if presents else (chunks[0].arrival if chunks else 0)
    period = frequency / rate
    window, hole = round(window_s * frequency), round(hole_s * frequency)
    min_fit, tolerance = round(min_fit_s * frequency), tolerance_s * frequency

    segments: list[Segment] = []
    losses: list[tuple[int, int]] = []
    seg_stats, steps, jitter = [], [], []
    stream = 0
    epoch_starts = [i for i in range(len(chunks)) if not i or chunks[i].epoch != chunks[i - 1].epoch]
    for e_index, e_start in enumerate(epoch_starts):
        e_stop = epoch_starts[e_index + 1] if e_index + 1 < len(epoch_starts) else len(chunks)
        part = chunks[e_start:e_stop]
        ends = [c.first + c.samples for c in part]
        arrivals = [c.arrival - origin for c in part]
        for r_index, (r_start, r_stop, step) in enumerate(split_runs(ends, arrivals, period, window, hole)):
            if r_index:
                steps.append(step)
            run = part[r_start:r_stop]
            xs, ys = ends[r_start:r_stop], arrivals[r_start:r_stop]
            for p_index, (s, t, a, b, measured) in enumerate(
                    _fit_pieces(xs, ys, period, frequency, min_fit, tolerance)):
                first = run[s].first
                samples = run[t - 1].first + run[t - 1].samples - first
                residuals = [y - (a + b * x) for x, y in zip(xs[s:t], ys[s:t], strict=True)]
                jitter += residuals
                flags = (MEASURED if measured else 0) | (JOINED if p_index else 0)
                segment = Segment(part[0].epoch, first, samples, stream, round(a + b * first),
                                  round(b * (1 << Q)), flags)
                segments.append(segment)
                seg_stats.append({
                    "epoch": segment.epoch, "samples": samples, "seconds": round(samples / rate, 3),
                    "measured": measured, "drift_ppm": round((period / b - 1) * 1e6, 2),
                    "jitter_us": {"p50": round(_percentile(residuals, .5) * 1e6 / frequency, 1),
                                  "p99": round(_percentile(residuals, .99) * 1e6 / frequency, 1),
                                  "max": round(max(residuals, default=0) * 1e6 / frequency, 1)},
                })
                stream += samples
            # Lost chunks become stream spans: the run's chunks fill its pieces contiguously.
            base = stream - (run[-1].first + run[-1].samples - run[0].first)
            for c in run:
                if c.lost:
                    span = (base + c.first - run[0].first, c.samples)
                    if losses and losses[-1][0] + losses[-1][1] == span[0]:
                        losses[-1] = (losses[-1][0], losses[-1][1] + span[1])
                    else:
                        losses.append(span)

    if any(b.anchor < a.anchor for a, b in zip(segments, segments[1:], strict=False)):
        raise ClockError("audio clock moved backward across a whole segment")
    pictures = [_map(p.sequence, p.qpc - origin, offset_ticks, segments, losses) for p in presents]
    counts = {name: 0 for name in STATUS_NAMES}
    for picture in pictures:
        counts[STATUS_NAMES[picture.status]] += 1
    measured = [s for s in seg_stats if s["measured"]]
    stats = {
        "segments": seg_stats,
        "epochs": len(epoch_starts),
        "holes": sum(1 for s in steps if s > 0),
        "overruns": sum(1 for s in steps if s < 0),
        "steps_ms": [round(s * 1e3 / frequency, 3) for s in steps],
        "lost_samples": sum(n for _, n in losses),
        "drift_ppm": (round(sum(s["drift_ppm"] * s["samples"] for s in measured)
                            / sum(s["samples"] for s in measured), 2) if measured else None),
        "jitter_us": {"p50": round(_percentile(jitter, .5) * 1e6 / frequency, 1),
                      "p99": round(_percentile(jitter, .99) * 1e6 / frequency, 1)},
        "pictures": counts,
    }
    return AudioClock(rate, frequency, origin, offset_ticks, stream, segments, losses, pictures, stats)


def locate(ticks: int, segments: list[Segment], losses: list[tuple[int, int]]) -> tuple[int, int]:
    """(status, stored-stream sample) sounding at relative `ticks`.

    The newest segment starting at or before `ticks` owns it (a later epoch or
    an overrun overrides what an earlier one still claims). A time past that
    segment's end is a gap unless the next segment joins it without one."""
    lo, hi = 0, len(segments)
    while lo < hi:                       # last segment with anchor <= ticks
        mid = (lo + hi) // 2
        if segments[mid].anchor <= ticks:
            lo = mid + 1
        else:
            hi = mid
    index = lo - 1
    if index < 0:
        return BEFORE, -1
    segment = segments[index]
    if ticks >= segment.end:
        following = segments[index + 1] if index + 1 < len(segments) else None
        if following is None:
            return AFTER, -1
        if not following.flags & JOINED:
            return GAP, -1
        sample = following.stream_first        # the junction between two fitted pieces of one run
    else:
        sample = segment.stream_first + segment.sample_at(ticks)
    lo, hi = 0, len(losses)
    while lo < hi:
        mid = (lo + hi) // 2
        if losses[mid][0] <= sample:
            lo = mid + 1
        else:
            hi = mid
    if lo and sample < losses[lo - 1][0] + losses[lo - 1][1]:
        return LOSS, sample
    return COVERED, sample


def _map(sequence: int, ticks: int, offset: int, segments, losses) -> Picture:
    status, sample = locate(ticks + offset, segments, losses)
    return Picture(sequence, ticks, status, sample)


def stream_pcm(chunks: list[Chunk], payloads: list[bytes | None], channels: int = 2) -> np.ndarray:
    """The stored stream: every chunk's s16 PCM in order, lost chunks as
    silence (their span is in AudioClock.losses). (samples, channels) int16."""
    if len(chunks) != len(payloads):
        raise ClockError("one payload per chunk required")
    parts = []
    for i, (chunk, data) in enumerate(zip(chunks, payloads, strict=True)):
        if chunk.lost:
            if data is not None:
                raise ClockError(f"chunk {i}: a lost chunk carries no bytes")
            parts.append(np.zeros((chunk.samples, channels), np.int16))
            continue
        if type(data) is not bytes or len(data) != chunk.samples * channels * 2:
            raise ClockError(f"chunk {i}: payload length does not match its sample count")
        parts.append(np.frombuffer(data, np.int16).reshape(-1, channels))
    if not parts:
        return np.zeros((0, channels), np.int16)
    return np.concatenate(parts)


def picture_spans(clock: AudioClock) -> list[tuple[int, int]]:
    """(first sample, samples) each covered picture plays at 1x forward: from
    its own sample to the next picture's, so a presentation-length hold plays
    exactly the source audio heard during it. Others get (-1, 0)."""
    spans = []
    pictures = clock.pictures
    for i, picture in enumerate(pictures):
        if picture.status not in (COVERED, LOSS):
            spans.append((-1, 0))
            continue
        nxt = pictures[i + 1] if i + 1 < len(pictures) else None
        if nxt is not None and nxt.status in (COVERED, LOSS) and nxt.sample >= picture.sample:
            spans.append((picture.sample, nxt.sample - picture.sample))
        else:
            spans.append((picture.sample, 0))
    return spans


def ppm_of(segment: Segment, rate: int, frequency: int) -> float:
    """The segment's device rate against the clock, in ppm (negative: slow)."""
    return (frequency / rate * (1 << Q) / segment.period_q32 - 1) * 1e6 if segment.period_q32 else math.nan
