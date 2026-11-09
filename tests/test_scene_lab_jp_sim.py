"""JP re-simulation's pieces that need no ROM (P15). The measured runs (both cold boots, the 102 JP stage resets, the
synthetic resume equal to the cold boot's own thread 5 through a death and a level load, the cross-cartridge
ghost corpus, a JP scene package and a JP save state) are tools/scene_lab/jp_sim_proof.py's; these pin the
readings off a build's code, the per-build spans and the guards that keep one build's facts out of the other."""
from __future__ import annotations

import re
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import frame_sim as sim  # noqa: E402
import moment_slot  # noqa: E402
import sim_scene  # noqa: E402
import start_check  # noqa: E402

from sm64_events.memory.layout import layout_for  # noqa: E402
from sm64_events.slotlibrary.usamune import BODY_BYTES  # noqa: E402

AT, T6, T7, A0, SP, RA = 1, 14, 15, 4, 29, 31


def lui(rt, imm):
    return 0x0F << 26 | rt << 16 | imm


def store(op, rt, offset, base):
    return op << 26 | base << 21 | rt << 16 | (offset & 0xFFFF)


def jal(target):
    return 0x03 << 26 | ((target & 0x0FFFFFFF) >> 2)


def branch_back(at, target):
    return 0x1000 << 16 | ((target - (at + 4)) // 4) & 0xFFFF


# A made-up build: every function at its own address, the code each reader looks for written there.
VSYNC, SEND, LOOP, SCRIPT, STACK, DEBUG, RANDOM = (0x80248060, 0x80246C10, 0x80248AC0, 0x803805C8, 0x80205200,
                                                   0x802CA6E0, 0x80383BB0)
MAP = f"""
 0x00000000{VSYNC:08x}  display_and_vsync
 0x00000000{SEND:08x}  send_display_list
 0x00000000{LOOP:08x}  thread5_game_loop
 0x00000000{SCRIPT:08x}  level_script_execute
 0x00000000{STACK:08x}  gThread5Stack
 0x00000000{STACK + 0x2000:08x}  gGfxSPTaskYieldBuffer
 0x00000000{DEBUG:08x}  try_print_debug_mario_level_info
 0x00000000{RANDOM:08x}  random_u16
"""


def game(tmp_path, *, closing_branch=True, script_load=True):
    ram = np.zeros(sim.RDRAM_BYTES, np.uint8)

    def put(address, *words):
        struct.pack_into(f">{len(words)}I", ram, sim.phys(address), *words)
    put(VSYNC, 0x27BDFFE8, 0xAFBF0014)                                     # addiu sp, -0x18; sw ra, 0x14(sp)
    put(VSYNC + 0x5C, jal(SEND), 0)
    put(LOOP, 0x27BDFFD8)                                                  # addiu sp, -0x28
    put(LOOP + 0xD8, jal(SCRIPT), 0x8FA40024 if script_load else 0)       # lw a0, 0x24(sp) in the delay slot
    put(LOOP + 0xF0, jal(VSYNC), 0)
    if closing_branch:
        put(LOOP + 0x160, branch_back(LOOP + 0x160, LOOP + 0x80))
    put(DEBUG + 0x88, jal(0x80410C10))                                     # into Usamune's image
    put(SCRIPT, lui(AT, 0x8039), store(0x29, T6, -0x41E0, AT), store(0x23, T7, 0x18, SP),
        lui(AT, 0x8039), store(0x2B, T7, -0x41D8, AT))                     # sh sScriptStatus; sw sCurrentCmd
    put(RANDOM, lui(T6, 0x8039), store(0x25, T6, -0x1120, T6))            # lhu gRandomSeed16
    path = tmp_path / "map"
    path.write_text(MAP, encoding="utf-8")
    return ram, sim.Symbols(path)


def test_a_builds_stop_and_frames_are_read_off_its_own_code(tmp_path):
    ram, symbols = game(tmp_path)
    build = sim.read_build(ram, symbols, "jp")
    top = STACK + 0x2000
    assert build.resume == VSYNC + 0x64 and build.loop_return == LOOP + 0xF8 and build.loop_head == LOOP + 0x80
    assert build.thread5_sp == top - sim.STACK_PAD - 0x28 and build.vsync_sp == build.thread5_sp - 0x18
    assert (build.script_slot, build.return_slot, build.site) == (0x24, 0x14, DEBUG + 0x88)
    jp = layout_for("jp")
    assert build.usamune_vi_count == jp.usamune_state_start + sim.USAMUNE_VI_OFFSET
    assert build.scene_bss == jp.scene_bss_start


@pytest.mark.parametrize("broken", [{"closing_branch": False}, {"script_load": False}])
def test_a_game_loop_this_simulator_does_not_know_is_refused(tmp_path, broken):
    ram, symbols = game(tmp_path, **broken)
    with pytest.raises(SystemExit):
        sim.read_build(ram, symbols, "jp")


def test_the_names_a_map_lacks_are_read_off_the_code_and_a_mapped_name_stays_the_maps(tmp_path):
    ram, symbols = game(tmp_path)
    sim.complete_symbols(symbols, ram)
    assert symbols["sCurrentCmd"] == 0x8038BE28 and symbols["gRandomSeed16"] == 0x8038EEE0
    ram, symbols = game(tmp_path)
    symbols.by_name["gRandomSeed16"] = 0x80123450
    sim.complete_symbols(symbols, ram)
    assert symbols["gRandomSeed16"] == 0x80123450
    empty, bare = np.zeros(sim.RDRAM_BYTES, np.uint8), game(tmp_path)[1]
    sim.complete_symbols(bare, empty)                                       # the engine not loaded yet: nothing added
    assert "sCurrentCmd" not in bare.by_name


FRAME_FACTS = ("RESUME", "VSYNC_SP", "THREAD5_SP", "THREAD5_STACK_TOP", "LOOP_HEAD", "LOOP_RETURN", "USAMUNE_VI_COUNT",
               "SCENE_BSS")


def test_no_build_fact_is_a_module_value_and_no_module_rewrites_another_ones():
    """The guard against the bug cold_boot had: it set frame_sim.RESUME and STUB_NAMES for the whole process, so a
    second build in one process (the atlas holds both) stopped at the first one's address."""
    assert not [name for name in FRAME_FACTS if hasattr(sim, name)]
    assignment = re.compile(r"\bsim\.(" + "|".join(FRAME_FACTS + ("STUB_NAMES",)) + r")\s*=(?!=)")
    offenders = [f"{path.name}:{number}" for path in sorted((ROOT / "tools" / "scene_lab").glob("*.py"))
                 for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
                 if assignment.search(line)]
    assert offenders == []


@pytest.mark.parametrize("version", ["us", "jp"])
def test_each_builds_scene_and_slot_spans_hold_its_own_globals(version):
    """The same rules the address gates check on a running game (sync/address_gates.py), over the shipped rows."""
    layout = layout_for(version)

    def span(row):
        return layout.value(f"{row}_start"), layout.value(f"{row}_end")
    data, bss, engine = span("scene_data"), span("scene_bss"), span("scene_engine_bss")
    for low, high in (data, bss, engine):
        assert low % 4 == 0 and high % 4 == 0 and low < high <= low + 512 * 1024
    assert all(data[0] <= layout.value(name) < data[1] for name in
               ("global_timer", "curr_level", "current_area_pointer", "area_update_counter", "last_completed_course"))
    assert all(bss[0] <= layout.value(name) < bss[1] for name in
               ("mario_struct", "curr_area", "warp_dest", "pending_warp_op", "hud_display", "player1_controller",
                "object_pool", "mario_object"))
    tail, hud, seed, save = span("slot_tail"), span("power_meter_hud"), span("random_seed"), span("save_buffer")
    assert tail == (bss[1], layout.mario_struct + BODY_BYTES)       # the body Usamune copies, past the .bss span
    assert (hud[1] - hud[0], seed[1] - seed[0], save[1] - save[0]) == (16, 4, 0x200)
    assert seed[0] == engine[1]
    assert span("main_pool") == (0x8005C010, 0x801C0FF0)


def test_a_jp_start_keeps_the_jp_builds_regions():
    """A ghost's start (ghost/start_frame.py) carries exactly the scene tap's regions of its game's build."""
    from sm64_events.ghost import start_frame
    jp = layout_for("jp")
    spans = start_frame.allowed_spans("jp")
    assert len(spans) == len(start_frame.SPAN_ROWS)
    assert (jp.scene_bss_start & 0x1FFFFFFF, jp.scene_bss_end - jp.scene_bss_start) in spans
    assert start_frame.main_pool("jp") == (0x5C010, 0x1C0FF0)


def test_a_scene_is_armed_for_its_roms_own_build_and_a_caller_cannot_say_otherwise(tmp_path):
    rom = tmp_path / "rom.z64"
    header = bytearray(0x1000)
    header[:4] = bytes.fromhex("80371240")
    header[0x20:0x33] = b"SM64 USAMUNE v1.93j"
    header[0x3E] = ord("J")
    rom.write_bytes(bytes(header))
    symbols = {"gFrameBuffer0": 0x8038F800, "gFrameBuffer1": 0x803B5000, "gFrameBuffer2": 0x803DA800}
    with pytest.raises(sim_scene.SimSceneError, match="jp, not us"):
        sim_scene.SimScene(tmp_path / "out.sm64scene", tmp_path / "work", rom, symbols, {"origin": 0x38FA80}, "us")


def test_a_starts_protected_places_are_the_builds_own():
    class Map:
        def __init__(self, **addresses):
            self.by_name = addresses

        def __getitem__(self, name):
            return self.by_name[name]
    jp = start_check.never_ranges(Map(gUnusedThread2Stack=0x801FF600, gGfxPools=0x80208100, gFrameBuffer0=0x8038F800))
    assert jp[0] == (0x1FF600, 0x208100) and jp[1] == (0x38F800, start_check.USAMUNE_IMAGE_BEGIN)
    assert start_check.never_ranges(Map()) == start_check.NEVER                # a map without those names: US's


def _pj64_extra(address: int, value: int, size: int) -> tuple[int, bytes]:
    """A tap identity extra as Project64 stores it: little-endian words."""
    base, data = address & ~3, bytearray(4)
    for at, byte in enumerate(value.to_bytes(size, "big")):
        data[((address + at) ^ 3) - base] = byte
    return base, bytes(data)


def test_a_packages_clocks_and_place_are_read_where_its_own_arming_put_them():
    jp = layout_for("jp")
    identity = [("global_timer", jp.global_timer & 0x1FFFFFFF, 4), ("curr_level", jp.curr_level & 0x1FFFFFFF, 2),
                ("curr_area", jp.curr_area & 0x1FFFFFFF, 2)]
    extras = [_pj64_extra(address, value, size) for (_name, address, size), value in zip(identity, (777, 24, 1), strict=True)]

    class Package:
        states = object()

        def __init__(self):
            self.identity = identity

        def __len__(self):
            return 1

        def scene_regions(self, index):
            return {"task_index": 5}, extras, [], np.zeros(sim.RDRAM_BYTES, np.uint8), None
    moment = moment_slot.PackageMoments(Package()).moment(0)
    assert (moment.timer, moment.level, moment.area) == (777, 24, 1)
