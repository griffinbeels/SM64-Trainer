"""The live spool (M4 round 2) through the REAL producer and writer.

tools/scene_lab/tap_offline_host.cpp feeds frame inputs through plugin/gfxwrap/scene_tap.cpp
(and its writer, scene_spool.cpp) as Project64 would, with --live: the tap thread watches the
lease tools/scene_lab/live_spool.py writes and records rolling segments. Synthetic RDRAM and a
synthetic ROM only. The contracts: a span read from the middle of the spool packages to the same
closures and scene states, byte for byte, as the one-shot tap of the same tasks; capture stops
when the lease ends or lapses and resumes in a new segment; old segments leave the window and
the byte cap; a forced ring overflow shows as lost records and a hole; a torn tail is ignored.

Skipped (not failed) on a machine without the x86 MSVC toolchain."""
from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "tools" / "scene_lab"))

import build_plugin  # noqa: E402
import live_spool  # noqa: E402
import package as pk  # noqa: E402
import tap  # noqa: E402

from sm64_events.core.childproc import quiet_spawn_kwargs  # noqa: E402
from sm64_events.memory.layout import US  # noqa: E402

needs_toolchain = pytest.mark.skipif(not build_plugin.toolchain_available(),
                                     reason="no x86 MSVC toolchain (vcvars32.bat)")

RDRAM = 8 << 20
LIST, MTX, UCODE, UCODE_DATA = 0x100000, 0x110000, 0x200000, 0x201000
CODE_ROM, CODE_BYTES = 0x180000, 0x4000            # the "practice ROM code" copied to expansion RAM
FRAMES = 6


def _rom() -> bytes:
    rng = np.random.default_rng(5)
    rom = bytearray(rng.integers(0, 256, 2 << 20, dtype=np.uint8).tobytes())
    rom[0:4] = bytes.fromhex("80371240")
    rom[0x20:0x34] = b"SYNTHETIC LIVE SPOOL"
    return bytes(rom)


def _set_word(ram: np.ndarray, address: int, value: int, size: int) -> None:
    """A big-endian game value into Project64's word-swapped RDRAM (byte b at b ^ 3)."""
    for i, byte in enumerate(value.to_bytes(size, "big")):
        ram[(address + i) ^ 3] = byte


