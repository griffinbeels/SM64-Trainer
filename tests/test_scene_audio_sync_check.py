"""The audio sync witness (tools/scene_lab/audio_sync_check.py) on synthetic signals.

No game audio and no game memory: a music-like bed with hi-hat-like bursts,
two made-up sounds with sharp onsets standing in for the coin and pause
sounds, a device clock running a known number of ppm off a 10 MHz QPC, and
a synthetic tap session whose per-task regions carry the HUD coin count and
the play mode. The truth each test checks is where the test itself put each
sound, never the module's own estimate.
"""
import random
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/scene_lab"))
import audio_clock as ac  # noqa: E402
import audio_session as asn  # noqa: E402
import audio_sync_check as sync  # noqa: E402
import tap  # noqa: E402

from sm64_events.memory import addresses as A  # noqa: E402
from sm64_events.memory.layout import US  # noqa: E402

RATE, FREQ, CHUNK = 48000, 10_000_000, 480
START = 7_000_000_000
MODE_AT = US.hud_display - 0x28          # where the synthetic map puts sCurrPlayMode
COINS_AT = US.hud_display + A.HUD_COINS_OFF


def coin_sound() -> np.ndarray:
    t = np.arange(int(0.12 * RATE)) / RATE
    return (np.sin(2 * np.pi * 2637 * t) + 0.6 * np.sin(2 * np.pi * 3520 * t)) * np.exp(-t / 0.05) * 9000


def pause_sound() -> np.ndarray:
    t = np.arange(int(0.2 * RATE)) / RATE
    return (np.sin(2 * np.pi * 1046 * t) + np.sin(2 * np.pi * 1568 * t)) * np.exp(-t / 0.08) * 6000


def bed(samples: int, seed: int) -> np.ndarray:
    """Low tones that swell, broadband noise, and a short bright burst every 250 ms."""
    rng = np.random.default_rng(seed)
    t = np.arange(samples) / RATE
    music = sum(2000 * np.sin(2 * np.pi * f * t) * (1 + 0.5 * np.sin(2 * np.pi * 0.3 * t + f))
                for f in (110.0, 164.8, 220.0))
    music = music + rng.normal(0, 120, samples)
    burst = int(0.005 * RATE)
    for at in range(int(0.07 * RATE), samples - burst, int(0.25 * RATE)):
        music[at:at + burst] += rng.normal(0, 1500, burst) * np.linspace(1, 0, burst)
    return music


# --- onsets ------------------------------------------------------------------

def test_each_sound_is_found_to_a_sample_among_bursts_and_music():
    rng = random.Random(3)
    windows, truth = [], []
    for i in range(12):
        x = bed(int(0.5 * RATE), seed=i)
        at = rng.randint(int(0.12 * RATE), int(0.3 * RATE))
        sound = coin_sound() * rng.uniform(0.5, 1.0)
        x[at:at + len(sound)] += sound
        windows.append(x)
        truth.append(at)
    onsets, reference = sync.locate_onsets(windows, RATE)
    assert reference is not None
    errors = np.array([onset - at for (onset, _), at in zip(onsets, truth, strict=True)])
    assert all(value > 0.8 for _, value in onsets)
    assert np.ptp(errors) <= 2                     # relative onsets: a sample or two
    assert abs(errors[reference]) <= RATE // 2000  # the template's own onset: within half a millisecond


def test_a_window_without_the_sound_is_not_matched():
    windows = [bed(int(0.5 * RATE), seed=i) for i in range(4)]
    for x in windows[:3]:
        x[9000:9000 + len(coin_sound())] += coin_sound()
    onsets, _ = sync.locate_onsets(windows, RATE)
    assert [value > sync.MIN_NCC for _, value in onsets] == [True, True, True, False]


# --- events ------------------------------------------------------------------

def _states(frames: int, coins: dict, modes: dict, *, gaps=(), unshown=()) -> list:
    """TaskStates over consecutive frames; coins/modes map a frame to the value from then on."""
    states, timer, coin, mode = [], 1000, 0, 0
    for i in range(frames):
        timer += 1 + (i in gaps)
        coin, mode = coins.get(i, coin), modes.get(i, mode)
        states.append(sync.TaskState(i, timer, coin, mode, None if i in unshown else i))
    return states


