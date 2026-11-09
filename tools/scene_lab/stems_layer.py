# SPDX-License-Identifier: GPL-2.0-or-later
# Runs in the simulation program (SM64TrainerSim.exe); licenses/README.md lists its GPL-2.0-or-later code.
"""The source-stems layer of an attempt scene: its music and its sound effects on its own sound's
samples (M4 round 4, lab only).

    payload = build(audio, first_qpc, rows)        # an audio_layer.Layer, its first present's QPC, stems_live rows
    payload = unavailable("why, in one sentence")  # a scene whose stems could not be made says so
    stems = read(payload, audio)                   # checked; stems.block(track, index) decodes one block

    uv run python tools/scene_lab/stems_layer.py <scene.sm64scene>... --rom <z64>   # each scene's split and numbers

A scene plays its source-audio layer: Project64's output as the recorder heard it ("as heard",
audio_layer.py), with the sample each picture appeared at. The stems come from the game's own
timeline instead: stems_live.py's rows hold, per VI, what Project64's RSP wrote (the game's
output) and the music and effects stems of the same task, at the game's 32006 Hz. A stem played
on the game's timeline would land early by Project64's audio delay, so switching between the
recording and a stem would move every sound. build() puts the stems on the recording's own
samples instead, the recording itself telling where each game sample was heard:

1. The game's output (the rows' real samples in VI order; a VI it lacks as silence of the
   neighbouring length) against the recording: first the delay, from millisecond loudness
   envelopes laid on the QPC both carry (a VI's time, the recording's fitted clock), searched
   over DELAY_S; then each WINDOW_S of the game's output found in the recording to a fraction of
   a sample, with its gain and SNR (stems_check.window_fit, G2's method), twice: the second pass
   reads the game at the rate the first measured.
2. A map from each recording sample to a game position: one slope (the median of the windows'
   pairwise slopes, which a step does not move), and a new piece wherever the windows step off
   it by more than FIT_MS: Project64's audio dropping or repeating samples. The newest piece
   starting at or before a recording sample owns it.
3. Music and effects read through the map (stems_check.interp, one kernel for the four
   channels), times the windows' median gain, rounded to int16, one block at a time. Where the
   map lands outside the rows, or on a VI the renderer missed or could not split, the tracks
   hold silence and a gap says so.

It refuses (unavailable, with the reason) when the windows' median SNR stays under PASS_DB (the
recording is not this output: another session, or Project64 played something else), when the
renderer's sampled G1 saw the microcode's sound differ from Project64's by more than rounding
(G1_PASS_DB), or when most of the scene has no split. "stats" records, per scene: the stems' sum
against the game's output (G3, settled VIs), G1's checks, its largest difference and its SNR, the
windows' SNR, the delay, the drift, the gain, the pieces, the gaps and the time it took. The
reason is for the logs; the viewer says one plain sentence (scene_audio.NOT_SPLIT).

Why G1 allows rounding (2026-10-27, round 5 item 4). Every take of his first live session was
refused: the sampled G1 differed on 22 of 28 to 53 of 53 checks per take. His Project64 runs the
audio microcode in its RSP plugin ("RSP emulation Plugin 1.6 by Jabo & Zilmar", which wermi's
build ships inside PJ64Fixups), not by high-level emulation (Audio HLE 0), on its recompiler (CPU
Core 1). That plugin, loaded offline on the same tasks as cxd4 (a savestate's live spool):
its interpreter matches cxd4 on 300 of 300 tasks; its recompiler differs on 222 of 300, up to
220 units, 35 dB SNR at the median task and 27 dB at the tenth percentile; a second savestate
24 to 31 dB over 120-task stretches, up to 598 units. Carrying the reverb ring through each
chain separately (as the stems do) costs at most 1.5 dB more. So the stems are the microcode as
the RSP runs it (cxd4, bit for bit with the plugin's own interpreter), and differ from what a
recompiler played by its rounding; a renderer that is wrong (another program, a misread state)
lands near 0 dB, far below G1_PASS_DB.

Format v1, little-endian: a 96-byte header, the reason or the tables, then the blocks.

    header   magic "SM64STM\\0", version, status (0 split, 1 unavailable), rate, total samples,
             block samples, blocks per track, gaps, reason bytes, stats bytes, sha256 of
             everything after the header
    reason   UTF-8, when unavailable (followed only by the stats)
    stats    UTF-8 JSON, at most MAX_STATS bytes
    blocks   music's (offset, stored bytes, samples, sha256 of the decoded s16 PCM), then effects'
    gaps     (first sample, samples) where the tracks hold silence for want of a split

The tracks tile the source-audio stream exactly: its rate, total samples and block size, so a
block index names the same span in all three (read() checks it against the scene's source-audio
layer). Each block is raw FLAC frames, a complete .flac with audio_layer's STREAMINFO, and
decodes alone.

Why the viewer plays the recording itself while both are on, not music + effects: the recording
is what he heard, bit for bit, and the sum is not quite it. The stems sum to the game's output
within 7 units once their reverb rings have run a second (G3 on savestates and on the stem
renderer's rows), but up to 223 before that; and the recording is the game's output through
Project64's audio path (its rate, its gain, any sound it dropped), which no live session has
measured yet (G2). Placing the stems on the recording's samples keeps every switch seamless
either way.

Evidence (2026-10-27): synthetic noise with a 137 ms delay, 120 ppm of drift, a 0.85 gain and
one dropped VI: music + effects 56 to 58 dB from the recording, each stem 66 to 70 dB from the
simulation's truth, the drop found. The game's real audio (the stem renderer's rows from a
savestate's live spool) with a recording synthesized the same way: 60.0 dB, music 74.8 dB and
effects 73.1 dB, with or without the drop; 1.4 s to build for an 18.5 s scene. A scene's own
numbers are in its layer's stats.
"""
from __future__ import annotations

