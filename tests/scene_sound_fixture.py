"""A synthetic scene recording with sound, for the source-audio layer's package and viewer tests.

A scene tap of FRAMES tasks, each shown by a present about 33 ms after the last (test_scene_package.py's
synthetic ROM and tasks), and a device clock running a known ppm off the 10 MHz QPC that delivers
10 ms chunks with one-sided jitter; LOST chunks are ones the fanout could not keep. The PCM is a
quiet tone with a click at each CLICK_EVERY-th frame's true sample. No private RAM image, real ROM,
emulator or recorded audio is involved.
"""
import importlib.util
import random
import struct
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import audio_clock as ac  # noqa: E402
import audio_layer as al  # noqa: E402
import package as pk  # noqa: E402

spec = importlib.util.spec_from_file_location("scene_package_synthetic_sound", ROOT / "tests/test_scene_package.py")
synthetic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(synthetic)

RATE, FREQ = 48000, 10_000_000
START = 5_000_000_000            # QPC ticks of the first present
FRAME_TICKS = FREQ // 30
FRAMES = 40
PPM = -90.0
CHUNK = 480
LOST = frozenset({60, 61, 62})   # 30 ms the fanout dropped, about 0.6 s in
CLICK_EVERY = 10


def tap_record(kind: int, sequence: int, qpc: int, body: bytes) -> bytes:
    size = (32 + len(body) + 31) & ~31
    return struct.pack("<IIIIq8x", pk.tap.MAGIC, kind, size, sequence, qpc) + body + bytes(size - 32 - len(body))


def tap_session(folder: Path, rom: bytes, segment7: bytes, sequence0: int = 0) -> tuple[Path, list]:
    """The tap session and its presents (ac.Present, in record order)."""
    rng = random.Random(4)
    folder.mkdir()
    records, presents, sequence = [], [], sequence0
    for frame in range(FRAMES):
        qpc = START + frame * FRAME_TICKS + rng.randint(-15_000, 15_000)
        image = synthetic._rdram(rom, segment7, frame)
        records.append(tap_record(pk.tap.REC_TASK, sequence, qpc - 50_000, synthetic._task_body(image, frame)))
        sequence += 1
        records.append(tap_record(pk.tap.REC_PRESENT, sequence, qpc, synthetic._present_body(frame)))
        presents.append(ac.Present(sequence, qpc))
        sequence += 1
    (folder / "tap.bin").write_bytes(b"".join(records))
    (folder / "session.txt").write_text(
        f"schema=scene-lab-tap/1\nwrapper_build=test\nqpc_frequency={FREQ}\nrdram_size={synthetic.RDRAM}\n"
        f"rom_header={pk.rom_header_hex(rom)}\n", encoding="ascii")
    (folder / "armed.txt").write_text(
        f"extra={synthetic.TIMER:x}:4\n# identity=global_timer:{synthetic.TIMER:x}:4\n", encoding="ascii")
    return folder, presents


def device_time(n: int, start: int) -> float:
    """True QPC tick at which sample n of the device's stream sounds."""
    return start + n * FREQ / (RATE * (1 + PPM / 1e6))


def sound(presents: list, *, codec: str = "flac", lost=LOST, seed: int = 9):
    """(AudioClock, stored PCM, layer payload, the true sample of each present)."""
    rng = random.Random(seed)
    start = START - FREQ // 5                                  # the tap opens 0.2 s before the first picture
    total = int((FRAMES / 30 + 0.6) * RATE) // CHUNK * CHUNK
    chunks, arrival = [], 0
    for index, first in enumerate(range(0, total, CHUNK)):
        ready = device_time(first + CHUNK, start)
        arrival = max(arrival, int(ready + 3_000 + rng.expovariate(1 / 20_000)))
        chunks.append(ac.Chunk(0, first, CHUNK, arrival, index in lost))
    period = FREQ / (RATE * (1 + PPM / 1e6))
    truth = [int((present.qpc - start) // period) for present in presents]
    t = np.arange(total) / RATE
    pcm = np.stack([np.sin(2 * np.pi * 330 * t), np.sin(2 * np.pi * 495 * t)], axis=1) * 1500
    for frame in range(0, len(presents), CLICK_EVERY):
        pcm[truth[frame]:truth[frame] + 48] = 28000
    pcm = pcm.astype(np.int16)
    payloads = [None if chunk.lost else pcm[chunk.first:chunk.first + CHUNK].tobytes() for chunk in chunks]
    clock = ac.build_clock(chunks, presents, rate=RATE, frequency=FREQ)
    stored = ac.stream_pcm(chunks, payloads)
    return clock, stored, al.encode_layer(clock, stored, codec=codec, block_frames=2), truth


def recording(base: Path) -> dict:
    """A closures-only package of the session, its sound and the parts a test checks against."""
    rom, segment7 = synthetic._rom()
    session, presents = tap_session(base / "tap-sound", rom, segment7)
    out = base / "sound-v0.sm64scene"
    pk.write_package(pk.load_source(session), rom, out, pk.ZLIB, group=4)
    clock, stored, payload, truth = sound(presents)
    return {"rom": rom, "segment7": segment7, "session": session, "presents": presents, "package": out.read_bytes(),
            "clock": clock, "pcm": stored, "payload": payload, "truth": truth}
