"""Hidden-GPU witness for the player-first fade (ghost_occlusion.h) on the real driver."""
import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "plugin/gfxwrap"


@pytest.mark.skipif(os.environ.get("SM64_TEST_GHOST_COMPOSITOR") != "1",
                   reason="explicit desktop WGL witness: SM64_TEST_GHOST_COMPOSITOR=1")
def test_ghost_never_blocks_the_player(tmp_path):
    spec = importlib.util.spec_from_file_location("occlusion_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vc = build.find_vcvars32()
    assert vc, "Occlusion witness requires x86 MSVC"
    executable = tmp_path / "ghost_occlusion_host.exe"
    flags = [flag for flag in build.COMMON_FLAGS if not flag.startswith("/std:")]
    build._cl(vc, flags + ["/std:c++17", "/EHsc", f"/I{SOURCE}", f"/I{ROOT / 'tests/native'}",
        str(SOURCE / "ghost_compositor.cpp"), str(ROOT / "tests/native/ghost_occlusion_host.cpp"),
        f"/Fe:{executable}", f"/Fo{tmp_path}\\", "/link", "opengl32.lib", "gdi32.lib", "user32.lib"], tmp_path)
    result = subprocess.run([str(executable)], capture_output=True, text=True, cwd=tmp_path,
        timeout=30, check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    assert ("front=faded_over_mario behind=exact away=exact camera=hidden,half,exact joint_floor=15 "
            "shadow=follows depth=ordinary,n64_image clean=exact") in result.stdout
    print(result.stdout.strip())
