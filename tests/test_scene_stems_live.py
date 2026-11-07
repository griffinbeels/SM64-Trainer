"""The stem renderer while he plays (tools/scene_lab/stems_live.py): its rows, its store, its
reading of the live spool and its process.

Every VI becomes one stored row: a VI holding a waiting audio task carries that task's music and
effects and, read at the next VI, what Project64's RSP wrote for it (the game's own output); one
VI in G1_EVERY also compares the microcode's own render with it. These tests hold that
bookkeeping with a stand-in for the microcode (the rendering itself is stems_check.Splitter's,
proven on savestates and live sessions by stems_probe.py from-tap), the store's span reads, the
follower against the REAL spool writer (tools/scene_lab/tap_offline_host.cpp, synthetic RAM;
skipped without the x86 MSVC toolchain), and that a child which cannot start answers every
request with why. No ROM, emulator or recorded sound.
"""
import importlib.util
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "tools" / "scene_lab"))

import build_plugin  # noqa: E402
import live_spool  # noqa: E402
import stems_check as sc  # noqa: E402
import stems_live as sl  # noqa: E402
import tap  # noqa: E402

from sm64_events.memory.layout import US  # noqa: E402


def _module(name: str):
    spec = importlib.util.spec_from_file_location(f"stems_live_{name}", REPO / f"tests/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


capture = _module("test_scene_stems_capture")          # audio_ram: a VI's audio state at the US spans
GLOBALS = US.audio_globals_start
SYM = {**capture.SYM, "gNotes": GLOBALS + 0x40, "gMaxSimultaneousNotes": GLOBALS + 0x44,
       "gSynthesisReverb": GLOBALS + 0x200}            # no notes and no reverb: the AI buffer is all a task writes
LENGTH = 544


def put_samples(ram: np.ndarray, address: int, samples: np.ndarray) -> None:
    """int16 stereo into Project64's word-swapped RDRAM, as the RSP writes an AI buffer."""
    raw = np.frombuffer(samples.astype(">i2").tobytes(), np.uint8)
    at = address & 0x1FFFFFFF
    ram[at:at + len(raw)] = sc.sp.swap32(raw.copy())


def tone(value: int) -> np.ndarray:
    return np.full((LENGTH, 2), value, np.int16)


class Splitter:
    """The microcode's stand-in: music 1s and effects 2s for every task; its own render of a task
    (G1's sample) is `mixes` in order."""

    def __init__(self, mixes):
        self.work = np.zeros(8 << 20, np.uint8)
        self.mixes, self.restarts = list(mixes), 0

    def restart(self):
        self.restarts += 1

    def mix(self, pending, state):
        put_samples(self.work, pending.ai_buffer, self.mixes.pop(0))
        return 0

    def split(self, pending, state, ram):
        seen = {"unknown_owner_commands": 0, "unattributed_envmixers": 0, "handovers": 0, "edit_errors": 0,
                "ring_reset": False, "settled": True}
        return {"music": tone(1), "effects": tone(2)}, seen


def vi(index: int, ram: np.ndarray):
    return SimpleNamespace(fields={"vi_index": index, "tasks": index // 2}, qpc=1000 + index * 166), \
        SimpleNamespace(ram=ram, dmem=np.zeros(0x1000, np.uint8))


def test_each_vi_is_a_row_and_a_task_row_takes_the_games_output_from_the_next_vi(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "G1_EVERY", 2)
    states = {0: capture.audio_ram(0, 10, True, ai=0), 1: capture.audio_ram(1, 11, True, ai=1),
              2: capture.audio_ram(1, 11, False, ai=1),              # an audio load: no task made
              4: capture.audio_ram(0, 12, True, ai=2),               # VI 3 never reached the spool
              5: capture.audio_ram(1, 13, True, ai=0)}
    for ram in states.values():
        capture.put_s16(ram, SYM["gAiBufferLengths"], LENGTH)
        capture.put_s16(ram, SYM["gAiBufferLengths"] + 2, LENGTH)
        capture.put_s16(ram, SYM["gAiBufferLengths"] + 4, LENGTH)
        for ai in range(3):
            capture.put_u32(ram, SYM["gAiBuffers"] + 4 * ai, US.audio_heap_start + 0xA00 * ai)
    put_samples(states[1], US.audio_heap_start, tone(100))           # what VI 0's task wrote
    put_samples(states[2], US.audio_heap_start + 0xA00, tone(200))   # VI 1's
    put_samples(states[5], US.audio_heap_start + 0x1400, tone(400))  # VI 4's
    splitter = Splitter([tone(201), tone(0)])                        # the 2nd task's own render: one unit off
    store = sl.StemStore(tmp_path, "live-test")
    renderer = sl.StemRenderer(splitter, SYM, store)
    for index, ram in states.items():
        renderer.visit(*vi(index, ram))
    renderer.finish()
    store.close()
    rows = {row["vi"]: row for row in sl.read_rows(store.folder, 0, 1 << 40)}
    assert sorted(rows) == [0, 1, 2, 4, 5]
    assert rows[0]["flags"] == sl.TASK | sl.REAL | sl.SETTLED | sl.AFTER_GAP     # the first a renderer sees
    assert np.array_equal(rows[0]["real"], tone(100)) and np.array_equal(rows[0]["music"], tone(1))
    assert np.array_equal(rows[0]["effects"], tone(2))
    assert rows[1]["flags"] & sl.REAL and np.array_equal(rows[1]["real"], tone(200))
    assert rows[1]["flags"] & sl.CHECKED and rows[1]["flags"] & sl.DIFFERS and rows[1]["g1"] == 1
    assert rows[1]["g1_rms"] == 1000                                 # one unit everywhere, in thousandths
    assert rows[2]["flags"] == 0 and rows[2]["real"] is None and rows[2]["samples"] == 0
    assert rows[4]["flags"] & sl.AFTER_GAP and np.array_equal(rows[4]["real"], tone(400))
    assert splitter.restarts == 1                                    # the stems start silent after the gap
    assert rows[5]["flags"] == sl.TASK | sl.SETTLED and rows[5]["real"] is None   # no next VI: never read
    assert renderer.counts | {} == {"vis": 5, "tasks": 4, "missed": 0, "split_failed": 0, "checked": 1, "differs": 1,
                                    "gaps": 1}
    assert renderer.cost()["vis"] == 5


def test_the_store_reads_a_span_from_the_files_it_touches_and_ignores_a_row_being_written(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "FILE_ROWS", 3)
    store = sl.StemStore(tmp_path, "live-span")
    for index in range(10):
        streams = [tone(index), tone(-index), tone(index)] if index % 2 == 0 else []
        flags = sl.TASK | sl.REAL if streams else 0
        store.append(index, flags, 10_000 + 100 * index, index, 0, streams)
    store.close()
    files = sorted(store.folder.glob("*.stm"))
    assert [path.name for path in files] == ["stems-00.stm", "stems-01.stm", "stems-02.stm", "stems-03.stm"]
    with files[-1].open("ab") as handle:
        handle.write(sl.ROW.pack(10, sl.TASK, 11_000, LENGTH, 5, 0, 0) + b"\x00" * 100)   # torn
    rows = sl.read_rows(store.folder, 10_250, 10_750)
    assert [row["vi"] for row in rows] == [3, 4, 5, 6, 7]
    assert np.array_equal(rows[1]["real"], tone(4)) and np.array_equal(rows[1]["music"], tone(-4))
    assert [row["vi"] for row in sl.read_rows(store.folder, 0, 1 << 40)] == list(range(10))
    assert sl.read_rows(store.folder, 0, 1 << 40, session="live-other") == []
    assert store.wait_past(10_900, 0.05, lambda: True) and not store.wait_past(20_000, 0.05, lambda: True)


def test_the_store_reuses_its_fixed_files_in_turn_and_never_deletes_one(tmp_path, monkeypatch):
    """STORE_FILES files written in turn and emptied in place: a later session's rows go into the
    same files, oldest first, and a reader asks for its session's rows by name."""
    monkeypatch.setattr(sl, "FILE_ROWS", 2)
    monkeypatch.setattr(sl, "STORE_FILES", 3)
    store = sl.StemStore(tmp_path, "live-a")
    seen = set()
    for index in range(10):                                 # five files' worth into three
        store.append(index, 0, 1_000 + index, index, 0, [])
        seen |= {path.name for path in store.folder.iterdir()}
    store.close()
    assert sorted(seen) == ["stems-00.stm", "stems-01.stm", "stems-02.stm"]
    assert [row["vi"] for row in sl.read_rows(store.folder, 0, 1 << 40, session="live-a")] == [4, 5, 6, 7, 8, 9]
    later = sl.StemStore(tmp_path, "live-b")                # the next session starts at the first file again
    for index in range(2):
        later.append(index, 0, 5_000 + index, index, 0, [])
    later.close()
    assert sorted(path.name for path in store.folder.iterdir()) == sorted(seen)
    assert [row["vi"] for row in sl.read_rows(store.folder, 0, 1 << 40, session="live-b")] == [0, 1]
    assert [row["vi"] for row in sl.read_rows(store.folder, 0, 1 << 40, session="live-a")] == [4, 5, 8, 9]


# ---------------------------------------------------------------- the live spool, through the real writer

needs_toolchain = pytest.mark.skipif(not build_plugin.toolchain_available(),
                                     reason="no x86 MSVC toolchain (vcvars32.bat)")


@needs_toolchain
def test_the_follower_reads_the_spools_vi_records_as_they_come_and_after_a_reused_file(tmp_path):
    spool_tests = _module("test_scene_live_spool")
    frames = spool_tests._frames(tmp_path / "frames", spool_tests._rom())
    lab = spool_tests._lab(tmp_path)
    lease = live_spool.Lease(lab, extra_lines=["segment_ms=50"])
    assert "vi_dmem=1" in lease.text                                  # the lease asks for the audio spans
    lease.start()
    host = spool_tests.Host(tmp_path, frames[0])
    try:
        assert host.send("poke") == "ok 1"
        for task in range(8):
            if task == 4:
                host.send("sleep 70")
            host.send(f"frame {frames[task % spool_tests.FRAMES]}")
            for repeat in range(2):
                host.send(f"vi {frames[(task + repeat) % spool_tests.FRAMES]}")
        host.send("poke")
    finally:
        lease.stop()
        host.close()
    spool = live_spool.open_spool(lab)
    session = spool.session
    numbers = sl.segment_numbers(session, spool.id)
    assert len(numbers) == 2
    follower = sl.ViFollower(session, spool.id)                       # live: from the newest segment
    seen = []
    for _ in range(50):
        seen += follower.poll()
        if follower.ended:
            break
    assert follower.ended
    newest = list(sl.session_vi_records(session, first_segment=numbers[-1]))
    assert [r.fields["vi_index"] for r in seen] == [r.fields["vi_index"] for r in newest] and seen
    whole = {record.fields["vi_index"]: state.ram.copy()
             for record, state in tap.vi_states(sl.session_vi_records(session), spool_tests.RDRAM)}
    for record, state in tap.vi_states(iter(seen), spool_tests.RDRAM):
        known = state.known
        assert np.array_equal(state.ram[known], whole[record.fields["vi_index"]][known])
    behind = sl.ViFollower(session, spool.id)
    behind.number = numbers[0]                                        # it was reading the oldest...
    table = bytearray((session / live_spool.TABLE).read_bytes())      # ...when the writer reused its file
    entry = live_spool.TABLE_HEAD.size + live_spool.ENTRY.size * sl.segment_files(session)[numbers[0]]
    table[entry:entry + live_spool.ENTRY.size] = bytes(live_spool.ENTRY.size)
    (session / live_spool.TABLE).write_bytes(bytes(table))
    assert behind.poll() == [] and behind.gaps == 1
    again = []
    for _ in range(50):
        again += behind.poll()
        if behind.ended:
            break
    assert [r.fields["vi_index"] for r in again] == [r.fields["vi_index"] for r in newest]


def test_a_child_that_cannot_start_answers_every_request_with_why(tmp_path):
    stems = sl.StemsProcess(tmp_path / "no.z64", tmp_path / "no.map", lab_root=tmp_path)
    try:
        deadline = time.monotonic() + 60
        while stems.problem is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert stems.problem and "could not start" in stems.problem
        answer = stems.layer(b"audio", 1, "live-x", tmp_path).result(timeout=5)
        assert answer == {"reason": stems.problem}
    finally:
        assert stems.close() is not None
    assert not any(thread.is_alive() for thread in stems.threads)   # nothing of it outlives close()
    assert not list(tmp_path.glob("stems-audio-*"))