def _frames(folder: Path, rom: bytes) -> list[Path]:
    """Six RDRAM images a few frames apart: scene state, level blocks, the list and identity move."""
    rng = np.random.default_rng(64)
    ram = rng.integers(0, 256, RDRAM, dtype=np.uint8)
    ram[0x400000:0x400000 + CODE_BYTES] = np.frombuffer(pk.swap_words(rom[CODE_ROM:CODE_ROM + CODE_BYTES]), np.uint8)
    ram[0x400000 + CODE_BYTES:0x400000 + CODE_BYTES + 4] ^= 0xFF
    main_pool = US.main_pool_start & 0x1FFFFFFF
    out = []
    for index in range(FRAMES):
        ram[(US.scene_bss_start & 0x1FFFFFFF) + 4096 * index:][:64] = rng.integers(0, 256, 64, dtype=np.uint8)
        ram[main_pool + 0x20000 + 768 * index:][:16] = rng.integers(0, 256, 16, dtype=np.uint8)
        ram[MTX + 0x40 * (index % 3):][:16] = rng.integers(0, 256, 16, dtype=np.uint8)
        ram[(US.audio_heap_start & 0x1FFFFFFF) + 512 * index:][:8] = rng.integers(0, 256, 8, dtype=np.uint8)
        _set_word(ram, US.global_timer & 0x1FFFFFFF, 1000 + index, 4)
        _set_word(ram, US.curr_level & 0x1FFFFFFF, 9, 2)
        _set_word(ram, US.curr_area & 0x1FFFFFFF, 1 + index // 3, 2)
        words = ram.view("<u4")
        base = 0x10000 if index < 4 else 0x20000         # segment 2 moves: a new level key at frame 4
        words[LIST // 4:LIST // 4 + 6] = [0xBC000806, base, 0x06000000, LIST + 0x40, 0xB8000000, 0]
        # The called list: a no-op whose word moves, a matrix load (a second closure run), the end.
        words[(LIST + 0x40) // 4:(LIST + 0x40) // 4 + 8] = [0, index, 0x01000040, MTX + 0x40 * (index % 3),
                                                           0, 7 * index, 0xB8000000, 0]
        frame = folder / f"f{index}"
        frame.mkdir(parents=True)
        (frame / "rdram.bin").write_bytes(ram.tobytes())
        dmem = bytearray(0x1000)
        task = [1, 0, 0, 0, UCODE, 0x1000, UCODE_DATA, 0x800, 0, 0, 0, 0, LIST, 48, 0, 0]
        struct.pack_into("<16I", dmem, 0xFC0, *task)
        (frame / "dmem.bin").write_bytes(bytes(dmem))
        (frame / "frame.json").write_text(json.dumps({"vi": {"origin": 0x200000 + 0x1000 * (index % 2)},
                                                      "rom_header": pk.rom_header_hex(rom)}), encoding="utf-8")
        out.append(frame)
    return out


def _script(frames: list[Path]) -> list[str]:
    """Twenty tasks, each shown by a VI, in groups of four 70 ms apart (segment_ms=50 rolls at each group)."""
    commands = []
    for task in range(20):
        if task and task % 4 == 0:
            commands.append("sleep 70")
        frame = frames[task % FRAMES] if task < 12 else frames[3 + task % 3]
        commands += [f"frame {frame}", f"vi {frame}"]
    return commands


class Host:
    """tap_offline_host --live --stdin: one command, one answer."""

    def __init__(self, lab: Path, header_frame: Path):
        self.process = subprocess.Popen([str(tap.build_offline_host()), str(lab), "--live", "--stdin"],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, **quiet_spawn_kwargs())
        assert self.send(f"header {header_frame}") == "ok"

    def send(self, line: str) -> str:
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()
        answer = self.process.stdout.readline().strip()
        assert answer.startswith("ok"), (line, answer, self.process.stderr.read() if answer == "" else "")
        return answer

    def close(self) -> str:
        output, errors = self.process.communicate("quit\n", timeout=120)
        assert self.process.returncode == 0, output + errors
        return output


def _lab(root: Path) -> Path:
    return root / "SM64Trainer" / "scene-lab"


@pytest.fixture(scope="module")
def recorded(tmp_path_factory):
    if not build_plugin.toolchain_available():
        pytest.skip("no x86 MSVC toolchain (vcvars32.bat)")
    folder = tmp_path_factory.mktemp("live")
    rom = _rom()
    frames = _frames(folder / "frames", rom)
    script = _script(frames)
    # The one-shot tap of the same tasks, armed with the same lines.
    one_shot = folder / "one-shot"
    _lab(one_shot).mkdir(parents=True)
    arming = tap.arming_text(type("Args", (), {"version": "us", "seconds": 600, "no_reference": True,
                                               "reference_every": 1, "full_every": 0, "no_scene_state": False,
                                               "no_audio_state": True})())
    (_lab(one_shot) / "armed.txt").write_text(arming, encoding="ascii")
    result = subprocess.run([str(tap.build_offline_host()), str(one_shot), *script], capture_output=True, text=True,
                            timeout=300, check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    (one_shot_session,) = _lab(one_shot).glob("tap-*")
    # The live spool: lease on, the same commands, then the host closes (the ROM closes).
    live = folder / "live"
    lease = live_spool.Lease(_lab(live), extra_lines=["segment_ms=50"])
    lease.start()
    try:
        host = Host(live, frames[0])
        assert host.send("poke") == "ok 1"
        for command in script:
            host.send(command)
        host.send("poke")
        spool = live_spool.open_spool(_lab(live))
        health = spool.health()
        output = host.close()
    finally:
        lease.stop()
    return {"rom": rom, "frames": frames, "one_shot": one_shot_session, "spool": live_spool.open_spool(_lab(live)),
            "health_while_recording": health, "host_output": output, "folder": folder}


@needs_toolchain
def test_a_mid_session_span_packages_to_the_one_shot_closures_and_states_byte_for_byte(recorded, tmp_path):
    spool, rom = recorded["spool"], recorded["rom"]
    assert recorded["health_while_recording"]["recording"] is True
    refs = spool.tasks()
    assert [ref.index for ref in refs] == list(range(20))
    assert [ref.global_timer for ref in refs[:6]] == [1000 + i for i in range(6)]
    assert {(ref.level, ref.area) for ref in refs[:3]} == {(9, 1)} and refs[3].area == 2
    shown = [present is not None for _, present in tap.paired(tap.read_records(recorded["one_shot"] / "tap.bin"))]
    assert [ref.has_present for ref in refs] == shown and not all(shown)   # a repeated origin shows nothing new
    segments = sorted(spool.session.glob("seg-*.tap"))
    assert len(segments) == 5, "segment_ms=50 rolls a segment at every 70 ms pause"
    # Task 8 opens a segment with no level change of its own: its state comes from the writer's key.
    # The span starts mid-segment and crosses two later segment starts.
    first, last = 9, 17
    live = tmp_path / "live.sm64scene"
    stats = pk.write_package(spool.source(first, last), rom, live, pk.ZLIB, group=3, checkpoint_every=4)
    whole = tmp_path / "one-shot.sm64scene"
    pk.write_package(pk.load_source(recorded["one_shot"], states=True), rom, whole, pk.ZLIB, group=3,
                     checkpoint_every=4)
    live_package, whole_package = pk.Package(live.read_bytes(), rom), pk.Package(whole.read_bytes(), rom)
    by_task = {whole_package.fields(i)["task_index"]: i for i in range(len(whole_package))}
    assert [live_package.fields(i)["task_index"] for i in range(len(live_package))] == list(range(first, last + 1))
    assert stats["frames"] == last - first + 1
    for index in range(len(live_package)):
        mine, theirs = live_package.frame(index), whole_package.frame(by_task[first + index])
        assert (mine.runs, mine.run_bytes, mine.task, mine.extras, mine.vi, mine.shown) == (
            theirs.runs, theirs.run_bytes, theirs.task, theirs.extras, theirs.vi, theirs.shown)
        assert pk.state_fingerprint(*live_package.scene_state(index)[1:]) == pk.state_fingerprint(
            *whole_package.scene_state(by_task[first + index])[1:]), f"task {first + index}"
    assert live_package.states.code_windows() == whole_package.states.code_windows() != []


@needs_toolchain
def test_the_spool_is_packed_and_its_session_closes_when_the_rom_closes(recorded):
    spool = recorded["spool"]
    summary = dict(line.partition("=")[::2] for line in (spool.session / "summary.txt").read_text().splitlines())
    assert summary["end_reason"] == "rom_closed" and summary["spool_tasks"] == "20"
    assert summary["spool_lost_records"] == "0" and summary["spool_holes"] == "0"
    assert int(summary["spool_compressed"]) > 0
    # Inside a segment, a task's regions travel as the blocks that changed (synthetic RAM is noise,
    # so XPRESS gains nothing here; the disk rate on his recording is in live_spool's docstring).
    for path in spool.session.glob("seg-*.tap"):
        data, packed = path.read_bytes(), []
        for at, kind, size, _sequence, _qpc, _flags in tap._walk(data, torn_ok=True):
            inner = struct.unpack_from("<8I", data, at + 32)
            if kind == tap.REC_PACKED and inner[0] == tap.REC_REGIONS and inner[6]:
                packed.append((size, inner[1]))
        assert packed and all(size < inner_bytes / 20 for size, inner_bytes in packed[1:]), path.name
    health = spool.health()
    assert health["recording"] is False and health["reason"] == "Project64 closed the game."
    assert health["lost"] == 0 and health["holes"] == []
    assert "task_us p50=" in recorded["host_output"] and "vi_us p50=" in recorded["host_output"]


@needs_toolchain
def test_capture_stops_when_the_lease_ends_or_lapses_and_resumes_in_a_new_segment(recorded, tmp_path):
    frames = recorded["frames"]
    lease = live_spool.Lease(_lab(tmp_path), extra_lines=["segment_ms=60000"])
    spools = _lab(tmp_path) / "live"
    for name in ("live-20200101-000000", "live-20200101-000001"):   # earlier runs' sessions
        (spools / name).mkdir(parents=True)
        (spools / name / "session.txt").write_text("schema=old\n", encoding="ascii")
        (spools / name / "seg-000001.tap").write_bytes(b"old")
    held = (spools / "live-20200101-000001" / "writer.lock").open("wb")   # a writer still holds this one
    host = Host(tmp_path, frames[0])
    try:
        assert host.send("poke") == "ok 0", "no lease: no capture"
        lease.start()
        assert host.send("poke") == "ok 1"
        for frame in frames[:3]:
            host.send(f"frame {frame}")
        assert host.send("poke") == "ok 1"
        spool = live_spool.open_spool(_lab(tmp_path))
        assert [ref.index for ref in spool.tasks()] == [0, 1, 2] and spool.health()["recording"]
        assert sorted(path.name for path in spools.iterdir()) == ["lease.txt", "live-20200101-000001",
                                                                  spool.session.name]
        assert (spools / "live-20200101-000001" / "seg-000001.tap").read_bytes() == b"old"
        held.close()
        lease.stop()
        assert host.send("poke") == "ok 0"
        host.send(f"frame {frames[3]}")                  # not recorded, not counted
        health = spool.health()
        assert health["recording"] is False and health["reason"] == "The Trainer ended the lease."
        lease.start()                                    # the same lines: the same session, a new segment
        assert host.send("poke") == "ok 1"
        host.send(f"frame {frames[4]}")
        host.send("poke")
        spool.refresh()
        refs = spool.tasks()
        assert [ref.index for ref in refs] == [0, 1, 2, 3] and refs[3].global_timer == 1004
        assert len(list(spool.session.glob("seg-*.idx"))) == 2
        lease._stop.set()                                # the Trainer crashes: no renewal, no delete
        lease._thread.join()
        stale = time.time() - 60
        os.utime(lease.path, (stale, stale))
        assert host.send("poke") == "ok 0"
        assert spool.health()["reason"] == "The lease lapsed: the Trainer stopped renewing it."
        assert [ref.index for ref in spool.tasks()] == [0, 1, 2, 3]
        source = spool.source(1, 3)
        assert [capture.fields[10] for capture in source.captures] == [1, 2, 3]
    finally:
        held.close()
        lease.stop()
        host.close()


@needs_toolchain
def test_a_later_segment_rebuilds_the_audio_spans_alone_when_the_lease_asks_for_them(recorded, tmp_path):
    """VI spans are off unless the lease's extra lines name them; then each segment starts with a VI key."""
    frames = recorded["frames"]
    lease = live_spool.Lease(_lab(tmp_path), extra_lines=[*tap.audio_state_lines(US), "segment_ms=50"])
    assert "vi_region=" not in live_spool.lease_text() and "vi_dmem=1" in lease.text
    lease.start()
    host = Host(tmp_path, frames[0])
    try:
        assert host.send("poke") == "ok 1"
        for task in range(8):
            if task == 4:
                host.send("sleep 70")
            host.send(f"frame {frames[task % FRAMES]}")
            for repeat in range(2):
                host.send(f"vi {frames[(task + repeat) % FRAMES]}")
        host.send("poke")
    finally:
        lease.stop()
        host.close()
    spool = live_spool.open_spool(_lab(tmp_path))
    segments = sorted(spool.session.glob("seg-*.tap"))
    assert len(segments) == 2

    def states(paths):
        """VI states read straight through these segments (a later segment's keys skipped)."""
        records = [record for position, path in enumerate(paths)
                   for _, flags, record in tap.read_segment(path.read_bytes(), RDRAM)
                   if not (position and flags & tap.WRITER_KEY)]
        return {record.fields["vi_index"]: (state.known.copy(), state.ram[state.known].tobytes(), state.dmem.tobytes())
                for record, state in tap.vi_states(iter(records), RDRAM)}

    whole, alone = states(segments), states(segments[1:])
    later = sorted(index for index in alone if index in whole)
    assert len(later) >= 8 and min(later) > min(whole)
    for index in later:
        assert np.array_equal(alone[index][0], whole[index][0]) and alone[index][1:] == whole[index][1:], index


@needs_toolchain
def test_old_segments_leave_the_window_and_the_byte_cap(recorded, tmp_path):
    frames = recorded["frames"]
    lease = live_spool.Lease(_lab(tmp_path), extra_lines=["segment_ms=30", "window_ms=150"])
    lease.start()
    host = Host(tmp_path, frames[0])
    try:
        assert host.send("poke") == "ok 1"
        for group in range(8):
            host.send(f"frame {frames[group % FRAMES]}")
            host.send(f"frame {frames[(group + 1) % FRAMES]}")
            host.send("sleep 60")
        host.send("poke")
        spool = live_spool.open_spool(_lab(tmp_path))
        kept = sorted(spool.session.glob("seg-*.tap"))
        assert 1 <= len(kept) <= 4 and kept[0].name != "seg-000001.tap"
        refs = spool.tasks()
        assert refs[-1].index == 15 and refs[0].index >= 6
        assert spool.source(refs[0].index, refs[-1].index) is not None
        # The byte cap: about a segment and a half (each starts with a 1.4 MB level key of noise)
        # keeps the open segment and at most the newest closed one.
        lease.stop()
        host.send("poke")
        capped = live_spool.Lease(_lab(tmp_path), extra_lines=["segment_ms=30", "max_bytes=2500000"])
        capped.start()
        assert host.send("poke") == "ok 1"
        for group in range(5):
            host.send(f"frame {frames[group % FRAMES]}")
            host.send("sleep 40")
        host.send("poke")
        capped.stop()
        host.send("poke")
        newest = live_spool.open_spool(_lab(tmp_path))
        assert newest.session != spool.session              # new lines: a new session
        assert len(list(newest.session.glob("seg-*.tap"))) <= 2
    finally:
        lease.stop()
        host.close()


@needs_toolchain
def test_a_forced_ring_overflow_shows_as_lost_records_and_a_hole(recorded, tmp_path):
    frames = recorded["frames"]
    lease = live_spool.Lease(_lab(tmp_path), extra_lines=["segment_ms=60000", "ring_mib=16"])
    lease.start()
    host = Host(tmp_path, frames[0])
    try:
        assert host.send("poke") == "ok 1"
        host.send(f"frame {frames[0]}")
        host.send("poke")
        host.send("hold")                                # the writer stops draining
        for task in range(1, 120):
            host.send(f"frame {frames[task % FRAMES]}")
        host.send("release")
        host.send("poke")
        for task in range(3):
            host.send(f"frame {frames[task]}")
        host.send("poke")
        spool = live_spool.open_spool(_lab(tmp_path))
        health = spool.health()
        assert health["lost"] > 0 and health["holes"], health
        indexes = {ref.index for ref in spool.tasks()}
        for first, last in health["holes"]:
            assert first <= last
        lost_tasks = set(range(120)) - indexes
        assert lost_tasks and all(any(first <= index <= last for first, last in health["holes"])
                                  for index in lost_tasks)
        assert {120, 121, 122} <= indexes                # capture continues after the overflow
        after = spool.source(120, 122)
        assert [capture.fields[10] for capture in after.captures] == [120, 121, 122]
    finally:
        lease.stop()
        host.close()


@needs_toolchain
def test_a_torn_segment_tail_and_index_row_are_ignored(recorded, tmp_path):
    spool = recorded["spool"]
    copy = tmp_path / spool.session.name
    copy.mkdir()
    for path in spool.session.iterdir():
        (copy / path.name).write_bytes(path.read_bytes())
    (copy / "summary.txt").unlink()
    newest = max(copy.glob("seg-*.tap"))
    data = newest.read_bytes()
    records = list(tap.read_segment(data, RDRAM))
    last_offset = records[-1][0]
    newest.write_bytes(data[:last_offset + 40])          # the last record half written
    assert [offset for offset, _, _ in tap.read_segment(newest.read_bytes(), RDRAM)] == [
        offset for offset, _, _ in records[:-1]]
    index = newest.with_suffix(".idx")
    rows = index.read_bytes()
    index.write_bytes(rows + rows[:20])                  # a row half written
    torn = live_spool.Spool(copy)
    assert [ref.index for ref in torn.tasks()] == [ref.index for ref in spool.tasks()]
