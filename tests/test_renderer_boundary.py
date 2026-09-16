"""Actual x86 C++ adapter/ticket tests, independent of live PJ64 or graphics."""
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
    elif mutation == "observe_before_original":
        marker = "    const bool result = original(self);\n"
        assert source.count(marker) == 1
        source = source.replace(marker, "").replace("    return result;", marker + "    return result;", 1)
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
        "/link", "bcrypt.lib", *build.LIBS], tmp_path)
    return target


@pytest.mark.parametrize("mutation", [None, "late_epoch", "final_epoch", "observe_before_original", "slot_aba"])
def test_renderer_boundary_contract(tmp_path, native_build, mutation):
    target = compile_host(tmp_path, native_build, mutation)
    result = subprocess.run([str(target)], capture_output=True, text=True, timeout=20,
                            cwd=tmp_path, **quiet_spawn_kwargs(), check=False)
    if mutation:
        assert result.returncode == 1 and "boundary contract line" in result.stderr
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert "full=1 retired=2 missed=9" in result.stdout


@pytest.fixture(scope="module")
def boundary_host(tmp_path_factory, native_build):
    return compile_host(tmp_path_factory.mktemp("boundary-host"), native_build)


@pytest.mark.parametrize("failure", ["protect-first", "protect-restore"])
def test_protection_failure_preserves_forwarding(boundary_host, failure):
    result = subprocess.run([str(boundary_host), failure], capture_output=True, text=True,
                            timeout=10, **quiet_spawn_kwargs(), check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "protection-refusal-forwarded" in result.stdout


def test_pinned_thunk_survives_close_and_module_release(tmp_path, native_build):
    build, vcvars = native_build
    flags = [flag for flag in build.COMMON_FLAGS if not flag.startswith("/std:")]
    flags += ["/std:c++17", "/EHsc", "/DRB_TEST_HOST", f"/I{ROOT / 'plugin/gfxwrap'}"]
    source = ROOT / "plugin/gfxwrap"
    adapter, original = tmp_path / "adapter.dll", tmp_path / "original.dll"
    host = tmp_path / "lifetime.exe"
    build._cl(vcvars, flags + ["/LD", "/DRB_BUILD_DLL", str(source / "renderer_boundary.cpp"),
        f"/Fe:{adapter}", f"/Fo{tmp_path}\\", "/link", "bcrypt.lib", *build.LIBS], tmp_path)
    build._cl(vcvars, flags + ["/LD", str(source / "renderer_boundary_original.cpp"),
        f"/Fe:{original}", f"/Fo{tmp_path}\\"], tmp_path)
    build._cl(vcvars, flags + [str(source / "renderer_boundary_lifetime_host.cpp"),
        f"/Fe:{host}", f"/Fo{tmp_path}\\"], tmp_path)
    result = subprocess.run([str(host), str(adapter), str(original)], capture_output=True,
                            text=True, timeout=10, **quiet_spawn_kwargs(), check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "pinned-two-modules; delayed-original-returned" in result.stdout
