"""Execute the staged FBE-off route, actual adapter and hidden GPU presentation."""
import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "plugin/gfxwrap"


def function(text: str, signature: str) -> str:
    """Extract a complete function from the pinned, compiled candidate source."""
    start = text.index(signature)
    opening = text.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def test_actual_screen_buffer_route(tmp_path):
    staged = os.environ.get("SM64_GHOST_WITNESS_SOURCE")
    if not staged:
        pytest.skip("requires current pinned renderer staging (SM64_GHOST_WITNESS_SOURCE)")
    if os.environ.get("SM64_TEST_GHOST_PRESENTATION") != "1":
        pytest.skip("explicit hidden WGL witness: SM64_TEST_GHOST_PRESENTATION=1")
    source = Path(staged) / "src"
    for name in ("ghost_link.cpp", "ghost_draw.cpp", "ghost_presentation.cpp", "ghost_presentation.h"):
        assert (source / "Graphics/OpenGLContext" / name).read_bytes() == (NATIVE / name).read_bytes(), name
    reads = {
        "screen": (source / "FrameBuffer.cpp").read_text(encoding="utf-8"),
        "list": (source / "FrameBuffer.h").read_text(encoding="utf-8"),
        "link": (NATIVE / "ghost_link.cpp").read_text(encoding="utf-8"),
        "presentation": (NATIVE / "ghost_presentation.cpp").read_text(encoding="utf-8"),
        "drawer": (source / "GraphicsDrawer.cpp").read_text(encoding="utf-8"),
    }
    snippets = {
        "screen_route": function(reads["screen"], "void FrameBufferList::_renderScreenSizeBuffer()"),
        "screen_accessor": function(reads["list"], "FrameBuffer * getScreenSizeBuffer()"),
        "raster_target": function(reads["link"], "FrameBuffer* raster_buffer()"),
        "blit_or_copy": function(reads["drawer"], "void GraphicsDrawer::blitOrCopyTexturedRect("),
        "vi_raster": function(reads["presentation"], "void raster_draw("),
        "vi_admission": function(reads["presentation"], "static void draw_vi_image("),
        "screen_vi": function(reads["presentation"], "void draw_screen_vi("),
    }
    for name, text in snippets.items():
        (tmp_path / f"{name}.inc").write_text(text, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("screen_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vcvars = build.find_vcvars32()
    assert vcvars, "requires x86 MSVC"
    executable = tmp_path / "ghost_screen_buffer_host.exe"
    build._cl(vcvars, ["/nologo", "/O2", "/W4", "/MT", "/EHsc", "/std:c++17",
        f"/I{NATIVE}", f"/I{tmp_path}", f"/I{ROOT / 'tests/native'}",
        str(NATIVE / "ghost_presentation.cpp"), str(NATIVE / "ghost_compositor.cpp"),
        str(ROOT / "tests/native/ghost_screen_buffer_host.cpp"), f"/Fe:{executable}", f"/Fo{tmp_path}\\",
        "/link", "opengl32.lib", "gdi32.lib", "user32.lib"], tmp_path)
    result = subprocess.run([str(executable)], cwd=tmp_path, capture_output=True, text=True,
                            timeout=40, check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    print(result.stdout.strip())
    assert "screen route passed: targets=bound blit=yes fallback=yes gamma=yes opacity=0,50,100 clean=exact" in result.stdout
