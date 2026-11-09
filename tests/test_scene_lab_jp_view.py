"""The JP free camera's pieces that need no ROM (P15, old node P6). STROOP's JP map lacks three names the viewer runs
(geo_process_object, obj_is_in_view, sFloorAlignMatrix); e2_georender reads them off the build's own code. A scene
package opens with the ROM it was written from and that region's map (scene_session.scene_inputs). Measured on the
real carts (2026-11-09): the reader gives each US map row exactly, and on JP 0x8027D14C, 0x8027CF68, 0x80339F50."""
from __future__ import annotations

import hashlib
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import e2_georender as e2  # noqa: E402
import package as scene_package  # noqa: E402
import scene_session  # noqa: E402

JR_RA, NOP = 0x03E00008, 0
CODE = 0x80246000                      # where the made-up main image sits (ROM offset 0x1000)
FLOOR, DISPATCH, SHADOW, IN_VIEW, OBJECT, HELD = (0x80246000, 0x80246100, 0x80246200, 0x80246300, 0x80246400,
                                                  0x80246500)
MATRIX = 0x80339F50


def jal(target):
    return 0x03 << 26 | ((target & 0x0FFFFFFF) >> 2)


def frame(size=0x18):
    return 0x27BD0000 | (0x10000 - size)


def function(words):
    return [frame(), *words, JR_RA, NOP]


def build(*, second_match=False, floor=True):
    """Code a reader walks: align_with_floor's matrix pointer, a dispatcher calling three functions, of which only
    OBJECT calls the function ending where it begins (IN_VIEW)."""
    words = {}

    def put(at, body):
        for index, value in enumerate(body):
            words[at + 4 * index] = value
    if floor:                                 # lui t6, hi; addiu t6, t6, lo (lo sign-extends)
        put(FLOOR, function([0x3C0E0000 | (MATRIX + 0x8000) >> 16, 0x25CE0000 | MATRIX & 0xFFFF]))
    put(DISPATCH, function([jal(SHADOW), NOP, jal(OBJECT), NOP, jal(HELD), NOP]))
    put(SHADOW, function([jal(FLOOR), NOP]))
    put(IN_VIEW, function([NOP] * 61))        # 64 words: it ends exactly where OBJECT begins
    put(OBJECT, function([jal(IN_VIEW), NOP]))
    put(HELD, function([jal(HELD - 0x40 if second_match else FLOOR), NOP]))
    if second_match:                          # HELD also calls a function ending where it begins: ambiguous
        put(HELD - 0x40, function([NOP] * 13))
    rom = bytearray(0x1000 + 0x800)
    for at, value in words.items():
        struct.pack_into(">I", rom, 0x1000 + at - CODE, value)
    return bytes(rom), [(CODE, 0x800, 0x1000)]


NAMES = {"align_with_floor": FLOOR, "geo_process_node_and_siblings": DISPATCH}


def test_the_three_view_names_are_read_off_the_code():
    rom, images = build()
    assert e2.read_view_symbols(dict(NAMES), images, rom) == {
        "sFloorAlignMatrix": MATRIX, "geo_process_object": OBJECT, "obj_is_in_view": IN_VIEW}


def test_two_candidates_name_neither_function():
    rom, images = build(second_match=True)
    found = e2.read_view_symbols(dict(NAMES), images, rom)
    assert "geo_process_object" not in found and "obj_is_in_view" not in found


def test_a_map_without_the_names_is_refused_without_the_rom(tmp_path):
    rows = "\n".join(f" 0x00000000{0x80300000 + 4 * i:08x}  {name}" for i, name in enumerate(e2.SYMBOLS)
                     if name not in ("geo_process_object", "obj_is_in_view", "sFloorAlignMatrix"))
    sections = "\n".join(f".{name} 0x{0x80246000:016x} 0x800 load address 0x{0x1000:016x}"
                         for name in ("main", "main.noload", "engine", "engine.noload"))
    path = tmp_path / "MappingJP.map"
    path.write_text(rows + "\n" + sections + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="geo_process_object"):
        e2.load_map(path, "jp")


def _package_head(rom: bytes) -> bytes:
    return scene_package.HEADER.pack(scene_package.MAGIC, 1, scene_package.HEADER.size, 0,
                                     hashlib.sha256(rom).digest(), 8 << 20, 0, 0, 0, 0, 0, bytes(32), bytes(16))


def test_a_scene_opens_with_the_rom_it_was_written_from_and_that_regions_map(tmp_path):
    us, jp = tmp_path / "us.z64", tmp_path / "jp.z64"
    us.write_bytes(b"US" * 64)
    jp.write_bytes(b"JP" * 64)
    maps = {"us": tmp_path / "MappingUS.map", "jp": tmp_path / "MappingJP.map"}
    config = SimpleNamespace(rom=us, map=maps["us"], roms={"us": us, "jp": jp}, maps=maps)
    scene = tmp_path / "jp.sm64scene"
    scene.write_bytes(_package_head(jp.read_bytes()))
    assert scene_session.scene_inputs(config, scene) == (jp, maps["jp"])
    scene.write_bytes(_package_head(us.read_bytes()))
    assert scene_session.scene_inputs(config, scene) == (us, maps["us"])


def test_the_installed_apps_config_carries_every_regions_rom_and_map(tmp_path):
    from sm64_events.server.scene_lab_api import LabConfig
    jp, jp_map = tmp_path / "jp.z64", tmp_path / "MappingJP.map"
    config = LabConfig.from_app({"rom": str(jp), "map": str(jp_map), "roms": {"us": None, "jp": str(jp)},
                                 "maps": {"us": str(tmp_path / "MappingUS.map"), "jp": str(jp_map)}})
    assert config.roms == {"us": None, "jp": jp} and config.maps["jp"] == jp_map


def test_a_lab_with_one_rom_keeps_its_pair(tmp_path):
    us = tmp_path / "us.z64"
    us.write_bytes(b"US" * 64)
    config = SimpleNamespace(rom=us, map=tmp_path / "MappingUS.map")      # a developer's lab.json: no "roms"
    scene = tmp_path / "other.sm64scene"
    scene.write_bytes(_package_head(b"another cartridge"))
    assert scene_session.scene_inputs(config, scene) == (us, tmp_path / "MappingUS.map")
    scene.write_bytes(b"not a package")
    assert scene_session.scene_inputs(config, scene) == (us, tmp_path / "MappingUS.map")
