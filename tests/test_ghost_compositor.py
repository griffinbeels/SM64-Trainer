"""Real hidden WGL compositor witness, independent of libsm64 and the emulator."""
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


def test_real_compositor(tmp_path):
    spec = importlib.util.spec_from_file_location("compositor_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vc = build.find_vcvars32()
    if vc is None:
        pytest.skip("x86 MSVC unavailable")
    executable = tmp_path / "ghost_compositor_host.exe"
    flags = [flag for flag in build.COMMON_FLAGS if not flag.startswith("/std:")]
    build._cl(vc, flags + ["/std:c++17", "/EHsc", f"/I{SOURCE}",
        str(SOURCE / "ghost_compositor.cpp"), str(ROOT / "tests/native/ghost_compositor_host.cpp"),
        f"/Fe:{executable}", f"/Fo{tmp_path}\\", "/link", "opengl32.lib", "gdi32.lib", "user32.lib"], tmp_path)
    result = subprocess.run([str(executable)], capture_output=True, text=True, cwd=tmp_path,
        timeout=40, check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    print(result.stdout.strip())
    assert "compositor passed:" in result.stdout
    assert "coplanar=once clean=unchanged hud=exact state=restored resize=yes contexts=2" in result.stdout
