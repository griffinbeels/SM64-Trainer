"""The stems capture (M4 stems): the tap's per-VI audio spans through the REAL producer, and
the checker's G0 and G2 on what it records.

tools/scene_lab/tap_offline_host.cpp feeds frame inputs through plugin/gfxwrap/scene_tap.cpp:
a ProcessDList starts the tap's clock, then each `vi:` frame is one UpdateScreen, where the
wrapper copies the vi_region spans and SP DMEM (whole at the first VI, then only the 256-byte
blocks that changed). Synthetic RAM only, no game data and no ROM: the audio state sits at the
US layout's spans with made-up globals inside them. G1 and G3 need the ROM's microcode and run
on real sessions (stems_probe.py from-tap). The producer tests are skipped (not failed) on a
machine without the x86 MSVC toolchain."""
from __future__ import annotations

import json
import re
import struct
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "tools" / "scene_lab"))

import audio_clock as ac  # noqa: E402
import audio_session as asn  # noqa: E402
import build_plugin  # noqa: E402
import stems_check  # noqa: E402
import tap  # noqa: E402

from sm64_events.core.childproc import quiet_spawn_kwargs  # noqa: E402
from sm64_events.memory.layout import US  # noqa: E402

needs_toolchain = pytest.mark.skipif(not build_plugin.toolchain_available(),
                                     reason="no x86 MSVC toolchain (vcvars32.bat)")

RDRAM = 8 << 20
LIST, UCODE, UCODE_DATA = 0x100000, 0x200000, 0x201000
GLOBALS = US.audio_globals_start
# Made-up audio globals inside the captured spans (the real ones come from the linker map).
SYM = {"gSPTaskMesgQueue": US.sp_task_queue_start, "gAudioTasks": GLOBALS + 0x100,
       "gAudioTaskIndex": GLOBALS + 0x10, "gAudioFrameCount": GLOBALS + 0x14,
       "gCurrAiBufferIndex": GLOBALS + 0x18, "gAiBuffers": GLOBALS + 0x20, "gAiBufferLengths": GLOBALS + 0x2C,
       "gAudioCmdBuffers": GLOBALS + 0x34, "sCurrentAudioSPTask": US.sp_task_pointers_start + 4}


def phys(address: int) -> int:
    return address & 0x1FFFFFFF


def put_u32(ram: np.ndarray, address: int, value: int) -> None:
    ram[phys(address):phys(address) + 4] = np.frombuffer(value.to_bytes(4, "little"), np.uint8)


def put_s16(ram: np.ndarray, address: int, value: int) -> None:
    at = phys(address) ^ 2                       # a halfword inside Project64's little-endian word
    ram[at:at + 2] = np.frombuffer((value & 0xFFFF).to_bytes(2, "little"), np.uint8)


