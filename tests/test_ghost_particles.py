"""Hidden-GPU witness: recorded particles drawn with their ghost.

The real compositor draws the ROM-style particle quads after the ghost's body:
cutout and blended frames, hidden behind the body and behind terrain, with the
game's own frame untouched and the renderer's GL state restored.
"""
import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "plugin/gfxwrap"
pytestmark = pytest.mark.skipif(os.environ.get("SM64_TEST_GHOST_COMPOSITOR") != "1",
    reason="explicit desktop OpenGL 4.3 witness: set SM64_TEST_GHOST_COMPOSITOR=1")


def test_particles_with_their_ghost(tmp_path):
    spec = importlib.util.spec_from_file_location("particle_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vc = build.find_vcvars32()
    if vc is None:
        pytest.skip("x86 MSVC unavailable")
    executable = tmp_path / "ghost_particles_host.exe"
    flags = [flag for flag in build.COMMON_FLAGS if not flag.startswith("/std:")]
    build._cl(vc, flags + ["/std:c++17", "/EHsc", f"/I{SOURCE}", f"/I{ROOT / 'tests/native'}",
        str(SOURCE / "ghost_compositor.cpp"), str(ROOT / "tests/native/ghost_particles_host.cpp"),
        f"/Fe:{executable}", f"/Fo{tmp_path}\\", "/link", "opengl32.lib", "gdi32.lib", "user32.lib"], tmp_path)
    result = subprocess.run([str(executable)], capture_output=True, text=True, cwd=tmp_path,
        timeout=40, check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    print(result.stdout.strip())
    assert "particles passed: cutout=texels blend=alpha mix=texel_alpha opaque=alpha_ignored behind_body=hidden behind_terrain=hidden" in result.stdout
    assert "player_first=follows_body" in result.stdout
