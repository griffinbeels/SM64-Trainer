"""The ghost's sound reads the player-first fade on the CPU: compile the real
header against the witnesses' statement of the rule."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]


def test_sound_fade_reads_the_picture_rule(tmp_path):
    if sys.platform != "win32":
        pytest.skip("Win32 renderer component")
    spec = importlib.util.spec_from_file_location("build_plugin", ROOT / "tools/build_plugin.py")
    compiler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compiler)
    vcvars = compiler.find_vcvars32()
    assert vcvars
    target = tmp_path / "ghost_sound_fade_host.exe"
    compiler._cl(vcvars, ["/nologo", "/O2", "/W4", "/MT", "/EHsc", "/std:c++17",
        f"/I{ROOT / 'plugin/gfxwrap'}", f"/I{ROOT / 'renderer/model'}",
        str(ROOT / "tests/native/ghost_sound_fade_host.cpp"),
        f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", "/MANIFEST:EMBED",
        "/MANIFESTUAC:level='asInvoker'"], tmp_path)
    result = subprocess.run([str(target)], cwd=tmp_path, capture_output=True, text=True, encoding="utf-8",
                            timeout=30, check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] and report["checks"] > 10000 and report["worst"] < 1e-5
