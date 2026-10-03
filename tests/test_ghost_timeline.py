"""The ghost sound reaches the ghost copy by wall time, never the clean audio.

The ghost sound player's tap feeds a GhostTimeline (replay/audio.py); the
ghost copy's sink asks it for the ghost samples under each game block by that
block's UTC stamp and mixes them in (mix_ghost). These tests drive both taps
on a simulated wall clock with delivery jitter and stalls, place the game the
way the GPU route does (AudioPlacement), and measure where the ghost lands
against a game sound emitted at the same instant.
"""
import random

import numpy as np
import pytest

from sm64_events.replay.audio import GhostTimeline, mix_ghost
from sm64_events.replay.audiopacing import AudioPlacement

RATE = 48000
T0 = 1_790_000_000.0  # a UTC instant on today's scale, where float rounding is real
LSB = 1 / 32767


def us(seconds):
    return round(seconds * 1_000_000)


def chunk(value, frames=480):
    return np.full(frames * 2, value, dtype=np.float32)


def test_take_is_silence_where_no_ghost_was_heard():
    timeline = GhostTimeline(RATE)
    assert not timeline.take(us(T0), 480).any(), "nothing heard yet"
    timeline.feed(chunk(0.5), T0 + 0.010)  # heard [T0, T0+10 ms)
    window = timeline.take(us(T0 - 0.005), 960)  # [T0-5 ms, T0+15 ms)
    assert window.shape == (960, 2) and window.dtype == np.float32
    assert not window[:240].any() and not window[720:].any()
    assert np.allclose(window[240:720], 0.5, atol=LSB)


def test_only_the_newest_seconds_wait():
    timeline = GhostTimeline(RATE, most_s=0.5)
    for index in range(100):  # one second of 10 ms chunks
        timeline.feed(chunk(0.25), T0 + (index + 1) * 0.010)
    assert not timeline.take(us(T0), 480).any(), "older than most_s: gone"
    assert np.allclose(timeline.take(us(T0 + 0.9), 480), 0.25, atol=LSB)


def test_a_stall_delays_chunks_without_moving_them():
    timeline = GhostTimeline(RATE)
    for index in range(50):
        stalled = 20 <= index < 32  # 120 ms of chunks held, then delivered together
        timeline.feed(chunk(index / 100), T0 + (0.330 if stalled else (index + 1) * 0.010))
    for index in (19, 20, 31, 32):
        assert np.allclose(timeline.take(us(T0 + index * 0.010), 480), index / 100, atol=LSB), index


def test_a_new_tap_or_a_skipped_stretch_starts_a_new_stream():
    timeline = GhostTimeline(RATE)
    for index in range(10):
        timeline.feed(chunk(0.25), T0 + (index + 1) * 0.010)
    timeline.feed(chunk(0.75), T0 + 0.410)  # the tap delivered nothing for 300 ms
    assert not timeline.take(us(T0 + 0.1), 14400).any(), "the skipped stretch is silence"
    assert np.allclose(timeline.take(us(T0 + 0.4), 480), 0.75, atol=LSB)
    timeline.restart()  # a new tap: its count starts again, at its own arrival
    timeline.feed(chunk(0.5), T0 + 0.430)
    assert np.allclose(timeline.take(us(T0 + 0.42), 480), 0.5, atol=LSB)
    assert np.allclose(timeline.take(us(T0 + 0.4), 480), 0.75, atol=LSB), "what was heard stays"


def test_mix_adds_the_ghost_and_saturates_instead_of_wrapping():
    game = np.array([[1000, -1000], [32000, -32000], [0, 0]], dtype=np.int16).tobytes()
    ghost = np.array([[0.5, -0.5], [0.5, -0.5]], dtype=np.float32)
    mixed = np.frombuffer(mix_ghost(game, ghost), dtype=np.int16).reshape(-1, 2)
    assert mixed[0].tolist() == [1000 + 16384, -1000 - 16384]
    assert mixed[1].tolist() == [32767, -32768], "saturates"
    assert mixed[2].tolist() == [0, 0], "a short ghost leaves the rest of the block alone"


