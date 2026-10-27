"""The scene viewer's ghosts inside the renderer (plugin/gfxwrap/ghost_viewer.cpp), as built.

ghost_viewer.cpp runs with saved playback's own clip validation and figure (ghost_playback.cpp,
ghost_figure.h, ghost_assets.cpp) against a stub model and pose (tests/native/ghost_viewer_host.cpp),
its renderer calls on a thread of their own as GLideN64 makes them: which row each ghost shows
at each frame, what it refuses, and that any order of pictures draws the same.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "plugin/gfxwrap"


def build(tmp_path, viewer: Path = NATIVE / "ghost_viewer.cpp") -> subprocess.CompletedProcess:
    spec = importlib.util.spec_from_file_location("build_plugin", ROOT / "tools/build_plugin.py")
    compiler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compiler)
    vcvars = compiler.find_vcvars32()
    assert vcvars, "x86 MSVC toolchain is required for the scene viewer ghost witness"
    target = tmp_path / "ghost_viewer_host.exe"
    sources = [viewer, *(NATIVE / name for name in ("ghost_playback.cpp", "ghost_origin.cpp",
                                                      "ghost_annotation_assets.cpp", "ghost_assets.cpp"))]
    compiler._cl(vcvars, ["/nologo", "/O2", "/W4", "/MT", "/EHsc", "/std:c++17", "/Gy",
        f"/I{ROOT / 'renderer/model'}", f"/I{NATIVE}", *(str(path) for path in sources),
        str(ROOT / "tests/native/ghost_viewer_host.cpp"), f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", "/OPT:REF",
        "/MANIFEST:EMBED", "/MANIFESTUAC:level='asInvoker'", *compiler.LIBS, "bcrypt.lib"], tmp_path)
    return subprocess.run([str(target)], cwd=tmp_path, capture_output=True, text=True, timeout=30, check=False,
                          **quiet_spawn_kwargs())


def test_each_ghost_shows_the_row_its_frame_names_in_any_order(tmp_path):
    if sys.platform != "win32":
        pytest.skip("Win32 renderer component")
    result = build(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] and report["checks"] > 40


def test_the_witness_sees_a_ghost_that_skips_its_gaps(tmp_path):
    """Restoring the violation: a viewer that draws the nearest row instead of hiding a ghost on a
    frame its recording lacks fails the witness."""
    if sys.platform != "win32":
        pytest.skip("Win32 renderer component")
    source = (NATIVE / "ghost_viewer.cpp").read_text(encoding="utf-8")
    anchor = "if (lo >= slot.indexed || ghost_figure::coordinate(h, slot.frames, lo) != at) return false;"
    assert source.count(anchor) == 1
    mutated = tmp_path / "ghost_viewer.cpp"
    mutated.write_text(source.replace(anchor, "if (lo >= slot.indexed) return false;"), encoding="utf-8")
    result = build(tmp_path, mutated)
    assert result.returncode != 0 and "lacks hides only that ghost" in result.stderr
