"""Independent hidden-GPU hue witness; optional read-only candidate source root."""
import hashlib
import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.environ.get("SM64_TEST_GHOST_COMPOSITOR") != "1",
                   reason="explicit desktop WGL witness: SM64_TEST_GHOST_COMPOSITOR=1")
def test_mario_tint_preserves_scene(tmp_path):
    source = Path(os.environ.get("SM64_GHOST_TINT_SOURCE", str(ROOT / "plugin/gfxwrap"))).resolve()
    before = {name: hashlib.sha256((source / name).read_bytes()).hexdigest()
              for name in ("ghost_compositor.cpp", "ghost_compositor.h", "ghost_compositor_gl.h")}
    spec = importlib.util.spec_from_file_location("tint_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vc = build.find_vcvars32()
    assert vc, "Tint witness requires x86 MSVC"
    executable = tmp_path / "ghost_compositor_tint_host.exe"
    flags = [flag for flag in build.COMMON_FLAGS if not flag.startswith("/std:")]
    build._cl(vc, flags + ["/std:c++17", "/EHsc", f"/I{source}",
        str(source / "ghost_compositor.cpp"), str(ROOT / "tests/native/ghost_compositor_tint_host.cpp"),
        f"/Fe:{executable}", f"/Fo{tmp_path}\\", "/link", "opengl32.lib", "gdi32.lib", "user32.lib"], tmp_path)
    result = subprocess.run([str(executable)], capture_output=True, text=True, cwd=tmp_path,
        timeout=30, check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    assert "hue0=unchanged hue120=mario_only opacity100=outside_exact depth=ordinary,n64_image clean=exact" in result.stdout
    assert before == {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in before}, "candidate changed during witness"
    print(result.stdout.strip(), before)
