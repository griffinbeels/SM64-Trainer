"""The live spool (M4 round 2) through the REAL producer and writer.

tools/scene_lab/tap_offline_host.cpp feeds frame inputs through plugin/gfxwrap/scene_tap.cpp
(and its writer, scene_spool.cpp) as Project64 would, with --live: the tap thread watches the
lease tools/scene_lab/live_spool.py writes and records segments over the fixed spool files.
Synthetic RDRAM and a synthetic ROM only. The contracts: a span read from the middle of the spool
packages to the same closures and scene states, byte for byte, as the one-shot tap of the same
tasks (through the Compression API's XPRESS_HUFF on the way); capture stops when the lease ends or
lapses and resumes in a new segment; the spool files are reused in turn and never deleted, a
later session included, within the byte cap; format 1's folders move out once; a forced ring
overflow shows as lost records and a hole; a torn tail is ignored.

Skipped (not failed) on a machine without the x86 MSVC toolchain."""
from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import threading
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


def _taps(spool) -> list[Path]:
    """The session's segment files (.tap), oldest segment first, as the spool table names them."""
    table = live_spool.read_table(spool.session)
    return [live_spool.spool_file(spool.session, table.files[number], "tap") for number in sorted(table.files)]


def _summary(folder: Path) -> dict:
    return dict(line.partition("=")[::2] for line in (folder / "summary.txt").read_text().splitlines())


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
            "health_while_recording": health, "host_output": output, "folder": folder, "arming": arming}


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
    assert len(_taps(spool)) == 5, "segment_ms=50 rolls a segment at every 70 ms pause"
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
    summary = _summary(spool.session)
    assert summary["session"] == str(spool.id) and summary["end_reason"] == "rom_closed"
    assert summary["spool_tasks"] == "20" and summary["spool_lost_records"] == "0" and summary["spool_holes"] == "0"
    assert int(summary["spool_compressed"]) > 0
    # Five segments: five spool files made, the first time any session reached them; nothing deleted.
    assert (summary["spool_segments"], summary["spool_files_created"], summary["spool_files_reused"]) == ("5", "10", "0")
    codecs = set()
    # Inside a segment, a task's regions travel as the blocks that changed (synthetic RAM is noise,
    # so compression gains little here; the disk rate on his recording is in live_spool's docstring).
    for path in _taps(spool):
        data, packed = path.read_bytes(), []
        for at, kind, size, _sequence, _qpc, _flags in tap._walk(data, torn_ok=True):
            inner = struct.unpack_from("<8I", data, at + 32)
            if kind == tap.REC_PACKED:
                codecs.add(inner[2])
            if kind == tap.REC_PACKED and inner[0] == tap.REC_REGIONS and inner[6]:
                packed.append((size, inner[1]))
        assert packed and all(size < inner_bytes / 20 for size, inner_bytes in packed[1:]), path.name
        assert [offset for offset, _, _ in tap.read_segment(data, RDRAM)]    # every record decodes in Python
    # The native writer's Compression API records are what the Python reader decoded above.
    assert codecs == {tap.CODEC_STORED, tap.CODEC_XPRESS_HUFF}
    health = spool.health()
    assert health["recording"] is False and health["reason"] == "Project64 closed the game."
    assert health["lost"] == 0 and health["holes"] == []
    assert "task_us p50=" in recorded["host_output"] and "vi_us p50=" in recorded["host_output"]


