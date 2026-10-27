"""A synthetic attempt scene for the stems: the renderer's rows and the recording that heard them.

The game makes music (smooth noise) and effects (bursts) at 32006 Hz in one task per VI (533 or
534 samples, 59.94 VIs a second); its output is their sum. The recording hears that output
DELAY_S later on a device clock PPM off QPC, scaled by GAIN, with a little noise, at 48 kHz in
10 ms chunks; `drop_at_s` makes Project64's audio skip one VI's worth there. Every expected value
is the simulation's truth. No private data, ROM or emulator.
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import audio_clock as ac  # noqa: E402
import audio_layer as al  # noqa: E402
import live_audio  # noqa: E402
import stems_check as sc  # noqa: E402
import stems_live as sl  # noqa: E402

GAME, RATE, FREQ = 32006, 48000, 10_000_000
VI_HZ = 60000 / 1001
START = 7_000_000_000             # QPC of the first VI
DELAY_S, PPM, GAIN = 0.137, 120.0, 0.85
CHUNK = 480


def streams(seconds: float, seed: int = 3) -> tuple[list[int], np.ndarray, np.ndarray]:
    """Per-VI sample counts, and the music and effects (int16 stereo) on the game's timeline."""
    rng = np.random.default_rng(seed)
    vis = int(seconds * VI_HZ)
    lengths = [int((v + 1) * GAME / VI_HZ) - int(v * GAME / VI_HZ) for v in range(vis)]
    total = sum(lengths)
    noise = rng.normal(0, 2500, (total, 2))
    music = np.stack([np.convolve(noise[:, c], np.ones(5) / 5, mode="same") for c in (0, 1)], axis=1)
    effects = np.zeros((total, 2))
    for at in range(int(0.2 * GAME), total - GAME // 10, int(0.7 * GAME)):
        effects[at:at + GAME // 10] = rng.normal(0, 4000, (GAME // 10, 2))
    return lengths, music.astype(np.int16), effects.astype(np.int16)


def rows(lengths: list[int], music: np.ndarray, effects: np.ndarray, *, lost=(), differs=(), failed=(), checked=(),
         wrong=()) -> list[dict]:
    """The renderer's rows: every VI a task, its output the exact sum, settled. G1 checks: `checked`
    agreed exactly, `differs` were one unit off everywhere (a recompiler's rounding), `wrong` as far
    off as the sound itself (another program)."""
    out, at = [], 0
    for vi, samples in enumerate(lengths):
        part = slice(at, at + samples)
        at += samples
        if vi in lost:
            continue
        real = np.clip(music[part].astype(np.int32) + effects[part], -32768, 32767).astype(np.int16)
        flags = sl.TASK | sl.REAL | sl.SETTLED
        flags |= (sl.CHECKED | sl.DIFFERS) if vi in differs or vi in wrong else sl.CHECKED if vi in checked else 0
        flags |= sl.SPLIT_FAILED if vi in failed else 0
        g1, g1_rms = (1, 1000) if vi in differs else (0, 0)
        if vi in wrong:
            g1 = 2 * int(np.abs(real).max())
            g1_rms = round(float(np.sqrt(np.mean(real.astype(np.float64) ** 2))) * 1414)
        out.append({"vi": vi, "flags": flags, "qpc": START + round(vi * FREQ / VI_HZ), "tasks": vi // 2,
                    "g1": g1, "g1_rms": g1_rms, "samples": samples, "real": real,
                    "music": music[part].copy(), "effects": effects[part].copy()})
    return out


def heard(music: np.ndarray, effects: np.ndarray, *, drop_at_s: float | None = None, seed: int = 4,
          before_s: float = 0.5, after_s: float = 0.4) -> tuple[np.ndarray, int, np.ndarray]:
    """(the recording's PCM, the QPC of its first sample, the game position each sample heard)."""
    rng = np.random.default_rng(seed)
    game = np.clip(music.astype(np.float32) + effects, -32768, 32767)
    seconds = len(game) / GAME + before_s + after_s
    count = int(seconds * RATE) // CHUNK * CHUNK
    first_qpc = START - int(before_s * FREQ)
    times = first_qpc + np.arange(count) * FREQ / (RATE * (1 + PPM / 1e6))
    positions = (times - START - DELAY_S * FREQ) * GAME / FREQ
    if drop_at_s is not None:
        positions[times > START + (drop_at_s + DELAY_S) * FREQ] += 534     # one VI the plugin never played
    inside = (positions >= 0) & (positions <= len(game) - 1)
    pcm = np.zeros((count, 2), np.float32)
    pcm[inside] = sc.interp(game, positions[inside]) * GAIN
    pcm += rng.normal(0, 2, pcm.shape)
    return np.clip(np.rint(pcm), -32768, 32767).astype(np.int16), first_qpc, positions


def layer(pcm: np.ndarray, first_qpc: int, presents: list[tuple[int, int]]) -> al.Layer:
    """The recording as the cutter attaches it: 10 ms chunks stamped on arrival, the clock fit to the pictures."""
    chosen = []
    for first in range(0, len(pcm), CHUNK):
        arrival = first_qpc + int((first + CHUNK) * FREQ / (RATE * (1 + PPM / 1e6))) + 30_000
        chosen.append((ac.Chunk(0, first, CHUNK, arrival), pcm[first:first + CHUNK].tobytes()))
    payload, reason = live_audio.span_layer(chosen, presents, FREQ, rate=RATE)
    assert payload is not None, reason
    return al.read_layer(payload)


def presents(seconds: float) -> list[tuple[int, int]]:
    """A picture every 1/30 s over the scene, as (present sequence, QPC)."""
    return [(2 * k + 1, START + round(k * FREQ / 30) + 20_000) for k in range(int(seconds * 30))]


def scene(seconds: float = 12.0, **options) -> dict:
    lengths, music, effects = streams(seconds)
    pcm, first_qpc, positions = heard(music, effects, drop_at_s=options.pop("drop_at_s", None))
    shown = presents(seconds)
    return {"lengths": lengths, "music": music, "effects": effects, "pcm": pcm, "positions": positions,
            "presents": shown, "layer": layer(pcm, first_qpc, shown), "first_qpc": shown[0][1],
            "rows": rows(lengths, music, effects, **options)}
