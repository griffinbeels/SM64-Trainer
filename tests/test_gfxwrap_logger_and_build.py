"""The wrapper's native logger against a blocked writer, and the wrapper build's
output folder; no emulator, window or GL."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def build():
    spec = importlib.util.spec_from_file_location("native_build", ROOT / "tools/build_plugin.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not module.toolchain_available():
        pytest.skip("no x86 MSVC toolchain")
    return module


def test_native_logging_never_blocks_a_graphics_callback(tmp_path, build):
    """plugin/gfxwrap/diagnostics_cpu_test.c blocks the real logger's file
    write and proves callback-side logging returns and overload drops only
    diagnostic records."""
    target = tmp_path / "native_cpu.exe"
    build._cl(build.find_vcvars32(), build.COMMON_FLAGS + [
        str(ROOT / "plugin/gfxwrap/diagnostics_cpu_test.c"),
        f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", *build.LIBS], tmp_path)
    result = subprocess.run([str(target)], capture_output=True, text=True, timeout=10,
                            cwd=tmp_path, **quiet_spawn_kwargs(), check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_wrapper_build_leaves_only_the_dll_in_its_output_folder(tmp_path, build):
    """An install candidate folder holds the file he installs and nothing else.
    The compiler's objects, export library and batch step used to land beside
    it (`_build_wrapper`, `.exp`, `.lib`); they belong in scratch."""
    out = tmp_path / "candidate"
    built = build.build_wrapper(out)
    assert built == out / "sm64_trainer_gfx.dll" and built.stat().st_size > 0
    assert sorted(p.name for p in out.iterdir()) == ["sm64_trainer_gfx.dll"]
