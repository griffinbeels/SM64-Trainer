"""A recorded viewer timeline's sound, mixed the way the Scene viewer's page mixed it (M4 round 6, item 3).

    runs = sound_map(table_samples, recorded_times, rate, fps)      # sceneaudio.js soundMap
    pcm = mix(entries, duration_ms, rate, runs, split, stage, ghosts)

The page (ui/components/sceneaudio.js, sceneaudioworklet.js) plays sound only during Play: the
recorded time sounding now maps to a sample of the scene's stream (soundMap's runs, sampleAt),
and that sample plays from the track the switches name (trackFor: the recording with both on or
without a split, one stem with one on, nothing with both off), with each ghost in the scene adding
its own track (ghost_sound.py) unless muted, all at the volume (0 when muted). Pause, a step and
the slider are silent at once; a play starts, stops and jumps with a short fade (FADE samples), a
track switch fades out and in, a ghost's mute moves over FADE samples, the volume over LEVEL_MS.

A timeline entry (interface G) says, for one display frame from `t_ms` on, which recorded time
the play's clock sounded (`play_ms`, None when nothing played: paused, stepping, seeking, or the
play not started yet) and the switches then. Between two entries the recorded time runs on at
the wall clock's pace, as the page's clock does, so the mix follows the play sample by sample and
not display frame by display frame. Scrubs (the slider) pause Play in the page, so they are
silent here too. The page steers its sound onto the pictures within a few milliseconds; this mix
is the ideal it steers toward: each picture's own sample exactly as it shows.

`stage(track)` gives the scene's whole stream for "mix", "music" or "effects" (int16, stereo);
`ghosts[ref]` a ghost's whole track on the same samples. Pure numpy; tests feed synthetic tracks.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

FADE = 64                      # sceneaudioworklet.js FADE: a start, stop, jump or switch, in samples
JOIN_MS = 2                    # sceneaudio.js SCENE_SOUND.joinMs: samples this close are one run
LEVEL_MS = 5.0                 # sceneaudio.js applyLevel: the master gain's time constant (0.005 s)
CONTINUOUS_MS = 1.0            # a play's recorded time this close to where it would be has not jumped


def track_for(split: bool, music: bool, effects: bool) -> str | None:
    """sceneaudio.js trackFor: the recording, one stem, or nothing (None)."""
    if not split or (music and effects):
        return "mix"
    return "music" if music else "effects" if effects else None


@dataclass(frozen=True)
class Run:
    """A stretch of sound with no cut: knots of recorded time (ms) against stream sample."""
    p0: float
    p1: float
    kp: np.ndarray
    ks: np.ndarray


def sound_map(samples, times, rate: int, fps: float) -> list[Run]:
    """sceneaudio.js soundMap, the same runs: knot k is (when the recording showed picture k, its
    sample). A run ends where the next picture's sample is not the recorded time later (within
    JOIN_MS) or has none; its last picture plays until the next one, at most one game frame."""
    runs, join = [], JOIN_MS * rate / 1000
    count = len(samples)

    def follows(k):
        return samples[k + 1] >= 0 and abs(samples[k + 1] - samples[k] - (times[k + 1] - times[k]) * rate / 1000) <= join

    a = 0
    while a < count:
        if samples[a] < 0:
            a += 1
            continue
        b = a
        while b + 1 < count and follows(b):
            b += 1
        tail = min(1000 / fps, times[b + 1] - times[b] if b + 1 < len(times) else math.inf)
        kp = np.array([*(times[k] for k in range(a, b + 1)), times[b] + tail], np.float64)
        ks = np.array([*(samples[k] for k in range(a, b + 1)), samples[b] + tail * rate / 1000], np.float64)
        runs.append(Run(float(kp[0]), float(kp[-1]), kp, ks))
        a = b + 1
    return runs


def sample_at(runs: list[Run], p: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """sceneaudioworklet.js sampleAt over many recorded times: (sample, edge) per time, NaN where
    no run covers it; `edge` is the distance in ms to the nearer end of its run."""
    p = np.asarray(p, np.float64)
    s, edge = np.full(p.shape, np.nan), np.full(p.shape, np.nan)
    if not runs or not p.size:
        return s, edge
    starts = np.array([run.p0 for run in runs])
    which = np.searchsorted(starts, p, side="right") - 1
    for index in np.unique(which[which >= 0]):
        run = runs[index]
        mine = (which == index) & (p < run.p1)
        if mine.any():
            s[mine] = np.interp(p[mine], run.kp, run.ks)
            edge[mine] = np.minimum(p[mine] - run.p0, run.p1 - p[mine])
    return s, edge


def _ramped(target: np.ndarray, step: float) -> np.ndarray:
    """`target` (piecewise constant) approached at `step` per sample, as the worklet moves a ghost's
    level; changes are few, so each is walked on its own."""
    out = target.astype(np.float64).copy()
    changes = np.flatnonzero(np.diff(target)) + 1
    level = float(target[0]) if target.size else 0.0
    start = 0
    for change in [*changes.tolist(), target.size]:
        goal = float(target[start]) if start < target.size else 0.0
        span = np.arange(change - start, dtype=np.float64)
        moved = level + np.sign(goal - level) * np.minimum(np.abs(goal - level), (span + 1) * step)
        out[start:change] = moved
        if change > start:
            level = float(moved[-1])
        start = change
    return out


def _smoothed(target: np.ndarray, samples: float) -> np.ndarray:
    """`target` (piecewise constant) approached exponentially with time constant `samples`, as
    AudioParam.setTargetAtTime moves the master gain."""
    out = target.astype(np.float64).copy()
    if not target.size:
        return out
    changes = np.flatnonzero(np.diff(target)) + 1
    level, start = float(target[0]), 0
    for change in [*changes.tolist(), target.size]:
        goal = float(target[start])
        decay = np.exp(-np.arange(1, change - start + 1, dtype=np.float64) / samples)
        out[start:change] = goal + (level - goal) * decay
        if change > start:
            level = float(out[change - 1])
        start = change
    return out


def _lerp(track: np.ndarray, s: np.ndarray, on: np.ndarray) -> np.ndarray:
    """The track's value at fractional samples `s` where `on`, linearly between neighbours (the
    worklet's value(whole) * (1 - part) + value(whole + 1) * part), as floats in -1..1."""
    out = np.zeros((s.size, 2))
    if not on.any() or track is None or not len(track):
        return out
    where = np.flatnonzero(on)
    whole = np.floor(s[where]).astype(np.int64)
    part = (s[where] - whole)[:, None]
    last = len(track) - 1
    low = np.clip(whole, 0, last)
    high = np.clip(whole + 1, 0, last)
    inside = ((whole >= 0) & (whole <= last))[:, None]
    after = ((whole + 1 >= 0) & (whole + 1 <= last))[:, None]
    value = track[low] * inside * (1 - part) + track[high] * after * part
    out[where] = value / 32768.0
    return out


