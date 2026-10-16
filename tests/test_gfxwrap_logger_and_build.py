"""The wrapper's native logger against a blocked writer, and the wrapper build's
output folder; no emulator, window or GL."""
import importlib.util
import re
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


def test_native_log_keeps_startup_evidence_and_every_slow_call_after_a_capture_request(tmp_path, build):
    """plugin/gfxwrap/diagnostics_window_test.c drives the real logger with
    shrunken sizes. The first capture's preparation is where a freeze would be,
    so every slow callback in the 15 s after a request is logged with its QPCs
    (the five-second throttle once hid three of them), the activation sidecar
    keeps this process's startup lines out of the rotation, and rotation keeps
    three files instead of two."""
    target = tmp_path / "native_window.exe"
    build._cl(build.find_vcvars32(), build.COMMON_FLAGS + [
        str(ROOT / "plugin/gfxwrap/diagnostics_window_test.c"),
        f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", *build.LIBS], tmp_path)
    result = subprocess.run([str(target)], capture_output=True, text=True, timeout=20,
                            cwd=tmp_path, **quiet_spawn_kwargs(), check=False)
    assert result.returncode == 0, result.stdout + result.stderr

    logs = sorted(p.name for p in tmp_path.glob("sm64_trainer_gfx.log*"))
    assert logs == ["sm64_trainer_gfx.log", "sm64_trainer_gfx.log.1", "sm64_trainer_gfx.log.2"]
    lines = "".join((tmp_path / name).read_text(encoding="utf-8") for name in reversed(logs)).splitlines()

    sidecar = (tmp_path / "sm64_trainer_gfx.activation.log").read_text(encoding="utf-8")
    assert (tmp_path / "sm64_trainer_gfx.activation.log.1").read_text() == "previous process startup\n"
    assert "previous process" not in sidecar and "event=rom_open_begin" in sidecar
    assert "event=filler" not in sidecar
    assert 3000 <= len(sidecar.encode()) < 3000 + 1024          # the cap, then drops
    assert 0 < sidecar.count("event=gpu_prepare") < 30

    stalls = [line for line in sidecar.splitlines() if "event=callback_stall" in line]
    assert len(stalls) == 5 and all(" watched=1 " in line for line in stalls)
    spans = [tuple(int(re.search(rf" {key}=(\d+)", line)[1]) for key in ("begin_qpc", "end_qpc"))
             for line in stalls]
    assert all(" total_ms=30.000 " in line for line in stalls)
    assert all(end > begin for begin, end in spans)
    assert [begin for begin, _ in spans] == sorted(begin for begin, _ in spans)

    first, second = [dict(re.findall(r" (\w+)=([\d.]+)", line)) for line in lines
                     if "event=callback_summary callback=ProcessDList" in line]
    assert 3000 < int(first["window_calls"]) < 4000
    assert first["p99_ms"] == "1.100" and first["max_ms"] == "10.000"
    assert 1.0 < float(first["mean_ms"]) < 1.01
    # The next minute starts empty: the 10 ms call belongs to the first only.
    assert 3000 < int(second["window_calls"]) < 4000
    assert second["max_ms"] == "1.000" and second["mean_ms"] == "1.000"


def test_the_wrapper_build_leaves_only_the_dll_in_its_output_folder(tmp_path, build):
    """An install candidate folder holds the file he installs and nothing else.
    The compiler's objects, export library and batch step used to land beside
    it (`_build_wrapper`, `.exp`, `.lib`); they belong in scratch."""
    out = tmp_path / "candidate"
    built = build.build_wrapper(out)
    assert built == out / "sm64_trainer_gfx.dll" and built.stat().st_size > 0
    assert sorted(p.name for p in out.iterdir()) == ["sm64_trainer_gfx.dll"]