def test_isolated_coins_and_pauses_are_events_at_the_frame_that_plays_them():
    states = _states(400, coins={50: 1, 100: 2, 102: 3, 150: 8, 200: 9, 250: 10, 300: 11},
                     modes={330: sync.PLAY_MODE_PAUSED, 380: sync.PLAY_MODE_NORMAL}, gaps={250}, unshown={300})
    events, skipped = sync.find_events(states, isolation_frames=15)
    assert [(e.kind, e.task) for e in events] == [("coin", 50), ("coin", 200), ("pause", 331)]
    assert events[2].frame == states[331].timer and events[2].present == 331
    assert skipped == {"coin: another coin or pause within the window": 2,     # a red coin's two sounds
                       "coin: that frame was never shown": 1}
    # The savestate-like +5 at 150 and the coin across the skipped frame at 250 are not pickups.


# --- the synthetic session ---------------------------------------------------

def _record(kind: int, sequence: int, qpc: int, body: bytes) -> bytes:
    size = (32 + len(body) + 31) & ~31
    return struct.pack("<IIIIq8x", tap.MAGIC, kind, size, sequence, qpc) + body + bytes(size - 32 - len(body))


def _pj64(big_endian: bytes) -> bytes:
    return np.frombuffer(big_endian, ">u4").astype("<u4").tobytes()


def _tap_session(folder: Path, frames: int, coins: list, modes: list, present_ticks: list) -> Path:
    """Task (gGlobalTimer identity), task regions (play mode .. HUD coins) and a present per frame."""
    folder.mkdir()
    timer_at = US.global_timer & 0x1FFFFFFF
    begin = (MODE_AT & 0x1FFFFFFF) & ~3
    span = ((COINS_AT & 0x1FFFFFFF) + 4 - begin + 3) & ~3
    task_fields = dict.fromkeys(tap.TASK_FIELDS, 0)
    records, sequence = [], 0
    for i in range(frames):
        task_fields.update(extras=1, task_index=i)
        body = struct.pack("<12I", *task_fields.values()) + bytes(64) + struct.pack("<II", timer_at, 4) \
            + struct.pack("<I", 5000 + i)
        records.append(_record(tap.REC_TASK, sequence, present_ticks[i] - 100_000, body))
        state = bytearray(span)
        struct.pack_into(">h", state, (MODE_AT & 0x1FFFFFFF) - begin, modes[i])
        struct.pack_into(">h", state, (COINS_AT & 0x1FFFFFFF) - begin, coins[i])
        regions = struct.pack("<8I", i, 0, 1, span, 0, 0, 256, 0) + bytes(64) + struct.pack("<II", begin, span)
        records.append(_record(tap.REC_REGIONS, sequence + 1, present_ticks[i] - 90_000, regions + _pj64(state)))
        present = struct.pack("<14I", *([0] * 14)) + struct.pack("<4I", 0, 0, 0, 1)
        records.append(_record(tap.REC_PRESENT, sequence + 2, present_ticks[i], present))
        sequence += 3
    (folder / "tap.bin").write_bytes(b"".join(records))
    (folder / "session.txt").write_text(f"schema=scene-lab-tap/1\nqpc_frequency={FREQ}\nrdram_size={8 << 20}\n",
                                        encoding="ascii")
    (folder / "armed.txt").write_text(f"seconds=60\n# identity=global_timer:{timer_at:x}:4\n", encoding="ascii")
    return folder


def _map(path: Path) -> Path:
    rows = (("sCurrPlayMode", MODE_AT), ("gHudDisplay", US.hud_display), ("gGlobalTimer", US.global_timer))
    path.write_text("".join(f"                0x{address:016x}                {name}\n" for name, address in rows),
                    encoding="utf-8")
    return path