def _segments(play: np.ndarray, p: np.ndarray, tracks: np.ndarray, rate: int) -> np.ndarray:
    """The envelope every sound is multiplied by (the worklet's env): 1 inside a stretch where the
    play's recorded time runs on unbroken from one track, fading in over its first FADE samples
    and out over its last; 0 while nothing plays."""
    env = np.zeros(play.size)
    if not play.any():
        return env
    step = 1000.0 / rate
    broken = np.ones(play.size, bool)
    broken[1:] = ~(play[1:] & play[:-1] & (np.abs(p[1:] - p[:-1] - step) <= CONTINUOUS_MS)
                   & (tracks[1:] == tracks[:-1]))
    breaks = np.flatnonzero(broken)
    for start in np.flatnonzero(play & broken):
        later = breaks[np.searchsorted(breaks, start, side="right"):]
        end = int(later[0]) if later.size else play.size
        length = end - start
        env[start:end] = np.minimum(np.minimum(np.arange(1, length + 1), np.arange(length, 0, -1)) / FADE, 1.0)
    return env


def mix(entries: list[dict], duration_ms: float, rate: int, runs: list[Run], split: bool, stage,
        ghosts: dict) -> np.ndarray:
    """The timeline's sound, int16 stereo, round(duration_ms * rate / 1000) samples long.
    `stage(track)` returns the scene's stream for "mix", "music" or "effects" (int16 (n, 2)), or
    None when there is no sound; `ghosts` maps a ghost ref to its track (int16 (n, 2)), a ghost
    without one playing silent."""
    count = max(0, round(duration_ms * rate / 1000))
    out = np.zeros((count, 2), np.int16)
    if not count or not entries:
        return out
    times = np.array([float(entry["t_ms"]) for entry in entries])
    at = np.clip(np.searchsorted(times, np.arange(count) * 1000.0 / rate, side="right") - 1, 0, len(entries) - 1)
    play_ms = np.array([np.nan if entry.get("play_ms") is None else float(entry["play_ms"]) for entry in entries])
    tau = np.arange(count) * 1000.0 / rate
    p = play_ms[at] + (tau - times[at])
    sound = [entry.get("sound") or {} for entry in entries]
    names = [track_for(split, bool(one.get("music", True)), bool(one.get("effects", True))) for one in sound]
    codes = {name: code for code, name in enumerate(dict.fromkeys(names))}
    track = np.array([codes[name] for name in names])[at]
    s, edge = sample_at(runs, np.where(np.isnan(p), -math.inf, p))
    play = ~np.isnan(p) & ~np.isnan(s)
    edge_gain = np.where(play, np.minimum(1.0, np.nan_to_num(edge) * rate / 1000 / FADE), 0.0)
    total = np.zeros((count, 2))
    for name, code in codes.items():
        if name is None:
            continue
        mine = play & (track == code)
        if mine.any():
            total += _lerp(stage(name), s, mine)
    for ref, ghost_track in ghosts.items():
        heard = np.array([ref in (entry.get("ghosts") or ()) and ref not in (one.get("ghost_mutes") or ())
                          for entry, one in zip(entries, sound, strict=True)], np.float64)[at]
        gain = _ramped(heard, 1.0 / FADE)
        if gain.any():
            total += _lerp(ghost_track, s, play & (gain > 0)) * gain[:, None]
    level = np.array([0.0 if one.get("muted") else min(1.0, max(0.0, float(one.get("volume", 1.0))))
                      for one in sound])[at]
    gain = _segments(play, np.nan_to_num(p), track, rate) * edge_gain * _smoothed(level, LEVEL_MS * rate / 1000)
    return np.clip(np.round(total * gain[:, None] * 32768.0), -32768, 32767).astype(np.int16)
