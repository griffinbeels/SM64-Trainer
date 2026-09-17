"""Actual x86 C++ boundary/ticket tests, independent of live PJ64 or graphics."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def native_build():
    spec = importlib.util.spec_from_file_location("boundary_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vcvars = build.find_vcvars32()
    if vcvars is None:
        pytest.skip("x86 MSVC unavailable for native renderer contract")
    return build, vcvars


def compile_host(tmp_path, native_build, mutation=None):
    build, vcvars = native_build
    source = (ROOT / "plugin/gfxwrap/renderer_boundary.cpp").read_text(encoding="utf-8")
    if mutation == "late_epoch":
        assert source.count("capture_epoch() != r.epoch") == 2
        source = source.replace("capture_epoch() != r.epoch", "false")
    elif mutation == "final_epoch":
        marker = "} else if (capture_epoch() != r.epoch) {"
        assert source.count(marker) == 1
        source = source.replace(marker, "} else if (false) {")
    elif mutation == "slot_aba":
        marker = "uint64_t expected = (token & ~TAG_MASK) | OFFERED;"
        assert source.count(marker) == 1
        source = source.replace(marker, "uint64_t expected = offered.state.load();")
    candidate = tmp_path / "renderer_boundary.cpp"
    candidate.write_bytes(source.encode())
    target = tmp_path / "boundary_host.exe"
    flags = [flag for flag in build.COMMON_FLAGS if not flag.startswith("/std:")]
    build._cl(vcvars, flags + ["/std:c++17", "/EHsc", "/DRB_TEST_HOST",
        str(candidate), str(ROOT / "plugin/gfxwrap/renderer_boundary_host.cpp"),
        f"/I{ROOT / 'plugin/gfxwrap'}", f"/Fe:{target}", f"/Fo{tmp_path}\\",
        "/link", *build.LIBS], tmp_path)
    return target


@pytest.mark.parametrize("mutation", [None, "late_epoch", "final_epoch", "slot_aba"])
def test_renderer_boundary_contract(tmp_path, native_build, mutation):
    target = compile_host(tmp_path, native_build, mutation)
    result = subprocess.run([str(target)], capture_output=True, text=True, timeout=20,
                            cwd=tmp_path, **quiet_spawn_kwargs(), check=False)
    if mutation:
        assert result.returncode == 1 and "boundary contract line" in result.stderr
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert "full=1 retired=2 missed=9" in result.stdout


def test_a_refused_bind_preserves_forwarding(tmp_path, native_build):
    host = compile_host(tmp_path, native_build)
    result = subprocess.run([str(host), "refused-bind"], capture_output=True, text=True,
                            timeout=10, **quiet_spawn_kwargs(), check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "refused-bind-forwarded" in result.stdout


def test_pinned_modules_survive_close_and_module_release(tmp_path, native_build):
    """The boundary DLL and a separate surface-provider DLL stay mapped while a
    renderer command is still running inside the provider after close and
    FreeLibrary; the stale capture then retires."""
    build, vcvars = native_build
    flags = [flag for flag in build.COMMON_FLAGS if not flag.startswith("/std:")]
    flags += ["/std:c++17", "/EHsc", f"/I{ROOT / 'plugin/gfxwrap'}"]
    source = ROOT / "plugin/gfxwrap"
    boundary, provider = tmp_path / "boundary.dll", tmp_path / "provider.dll"
    host = tmp_path / "lifetime.exe"
    build._cl(vcvars, flags + ["/LD", "/DRB_BUILD_DLL", str(source / "renderer_boundary.cpp"),
        f"/Fe:{boundary}", f"/Fo{tmp_path}\\", "/link", *build.LIBS], tmp_path)
    build._cl(vcvars, flags + ["/LD", str(source / "renderer_boundary_original.cpp"),
        f"/Fe:{provider}", f"/Fo{tmp_path}\\"], tmp_path)
    build._cl(vcvars, flags + [str(source / "renderer_boundary_lifetime_host.cpp"),
        f"/Fe:{host}", f"/Fo{tmp_path}\\"], tmp_path)
    result = subprocess.run([str(host), str(boundary), str(provider)], capture_output=True,
                            text=True, timeout=10, **quiet_spawn_kwargs(), check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "pinned-two-modules; delayed-original-returned" in result.stdout