def _stream(frames, seconds, tone_at, channel, jitter_ms, stall=None, seed=0):
    """(arrival, float32 stereo chunk) for one tap: a tone on `channel` from
    true time `tone_at`; each chunk arrives jitter after its last sample, and
    a stall holds every chunk due inside it until it ends. A tap delivers
    in order, so a backlog arrives as a quick burst."""
    rng = random.Random(seed)
    events, last = [], 0.0
    for first in range(0, int(seconds * RATE), frames):
        times = T0 + (first + np.arange(frames)) / RATE
        data = np.zeros((frames, 2), dtype=np.float32)
        data[times >= tone_at, channel] = 0.5
        arrival = T0 + (first + frames) / RATE + rng.uniform(*jitter_ms) / 1000
        if stall is not None and stall[0] <= arrival < stall[1]:
            arrival = stall[1]
        last = arrival = max(arrival, last + 0.0001)
        events.append((arrival, data))
    return events


@pytest.mark.parametrize("stall_in", [None, "ghost", "ghost-start"])
@pytest.mark.parametrize("tone_after_stall_s", [0.04, 1.0])
def test_a_ghost_tone_lands_within_10_ms_of_the_game_sound_it_played_with(stall_in, tone_after_stall_s):
    """Both taps hear a tone at the same instant; in the ghost copy's audio
    the ghost's onset sits within 10 ms of the game's. The game tap is also
    late by its pump's handoff, which the ghost tap does not pay, and the
    sink sees each game block only once the video covering it is muxed. A
    stall of the ghost tap, even over its first chunk, moves nothing. (A
    stall of the GAME tap leaves the game's own placement up to 50 ms late
    for seconds, audiopacing.AudioPlacement; the ghost stays on true time.)"""
    stall = (T0 + 1.20, T0 + 1.34)  # a stop-the-world pause
    tone_at = stall[1] + tone_after_stall_s
    ghost_stall = {"ghost": stall, "ghost-start": (T0, T0 + 0.14)}.get(stall_in)
    ghost = _stream(441, tone_at - T0 + 1.0, tone_at, 1, (0.5, 4.0), ghost_stall, seed=1)
    pump_ms, drain_s = 2.0, 0.040
    game = _stream(480, tone_at - T0 + 1.0, tone_at, 0, (0.5 + pump_ms, 4.0 + pump_ms), seed=2)
    timeline = GhostTimeline(RATE)
    placed, mixed = [], []
    placement = AudioPlacement(RATE, lambda buf, first_us: placed.append((buf, first_us)))

    def sink_until(now):
        while placed and placed[0][1] / 1e6 + len(placed[0][0]) / 4 / RATE <= now - drain_s:
            buf, first_us = placed.pop(0)
            ghost_part = timeline.take(first_us, len(buf) // 4)
            mixed.append((first_us, np.frombuffer(mix_ghost(buf, ghost_part), dtype=np.int16).reshape(-1, 2)))

    events = sorted([(at, 0, d) for at, d in ghost] + [(at, 1, d) for at, d in game], key=lambda e: e[:2])
    for at, kind, data in events:
        sink_until(at)
        if kind == 0:
            timeline.feed(data.reshape(-1), at)
        else:
            placement.put_at((data * 32767).astype(np.int16).tobytes(), at)
    sink_until(float("inf"))

    def onset(channel):
        for first_us, block in mixed:
            loud = np.flatnonzero(np.abs(block[:, channel]) > 8000)
            if len(loud):
                return first_us / 1_000_000 + loud[0] / RATE
        raise AssertionError(f"no tone on channel {channel}")

    game_onset, ghost_onset = onset(0), onset(1)
    assert abs(game_onset - tone_at) < 0.010, "the game's own placement is the reference"
    assert abs(ghost_onset - game_onset) <= 0.010, (ghost_onset - game_onset) * 1000