@needs_toolchain
def test_capture_stops_when_the_lease_ends_or_lapses_and_resumes_in_a_new_segment(recorded, tmp_path):
    frames = recorded["frames"]
    lease = live_spool.Lease(_lab(tmp_path), extra_lines=["segment_ms=60000"])
    spools = _lab(tmp_path) / "live"
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
        # One folder, the session's own files and the first spool file pair; no folder per session.
        assert sorted(path.name for path in spools.iterdir()) == [
            "boot.bin", "lease-lines.txt", "lease.txt", "session.txt", "spool-00.idx", "spool-00.tap",
            "spool-table.bin", "writer.lock"]
        lease.stop()
        assert host.send("poke") == "ok 0"
        host.send(f"frame {frames[3]}")                  # not recorded, not counted
        health = spool.health()
        assert health["recording"] is False and health["reason"] == "The Trainer stopped the scene recording."
        lease.start()                                    # the same lines: the same session, a new segment
        assert host.send("poke") == "ok 1"
        host.send(f"frame {frames[4]}")
        host.send("poke")
        spool.refresh()
        refs = spool.tasks()
        assert [ref.index for ref in refs] == [0, 1, 2, 3] and refs[3].global_timer == 1004
        assert len(_taps(spool)) == 2
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
        lease.stop()
        host.close()


@needs_toolchain
def test_a_later_segment_rebuilds_the_audio_spans_alone_when_the_lease_asks_for_them(recorded, tmp_path):
    """The lease asks for the VI spans (the stem renderer's input) unless told not to; then each
    segment starts with a VI key."""
    frames = recorded["frames"]
    lease = live_spool.Lease(_lab(tmp_path), extra_lines=["segment_ms=50"])
    assert all(line in lease.text.splitlines() for line in tap.audio_state_lines(US))
    assert "vi_region=" not in live_spool.lease_text(audio=False)
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
    segments = _taps(spool)
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


def _spool_names(folder: Path) -> set[str]:
    return {path.name for path in folder.glob("spool-??.*")}


@needs_toolchain
def test_the_spool_files_are_reused_in_turn_never_deleted_and_hold_the_byte_cap(recorded, tmp_path):
    frames = recorded["frames"]
    folder = _lab(tmp_path) / "live"
    lease = live_spool.Lease(_lab(tmp_path), extra_lines=["segment_ms=30", "spool_files=3"])
    lease.start()
    host, closed = Host(tmp_path, frames[0]), False
    try:
        assert host.send("poke") == "ok 1"
        seen = set()
        for group in range(8):
            host.send(f"frame {frames[group % FRAMES]}")
            host.send(f"frame {frames[(group + 1) % FRAMES]}")
            host.send("sleep 60")
            seen |= _spool_names(folder)
        host.send("poke")
        spool = live_spool.open_spool(_lab(tmp_path))
        files = ["spool-00.idx", "spool-00.tap", "spool-01.idx", "spool-01.tap", "spool-02.idx", "spool-02.tap"]
        assert sorted(seen) == sorted(_spool_names(folder)) == files       # never more files, none gone
        table = live_spool.read_table(folder)
        assert sorted(table.files) == [6, 7, 8] and table.files == {6: 2, 7: 0, 8: 1}   # segment n in file (n-1) % 3
        refs = spool.tasks()
        assert refs[-1].index == 15 and refs[0].index >= 6               # the files hold the newest segments
        assert spool.source(refs[0].index, refs[-1].index) is not None
        lease.stop()
        host.send("poke")
        # The byte cap, a new session (new lines): each file holds at most max_bytes / files, so with a
        # cap below one level key (1.4 MB of noise) every task opens a segment; the same files again.
        capped = live_spool.Lease(_lab(tmp_path), extra_lines=["segment_ms=60000", "spool_files=3",
                                                                "max_bytes=2500000"])
        capped.start()
        assert host.send("poke") == "ok 1"
        summary = _summary(folder)                       # the new lines ended the first session
        assert summary["session"] == str(spool.id) and (
            summary["spool_segments"], summary["spool_files_created"], summary["spool_files_reused"]) == ("8", "6", "10")
        for group in range(5):
            host.send(f"frame {frames[group % FRAMES]}")
        host.send("poke")
        newest = live_spool.open_spool(_lab(tmp_path))
        assert newest.id != spool.id and newest.name != spool.name
        spool.refresh()
        assert spool.tasks() == [] and spool.health()["recording"] is False   # the old one's readers let go
        assert spool.health()["reason"] == "The lease's settings changed."
        assert len(newest.tasks()) == 3 and all(len(segment.tasks) == 1 for segment in newest._segments.values())
        capped.stop()
        host.close()                                     # the game closes: the second session's summary
        closed = True
        summary = _summary(folder)
        assert summary["session"] == str(newest.id) and summary["spool_files_created"] == "0"
        assert summary["spool_files_reused"] == "10" and _spool_names(folder) == set(files)
    finally:
        lease.stop()
        if not closed:
            host.close()


