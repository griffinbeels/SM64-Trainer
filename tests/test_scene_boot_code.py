"""Locating a practice ROM's boot-copied code (tools/scene_lab/provenance.py) on synthetic data.

The E2 evaluator runs this code only from the viewer's own ROM file: a session's
full RDRAM copy locates the ROM window and proves the bytes were the game's, but
never supplies them. No private RAM image or real ROM is involved.
"""
import importlib.util
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("scene_provenance", ROOT / "tools/scene_lab/provenance.py")
pv = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pv
spec.loader.exec_module(pv)

RDRAM = 8 << 20
ROM_OFFSET = 0x1C0
IMAGE_BYTES = 0x3000        # code and constants, still equal to the ROM
SETTINGS = 0x40             # what the code writes at run time, right after the image


def _rom_and_copy() -> tuple[bytes, bytearray]:
    rng = random.Random(93)
    rom = bytearray(rng.randbytes(1 << 16))
    ram = bytearray(RDRAM)
    base = pv.EXPANSION_RAM
    ram[base:base + IMAGE_BYTES + SETTINGS] = rom[ROM_OFFSET:ROM_OFFSET + IMAGE_BYTES + SETTINGS]
    ram[base + IMAGE_BYTES] ^= 0xFF          # the first setting the game changed since boot
    return bytes(rom), ram


def test_the_image_ends_where_the_game_first_changed_a_byte():
    rom, ram = _rom_and_copy()
    assert pv.boot_copied_code(rom, bytes(ram)) == (0x80000000 | pv.EXPANSION_RAM, IMAGE_BYTES, ROM_OFFSET)


def test_a_copy_without_a_single_rom_window_locates_nothing():
    rom, ram = _rom_and_copy()
    assert pv.boot_copied_code(rom, None) is None
    unanchored = bytearray(ram)
    unanchored[pv.EXPANSION_RAM] ^= 0xFF               # the anchor is in no ROM window
    assert pv.boot_copied_code(rom, bytes(unanchored)) is None
    repeated = rom + rom[ROM_OFFSET:ROM_OFFSET + 64]   # the anchor is in two
    assert pv.boot_copied_code(repeated, bytes(ram)) is None
    empty = bytearray(ram)
    empty[pv.EXPANSION_RAM:pv.EXPANSION_RAM + 64] = bytes(64)   # nothing was copied
    assert pv.boot_copied_code(rom + bytes(64), bytes(empty)) is None


def test_an_unaligned_window_is_refused():
    rom, ram = _rom_and_copy()
    shifted = b"\x00" + rom                             # the same bytes, one byte off a word
    assert pv.boot_copied_code(shifted, bytes(ram)) is None