class Synthetic:
    """70 s of play: a device running `ppm` off QPC, a coin every 3 s and a pause
    every 11 s, each sound starting `latency_ms` (+ up to 8 ms) after the picture
    of the frame that plays it."""

    FRAMES, LATENCY_MS = 2085, 85.0

    def __init__(self, ppm: float = -300.0):
        rng = random.Random(11)
        self.period = FREQ / (RATE * (1 + ppm / 1e6))
        self.present_ticks = [START + int((0.5 + i / 30) * FREQ) + rng.randint(-2000, 2000)
                              for i in range(self.FRAMES)]
        coins, modes, coin, self.sounds = [], [], 0, []
        pauses = set(range(150, self.FRAMES - 100, 330))
        coin_frames = {f for f in range(60, self.FRAMES - 60, 90) if all(abs(f - p) > 60 for p in pauses)}
        mode = 0
        for i in range(self.FRAMES):
            if i in coin_frames:
                coin += 1
                self.sounds.append(("coin", i, rng.uniform(0, 8)))
            if any(p <= i < p + 45 for p in pauses):
                if mode == 0:
                    self.sounds.append(("pause", i + 1, rng.uniform(0, 8)))
                mode = 2
            else:
                mode = 0
            coins.append(coin)
            modes.append(mode)
        self.coins, self.modes = coins, modes
        self.samples = int((self.present_ticks[-1] - START) / self.period) + RATE
        self.samples -= self.samples % CHUNK
        delay_rng = random.Random(5)
        self.chunks, arrival = [], 0
        for k in range(self.samples // CHUNK):
            ready = START + (k + 1) * CHUNK * self.period
            arrival = max(arrival, int(ready + 3000 + delay_rng.expovariate(1 / 20_000)))
            self.chunks.append(ac.Chunk(0, k * CHUNK, CHUNK, arrival))

    def pcm(self, extra_ms=lambda kind, frame: 0.0) -> np.ndarray:
        mono = bed(self.samples, seed=1)
        for kind, frame, jitter in self.sounds:
            when = self.present_ticks[frame] + (self.LATENCY_MS + jitter + extra_ms(kind, frame)) * FREQ / 1000
            at = round((when - START) / self.period)
            sound = coin_sound() if kind == "coin" else pause_sound()
            mono[at:at + len(sound)] += sound[:max(0, self.samples - at)]
        stereo = np.stack([mono * 0.9, mono * 1.1], axis=1)
        return np.clip(np.rint(stereo), -32768, 32767).astype(np.int16)

    def expected_ms(self, kind: str) -> float:
        return self.LATENCY_MS + float(np.mean([j for k, _, j in self.sounds if k == kind]))


@pytest.fixture(scope="module")
def synthetic():
    return Synthetic()


def test_a_synthetic_session_reads_back_and_passes(tmp_path, synthetic):
    session = _tap_session(tmp_path / "tap-synthetic", synthetic.FRAMES, synthetic.coins, synthetic.modes,
                           synthetic.present_ticks)
    pcm = synthetic.pcm()
    capture = tmp_path / "audio.cap"
    asn.write_capture(capture, RATE, FREQ, [(c, pcm[c.first:c.first + CHUNK].tobytes()) for c in synthetic.chunks])
    report = sync.run(session, capture, _map(tmp_path / "MappingUS.map"))
    assert report["verdict"] == "pass", report["kinds"]
    assert report["tasks"] == report["presents"] == synthetic.FRAMES and report["left_out"] == {}
    assert abs(report["clock"]["drift_ppm"] + 300) < 5
    for kind in ("coin", "pause"):
        entry = report["kinds"][kind]
        assert entry["used"] == entry["events"] == sum(k == kind for k, _, _ in synthetic.sounds) >= 6
        # The clock puts each sample about its delivery floor (0.3 ms) late; nothing else moves it.
        assert abs(entry["mean_ms"] - synthetic.expected_ms(kind)) < 1.5
        assert entry["spread_ms"] < 10 and abs(entry["early_late_ms"]) < 5
        # Without the fitted rate the thirds part by the drift: -300 ppm over about a minute.
        assert entry["nominal_early_late_ms"] < -10
    rows = [r for r in report["events"] if r["kind"] == "coin"]
    assert [r["frame"] for r in rows] == [5000 + f for k, f, _ in synthetic.sounds if k == "coin"]


def test_sound_that_drifts_late_or_one_late_sound_fails(synthetic):
    clock = ac.build_clock(synthetic.chunks, [ac.Present(3 * i + 2, t) for i, t in enumerate(synthetic.present_ticks)],
                           rate=RATE, frequency=FREQ)
    states = [sync.TaskState(i, 5000 + i, c, m, i) for i, (c, m) in
              enumerate(zip(synthetic.coins, synthetic.modes, strict=True))]
    events, _ = sync.find_events(states, isolation_frames=15)
    late_half = synthetic.FRAMES // 2
    drifting = synthetic.pcm(lambda kind, frame: 15.0 if frame > late_half else 0.0)
    summary = sync.summarize(sync.measure(clock, drifting, events))
    assert summary["verdict"] == "fail" and summary["kinds"]["coin"]["early_late_ms"] > 10
    first_coin = next(f for k, f, _ in synthetic.sounds if k == "coin")
    one_late = synthetic.pcm(lambda kind, frame: 40.0 if frame == first_coin else 0.0)
    coin = sync.summarize(sync.measure(clock, one_late, events))["kinds"]["coin"]
    assert coin["verdict"] == "fail" and coin["spread_ms"] > sync.ONE_FRAME_MS


def test_a_map_from_another_build_is_refused(tmp_path):
    path = tmp_path / "MappingJP.map"
    path.write_text(f"                0x{US.hud_display + 0x10:016x}                gHudDisplay\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="disagrees with layout.hud_display"):
        sync.addresses(path, "us")
    assert sync.addresses(_map(tmp_path / "MappingUS.map"), "us") == {"coins": COINS_AT, "play_mode": MODE_AT}
