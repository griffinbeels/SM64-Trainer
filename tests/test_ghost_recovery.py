"""Real clock → origin/IPC → FOLLOW recovery, without a live emulator."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]


def test_native_savestate_recovery(tmp_path):
    if sys.platform != "win32":
        pytest.skip("Win32 native playback")
    spec = importlib.util.spec_from_file_location("build_plugin", ROOT / "tools/build_plugin.py")
    compiler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compiler)
    vcvars = compiler.find_vcvars32()
    assert vcvars, "x86 MSVC toolchain is required for the recovery witness"
    target = tmp_path / "ghost_recovery_host.exe"
    sources = ["ghost_clock.cpp", "ghost_origin.cpp", "ghost_playback.cpp",
               "ghost_assets.cpp", "ghost_rom.cpp"]
    compiler._cl(vcvars, ["/nologo", "/O2", "/W4", "/MT", "/EHsc", "/std:c++17", "/Gy",
        f"/I{ROOT / 'renderer/model'}", f"/I{ROOT / 'plugin/gfxwrap'}",
        *(str(ROOT / "plugin/gfxwrap" / name) for name in sources),
        str(ROOT / "tests/native/ghost_recovery_host.cpp"),
        f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", "/OPT:REF",
        "/MANIFEST:EMBED", "/MANIFESTUAC:level='asInvoker'", *compiler.LIBS], tmp_path)
    result = subprocess.run([str(target)], cwd=tmp_path, capture_output=True, text=True,
                            timeout=30, check=False, **quiet_spawn_kwargs())
    (tmp_path / "recovery-witness.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] and report["checks"] > 200
    assert report["render_allocations"] == report["render_frees"] == 0
    # The frame a state loads already shows the ghost at its restored place,
    # with the worker a list behind the renderer as it is live.
    blink = report["blink"]
    assert blink["loads"] == 11 and blink["frames"] > 90
    assert blink["ghostless_frames"] == blink["wrong_frames"] == 0, blink
