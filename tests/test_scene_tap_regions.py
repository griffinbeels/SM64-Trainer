"""The scene tap's state regions (M4 E2-live) through the REAL producer.

tools/scene_lab/tap_offline_host.cpp feeds frame inputs through
plugin/gfxwrap/scene_tap.cpp exactly as ProcessDList would; tap.py reads the
session back. Synthetic RDRAM only (no game data): a two-command task whose
segment base keys the level. The contract: a task region is copied whole with
every task; a level region is copied whole at the first task and at each new
segment key, else only its changed 256-byte blocks; a task region sits at the
task's own data pointer; spans that leave RDRAM are dropped at arming; and
scene_states rebuilds, for every task, exactly the bytes RDRAM held at that
task over every captured span. `tap.py arm` names the layout's spans within
the wrapper's region limits.

The producer test is skipped (not failed) on a machine without the x86 MSVC
toolchain."""
from __future__ import annotations

import dataclasses
import json
import re
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "tools" / "scene_lab"))

import build_plugin  # noqa: E402
import tap  # noqa: E402

from sm64_events.core.childproc import quiet_spawn_kwargs  # noqa: E402
from sm64_events.memory.layout import US  # noqa: E402

needs_toolchain = pytest.mark.skipif(not build_plugin.toolchain_available(),
                                     reason="no x86 MSVC toolchain (vcvars32.bat)")

RDRAM = 8 << 20
LIST, UCODE, UCODE_DATA = 0x100000, 0x200000, 0x201000
TASK_REGION = (0x300000, 4096)
POOL_TAIL = (0x200, 256)          # task_region: offset from the task's data pointer, bytes
LEVEL_REGION = (0x400000, 65536)


