"""Opt-in real pinned LINK loader/cache witness; no PJ64 or installation involved.

Set SM64_LINK_WITNESS_SOURCE to tools/stage_link_witness.py's output. An absent
source checkout is an explicit skip, never evidence that renderer integration works.
"""
import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def source_build():
    value = os.environ.get("SM64_LINK_WITNESS_SOURCE")
    if not value:
        pytest.skip("requires separately staged pinned LINK source (SM64_LINK_WITNESS_SOURCE)")
    source = Path(value).resolve() / "src"
    gl = source / "Graphics/OpenGLContext"
    for name in ["link_dispatch.h", "renderer_gl_state.h", "gl_snapshot.h", "source_format.h"]:
        assert (gl / name).read_bytes() == (ROOT / "plugin/gfxwrap" / name).read_bytes(), name
    spec = importlib.util.spec_from_file_location("link_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vcvars = build.find_vcvars32()
    assert vcvars, "requested LINK source test requires x86 MSVC"
    flags = [flag for flag in build.COMMON_FLAGS if not flag.startswith("/std:")]
    flags += ["/std:c++17", "/EHsc", "/Gy", "/DUNICODE", "/D_UNICODE", "/DOS_WINDOWS", "/DSM64_REPLAY_GL_WITNESS", "/DSM64_REPLAY_CONTEXT_LIFETIME",
              f"/I{source}", f"/I{source / 'inc'}", f"/I{gl}"]
    return source, gl, build, vcvars, flags


@pytest.mark.parametrize("mutation", [None, "miss_cached_bind", "miss_core_read", "miss_error_gateway"])
def test_real_link_dispatch(tmp_path, source_build, mutation):
    source, gl, build, vcvars, flags = source_build
    dispatch = (ROOT / "plugin/gfxwrap/link_dispatch.cpp").read_bytes()
    if mutation == "miss_cached_bind":
        marker = b"if (raw_bind_framebuffer) g_glBindFramebuffer = &BindFramebuffer;"
        assert dispatch.count(marker) == 1
        dispatch = dispatch.replace(marker, b"// deliberately missing captured-pointer observation")
    elif mutation == "miss_core_read":
        marker = b"state.read_buffer(mode);"
        assert dispatch.count(marker) == 1
        dispatch = dispatch.replace(marker, b"// deliberately missing core observation")
    elif mutation == "miss_error_gateway":
        marker = b"if (error != GL_NO_ERROR) invalidate();"
        assert dispatch.count(marker) == 1
        dispatch = dispatch.replace(marker, b"// deliberately missing diagnostic invalidation")
    changed = tmp_path / "link_dispatch.cpp"
    changed.write_bytes(dispatch)
    target = tmp_path / "link_dispatch_host.exe"
    build._cl(vcvars, flags + [str(gl / "GLFunctions.cpp"), str(gl / "opengl_CachedFunctions.cpp"),
        str(gl / "opengl_Parameters.cpp"), str(gl / "context_lifetime.cpp"), str(changed), str(ROOT / "plugin/gfxwrap/link_dispatch_host.cpp"),
        f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", "/OPT:REF", "/MANIFEST:EMBED", "/MANIFESTUAC:level='asInvoker'", *build.LIBS], tmp_path)
    command = [str(target)] + (["renderer"] if mutation == "miss_error_gateway" else [])
    result = subprocess.run(command, capture_output=True, text=True, timeout=15,
                            cwd=tmp_path, **quiet_spawn_kwargs(), check=False)
    (tmp_path / "witness.txt").write_bytes((result.stdout + result.stderr).encode())
    if mutation:
        assert result.returncode == 1 and "link witness line" in result.stderr, result.stdout + result.stderr
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert "LINK source witness passed:" in result.stdout


def test_vanilla_cartridge_leaves_link_dispatch_untouched(tmp_path, source_build):
    """A real run on vanilla SM64 uses the same renderer DLL: the overlay must
    observe nothing, keep GLideN64's raw GL function table and offer no
    capture source (his ruling, 2026-09-16)."""
    source, gl, build, vcvars, flags = source_build
    target = tmp_path / "link_baseline.exe"
    build._cl(vcvars, flags + [str(gl / "GLFunctions.cpp"), str(gl / "opengl_CachedFunctions.cpp"),
        str(gl / "opengl_Parameters.cpp"), str(gl / "context_lifetime.cpp"), str(gl / "link_dispatch.cpp"),
        str(ROOT / "plugin/gfxwrap/link_dispatch_host.cpp"), f"/Fe:{target}", f"/Fo{tmp_path}\\",
        "/link", "/OPT:REF", "/MANIFEST:EMBED", "/MANIFESTUAC:level='asInvoker'", *build.LIBS], tmp_path)
    result = subprocess.run([str(target), "baseline_rom"], capture_output=True, text=True, timeout=15,
                            cwd=tmp_path, **quiet_spawn_kwargs(), check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "LINK baseline witness passed" in result.stdout


def test_modified_link_windows_compiles(tmp_path, source_build):
    source, gl, build, vcvars, flags = source_build
    # Compile the actual modified lifecycle unit. Whole DLL linking still needs Qt.
    build._cl(vcvars, flags + ["/c", "/DTXFILTER_LIB", "/DGL_USE_UNIFORMBLOCK",
        f"/I{source / 'GLideNHQ'}", str(gl / "windows/windows_DisplayWindow.cpp"),
        f"/Fo{tmp_path}\\"], tmp_path)


@pytest.fixture(scope="module")
def error_host(tmp_path_factory, source_build):
    source, gl, build, vcvars, flags = source_build
    work = tmp_path_factory.mktemp("link_errors")
    target = work / "link_errors.exe"
    dispatch = work / "link_dispatch.cpp"
    dispatch.write_bytes((ROOT / "plugin/gfxwrap/link_dispatch.cpp").read_bytes())
    build._cl(vcvars, flags + [str(gl / "GLFunctions.cpp"), str(gl / "opengl_CachedFunctions.cpp"),
        str(gl / "opengl_Parameters.cpp"), str(gl / "context_lifetime.cpp"), str(dispatch),
        str(ROOT / "plugin/gfxwrap/link_dispatch_host.cpp"), f"/Fe:{target}", f"/Fo{work}\\",
        "/link", "/OPT:REF", "/MANIFEST:EMBED", "/MANIFESTUAC:level='asInvoker'", *build.LIBS], work)
    return target


@pytest.mark.parametrize("mode", ["pre", "post", "renderer", "checked", "inactive", "clean"])
def test_source_error_gateway(error_host, mode):
    result = subprocess.run([str(error_host), mode], capture_output=True, text=True, timeout=15,
                            cwd=error_host.parent, **quiet_spawn_kwargs(), check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"LINK source errors passed: {mode}" in result.stdout


@pytest.mark.parametrize("mode, message", [
    ("format_copy", "LINK source format copy passed:"),
    ("format_unsupported", "LINK source format refused:"),
])
def test_source_format(error_host, mode, message):
    result = subprocess.run([str(error_host), mode], capture_output=True, text=True, timeout=15,
                            cwd=error_host.parent, **quiet_spawn_kwargs(), check=False)
    (error_host.parent / f"{mode}.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    assert message in result.stdout
