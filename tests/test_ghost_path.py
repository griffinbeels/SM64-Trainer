"""The ghost's path (plugin/gfxwrap/ghost_path.h), CPU only: which recorded
frames the window holds (live Mario's place, the window, warp breaks, the
averaged path's gate, button presses) and how its mesh is laid out (ground and
air, the casing, the chevrons' spacing), with nothing allocated."""
import gzip
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]
REVISITS = ROOT / "tests/fixtures/ghost_path_revisits.bin.gz"


def test_native_path_selection_and_mesh(tmp_path):
    if sys.platform != "win32":
        pytest.skip("Win32 native compiler")
    spec = importlib.util.spec_from_file_location("path_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vc = build.find_vcvars32()
    assert vc, "x86 MSVC is required for the path witness"
    target = tmp_path / "ghost_path_host.exe"
    build._cl(vc, ["/nologo", "/O2", "/W4", "/WX", "/MT", "/EHsc", "/std:c++17",
        f"/I{ROOT / 'plugin/gfxwrap'}", f"/I{ROOT / 'renderer/model'}",
        str(ROOT / "tests/native/ghost_path_host.cpp"),
        f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", "/MANIFEST:EMBED", "/MANIFESTUAC:level='asInvoker'"], tmp_path)
    revisits = tmp_path / "revisits.bin"
    revisits.write_bytes(gzip.decompress(REVISITS.read_bytes()))
    result = subprocess.run([str(target), str(revisits)], cwd=tmp_path, capture_output=True, text=True, timeout=60,
        check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] and report["checks"] > 90 and report["allocations"] == 0
    # The longest window: 10 s ahead, its 1 s fade and 0.5 s behind at 30 Hz.
    assert report["samples"] >= (10000 + 1000 + 500) * 30 // 1000 + 1
    assert_progress_on_his_recordings(report["revisits"])


def assert_progress_on_his_recordings(pairs):
    """His multi-pass runs (WDW and WF 100 coins, two SSL stars): another attempt
    replayed as live Mario against the fastest attempt's path, measured against
    the two runs' own alignment (dynamic time warping, built apart from the
    progress rule). His ask, 2026-10-03: "the first time we go through an area,
    we show the first path going through that area; then when we come back,
    we're using the second path". The replaced nearest-record rule runs on the
    same frames, so the measures are seen to catch what it did wrong."""
    print(json.dumps(pairs, indent=1))
    assert [pair["name"] for pair in pairs] == ["wdw-100c-path", "wf-100c-path", "ssl-pillarless-path", "ssl-8-0-path"]
    for pair in pairs:
        frames = pair["frames"]
        assert pair["monotonic"], pair  # progress and the drawn window never move back
        assert pair["later"] == 0, pair  # never on a later pass than his
        assert pair["lag"] <= 0.06 * frames, pair  # on the route, rarely more than 5 s behind him
        assert pair["path_end"] - pair["end"] <= 150, pair  # it follows him to the end (the dance tail aside)
        assert pair["max_step"] <= 8.0, pair  # a correction slides: no more than 8 frames of the run a list
        assert pair["two_pass"] <= 0.1 * frames, pair  # one place twice in view: the ghost's own quick loops only
    # The nearest-record rule moved back (the blink) and onto later passes on
    # this data; the window now shows a place twice far less often.
    assert any(not pair["old"]["monotonic"] for pair in pairs)
    assert sum(pair["old"]["later"] for pair in pairs) > 0
    assert 3 * sum(pair["two_pass"] for pair in pairs) <= sum(pair["old"]["two_pass"] for pair in pairs)


def mio0(data: bytes) -> bytes:
    """The cartridge's MIO0 decompression, written here apart from the renderer's."""
    assert data[:4] == b"MIO0"
    size, packed, raw = struct.unpack(">III", data[4:16])
    out, flags, bit = bytearray(), 16, 0
    while len(out) < size:
        if data[flags] & (0x80 >> bit):
            out.append(data[raw])
            raw += 1
        else:
            word = struct.unpack(">H", data[packed:packed + 2])[0]
            packed += 2
            for _ in range((word >> 12) + 3):
                out.append(out[-((word & 0xFFF) + 1)])
        bit += 1
        if bit == 8:
            bit, flags = 0, flags + 1
    return bytes(out)


def rom_chevron(rom: bytes):
    """The 8x8 chevron his choice names: the fire bank's arrow texture, columns
    12-19, rows 1-8, its pixels where blue > 3, their brightness max(r, g, b)."""
    texture = mio0(rom[0x31E1D0:0x31E1D0 + 0x20000])[0xB800:0xB800 + 2048]
    cells = []
    for row in range(1, 9):
        line = []
        for column in range(12, 20):
            c = (texture[(row * 32 + column) * 2] << 8) | texture[(row * 32 + column) * 2 + 1]
            r, g, b = ((c >> 11) & 31) * 255 // 31, ((c >> 6) & 31) * 255 // 31, ((c >> 1) & 31) * 255 // 31
            line.append(max(r, g, b, 89) if ((c >> 1) & 31) > 3 else None)
        cells.append(line)
    return cells


@pytest.mark.skipif(os.environ.get("SM64_TEST_GHOST_COMPOSITOR") != "1",
    reason="explicit desktop OpenGL 4.3 witness: set SM64_TEST_GHOST_COMPOSITOR=1")
def test_path_through_the_real_compositor(tmp_path):
    """Hidden behind a wall like the ghost and whole in front of it; one blend a
    layer; icons world-sized, capped near the camera, hidden behind walls; the
    chevron exactly the cartridge's 8x8 cell, its tip the way the line runs
    (with SM64_GHOST_ROM)."""
    spec = importlib.util.spec_from_file_location("path_gl_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vc = build.find_vcvars32()
    if vc is None:
        pytest.skip("x86 MSVC unavailable")
    target = tmp_path / "ghost_path_gl_host.exe"
    flags = [flag for flag in build.COMMON_FLAGS if not flag.startswith("/std:")]
    native = ROOT / "plugin/gfxwrap"
    build._cl(vc, flags + ["/std:c++17", "/EHsc", f"/I{native}",
        str(native / "ghost_compositor.cpp"), str(native / "ghost_annotation_assets.cpp"),
        str(ROOT / "tests/native/ghost_path_gl_host.cpp"),
        f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", "opengl32.lib", "gdi32.lib", "user32.lib"], tmp_path)
    rom = os.environ.get("SM64_GHOST_ROM")
    result = subprocess.run([str(target), *([rom] if rom else [])], capture_output=True, text=True, cwd=tmp_path,
        timeout=40, check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] and report["final_checks"] > 20
    assert report["icon_pixels"] == [25, 9, 0]  # capped near, world-sized far, hidden behind a wall
    if not rom:
        pytest.skip("the chevron crop needs the cartridge: set SM64_GHOST_ROM")
    expected = rom_chevron(Path(rom).read_bytes())
    drawn = report["chevron"]  # 8 picture rows across the ribbon x 32 columns along it
    background = [20, 40, 60]
    casing = [round(value * 0.4 + 0x1E * 0.6) for value in background]
    for across in range(8):
        for along in range(32):
            cell = expected[along % 8][across]  # ROM row: along the line; column: across it
            pixel = drawn[across][along]
            want = casing if cell is None else [cell] * 3
            assert all(abs(a - b) <= 1 for a, b in zip(pixel, want, strict=True)), (across, along, pixel, want)
    # The V: its arms' ends on the cell's first row, its tip (centre) on the
    # last, which the line draws furthest along, the way the ghost travels.
    assert {i for i, cell in enumerate(expected[0]) if cell} == {0, 7}
    assert {i for i, cell in enumerate(expected[-1]) if cell} == {3, 4}