def test_format_1s_folders_move_out_once_and_only_whole(tmp_path):
    """clear_old_layout: a folder holding only format 1's files goes; one an older wrapper still
    writes (its writer.lock open) or with anything else in it stays; the run is logged."""
    lab = _lab(tmp_path)
    old = lab / "live" / "live-20261105-115544"
    old.mkdir(parents=True)
    for name in ("seg-000001.tap", "seg-000001.idx", "boot.bin", "session.txt", "armed.txt", "summary.txt",
                 "writer.lock"):
        (old / name).write_bytes(b"x" * 10)
    held = lab / "live" / "live-20261106-090000"
    held.mkdir()
    (held / "seg-000002.tap").write_bytes(b"y")
    lock = (held / "writer.lock").open("wb")              # an older wrapper still writes this one
    mixed = lab / "live" / "live-20261106-100000"
    mixed.mkdir()
    (mixed / "notes.txt").write_text("mine", encoding="ascii")
    stems = lab / "stems" / "live-20261105-115544"
    stems.mkdir(parents=True)
    (stems / "vi-00000001-00000000000000000001.stm").write_bytes(b"z" * 5)
    try:
        removed = live_spool.clear_old_layout(lab)
        assert (removed["folders"], removed["files"], removed["bytes"]) == (2, 8, 75)
        assert sorted(removed["kept"]) == ["live-20261106-090000", "live-20261106-100000"]
        assert not old.exists() and not stems.exists() and (held / "seg-000002.tap").exists()
        assert (mixed / "notes.txt").read_text(encoding="ascii") == "mine"
        assert "removed 2 format 1 folders, 8 files, 75 bytes" in (lab / "live" / live_spool.OLD_LAYOUT_LOG).read_text()
    finally:
        lock.close()
    assert live_spool.clear_old_layout(lab)["folders"] == 1               # the wrapper let go: it goes now
    log = (lab / "live" / live_spool.OLD_LAYOUT_LOG).read_text().splitlines()
    assert len(log) == 2 and live_spool.clear_old_layout(lab) == {"folders": 0, "files": 0, "bytes": 0,
                                                                  "kept": ["live-20261106-100000"]}
    assert len((lab / "live" / live_spool.OLD_LAYOUT_LOG).read_text().splitlines()) == 2   # nothing removed: no line


def test_the_reader_decodes_only_this_formats_codecs():
    assert tap.unpack_payload(tap.CODEC_STORED, b"abcd", 4) == b"abcd"
    with pytest.raises(ValueError, match="format 1's XPRESS"):
        tap.unpack_payload(1, b"\x00" * 8, 64)
    with pytest.raises(ValueError, match="does not decompress"):
        tap.unpack_payload(tap.CODEC_XPRESS_HUFF, b"\x00" * 8, 64)


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


class Follower:
    """Spool.follow's captures drained on a thread, as the lab's cutter would."""

    def __init__(self, source, rom: bytes | None = None, out: Path | None = None):
        self.source, self.indexes, self.stats = source, [], None
        self.thread = threading.Thread(target=self._run, args=(rom, out), daemon=True)
        self.thread.start()

    def _run(self, rom, out):
        captures = self.source.captures
        if rom is None:
            for capture in captures:
                self.indexes.append(capture.fields[10])
            return
        def counted():
            for capture in captures:
                self.indexes.append(capture.fields[10])
                yield capture
        self.source.captures = counted()
        self.stats = pk.write_package(self.source, rom, out, pk.ZLIB, group=3, checkpoint_every=4)

    def wait_for(self, count: int, timeout: float = 10) -> None:
        until = time.monotonic() + timeout
        while len(self.indexes) < count and time.monotonic() < until:
            time.sleep(0.01)
        assert len(self.indexes) >= count, (self.indexes, self.source.ended)

    def join(self, timeout: float = 10) -> None:
        self.thread.join(timeout)
        assert not self.thread.is_alive(), "the follower did not end"


