"""The particle probe alone: Mario's objects only, executed lists only, and the
16 nearest Mario kept (the rest counted) when more were drawn."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "plugin/gfxwrap"


def test_particle_probe(tmp_path):
    if sys.platform != "win32":
        pytest.skip("Win32 native component")
    spec = importlib.util.spec_from_file_location("particle_capture_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vcvars = build.find_vcvars32()
    assert vcvars, "x86 MSVC toolchain is required for the native particle witness"
    target = tmp_path / "ghost_particle_capture_host.exe"
    build._cl(vcvars, ["/nologo", "/O2", "/W4", "/MT", "/EHsc", "/std:c++17", f"/I{NATIVE}",
        str(NATIVE / "ghost_particle_capture.cpp"), str(ROOT / "tests/native/ghost_particle_capture_host.cpp"),
        f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", "/MANIFEST:EMBED", "/MANIFESTUAC:level='asInvoker'"], tmp_path)
    result = subprocess.run([str(target)], cwd=tmp_path, capture_output=True, text=True,
                            timeout=15, check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    assert "particle capture passed: mario=22 kept=16 nearest dropped=5 foreign=no nested=no mist=opacity" in result.stdout


def test_particle_placement(tmp_path):
    """Known frames only; a fading frame (mist) takes its recorded opacity."""
    if sys.platform != "win32":
        pytest.skip("Win32 native component")
    spec = importlib.util.spec_from_file_location("particle_pose_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vcvars = build.find_vcvars32()
    assert vcvars, "x86 MSVC toolchain is required for the native particle witness"
    target = tmp_path / "ghost_particle_pose_host.exe"
    build._cl(vcvars, ["/nologo", "/O2", "/W4", "/MT", "/EHsc", "/std:c++17", f"/I{NATIVE}",
        f"/I{ROOT / 'renderer/model'}", str(NATIVE / "ghost_particle_pose.cpp"),
        str(ROOT / "tests/native/ghost_particle_pose_host.cpp"),
        f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", "/MANIFEST:EMBED", "/MANIFESTUAC:level='asInvoker'"], tmp_path)
    result = subprocess.run([str(target)], cwd=tmp_path, capture_output=True, text=True,
                            timeout=15, check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    assert "particle pose passed: known=3 unknown=1 fade=recorded lit=f3d" in result.stdout