import hashlib
import json
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audio_clock as ac  # noqa: E402
import audio_layer as al  # noqa: E402
import stems_check as sc  # noqa: E402

MAGIC = b"SM64STM\x00"
VERSION = 1
LAYER = ("source-stems", 1)
SPLIT, UNAVAILABLE = 0, 1
TRACKS = ("music", "effects")
HEADER = struct.Struct("<8sHHIQIIIII32s")
BLOCK = al.BLOCK
GAP = al.LOSS
MAX_STATS = 1 << 14
MAX_REASON = 1 << 10

GAME_RATE = sc.sp.AI_RATE
STEP_S = 1.0                    # a window of the game's output every second
COARSE_S, COARSE_SEARCH_S = 1.0, 0.08   # pass one: a second at the nominal rate, within 80 ms of the clocks' guess
FINE_S, FINE_SEARCH_S = 0.25, 0.004     # pass two: a quarter second at the measured rate, within 4 ms of pass one
DELAY_S = (-0.25, 1.5)          # where the recording may hear a VI's sound, after its QPC
ENVELOPE_HZ = 1000
FIT_MS = 1.0                    # windows this close to the line share a piece of the map
MIN_RMS = 30.0                  # a window this quiet does not place itself
FIRST_NCC = 0.3                 # pass one reads at the nominal rate, so drift blurs it: correlation only
PASS_DB = 20.0                  # the recording is this output: a misplaced window scores near 0 dB
G1_PASS_DB = 12.0               # the microcode made this output: a recompiler's rounding measured 24 dB and up
MAX_GAP_FRACTION = 0.5          # a scene mostly without a split has none
LEVEL = 5                       # FLAC compression level (audio_layer's own is 8, for one track)
TYPICAL_TASK = 534              # samples a VI's task makes at 32006 Hz and 60 Hz (533 or 534)
# Why a scene has no split, as the viewer says it (the numbers go in the layer's stats).
NO_ROWS = "Music and sound effects were not being split when this scene was played."
SHORT = "This scene is too short to line its game sound up with its recording."
QUIET = "This scene's game sound is too quiet to line up with its recording."
MISMATCH = "This scene's recording does not match the game's own sound, so it cannot be split."
DIFFERS = "The stem renderer's sound differs from what Project64 played by more than rounding (G1)."
MOSTLY_GAPS = "Most of this scene's game sound could not be split."


