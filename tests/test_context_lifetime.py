"""Native ownership races and isolated hidden WGL sharing, never live PJ64."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "plugin/gfxwrap"


@pytest.fixture(scope="module")
def native(tmp_path_factory):
    work = tmp_path_factory.mktemp("context_lifetime")
    spec = importlib.util.spec_from_file_location("context_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vcvars = build.find_vcvars32()
    assert vcvars, "requires x86 MSVC"
    flags = [f for f in build.COMMON_FLAGS if not f.startswith("/std:")]
    flags += ["/std:c++17", "/EHsc", f"/I{NATIVE}"]
    link = ["/link", "/MANIFEST:EMBED", "/MANIFESTUAC:level='asInvoker'", *build.LIBS]
    module = work / "context_lifetime.dll"
    build._cl(vcvars, flags + ["/LD", str(NATIVE / "context_lifetime.cpp"),
        f"/Fe:{module}", f"/Fo{work}\\", *link], work)
    target = work / "context_lifetime.exe"
    build._cl(vcvars, flags + ["/Gz", "/DRCL_TEST_HOST", str(NATIVE / "context_lifetime.cpp"),
        str(NATIVE / "context_lifetime_host.cpp"), f"/Fe:{target}", f"/Fo{work}\\", *link], work)
    return target, module


@pytest.mark.parametrize("mode", ["before", "between", "after", "stale", "capacity",
    "delete_failure", "unbind_failure", "simultaneous", "real_before", "real_after", "real_lists", "real_anchor",
    "production_anchor", "acquiring_before", "acquiring_after", "create_failure", "abi"])
def test_lifetime(native, mode, request):
    if mode.startswith(("real_", "production_")):
        request.getfixturevalue("modern_gl")   # these modes open a real WGL context
    target, module = native
    result = subprocess.run([str(target), mode, str(module)], capture_output=True, text=True,
                            timeout=30, cwd=target.parent, **quiet_spawn_kwargs(), check=False)
    (target.parent / f"{mode}.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"context lifetime passed: {mode}" in result.stdout
