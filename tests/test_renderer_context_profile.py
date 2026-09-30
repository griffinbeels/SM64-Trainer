"""Opt-in profile A/B candidate: staging needs no GPU, native host uses no GL."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The exact upstream attribute list, including its unmodified version values.
CONTEXT_SITE = b"""#include <DisplayWindow.h>
const int attribList[] =
{
                WGL_CONTEXT_MAJOR_VERSION_ARB, majorVersion,
                WGL_CONTEXT_MINOR_VERSION_ARB, minorVersion,
\t\t\t\tWGL_CONTEXT_PROFILE_MASK_ARB, WGL_CONTEXT_CORE_PROFILE_BIT_ARB,
                0
};
HGLRC coreHrc = wglCreateContextAttribsARB(hDC, 0, attribList);
"""


def test_profile_transform_changes_only_the_selected_profile():
    staged = load_tool("stage_link_witness").context_profile_overlay(CONTEXT_SITE)
    assert b"WGL_CONTEXT_MAJOR_VERSION_ARB, majorVersion" in staged
    assert b"WGL_CONTEXT_MINOR_VERSION_ARB, minorVersion" in staged
    assert staged.count(b"wglCreateContextAttribsARB(hDC, 0, attribList)") == 1
    assert staged.count(b"renderer_context_profile::requested(HEADER,") == 1
    assert staged.count(b"#if defined(OS_WINDOWS) && defined(SM64_REPLAY_GL_WITNESS)") == 2
    assert b"#else\n\t\t\t\tWGL_CONTEXT_CORE_PROFILE_BIT_ARB,\n#endif" in staged


@pytest.mark.parametrize("changed", [
    CONTEXT_SITE.replace(b"WGL_CONTEXT_CORE_PROFILE_BIT_ARB,", b"WGL_CONTEXT_COMPATIBILITY_PROFILE_BIT_ARB,"),
    CONTEXT_SITE + CONTEXT_SITE,
])
def test_profile_transform_refuses_upstream_drift(changed):
    with pytest.raises(ValueError, match="source anchor is not unique"):
        load_tool("stage_link_witness").context_profile_overlay(changed)


def test_context_profile_header_participates_in_renderer_identity():
    build = load_tool("build_renderer")
    assert "renderer_context_profile.h" in build.overlay_sources()


def test_native_context_profile_baseline_and_opt_in(tmp_path):
    """Compiles a CPU-only host; do not run while live-play forbids native builds."""
    build = load_tool("build_plugin")
    vcvars = build.find_vcvars32()
    if not vcvars:
        pytest.skip("MSVC unavailable")
    target = tmp_path / "context_profile.exe"
    flags = [flag for flag in build.COMMON_FLAGS if not flag.startswith("/std:")]
    build._cl(vcvars, flags + ["/std:c++17", "/EHsc",
        str(ROOT / "tests/native/renderer_context_profile_host.cpp"),
        f"/I{ROOT / 'plugin/gfxwrap'}", f"/Fe:{target}", f"/Fo{tmp_path}\\"], tmp_path)
    result = subprocess.run([str(target)], capture_output=True, text=True, timeout=15,
                            **quiet_spawn_kwargs(), check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "8 requests, 3 baseline refusals, JP + stored-word-order passed" in result.stdout
