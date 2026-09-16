"""Compile the bounded native diagnostics against CPU mocks; never call GL."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("source", ["diagnostics_cpu_test.c"])
def test_native_nonblocking_diagnostics(tmp_path, source):
    spec = importlib.util.spec_from_file_location("native_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vcvars = build.find_vcvars32()
    if vcvars is None:
        pytest.skip("MSVC unavailable for native callback contract check")
    target = tmp_path / "native_cpu.exe"
    build._cl(vcvars, build.COMMON_FLAGS + [
        str(ROOT / "plugin/gfxwrap" / source),
        f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", *build.LIBS], tmp_path)
    result = subprocess.run([str(target)], capture_output=True, text=True, timeout=10,
                            cwd=tmp_path, **quiet_spawn_kwargs(), check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_wrapper_build_leaves_only_the_dll_in_its_output_folder(tmp_path):
    """An install candidate folder holds the file he installs and nothing else.
    The compiler's objects, export library and batch step used to land beside
    it (`_build_wrapper`, `.exp`, `.lib`); they belong in scratch."""
    spec = importlib.util.spec_from_file_location(
        "wrapper_build_layout", Path(__file__).resolve().parents[1] / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    if not build.toolchain_available():
        pytest.skip("no x86 MSVC toolchain")
    out = tmp_path / "candidate"
    built = build.build_wrapper(out)
    assert built == out / "sm64_trainer_gfx.dll" and built.stat().st_size > 0
    assert sorted(p.name for p in out.iterdir()) == ["sm64_trainer_gfx.dll"]