@needs_toolchain
def test_follow_yields_tasks_as_they_flush_across_segments_and_matches_source(recorded, tmp_path):
    frames, rom = recorded["frames"], recorded["rom"]
    lease = live_spool.Lease(_lab(tmp_path), extra_lines=["segment_ms=50"])
    lease.start()
    host = Host(tmp_path, frames[0])
    try:
        assert host.send("poke") == "ok 1"
        for task in range(3):
            host.send(f"frame {frames[task]}")
            host.send(f"vi {frames[task]}")
        host.send("poke")
        spool = live_spool.open_spool(_lab(tmp_path))
        live = Follower(spool.follow(1), rom, tmp_path / "followed.sm64scene")
        abandoned = Follower(spool.follow(40))                # waits for a task still to come
        live.wait_for(2)                                     # tasks 1, 2 before any more play
        for task in range(3, 14):
            if task % 4 == 0:
                host.send("sleep 70")                        # a new segment
            host.send(f"frame {frames[task % FRAMES]}")
            host.send(f"vi {frames[(task + task // 6) % FRAMES]}")
            if task == 6:
                live.wait_for(5)                             # flushed tasks arrive while play goes on
        live.source.stop_at(11)
        live.join()
        abandoned.source.stop_at(-1)
        abandoned.join(timeout=1)
        assert abandoned.indexes == [] and abandoned.source.ended is None
        assert live.indexes == list(range(1, 12)) and live.source.ended is None and live.source.last == 11
        assert len(_taps(spool)) >= 3
        expected = tmp_path / "source.sm64scene"
        cut = spool.source(1, 11)
        pk.write_package(cut, rom, expected, pk.ZLIB, group=3, checkpoint_every=4)
        spool.refresh()
        first_qpc = next(segment.tasks[1][2] for segment in spool._segments.values() if 1 in segment.tasks)
        assert live.source.origin_qpc == cut.origin_qpc == first_qpc   # a capture's times count from it
        followed, cut = (pk.Package(path.read_bytes(), rom) for path in (tmp_path / "followed.sm64scene", expected))
        assert len(followed) == len(cut) == 11
        for index in range(11):
            assert followed.frame(index) == cut.frame(index)
            assert pk.state_fingerprint(*followed.scene_state(index)[1:]) == pk.state_fingerprint(
                *cut.scene_state(index)[1:])
        # The lease ends: a follower at the live edge ends too, saying why.
        tail = Follower(spool.follow(12))
        tail.wait_for(2)
        lease.stop()
        assert host.send("poke") == "ok 0"
        tail.join()
        assert tail.indexes == [12, 13] and tail.source.ended == "The Trainer stopped the scene recording."
    finally:
        lease.stop()
        host.close()


