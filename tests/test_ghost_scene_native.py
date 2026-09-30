"""DDD connector admission and the actual pose/shadow/particle conversion.

The model shell exposes the pose passed to it; the real conversion and camera
inversion run unchanged. The playback witness separately checks row selection.
"""
import importlib.util
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]


def test_ddd_portal_coordinates(tmp_path):
    if sys.platform != "win32":
        pytest.skip("Win32 native component")
    spec = importlib.util.spec_from_file_location("scene_build", ROOT / "tools/build_plugin.py")
    compiler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compiler)
    vcvars = compiler.find_vcvars32()
    assert vcvars, "x86 MSVC toolchain is required for the DDD witness"
    target = tmp_path / "ghost_scene_host.exe"
    compiler._cl(vcvars, ["/nologo", "/O2", "/W4", "/MT", "/EHsc", "/std:c++17", "/Gy",
        f"/I{ROOT / 'renderer/model'}", f"/I{ROOT / 'plugin/gfxwrap'}",
        str(ROOT / "plugin/gfxwrap/ghost_pose.cpp"), str(ROOT / "plugin/gfxwrap/ghost_particle_pose.cpp"),
        str(ROOT / "tests/native/ghost_scene_host.cpp"),
        f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", "/OPT:REF",
        "/MANIFEST:EMBED", "/MANIFESTUAC:level='asInvoker'", *compiler.LIBS], tmp_path)
    result = subprocess.run([str(target)], cwd=tmp_path, capture_output=True, text=True,
                            timeout=15, check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DDD portal passed: roots=TRS+modelview held=world shadow=once particles=once connector=bounded" in result.stdout


def test_scene_header_changes_renderer_identity(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("scene_renderer_build", ROOT / "tools/build_renderer.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    assert "ghost_scene.h" in build.overlay_sources()
    native = tmp_path / "plugin/gfxwrap"
    native.mkdir(parents=True)
    header = native / "ghost_scene.h"
    header.write_bytes((ROOT / "plugin/gfxwrap/ghost_scene.h").read_bytes())
    recipe = tmp_path / "tools"
    recipe.mkdir()
    for name in ("stage_link_witness.py", "stage_ghost_renderer.py", "stage_ghost_presentation.py"):
        (recipe / name).write_bytes((ROOT / "tools" / name).read_bytes())
    monkeypatch.setattr(build, "REPO", tmp_path)
    monkeypatch.setattr(build, "overlay_sources", lambda: ("ghost_scene.h",))
    monkeypatch.setattr(build, "_load", lambda *args: SimpleNamespace(component_hash=lambda: "model"))
    original = build.renderer_build_id()
    header.write_bytes(header.read_bytes() + b"\nchanged connector\n")
    assert build.renderer_build_id() != original
