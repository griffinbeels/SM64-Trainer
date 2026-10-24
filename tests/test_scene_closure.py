"""The scene capture's closure walker on hand-built Fast3D lists.

Sufficiency on real frames is a separate, external proof (tools/scene_lab):
the real renderer draws a task from RDRAM poisoned outside the closure.
"""
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]


def test_closure_walker_marks_reads_and_refuses_what_it_cannot_follow(tmp_path):
    if sys.platform != "win32":
        pytest.skip("Win32 native component")
    spec = importlib.util.spec_from_file_location("closure_build", ROOT / "tools/build_plugin.py")
    compiler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compiler)
    vcvars = compiler.find_vcvars32()
    assert vcvars, "x86 MSVC toolchain is required for the closure witness"
    target = tmp_path / "scene_closure_host.exe"
    compiler._cl(vcvars, ["/nologo", "/O2", "/W4", "/MT", "/EHsc", "/std:c++17",
                          f"/I{ROOT / 'plugin/gfxwrap'}", str(ROOT / "tests/native/scene_closure_host.cpp"),
                          f"/Fe:{target}", f"/Fo{tmp_path}\\"], tmp_path)
    result = subprocess.run([str(target)], cwd=tmp_path, capture_output=True, text=True,
                            timeout=30, check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    assert "scene closure passed" in result.stdout
