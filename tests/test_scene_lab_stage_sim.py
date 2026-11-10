"""The cold-boot stage recipe's own decisions (tools/scene_lab/stage_sim.py). Making a reset needs his ROM: its
measured runs (same bytes twice, US and JP; his ghosts exact from it) are in the module's docstring; these pin
the pieces that can drift without the ROM."""
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import stage_sim  # noqa: E402


def test_the_act_selector_waits_then_pushes_once_per_act_after_the_first_then_presses_a():
    inputs, pushes = [], 0
    for frame in range(stage_sim.MENU_WAIT + 4 * stage_sim.MENU_PERIOD + 1):
        buttons, stick, pushes = stage_sim.menu_input(frame, pushes, act=3)
        if buttons or stick != (0, 0):
            inputs.append((frame, buttons, stick))
    wait, period = stage_sim.MENU_WAIT, stage_sim.MENU_PERIOD
    assert inputs == [(wait, 0, (80, 0)), (wait + period, 0, (80, 0)), (wait + 2 * period, stage_sim.A_BUTTON, (0, 0)),
                      (wait + 3 * period, stage_sim.A_BUTTON, (0, 0)), (wait + 4 * period, stage_sim.A_BUTTON, (0, 0))]
    assert stage_sim.menu_input(wait, 0, act=1)[0] == stage_sim.A_BUTTON       # act 1: no push at all


def test_the_vi_registers_come_from_the_roms_ntsc_mode_with_sm64s_special_features():
    """Reference: the US ROM's osViModeTable NTSC LAN1 entry and his recording's first picture of play
    (2026-11-08): ctrl 0x311E is shown as status 0x13016 (gamma off, dither filter on, no anti-alias mode)."""
    table, buffer0 = 0x80335010, 0x8038F800
    ram = np.zeros(0x400000, np.uint8)
    entry = struct.pack(">B3x9I5I5I", 2, 0x311E, 0x140, 0x3E52239, 0x20D, 0xC15, 0xC150C15, 0x6C02EC, 0x200, 0,
                        0x280, 0x400, 0x2501FF, 0xE0204, 2, 0x500, 0x400, 0x2501FF, 0xE0204, 2)
    at = (table & 0x1FFFFFFF) + stage_sim.NTSC_LAN1 * stage_sim.VI_MODE_BYTES
    ram[at:at + len(entry)] = np.frombuffer(entry, np.uint8)
    vi = stage_sim.vi_of_mode(ram, {"osViModeTable": table, "gFrameBuffer0": buffer0})
    assert vi["status"] == 0x13016
    assert vi["origin"] == (buffer0 & 0x1FFFFFFF) + 0x280
    assert (vi["width"], vi["h_start"], vi["v_start"], vi["x_scale"], vi["y_scale"], vi["intr"]) == (
        0x140, 0x6C02EC, 0x2501FF, 0x200, 0x400, 2)


def _rom(tmp_path, country, name=b"SM64 USAMUNE v1.93u "):
    header = bytearray(0x1000)
    header[:4] = bytes.fromhex("80371240")
    header[0x20:0x34] = name
    header[0x3E] = ord(country)
    path = tmp_path / f"rom-{country}.z64"
    path.write_bytes(bytes(header))
    return path


def _stages(tmp_path, rom):
    (tmp_path / "map").write_text("", encoding="utf-8")
    return stage_sim.Stages(tmp_path, tmp_path / "cache", rom_path=rom, symbols={}, map_path=tmp_path / "map")


def test_a_reset_is_named_by_its_build_and_rom_so_two_cartridges_never_share_one(tmp_path):
    us, jp = _stages(tmp_path, _rom(tmp_path, "E")), _stages(tmp_path, _rom(tmp_path, "J", b"SM64 USAMUNE v1.93j "))
    assert us.key(24, 1, 2).startswith(f"v{stage_sim.STAGE_VERSION}-us-")
    assert jp.key(24, 1, 2).startswith(f"v{stage_sim.STAGE_VERSION}-jp-")
    assert us.key(24, 1, 2).split("-")[2] != jp.key(24, 1, 2).split("-")[2]     # the ROM's own hash
    assert us.key(24, 1, 1) != us.key(24, 1, 2)


def test_both_usamune_cartridges_simulate_and_another_rom_makes_nothing(tmp_path):
    """JP re-simulation (2026-11-09): each build's frame facts come from its own code (frame_sim.SimBuild), so
    neither Usamune cartridge is refused; any other ROM is, with a sentence, before anything boots."""
    _stages(tmp_path, _rom(tmp_path, "J", b"SM64 USAMUNE v1.93j ")).simulates()
    _stages(tmp_path, _rom(tmp_path, "E")).simulates()
    other = _stages(tmp_path, _rom(tmp_path, "E", b"SOME OTHER GAME     "))
    for refusal in (other.simulates, lambda: other.reset(24, 1, 1)):
        with pytest.raises(stage_sim.StageError, match="not a Usamune"):
            refusal()