def write_frame(folder: Path, ram: np.ndarray, segment_base: int) -> Path:
    """A frame input whose task sets segment 2 and ends: the walker's whole job here."""
    folder.mkdir(parents=True)
    words = ram.view("<u4")
    words[LIST // 4:LIST // 4 + 4] = [0xBC000806, segment_base, 0xB8000000, 0]   # G_MW_SEGMENT 2, G_ENDDL
    (folder / "rdram.bin").write_bytes(ram.tobytes())
    dmem = bytearray(0x1000)
    task = [1, 0, 0, 0, UCODE, 0x1000, UCODE_DATA, 0x800, 0, 0, 0, 0, LIST, 16, 0, 0]
    struct.pack_into("<16I", dmem, 0xFC0, *task)
    (folder / "dmem.bin").write_bytes(bytes(dmem))
    (folder / "frame.json").write_text(json.dumps({"vi": {"origin": 0}, "rom_header": "ab" * 64}, indent=2),
                                       encoding="utf-8")
    return folder


@needs_toolchain
def test_regions_copy_whole_tasks_and_level_keys_then_only_changed_blocks(tmp_path):
    rng = np.random.default_rng(64)
    ram = rng.integers(0, 256, RDRAM, dtype=np.uint8)
    frames, snapshots = [], []

    def frame(name: str, segment_base: int) -> None:
        frames.append(write_frame(tmp_path / name, ram, segment_base))
        snapshots.append(ram.copy())

    frame("a", 0x10000)                       # first task: a level key
    ram[LEVEL_REGION[0] + 300] ^= 0xFF        # block 1 of the level region
    ram[LEVEL_REGION[0] + LEVEL_REGION[1] - 1] ^= 0xFF   # its last block
    ram[TASK_REGION[0]:TASK_REGION[0] + 64] = 7
    frame("b", 0x10000)                       # same key: a delta of exactly those two blocks
    frame("c", 0x20000)                       # segment 2 moved: a new key
    frame("d", 0x20000)                       # nothing changed: an empty delta

    root = tmp_path / "lab"
    lab = root / "SM64Trainer" / "scene-lab"
    lab.mkdir(parents=True)
    (lab / "armed.txt").write_text(
        "seconds=600\nreference=0\n"
        f"region={TASK_REGION[0]:x}:{TASK_REGION[1]}\n"
        f"region={RDRAM - 16:x}:64\n"         # leaves RDRAM: dropped at arming
        f"task_region={POOL_TAIL[0]:x}:{POOL_TAIL[1]}\n"
        f"level_region={LEVEL_REGION[0]:x}:{LEVEL_REGION[1]}\n", encoding="ascii")
    result = subprocess.run([str(tap.build_offline_host()), str(root), *map(str, frames)], capture_output=True,
                            text=True, timeout=120, check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    (session,) = lab.glob("tap-*")
    header = tap.session_header(session)
    assert [line for line in (session / "session.txt").read_text().splitlines() if "region=" in line] == [
        f"region={TASK_REGION[0]:x}:{TASK_REGION[1]}", f"task_region={POOL_TAIL[0]:x}:{POOL_TAIL[1]}",
        f"level_region={LEVEL_REGION[0]:x}:{LEVEL_REGION[1]}"]
    assert header["level_block_bytes"] == "256"

    records = list(tap.read_records(session / "tap.bin"))
    level = [r for r in records if isinstance(r, tap.Regions) and r.kind != "task"]
    assert [r.kind for r in level] == ["level_key", "level_delta", "level_key", "level_delta"]
    assert level[0].spans == [LEVEL_REGION] and level[2].spans == [LEVEL_REGION]
    assert level[1].spans == [(LEVEL_REGION[0] + 256, 256), (LEVEL_REGION[0] + LEVEL_REGION[1] - 256, 256)]
    assert level[3].spans == [] and level[3].fields["data_bytes"] == 0
    assert level[1].segments[2] == 0x10000 and level[2].segments[2] == 0x20000
    task_regions = [r for r in records if isinstance(r, tap.Regions) and r.kind == "task"]
    assert [r.fields["task_index"] for r in task_regions] == [0, 1, 2, 3]
    pool_tail = (LIST + POOL_TAIL[0], POOL_TAIL[1])
    assert all(r.spans == [TASK_REGION, pool_tail] for r in task_regions)

    states = list(tap.scene_states(iter(records), RDRAM))
    assert len(states) == len(snapshots)
    for (task, regions, rebuilt, known), expected in zip(states, snapshots, strict=True):
        assert regions is not None
        for begin, size in (TASK_REGION, pool_tail, LEVEL_REGION):
            assert known[begin:begin + size].all()
            assert np.array_equal(rebuilt[begin:begin + size], expected[begin:begin + size])
        assert np.array_equal(rebuilt[known], expected[known]), f"task {task.fields['task_index']}"
        assert not known[TASK_REGION[0] + TASK_REGION[1]:LEVEL_REGION[0]].any()


def _wrapper_limits() -> tuple[int, int]:
    """(regions, bytes) scene_tap.cpp accepts for region= lines, read off its source."""
    source = (REPO / "plugin/gfxwrap/scene_tap.cpp").read_text(encoding="utf-8")
    count, value, shift = map(int, re.search(
        r"kMaxRegions = (\d+), kMaxRegionBytes = (\d+)u << (\d+)", source).groups())
    return count, value << shift


def _region_lines(lines: list[str]) -> list[tuple[int, int]]:
    return [tuple(int(part, 16) if i == 0 else int(part) for i, part in
                  enumerate(line.removeprefix("region=").split(":"))) for line in lines if line.startswith("region=")]


def test_arming_names_the_act_selectors_spans_beside_the_renderers_within_the_wrapper_limits():
    """The act selector re-renders only with gSaveBuffer (the course's coin score) and
    Usamune's settings and counters (its hook prints settings +7/+8 and the u16 at
    +0x122): five whole spans per task, inside what the wrapper keeps, never overlapping."""
    lines = tap.scene_state_lines(US)
    regions = _region_lines(lines)
    count, budget = _wrapper_limits()
    assert len(regions) == len(tap.SCENE_SPANS) + len(tap.SCENE_OPTIONAL_SPANS) <= count
    assert sum(size for _, size in regions) + tap.POOL_TAIL_BYTES <= budget
    ordered = sorted(regions)
    assert all(a + n <= b for (a, n), (b, _) in zip(ordered, ordered[1:], strict=False))
    assert all(begin % 4 == 0 and size % 4 == 0 and begin + size <= RDRAM for begin, size in regions)
    # What the 9 act-select tasks of tap-20261025-184022 read and the tap missed.
    hook_reads = ((US.save_buffer_start + 0x25, 1), (US.usamune_state_start + 7, 2),
                  (US.usamune_state_start + 0x122, 2))
    for at, size in hook_reads:
        at &= 0x1FFFFFFF
        assert any(begin <= at and at + size <= begin + n for begin, n in regions), hex(at)
    assert sum(line.startswith("level_region=") for line in lines) == 1


def test_a_layout_without_the_act_selectors_spans_still_arms_the_renderers():
    bare = dataclasses.replace(US, save_buffer_start=None, save_buffer_end=None,
                               usamune_state_start=None, usamune_state_end=None)
    assert len(_region_lines(tap.scene_state_lines(bare))) == len(tap.SCENE_SPANS)
    assert tap.scene_state_lines(dataclasses.replace(US, scene_bss_end=None)) == []