@needs_toolchain
def test_follow_ends_with_a_reason_when_it_falls_behind_or_reaches_a_hole(recorded, tmp_path):
    frames = recorded["frames"]
    lease = live_spool.Lease(_lab(tmp_path), extra_lines=["segment_ms=30", "spool_files=2", "ring_mib=16"])
    lease.start()
    host = Host(tmp_path, frames[0])
    try:
        assert host.send("poke") == "ok 1"
        for task in range(3):
            host.send(f"frame {frames[task]}")
            host.send(f"vi {frames[task + 1]}")
        host.send("poke")
        spool = live_spool.open_spool(_lab(tmp_path))
        slow = spool.follow(0)
        first = next(slow.captures)                          # it has read segment 1 so far, then stalls
        for group in range(6):
            host.send("sleep 60")
            host.send(f"frame {frames[group % FRAMES]}")
        host.send("poke")                                    # segment 1's file took a later segment
        assert 1 not in live_spool.read_table(spool.session).files
        rest = [capture.fields[10] for capture in slow.captures]
        assert [first.fields[10], *rest] == list(range(len(rest) + 1))   # never a silent skip
        assert "fell behind" in slow.ended
        late = spool.follow(0)
        assert list(late.captures) == [] and late.ended == "Task 0 already left the spool's window."
        # A full ring: the follower stops at the first incomplete task (a new session, a wide window).
        lease.stop()
        host.send("poke")
        lease = live_spool.Lease(_lab(tmp_path), extra_lines=["segment_ms=60000", "ring_mib=16"])
        lease.start()
        assert host.send("poke") == "ok 1"
        host.send(f"frame {frames[0]}")
        host.send("poke")
        spool = live_spool.open_spool(_lab(tmp_path))
        newest = spool.tasks()[-1].index
        through = Follower(spool.follow(newest))
        host.send("hold")
        for task in range(80):                               # each shown, as the game shows them
            host.send(f"frame {frames[task % FRAMES]}")
            host.send(f"vi {frames[task % FRAMES]}")
        host.send("release")
        host.send("poke")
        for task in range(3):
            host.send(f"frame {frames[task]}")
            host.send(f"vi {frames[task]}")
        host.send("poke")
        through.join()
        holes = spool.health()["holes"]
        assert holes and through.indexes == list(range(newest, holes[0][0]))
        assert through.source.ended == f"Task {holes[0][0]} lost records when the capture ring was full."
    finally:
        lease.stop()
        host.close()


@needs_toolchain
def test_the_shared_capture_loop_gives_what_tap_source_gives(recorded, tmp_path):
    """live_spool.captures is the loop package.tap_source repeats: the same captures, refusals
    and times over a one-shot session with a refused task and an overtaken one, in both pairings."""
    frames = recorded["frames"]
    refused = tmp_path / "refused"
    shutil.copytree(frames[1], refused)
    ram = bytearray((refused / "rdram.bin").read_bytes())
    struct.pack_into("<II", ram, LIST + 0x40, 0x0A000000, 0)   # a command the walker does not know: refused
    (refused / "rdram.bin").write_bytes(bytes(ram))
    root = tmp_path / "one-shot"
    _lab(root).mkdir(parents=True)
    (_lab(root) / "armed.txt").write_text(recorded["arming"], encoding="ascii")
    script = [f"frame {frames[0]}", f"vi {frames[0]}", f"frame {refused}", f"vi {frames[1]}",
              f"frame {frames[2]}", f"frame {frames[3]}", f"vi {frames[3]}", f"frame {frames[4]}", f"vi {frames[4]}"]
    result = subprocess.run([str(tap.build_offline_host()), str(root), *script], capture_output=True, text=True,
                            timeout=300, check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    (session,) = _lab(root).glob("tap-*")
    frequency = int(tap.session_header(session)["qpc_frequency"])
    for pair in ("next", "origin"):
        expected = pk.tap_source(session, pair, states=True)
        wanted = list(expected.captures)
        mine = pk.Source("scene-tap", expected.rdram_size, expected.rom_header, expected.identity, expected.build)
        mine.states = pk.TapStates()
        got = list(live_spool.captures(mine, tap.read_records(session / "tap.bin"), frequency, pair=pair))
        assert got == wanted and len(got) == 4 and mine.refused == expected.refused == 1
        assert mine.origin_qpc == next(r.qpc for r in tap.read_records(session / "tap.bin") if isinstance(r, tap.Task))
    assert [capture.shown for capture in got] != [capture.shown for capture in pk.tap_source(session, "next").captures]


@needs_toolchain
def test_a_torn_segment_tail_and_index_row_are_ignored(recorded, tmp_path):
    spool = recorded["spool"]
    copy = tmp_path / "live"
    copy.mkdir()
    for path in spool.session.iterdir():
        (copy / path.name).write_bytes(path.read_bytes())
    (copy / "summary.txt").unlink()
    table = live_spool.read_table(copy)
    newest = live_spool.spool_file(copy, table.files[max(table.files)], "tap")
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