class StemsError(ValueError):
    """The stems layer is malformed, corrupt, or does not fit its scene's sound."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise StemsError(message)


# ---------------------------------------------------------------- the game's streams from the rows

@dataclass
class GameStreams:
    real: np.ndarray        # (samples, 2) int16: what Project64's RSP wrote, silence where unread
    music: np.ndarray
    effects: np.ndarray
    heard: np.ndarray       # bool per sample: the game's output is known there
    split: np.ndarray       # bool per sample: the stems are the game's split there
    qpc: np.ndarray         # int64 per task row: the VI's QPC
    first: np.ndarray       # int64 per task row: its first sample in the streams
    stats: dict


def game_streams(rows: list[dict]) -> GameStreams:
    """The rows (stems_live.read_rows, in VI order) as three streams on the game's timeline. A VI
    whose task the renderer missed, or one lost from the recording, takes the length of the last
    task as silence, marked neither heard nor split, so the timeline keeps its length."""
    import stems_live as sl
    real, music, effects, heard, split, qpcs, firsts = [], [], [], [], [], [], []
    at, length, previous = 0, TYPICAL_TASK, None
    counts = {"rows": len(rows), "tasks": 0, "settled": 0, "split_failed": 0, "missed": 0, "lost_vis": 0,
              "g1_checked": 0, "g1_differs": 0, "g1_max_abs": 0, "stem_sum_max_abs": 0}
    g1_signal = g1_error = 0.0
    for row in rows:
        flags = row["flags"]
        if flags & sl.CHECKED and row["real"] is not None:
            values = row["real"].size
            g1_signal += float(np.sum(row["real"].astype(np.float64) ** 2))
            rms = row.get("g1_rms", 0) / 1000 or (row["g1"] if flags & sl.DIFFERS else 0)   # a row without it: the bound
            g1_error += rms * rms * values
            counts["g1_max_abs"] = max(counts["g1_max_abs"], row["g1"])
        lost = row["vi"] - previous - 1 if previous is not None and row["vi"] > previous + 1 else 0
        previous = row["vi"]
        counts["lost_vis"] += lost
        counts["missed"] += bool(flags & sl.MISSED)
        missing = (lost + bool(flags & sl.MISSED)) * length
        if missing:
            for stream in (real, music, effects):
                stream.append(np.zeros((missing, 2), np.int16))
            heard.append(np.zeros(missing, bool))
            split.append(np.zeros(missing, bool))
            at += missing
        counts["g1_checked"] += bool(flags & sl.CHECKED)
        counts["g1_differs"] += bool(flags & sl.DIFFERS)
        if not flags & sl.TASK:
            continue
        samples = row["samples"]
        output = row["real"] if row["real"] is not None else np.zeros((samples, 2), np.int16)
        qpcs.append(row["qpc"])
        firsts.append(at)
        real.append(output)
        music.append(row["music"])
        effects.append(row["effects"])
        heard.append(np.full(samples, row["real"] is not None))
        split.append(np.full(samples, not flags & sl.SPLIT_FAILED))
        counts["tasks"] += 1
        counts["settled"] += bool(flags & sl.SETTLED)
        counts["split_failed"] += bool(flags & sl.SPLIT_FAILED)
        if samples and row["real"] is not None and flags & sl.SETTLED and not flags & sl.SPLIT_FAILED:
            counts["stem_sum_max_abs"] = max(counts["stem_sum_max_abs"], int(np.abs(
                row["music"].astype(np.int32) + row["effects"] - output).max()))
        at += samples
        length = samples or length
    if counts["g1_checked"]:
        counts["g1_snr_db"] = round(10 * np.log10(g1_signal / g1_error), 2) if g1_error else None   # None: identical

    def stereo(parts):
        return np.concatenate(parts) if parts else np.zeros((0, 2), np.int16)
    return GameStreams(stereo(real), stereo(music), stereo(effects),
                       np.concatenate(heard) if heard else np.zeros(0, bool),
                       np.concatenate(split) if split else np.zeros(0, bool),
                       np.array(qpcs, np.int64), np.array(firsts, np.int64), counts)


# ---------------------------------------------------------------- where the recording heard each game sample

def _bins(values: np.ndarray, times: np.ndarray, origin: float, tick: float, total: np.ndarray,
          count: np.ndarray) -> None:
    index = np.floor((times - origin) / tick).astype(np.int64)
    keep = (index >= 0) & (index < len(total))
    total += np.bincount(index[keep], np.abs(values[keep]), len(total))
    count += np.bincount(index[keep], minlength=len(total))


def _game_envelope(game: GameStreams, mono: np.ndarray, frequency: int, origin: float, bins: int) -> np.ndarray:
    tick, total, count = frequency / ENVELOPE_HZ, np.zeros(bins), np.zeros(bins, np.int64)
    ends = np.append(game.first[1:], len(mono))
    for qpc, first, end in zip(game.qpc.tolist(), game.first.tolist(), ends.tolist(), strict=True):
        times = qpc + np.arange(end - first) * (frequency / GAME_RATE)
        _bins(mono[first:end], times, origin, tick, total, count)
    return np.where(count > 0, total / np.maximum(count, 1), 0.0)


def _recording_envelope(audio: al.Layer, mono: np.ndarray, first_qpc: int, origin: float, bins: int) -> np.ndarray:
    tick, total, count = audio.frequency / ENVELOPE_HZ, np.zeros(bins), np.zeros(bins, np.int64)
    for segment in audio.segments:
        period = segment.period_q32 / (1 << ac.Q)
        for at in range(0, segment.samples, 1 << 20):
            offsets = np.arange(at, min(segment.samples, at + (1 << 20)))
            times = first_qpc + segment.anchor + offsets * period
            first = segment.stream_first + at
            _bins(mono[first:first + len(offsets)], times, origin, tick, total, count)
    return np.where(count > 0, total / np.maximum(count, 1), 0.0)


def delay_ticks(game: GameStreams, mono: np.ndarray, audio: al.Layer, capture: np.ndarray,
                first_qpc: int) -> tuple[float, float]:
    """(QPC ticks from a VI to when the recording heard its first sample, the loudness
    envelopes' normalised correlation there), the lag searched over DELAY_S."""
    frequency = audio.frequency
    lo, hi = DELAY_S
    start = float(game.qpc[0])
    span = int((hi - lo) * ENVELOPE_HZ)
    length = int((game.qpc[-1] - start) / frequency * ENVELOPE_HZ) + 20
    env_game = _game_envelope(game, mono, frequency, start, length)
    env_heard = _recording_envelope(audio, capture, first_qpc, start + lo * frequency, length + span)
    lag, ncc = sc.xcorr_lag(env_game - env_game.mean(), env_heard - env_heard.mean())
    return (lag / ENVELOPE_HZ + lo) * frequency, ncc


def coarse_fit(game: np.ndarray, capture: np.ndarray, start: int, ratio: float, centre: float) -> float | None:
    """Pass one: where game sample `start` sounds in the recording, to the nearest sample, from a
    second of the game read at the nominal rate correlated within COARSE_SEARCH_S of `centre`;
    None when it does not correlate (FIRST_NCC)."""
    count = int(COARSE_S * GAME_RATE * ratio)
    search = int(COARSE_SEARCH_S * GAME_RATE * ratio)
    lo = int(round(centre)) - search
    if lo < 0 or lo + count + 2 * search > len(capture):
        return None
    reference = sc.interp(game, start + np.arange(count) / ratio)
    if not np.any(reference):
        return None
    shift, ncc = sc.xcorr_lag(reference, capture[lo:lo + count + 2 * search])
    return float(lo + shift) if ncc >= FIRST_NCC else None


def adjacent_slope(xs: np.ndarray, ys: np.ndarray, fallback: float) -> float:
    """The median slope between neighbouring windows: a step (dropped or repeated sound) moves
    only the one pair it lies between."""
    if len(xs) < 2:
        return fallback
    return float(np.median(np.diff(ys) / np.diff(xs)))


def pieces_of(xs: np.ndarray, ys: np.ndarray, slope: float, tolerance: float) -> np.ndarray:
    """Each window's piece: a new one where its intercept leaves the running piece's median."""
    labels, members = [], []
    for intercept in (ys - slope * xs).tolist():
        if members and abs(intercept - np.median(members)) <= tolerance:
            members.append(intercept)
            labels.append(labels[-1])
        else:
            members = [intercept]
            labels.append(labels[-1] + 1 if labels else 0)
    return np.array(labels, np.int64)


@dataclass
class Placement:
    slope: float            # recording samples per game sample
    pieces: list            # (game start, intercept): recording position = slope * game + intercept
    windows: list           # (game start, stems_check.window_fit row) of the fine pass
    gain: float
    delay_ms: float
    envelope_ncc: float


def place(game: GameStreams, audio: al.Layer, capture: np.ndarray, first_qpc: int) -> Placement | tuple:
    """Where the recording heard each game sample, or (the sentence why it could not be told, the
    numbers behind it)."""
    frequency, rate = audio.frequency, audio.rate
    mono = np.where(game.heard, game.real.astype(np.float32).mean(axis=1), 0.0).astype(np.float32)
    if len(game.qpc) < 2:
        return SHORT, {}
    delay, envelope_ncc = delay_ticks(game, mono, audio, capture, first_qpc)
    ratio = rate / GAME_RATE
    count = int(COARSE_S * GAME_RATE)
    starts = [start for start in range(0, len(mono) - count, int(STEP_S * GAME_RATE))
              if game.heard[start:start + count].all()
              and float(np.sqrt(np.mean(mono[start:start + count] ** 2))) >= MIN_RMS]

    def predicted(start: int) -> float:
        """The recording sample at which game sample `start` should sound: its VI's QPC plus the delay."""
        row = int(np.searchsorted(game.first, start, side="right") - 1)
        ticks = game.qpc[row] - first_qpc + (start - game.first[row]) * frequency / GAME_RATE + delay
        status, sample = ac.locate(int(ticks), audio.segments, audio.losses)
        return float(sample) if status in (ac.COVERED, ac.LOSS) else -1.0

    coarse = []
    for start in starts:
        centre = predicted(start)
        found = coarse_fit(mono, capture, start, ratio, centre) if centre >= 0 else None
        if found is not None:
            coarse.append((start, found))
    if not starts:
        return QUIET, {}
    if not coarse:                  # loud enough, yet nothing in the recording looks like it
        return MISMATCH, {"windows": len(starts), "correlated": 0}
    slope = adjacent_slope(np.array([start for start, _ in coarse], np.float64),
                           np.array([found for _, found in coarse]), ratio)

    def fine_pass(slope: float, positions: dict) -> list:
        found = []
        for start, at in positions.items():
            row = sc.window_fit(mono, capture, start, ratio, at - start * ratio, ratio / slope,
                                seconds=FINE_S, search_s=FINE_SEARCH_S)
            if row is not None:
                found.append((start, row))
        return found

    fine = fine_pass(slope, dict(coarse))
    for _ in range(2):              # once at the coarse rate, again at the rate the windows measured
        snrs = [row["snr_db"] for _, row in fine]
        good = [(start, row) for start, row in fine if row["snr_db"] >= PASS_DB]
        if not good or float(np.median(snrs)) < PASS_DB:
            return MISMATCH, {"windows": len(snrs), "snr_db_median": round(float(np.median(snrs)), 2) if snrs else None}
        measured, labels, intercepts, xs = fit_pieces(good, slope, ratio, rate)
        if abs(measured / slope - 1) < 1e-6:
            break
        slope = measured
        fine = fine_pass(slope, {start: start * ratio + row["lag"] for start, row in fine})
    slope = measured
    gain = float(np.median([row["gain"] for _, row in good]))
    starts_of = [0.0]
    for i in np.flatnonzero(np.diff(labels)) + 1:
        piece = int(labels[i])
        starts_of.append(step_at(mono, capture, slope, gain, intercepts[piece - 1], intercepts[piece],
                                 float(xs[i - 1]), float(xs[i])))
    return Placement(slope, list(zip(starts_of, intercepts.tolist(), strict=True)), fine, gain,
                     round(delay * 1000 / frequency, 2), envelope_ncc)


def fit_pieces(good: list, slope: float, ratio: float, rate: int) -> tuple:
    """(slope, each window's piece, each piece's intercept, the windows' middles in game samples):
    one slope and an intercept per piece, least squares over every window's middle (a window
    places its own middle best: a slightly wrong rate tilts it about its centre)."""
    half = int(FINE_S * GAME_RATE * ratio) / 2
    starts = np.array([start for start, _ in good], np.float64)
    xs = starts + half / slope
    ys = starts * ratio + np.array([row["lag"] for _, row in good]) + half
    labels = pieces_of(xs, ys, adjacent_slope(xs, ys, slope), FIT_MS * rate / 1000)
    count = int(labels[-1]) + 1
    if len(xs) > count:
        design = np.zeros((len(xs), 1 + count))
        design[:, 0] = xs - xs[0]
        design[np.arange(len(xs)), 1 + labels] = 1.0
        solution = np.linalg.lstsq(design, ys, rcond=None)[0]
        return float(solution[0]), labels, solution[1:] - solution[0] * xs[0], xs
    return slope, labels, np.array([float(np.mean(ys[labels == p] - slope * xs[labels == p]))
                                    for p in range(count)]), xs


def step_at(game: np.ndarray, capture: np.ndarray, slope: float, gain: float, before: float, after: float,
            x_before: float, x_after: float) -> float:
    """The game sample where the map steps from intercept `before` to `after` between two windows'
    middles: the recording sample that best splits the stretch into one that sounds like the
    first placement and one that sounds like the second (cumulative squared error)."""
    lo = int(np.floor(min(slope * x_before + before, slope * x_before + after)))
    hi = int(np.ceil(max(slope * x_after + before, slope * x_after + after)))
    lo, hi = max(lo, 0), min(hi, len(capture))
    if hi - lo < 2:
        return (x_before + x_after) / 2
    recording = np.arange(lo, hi, dtype=np.float64)
    heard = capture[lo:hi].astype(np.float64)
    errors = []
    for intercept in (before, after):
        positions = (recording - intercept) / slope
        inside = (positions >= 0) & (positions <= len(game) - 1)
        guess = np.zeros(len(recording))
        guess[inside] = sc.interp(game, positions[inside]) * gain
        errors.append((heard - guess) ** 2)
    cost = np.concatenate([[0.0], np.cumsum(errors[0])]) + (errors[1].sum() - np.concatenate([[0.0], np.cumsum(errors[1])]))
    switch = lo + int(np.argmin(cost))
    return (switch - after) / slope


def game_positions(placement: Placement, first: int, count: int) -> np.ndarray:
    """The game position recording samples first..first+count heard: the newest piece starting at
    or before a sample owns it."""
    recording = np.arange(first, first + count, dtype=np.float64)
    starts = np.maximum.accumulate(np.array([placement.slope * start + intercept
                                             for start, intercept in placement.pieces]))
    owner = np.clip(np.searchsorted(starts, recording, side="right") - 1, 0, len(placement.pieces) - 1)
    intercepts = np.array([intercept for _, intercept in placement.pieces])
    return (recording - intercepts[owner]) / placement.slope


# ---------------------------------------------------------------- the payload

def _gaps(valid: np.ndarray) -> list[tuple[int, int]]:
    edges = np.flatnonzero(np.diff(np.concatenate([[1], valid.astype(np.int8), [1]])))
    return [(int(a), int(b - a)) for a, b in zip(edges[0::2], edges[1::2], strict=True)]


def _payload(status: int, reason: str, stats: dict, rate: int = 0, total: int = 0, block_samples: int = 0,
             tracks: dict | None = None, gaps: list | None = None) -> bytes:
    """Header, reason, stats, the block and gap tables, the blocks. `tracks`: name -> [(stored
    bytes, samples, sha256 of the decoded PCM)] per block, in TRACKS order."""
    reason_raw = reason.encode("utf-8")[:MAX_REASON]
    stats_raw = json.dumps(stats, separators=(",", ":")).encode("utf-8")
    _require(len(stats_raw) <= MAX_STATS, "stems stats too large")
    tracks, gaps = tracks or {}, gaps or []
    count = len(tracks[TRACKS[0]]) if tracks else 0
    offset = HEADER.size + len(reason_raw) + len(stats_raw) + BLOCK.size * count * len(tracks) + GAP.size * len(gaps)
    table, data = bytearray(), bytearray()
    for name in TRACKS if tracks else ():
        for stored, samples, digest in tracks[name]:
            table += BLOCK.pack(offset, len(stored), samples, digest)
            data += stored
            offset += len(stored)
    for row in gaps:
        table += GAP.pack(*row)
    rest = reason_raw + stats_raw + bytes(table) + bytes(data)
    header = HEADER.pack(MAGIC, VERSION, status, rate, total, block_samples, count, len(gaps), len(reason_raw),
                         len(stats_raw), hashlib.sha256(rest).digest())
    return header + rest


def _flac_block(part: np.ndarray, rate: int, level: int) -> tuple[bytes, int, bytes]:
    """(stored FLAC frames, samples, sha256 of the PCM) for one block, checked to decode back."""
    part = np.ascontiguousarray(part)
    stored = al._flac_encode(part, rate, level)
    _require(np.array_equal(al._flac_decode(stored, rate, len(part)), part), "FLAC round trip differs")
    return stored, len(part), hashlib.sha256(part.tobytes()).digest()


def encode(audio: al.Layer, music: np.ndarray, effects: np.ndarray, gaps: list | None = None,
           stats: dict | None = None, *, level: int = LEVEL) -> bytes:
    """A split layer from tracks already on the recording's samples ((total, 2) int16 each)."""
    _require(music.shape == effects.shape == (audio.total_samples, 2), "tracks must tile the scene's sound")
    encoded = {name: [_flac_block(track[at:at + audio.block_samples], audio.rate, level)
                      for at in range(0, audio.total_samples, audio.block_samples)]
               for name, track in zip(TRACKS, (music, effects), strict=True)}
    payload = _payload(SPLIT, "", stats or {}, audio.rate, audio.total_samples, audio.block_samples, encoded,
                       list(gaps or []))
    read(payload, audio)
    return payload


def unavailable(reason: str, stats: dict | None = None) -> bytes:
    """A layer that says why this scene has no stems (and carries what was measured)."""
    return _payload(UNAVAILABLE, reason, stats or {})


def build(audio: al.Layer, first_qpc: int, rows: list[dict], *, level: int = LEVEL) -> bytes:
    """The stems layer for the scene whose sound is `audio` (its first present at absolute
    `first_qpc`), from the renderer's rows around it; unavailable(...) when it cannot be split."""
    began = time.perf_counter()
    import stems_live as sl
    if not any(row["flags"] & sl.TASK for row in rows):
        return unavailable(NO_ROWS)
    game = game_streams(rows)
    stats = dict(game.stats)
    if stats["g1_differs"] and (stats.get("g1_snr_db") or 0) < G1_PASS_DB:
        return unavailable(DIFFERS, stats)
    capture = np.concatenate([audio.block(i) for i in range(len(audio.blocks))]).astype(np.float32).mean(axis=1) \
        if audio.blocks else np.zeros(0, np.float32)
    placement = place(game, audio, capture, first_qpc)
    if not isinstance(placement, Placement):
        reason, numbers = placement
        return unavailable(reason, stats | numbers)
    snrs = [row["snr_db"] for _, row in placement.windows]
    stats.update(windows=len(snrs), pieces=len(placement.pieces), gain=round(placement.gain, 4),
                 delay_ms=placement.delay_ms, envelope_ncc=round(placement.envelope_ncc, 3),
                 drift_ppm=round((audio.rate / GAME_RATE / placement.slope - 1) * 1e6, 1),
                 snr_db_median=round(float(np.median(snrs)), 2), snr_db_p10=round(float(np.percentile(snrs, 10)), 2))
    channels = np.concatenate([game.music, game.effects], axis=1)
    encoded = {name: [] for name in TRACKS}
    valid = np.zeros(audio.total_samples, bool)
    for at in range(0, audio.total_samples, audio.block_samples):
        count = min(audio.block_samples, audio.total_samples - at)
        positions = game_positions(placement, at, count)
        inside = (positions >= 0) & (positions <= len(channels) - 1)
        here = inside.copy()
        here[inside] = game.split[np.floor(positions[inside]).astype(np.int64)]
        valid[at:at + count] = here
        block = np.zeros((count, 4), np.float32)
        if here.any():
            block[here] = sc.interp(channels, positions[here])
        block = np.clip(np.rint(block * placement.gain), -32768, 32767).astype(np.int16)
        for number, name in enumerate(TRACKS):
            encoded[name].append(_flac_block(block[:, 2 * number:2 * number + 2], audio.rate, level))
    stats["gap_samples"] = int(audio.total_samples - valid.sum())
    if audio.total_samples and valid.mean() < 1 - MAX_GAP_FRACTION:
        return unavailable(MOSTLY_GAPS, stats)
    stats["build_ms"] = round((time.perf_counter() - began) * 1000, 1)
    payload = _payload(SPLIT, "", stats, audio.rate, audio.total_samples, audio.block_samples, encoded, _gaps(valid))
    read(payload, audio)                       # the writer refuses what a reader would
    return payload


# ---------------------------------------------------------------- the reader

@dataclass(frozen=True)
class Stems:
    """A parsed, checked stems layer; a track's blocks decode on demand."""
    payload: bytes
    status: int
    reason: str
    stats: dict
    rate: int
    total_samples: int
    block_samples: int
    blocks: dict            # track: [(offset, stored, samples, sha256)]
    gaps: list              # (first sample, samples): silence for want of a split

    @property
    def available(self) -> bool:
        return self.status == SPLIT

    def stored(self, track: str, index: int) -> bytes:
        offset, stored, _samples, _digest = self.blocks[track][index]
        return self.payload[offset:offset + stored]

    def block(self, track: str, index: int) -> np.ndarray:
        """One block of a track, decoded and checked against its hash."""
        offset, stored, samples, digest = self.blocks[track][index]
        pcm = al._flac_decode(self.payload[offset:offset + stored], self.rate, samples)
        _require(hashlib.sha256(pcm.tobytes()).digest() == digest, f"{track} block {index} fails its hash")
        return pcm


def read(payload: bytes, audio: al.Layer | None) -> Stems:
    """Parse and check a stems payload against its scene's source-audio layer; StemsError on
    anything malformed, and on tracks that do not tile that layer's stream."""
    _require(isinstance(payload, (bytes, bytearray)) and len(payload) >= HEADER.size, "stems layer truncated")
    payload = bytes(payload)
    (magic, version, status, rate, total, block_samples, count, gap_count, reason_bytes, stats_bytes,
     digest) = HEADER.unpack_from(payload)
    _require(magic == MAGIC, "not a stems layer")
    _require(version == VERSION, f"unknown stems layer version {version}")
    _require(status in (SPLIT, UNAVAILABLE), f"unknown stems status {status}")
    _require(reason_bytes <= MAX_REASON and stats_bytes <= MAX_STATS, "stems layer text out of bounds")
    _require(hashlib.sha256(payload[HEADER.size:]).digest() == digest, "stems layer fails its hash")
    at = HEADER.size
    _require(at + reason_bytes + stats_bytes <= len(payload), "stems layer truncated")
    reason = payload[at:at + reason_bytes].decode("utf-8", "replace")
    at += reason_bytes
    try:
        stats = json.loads(payload[at:at + stats_bytes].decode("utf-8")) if stats_bytes else {}
    except ValueError:
        raise StemsError("stems layer stats do not parse") from None
    _require(isinstance(stats, dict), "stems layer stats are not an object")
    at += stats_bytes
    if status == UNAVAILABLE:
        _require(bool(reason) and count == 0 and gap_count == 0 and at == len(payload),
                 "an unavailable stems layer carries its reason and nothing else")
        return Stems(payload, status, reason, stats, 0, 0, 0, {}, [])
    _require(audio is not None, "stems need the scene's source-audio layer")
    _require((rate, total, block_samples, count) == (audio.rate, audio.total_samples, audio.block_samples,
                                                    len(audio.blocks)), "the stems do not tile the scene's sound")
    _require(gap_count <= al.MAX_LOSSES, "stems gaps out of bounds")
    _require(at + BLOCK.size * count * len(TRACKS) + GAP.size * gap_count <= len(payload), "stems tables truncated")
    blocks = {}
    for name in TRACKS:
        blocks[name] = [BLOCK.unpack_from(payload, at + BLOCK.size * i) for i in range(count)]
        at += BLOCK.size * count
    gaps = [GAP.unpack_from(payload, at + GAP.size * i) for i in range(gap_count)]
    at += GAP.size * gap_count
    end = 0
    for i, (first, samples) in enumerate(gaps):
        _require(samples > 0 and first >= end and first + samples <= total, f"stems gap {i} out of order")
        end = first + samples
    for name in TRACKS:
        for i, (start, stored, samples, _digest) in enumerate(blocks[name]):
            _require(start == at, f"{name} block {i} is not where the previous one ends")
            _require(samples == audio.blocks[i][2], f"{name} block {i} sample count")
            _require(0 < stored <= samples * 4 + 64 * (samples // al.FLAC_FRAME + 1) + 4096,
                     f"{name} block {i} stored size out of range")
            at += stored
    _require(at == len(payload), "stems layer length differs from its blocks")
    return Stems(payload, status, reason, stats, rate, total, block_samples, blocks, gaps)


def main() -> int:
    """One JSON line per scene: whether it is split, why not, and its layer's numbers."""
    import argparse

    import package
    parser = argparse.ArgumentParser(description="Print each attempt scene's music and effects split.")
    parser.add_argument("scenes", nargs="+", type=Path)
    parser.add_argument("--rom", required=True, type=Path)
    args = parser.parse_args()
    rom = args.rom.read_bytes()
    for path in args.scenes:
        try:
            stems = package.Package(path.read_bytes(), rom).stems
        except (OSError, ValueError) as error:
            print(json.dumps({"scene": path.name, "error": str(error)}))
            continue
        print(json.dumps({"scene": path.name, "split": None if stems is None else stems.available,
                          "reason": None if stems is None else stems.reason or None,
                          "gaps": None if stems is None else len(stems.gaps),
                          "stats": None if stems is None else stems.stats}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
