"""The cold boot's own reading of a cartridge: where Usamune's image is, and where a frame resumes. No ROM needed."""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "scene_lab"))
import cold_boot  # noqa: E402


def entry(cart: int, length: int) -> bytes:
    """Usamune's entry as both builds carry it: PI DRAM address, cartridge address (lui/addiu), length - 1 (lui/addiu)."""
    lo, hi = cart & 0xFFFF, (cart >> 16) + (1 if cart & 0x8000 else 0)
    llo, lhi = (length - 1) & 0xFFFF, ((length - 1) >> 16) + (1 if (length - 1) & 0x8000 else 0)
    words = [0x3C02A460, 0x3C030040, 0xAC430000, 0x3C030000 | hi, 0x24630000 | lo, 0xAC430004,
             0x3C030000 | lhi, 0x24630000 | llo]
    data = bytearray(0x1100)
    data[0x1000:0x1020] = struct.pack(">8I", *words)
    return bytes(data)


@pytest.mark.parametrize(("rom_offset", "length"), [(0x7CC6C0, 0x21BDC), (0x761BE0, 0x21CA4)], ids=["us", "jp"])
def test_the_image_and_its_length_come_from_the_entry_code(rom_offset, length):
    assert cold_boot.read_entry_dma(entry(0x10000000 + rom_offset, length)) == (rom_offset, length)


def test_a_cartridge_without_usamunes_entry_is_refused():
    with pytest.raises(SystemExit):
        cold_boot.read_entry_dma(bytes(0x1100))


def test_the_resume_point_is_after_the_calls_delay_slot():
    ram = np.zeros(cold_boot.sim.RDRAM_BYTES, np.uint8)
    function, callee = 0x80248060, 0x80246C10
    jal = 0x0C000000 | ((callee & 0x0FFFFFFF) >> 2)
    struct.pack_into(">I", ram, cold_boot.sim.phys(function) + 0x5C, jal)
    assert cold_boot.jal_site_after(ram, function, callee) == function + 0x64
    with pytest.raises(SystemExit):
        cold_boot.jal_site_after(ram, function, callee + 4)
