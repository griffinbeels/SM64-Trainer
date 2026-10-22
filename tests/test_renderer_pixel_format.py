"""Window-format selection preserves baseline rendering and strict GL admission."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]


def tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


SITE = b'#include "../renderer_context_profile.h"\n\tif ((pixelFormat = ChoosePixelFormat(hDC, &pfd )) == 0) {\n'


def test_pixel_format_overlay_uses_the_selected_result_before_context_creation():
    result = tool("stage_link_witness").pixel_format_overlay(SITE)
    assert b"renderer_pixel_format::choose(HEADER, hDC, &pfd)" in result
    assert b"#else\n\tpixelFormat = ChoosePixelFormat(hDC, &pfd);" in result
    assert b"if (pixelFormat == 0)" in result
    assert "renderer_pixel_format.h" in tool("build_renderer").overlay_sources()


@pytest.mark.parametrize("site", [b"upstream changed", SITE + SITE])
def test_pixel_format_overlay_refuses_upstream_drift(site):
    with pytest.raises(ValueError, match="source anchor is not unique"):
        tool("stage_link_witness").pixel_format_overlay(site)


def test_pixel_format_selection_preserves_supported_storage_and_baseline(tmp_path):
    build = tool("build_plugin")
    vcvars = build.find_vcvars32()
    assert vcvars, "pixel-format witness requires x86 MSVC"
    exe = tmp_path / "pixel_format.exe"
    flags = [flag for flag in build.COMMON_FLAGS if not flag.startswith("/std:")]
    build._cl(vcvars, flags + ["/std:c++17", "/EHsc", f"/I{ROOT / 'plugin/gfxwrap'}",
        str(ROOT / "tests/native/renderer_pixel_format_host.cpp"), f"/Fe:{exe}", f"/Fo{tmp_path}\\",
        "/link", "gdi32.lib"], tmp_path)
    result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=5,
                            check=True, **quiet_spawn_kwargs())
    assert result.stdout.startswith("pixel format passed:")
