"""No native ships Nintendo's model data; the model fills it from the ROM.

tools/build_model.py turns each decomp data definition into empty storage and
a row naming its ROM address (renderer/model/rom_data.h), and
tools/native_data_scan.py refuses any built native that still holds samples
of that data (tools/build_natives.py runs it on every compile). Synthetic
definitions only: no Nintendo bytes in this repository.
"""
import importlib.util
from pathlib import Path
import struct

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SYNTHETIC = """
// 0x05000100
static const Vtx toy_seg5_vertex_05000100[] = {
    {{{   101,    -57,     33}, 0, {   480,      0}, {0x11, 0x22, 0x33, 0xff}}},
    {{{   -12,    250,   -400}, 0, {     0,    990}, {0x44, 0x55, 0x66, 0xff}}},
};

// 0x05000120
static const Lights1 toy_seg5_lights_05000128 = gdSPDefLights1(
    0x10, 0x20, 0x30,
    0x40, 0x50, 0x60, 0x28, 0x28, 0x28
);

// 0x05000138
const Gfx toy_seg5_dl_05000138[] = {
    gsSPLight(&toy_seg5_lights_05000128.l, 1),
    gsSPVertex(toy_seg5_vertex_05000100, 2, 0),
    gsSP2Triangles( 0,  1,  2, 0x0,  3,  4,  5, 0x0),
    gsSP2Triangles( 6,  7,  8, 0x0,  9, 10, 11, 0x0),
    gsSPEndDisplayList(),
};

// 0x05000200
static const s16 toy_seg5_animvalue_05000200[] = {
    0x0001, 0x0102, 0x0203, 0x0304, 0x0405, 0x0506, 0x0607, 0x0708,
    0x0809, 0x090A, 0x0A0B, 0x0B0C, 0x0C0D, 0x0D0E, 0x0E0F, 0x0F10,
};
"""


def test_every_data_definition_becomes_storage_and_a_row():
    builder = load("build_model")
    text = builder._romify(SYNTHETIC, "toy", 3, {0x05: 0x123450}, {0x05: 0xF0})
    assert "101" not in text and "0x0F10" not in text and "0x50" not in text, "no literal data compiles in"
    assert "static Vtx toy_seg5_vertex_05000100[2];" in text
    assert "static Lights1 toy_seg5_lights_05000128;" in text
    assert "static s16 toy_seg5_animvalue_05000200[16];" in text
    assert "static const intptr_t sg_tpl_toy_seg5_dl_05000138[] = {" in text
    assert "Gfx toy_seg5_dl_05000138[sizeof(sg_tpl_toy_seg5_dl_05000138) / sizeof(intptr_t)];" in text
    # Each row: its owner, its block and its US address (decomp address + shift).
    assert "{SG_ROM_VTX, 3, 0x123450u, 0x050001f0u, 2u, (void *)toy_seg5_vertex_05000100, NULL}" in text
    assert "{SG_ROM_LIGHTS, 3, 0x123450u, 0x05000210u, 1u" in text, "a light row starts at its comment"
    assert "sg_rom_count_toy" in text


def test_a_list_macro_without_a_template_is_refused():
    builder = load("build_model")
    with pytest.raises(ValueError, match="no template"):
        builder._romify(SYNTHETIC.replace("gsSPEndDisplayList()", "gsSPCullDisplayList(0, 1)"), "toy", 1,
                        {0x05: 0x123450})


def test_data_without_an_address_or_block_is_refused():
    builder = load("build_model")
    with pytest.raises(ValueError, match="address comment"):
        builder._romify("static const Vtx toy_vertex[] = {\n};\n", "toy", 1, {0x05: 0x1})
    with pytest.raises(ValueError, match="names no block"):
        builder._romify(SYNTHETIC, "toy", 1, {0x06: 0x1})


def test_the_template_macros_cover_every_list_macro_the_template_header_defines():
    """The generator's list and rom_gfx_template.h redefine the same macros."""
    builder = load("build_model")
    header = (ROOT / "renderer/model/rom_gfx_template.h").read_text(encoding="utf-8")
    import re
    assert set(re.findall(r"^#define (gs\w+)\(", header, re.M)) == builder.TEMPLATE_MACROS
    assert set(re.findall(r"^#undef (gs\w+)$", header, re.M)) == builder.TEMPLATE_MACROS


def test_the_scan_finds_every_compiled_form_of_the_samples():
    scanner = load("native_data_scan")
    searched = scanner.patterns_from([SYNTHETIC])
    labels = {label for label, _ in searched}
    assert {"toy_seg5_vertex_05000100 vertices (float)", "toy_seg5_vertex_05000100 vertices (rom)",
            "toy_seg5_animvalue_05000200 animation values", "toy_seg5_dl_05000138 triangles (rom)"} <= labels
    vertices = [(101, -57, 33, 0, 480, 0, 0x11, 0x22, 0x33, 0xFF), (-12, 250, -400, 0, 0, 990, 0x44, 0x55, 0x66, 0xFF)]
    compiled = b"".join(struct.pack("<3fH2h4B", *map(float, v[:3]), *v[3:]) + b"\0\0" for v in vertices)
    rom = b"".join(struct.pack(">3hH2h4B", *v) for v in vertices)
    values = struct.pack("<16h", 1, *(i * 0x101 + 1 for i in range(1, 16)))
    for embedded, label in ((compiled, "vertices (float)"), (rom, "vertices (rom)"), (values, "animation values")):
        image = b"MZ" + b"\x90" * 1000 + embedded + b"\xcc" * 1000
        assert any(found.endswith(label) for found, _ in scanner.scan_bytes(image, searched)), label
    triangles = b"".join(struct.pack("<5I", 4, *t, 0) for t in ((0, 1, 2), (3, 4, 5), (6, 7, 8), (9, 10, 11)))
    assert scanner.scan_bytes(b"\0" * 64 + triangles, searched), "libsm64's compiled triangles"
    assert not scanner.scan_bytes(b"MZ" + bytes(range(256)) * 64, searched), "clean bytes pass"


def test_require_clean_names_the_file_and_refuses_an_empty_pattern_set(tmp_path):
    scanner = load("native_data_scan")
    searched = scanner.patterns_from([SYNTHETIC])
    rom_vertices = dict((label, data) for label, data in searched)["toy_seg5_vertex_05000100 vertices (rom)"]
    dirty, clean = tmp_path / "dirty.dll", tmp_path / "clean.dll"
    dirty.write_bytes(b"MZ" + rom_vertices)
    clean.write_bytes(b"MZ" + b"\0" * 100)
    scanner.require_clean([clean], searched)
    with pytest.raises(RuntimeError, match="dirty.dll holds Nintendo model data"):
        scanner.require_clean([clean, dirty], searched)
    with pytest.raises(ValueError, match="would pass anything"):
        scanner.require_clean([clean], [])