def write_frame(folder: Path, ram: np.ndarray, dmem: np.ndarray | None = None) -> Path:
    """A frame input whose graphics task sets segment 2 and ends (the tap's clock starts at a task)."""
    folder.mkdir(parents=True)
    words = ram.view("<u4")
    words[LIST // 4:LIST // 4 + 4] = [0xBC000806, 0x10000, 0xB8000000, 0]
    (folder / "rdram.bin").write_bytes(ram.tobytes())
    sp = bytearray(0x1000) if dmem is None else bytearray(dmem.tobytes())
    struct.pack_into("<16I", sp, 0xFC0, 1, 0, 0, 0, UCODE, 0x1000, UCODE_DATA, 0x800, 0, 0, 0, 0, LIST, 16, 0, 0)
    (folder / "dmem.bin").write_bytes(bytes(sp))
    (folder / "frame.json").write_text(json.dumps({"vi": {"origin": 0}, "rom_header": "ab" * 64}), encoding="utf-8")
    return folder


def record(tmp_path: Path, armed: str, frames: list[str]) -> Path:
    root = tmp_path / "lab"
    lab = root / "SM64Trainer" / "scene-lab"
    lab.mkdir(parents=True)
    (lab / "armed.txt").write_text(armed, encoding="ascii")
    result = subprocess.run([str(tap.build_offline_host()), str(root), *frames], capture_output=True, text=True,
                            timeout=120, check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    (session,) = lab.glob("tap-*")
    return session


def vi_lines(session: Path) -> list[str]:
    return [line for line in (session / "session.txt").read_text().splitlines() if line.startswith("vi_")]


@needs_toolchain
def test_vi_spans_are_kept_only_whole_inside_rdram_within_the_budget_and_the_line_cap(tmp_path):
    first = write_frame(tmp_path / "task", np.zeros(RDRAM, np.uint8))
    lines = ["vi_region=1000:4096",          # kept
             "vi_region=7ff000:8192",        # leaves RDRAM: dropped at arming
             "vi_region=3000:0",             # empty: dropped
             "vi_region=10000:1048576",      # past the 1 MiB budget with the first: dropped
             "vi_region=20001:7",            # kept as whole words: 0x20000, 8 bytes
             "vi_region=30000:64", "vi_region=40000:64", "vi_region=50000:64",
             "vi_region=60000:64"]           # the ninth line: past the eight the wrapper reads
    session = record(tmp_path, "seconds=600\nreference=0\n" + "\n".join(lines) + "\nvi_dmem=1\n", [str(first)])
    assert vi_lines(session) == ["vi_region=1000:4096", "vi_region=20000:8", "vi_region=30000:64",
                                 "vi_region=40000:64", "vi_region=50000:64", "vi_dmem=4000000:4096"]


@needs_toolchain
def test_vi_records_round_trip_key_then_changed_blocks_with_dmem(tmp_path):
    rng = np.random.default_rng(26)
    ram = rng.integers(0, 256, RDRAM, dtype=np.uint8)
    dmem = rng.integers(0, 256, 0x1000, dtype=np.uint8)
    span = (0x300000, 3 * 4096)
    frames, snapshots = [str(write_frame(tmp_path / "task", ram, dmem))], []

    def vi(name: str) -> None:
        folder = write_frame(tmp_path / name, ram, dmem)
        frames.append(f"vi:{folder}")
        snapshots.append((ram.copy(), np.fromfile(folder / "dmem.bin", np.uint8)))   # with its OSTask

    vi("a")                                   # first VI: a key, both spans whole
    ram[span[0] + 4096 + 300] ^= 0xFF         # block 17 of the span
    dmem[0x900] ^= 0xFF                       # one DMEM block
    vi("b")
    vi("c")                                   # nothing changed: an empty delta
    session = record(tmp_path, f"seconds=600\nreference=0\nvi_region={span[0]:x}:{span[1]}\nvi_dmem=1\n", frames)
    records = [r for r in tap.read_records(session / "tap.bin") if isinstance(r, tap.ViRegions)]
    assert [r.kind for r in records] == ["vi_key", "vi_delta", "vi_delta"]
    assert [r.fields["vi_index"] for r in records] == [0, 1, 2]
    assert all(r.fields["tasks"] == 1 for r in records)
    assert records[0].spans == [span, (tap.DMEM_BEGIN, tap.DMEM_BYTES)]
    assert records[1].spans == [(span[0] + 4096 + 256, 256), (tap.DMEM_BEGIN + 0x900, 256)]
    assert records[2].spans == [] and records[2].fields["data_bytes"] == 0
    rebuilt = tap.ViState(RDRAM)
    for rec, (want_ram, want_dmem) in zip(records, snapshots, strict=True):
        rebuilt.apply(rec)
        assert np.array_equal(rebuilt.ram[span[0]:span[0] + span[1]], want_ram[span[0]:span[0] + span[1]])
        assert np.array_equal(rebuilt.dmem, want_dmem)
        assert rebuilt.known.sum() == span[1]
    summary = tap.vi_summary([r.fields for r in records], tap.summary_calls(session))
    assert summary["records"] == 3 and summary["lost"] == 0 and summary["keys"] == 1


def audio_ram(index: int, frame: int, waiting: bool, ai: int = 1) -> np.ndarray:
    """The audio state at one VI: task `index` dispatched and waiting, or nothing waiting."""
    ram = np.zeros(RDRAM, np.uint8)
    queue, messages = US.sp_task_queue_start, US.sp_task_messages_start
    for offset, value in ((8, 1 if waiting else 0), (12, 3), (16, 16), (20, messages)):
        put_u32(ram, queue + offset, value)
    task = SYM["gAudioTasks"] + 0x50 * index
    put_u32(ram, messages + 4 * 3, task)
    for name, value in (("gAudioTaskIndex", index), ("gAudioFrameCount", frame), ("gCurrAiBufferIndex", ai)):
        put_u32(ram, SYM[name], value)
    put_u32(ram, SYM["gAiBuffers"] + 4 * ai, US.audio_heap_start + 0xA00 * ai)
    put_s16(ram, SYM["gAiBufferLengths"] + 2 * ai, 544)
    put_u32(ram, SYM["gAudioCmdBuffers"] + 4 * index, US.audio_heap_start + 0x2500 + 0x3200 * index)
    put_u32(ram, task, 2)                                                 # M_AUDTASK
    put_u32(ram, task + 0x30, US.audio_heap_start + 0x2500 + 0x3200 * index)   # data_ptr
    return ram


@needs_toolchain
def test_g0_accepts_a_waiting_task_and_judges_a_vi_without_one(tmp_path):
    states = [audio_ram(0, 10, True, ai=0),     # a task waits
              audio_ram(1, 11, True, ai=1),     # the next frame's task waits
              audio_ram(1, 12, False, ai=1),    # none made since (an audio load): holds
              audio_ram(0, 13, False, ai=2)]    # one made and gone: the VI came too late
    frames = [str(write_frame(tmp_path / "task", states[0]))]
    frames += ["vi:" + str(write_frame(tmp_path / f"vi{i}", ram)) for i, ram in enumerate(states)]
    armed = "seconds=600\nreference=0\n" + "\n".join(tap.audio_state_lines(US)) + "\n"
    session = record(tmp_path, armed, frames)
    verdicts, last = [], None
    for rec, state in tap.vi_states(tap.read_records(session / "tap.bin"), RDRAM):
        pending = stems_check.pending_task(stems_check.WordRam(state.ram, SYM), SYM, rec.fields["vi_index"])
        verdicts.append((pending.ok, stems_check.judge(pending, last)))
        last = pending
    assert verdicts == [(True, None), (True, None), (False, None),
                        (False, "a task was made since the last VI and none waits")]
    first = stems_check.pending_task(stems_check.WordRam(states[0], SYM), SYM, 0)
    assert first.ai_buffer == US.audio_heap_start and first.ai_length == 544
    assert first.words[12] == US.audio_heap_start + 0x2500


def test_g0_refuses_a_running_audio_task_and_a_skipped_frame():
    ram = audio_ram(0, 10, True)
    put_u32(ram, SYM["sCurrentAudioSPTask"], SYM["gAudioTasks"])
    assert stems_check.pending_task(stems_check.WordRam(ram, SYM), SYM, 0).reason == "an audio task is running"
    before = stems_check.pending_task(stems_check.WordRam(audio_ram(0, 10, True), SYM), SYM, 0)
    skipped = stems_check.pending_task(stems_check.WordRam(audio_ram(1, 12, True), SYM), SYM, 1)
    assert stems_check.judge(skipped, before) == "the audio frame count moved by 2 with a task waiting"


def test_arming_names_the_audio_spans_and_dmem_within_the_wrappers_vi_limits():
    source = (REPO / "plugin/gfxwrap/scene_tap.cpp").read_text(encoding="utf-8")
    count, value, shift = map(int, re.search(r"kMaxViRegions = (\d+), kMaxViBytes = (\d+)u << (\d+)", source).groups())
    lines = tap.audio_state_lines(US)
    spans = [tuple(int(part, 16) if i == 0 else int(part) for i, part in
                   enumerate(line.removeprefix("vi_region=").split(":"))) for line in lines[:-1]]
    assert lines[-1] == "vi_dmem=1" and len(spans) == len(tap.AUDIO_SPANS) <= count
    assert sum(size for _, size in spans) <= value << shift
    assert all(begin % 4 == 0 and size % 4 == 0 and begin + size <= RDRAM for begin, size in spans)
    args = SimpleNamespace(version="us", seconds=420, no_reference=True, reference_every=1, full_every=0,
                           no_scene_state=True, no_audio_state=False)
    assert all(line in tap.arming_text(args).splitlines() for line in lines)
    args.no_audio_state = True
    assert not any(line.startswith("vi_") for line in tap.arming_text(args).splitlines())


def test_g2_finds_the_offset_and_rate_of_a_recording_and_flags_a_dropped_stretch(tmp_path):
    rng = np.random.default_rng(64)
    rate, seconds, lag, drift = 48000, 14, 7000.0, 50e-6
    noise = rng.normal(0, 4000, seconds * stems_check.sp.AI_RATE).astype(np.float32)
    game = np.convolve(noise, np.ones(6) / 6, mode="same")                   # band-limited, never periodic
    stereo = np.repeat(np.clip(game, -32768, 32767).astype(np.int16)[:, None], 2, axis=1)
    count = int((seconds - 1) * rate)
    positions = (np.arange(count) - lag) * (stems_check.sp.AI_RATE / rate) * (1 + drift)
    heard = 0.8 * stems_check.interp(stereo.astype(np.float32).mean(axis=1), positions) + rng.normal(0, 1, count)

    def capture(path: Path, pcm: np.ndarray) -> Path:
        mono = np.clip(pcm, -32768, 32767).astype(np.int16)
        frames = np.repeat(mono[:, None], 2, axis=1)
        chunks = [ac.Chunk(0, at, min(480, len(frames) - at), 1000 + at * 200) for at in range(0, len(frames), 480)]
        asn.write_capture(path, rate, 10_000_000, [(c, frames[c.first:c.first + c.samples].tobytes()) for c in chunks])
        return path

    clean = stems_check.as_heard(stereo, [], capture(tmp_path / "clean.cap", heard))
    assert clean["verdict"] == "pass", clean
    assert abs(clean["offset_ms"] - lag / rate * 1000) < 0.1
    assert abs(clean["rate_ppm"] + drift * 1e6) < 5
    assert clean["snr_db_median"] > 50
    dropped = np.concatenate([heard[:9 * rate], heard[9 * rate + 960:]])     # 20 ms the plugin never played
    report = stems_check.as_heard(stereo, [], capture(tmp_path / "dropped.cap", dropped))
    assert report["off_fit_at_s"], report
