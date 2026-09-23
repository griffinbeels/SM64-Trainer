"""Hidden-GPU witness: the game's water blends over a ghost behind it.

Builds the real compositor with a host that plays the renderer: it draws two
translucent water bands the way the pinned GLideN64 does and mirrors each
through the translucent world layer. Set SM64_GHOST_WITNESS_IMAGES to a folder
to keep the frames as PNG for inspection.
"""
import importlib.util
import os
import struct
import subprocess
import zlib
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "plugin/gfxwrap"
pytestmark = pytest.mark.skipif(os.environ.get("SM64_TEST_GHOST_COMPOSITOR") != "1",
    reason="explicit desktop OpenGL 4.3 witness: set SM64_TEST_GHOST_COMPOSITOR=1")


def _png(raw: Path) -> None:
    data = raw.read_bytes()
    width, height = struct.unpack_from("<II", data)
    pixels = data[8:]
    scale = 8  # nearest-neighbour upscale so each pixel is inspectable
    rows = []
    for y in range(height - 1, -1, -1):  # GL rows are bottom-up
        row = bytearray()
        for x in range(width):
            row += pixels[(y * width + x) * 4:(y * width + x) * 4 + 3] * scale
        rows.extend([b"\0" + bytes(row)] * scale)
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    header = struct.pack(">IIBBBBB", width * scale, height * scale, 8, 2, 0, 0, 0)
    raw.with_suffix(".png").write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b""))
    raw.unlink()


def test_water_layer_over_ghost(tmp_path):
    spec = importlib.util.spec_from_file_location("water_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vc = build.find_vcvars32()
    if vc is None:
        pytest.skip("x86 MSVC unavailable")
    executable = tmp_path / "ghost_water_host.exe"
    flags = [flag for flag in build.COMMON_FLAGS if not flag.startswith("/std:")]
    build._cl(vc, flags + ["/std:c++17", "/EHsc", f"/I{SOURCE}",
        str(SOURCE / "ghost_compositor.cpp"), str(ROOT / "tests/native/ghost_water_host.cpp"),
        f"/Fe:{executable}", f"/Fo{tmp_path}\\", "/link", "opengl32.lib", "gdi32.lib", "user32.lib"], tmp_path)
    images = os.environ.get("SM64_GHOST_WITNESS_IMAGES")
    result = subprocess.run([str(executable)], capture_output=True, text=True, cwd=tmp_path,
        timeout=40, check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    print(result.stdout.strip())
    assert "water passed: behind=water_blend front=unchanged outside=exact clean=identical state=restored" in result.stdout
    if images:
        for raw in Path(images).glob("*.rgba"):
            _png(raw)
