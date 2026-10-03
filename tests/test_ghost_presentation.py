"""Real default-drawable clean/display witness plus pinned staging contracts."""
import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "plugin/gfxwrap"


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


WITNESS = pytest.mark.skipif(os.environ.get("SM64_TEST_GHOST_PRESENTATION") != "1",
                             reason="explicit desktop WGL witness: SM64_TEST_GHOST_PRESENTATION=1")
# The display copy goes to its own surface. Restoring the overwrite of the
# clean surface that "Ghosts in recording" made before the ghost copy existed
# must turn the witness red (the negative control below).
DISPLAY_COPY = "p->display_copied=copy_back(p,Instance::DISPLAY);"
OLD_OVERWRITE = "p->display_copied=copy_back(p,Instance::DISPLAY) && copy_back(p,Instance::CLEAN);"


def run_host(tmp_path, presentation=SOURCE / "ghost_presentation.cpp"):
    build = module("presentation_build", "tools/build_plugin.py")
    vc = build.find_vcvars32()
    if vc is None:
        pytest.skip("x86 MSVC unavailable")
    executable = tmp_path / "ghost_presentation_host.exe"
    flags = [flag for flag in build.COMMON_FLAGS if not flag.startswith("/std:")]
    build._cl(vc, flags + ["/std:c++17", "/EHsc", f"/I{SOURCE}",
        str(presentation), str(ROOT / "tests/native/ghost_presentation_host.cpp"),
        f"/Fe:{executable}", f"/Fo{tmp_path}\\", "/link", "opengl32.lib", "gdi32.lib", "user32.lib"], tmp_path)
    return subprocess.run([str(executable)], capture_output=True, text=True, cwd=tmp_path,
        timeout=40, check=False, **quiet_spawn_kwargs())


@WITNESS
def test_real_presentation(tmp_path):
    result = run_host(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    print(result.stdout.strip())
    assert "clean=exact gamma=yes osd=samecolor,partial swap=qualified revoke=clean resize=invalidates state=restored" in result.stdout
    # With the renderer's record the clean save and the sRGB check ask nothing.
    assert "known=asks_nothing" in result.stdout
    # Ghosts in recording: the clean recording stays clean; the ghost copy's
    # display surface takes the displayed picture, only while its lane consumes it.
    assert "recorded=clean ghost_copy=displayed consumer_off=no_copy" in result.stdout


@WITNESS
def test_clean_overwrite_negative_control(tmp_path):
    source = (SOURCE / "ghost_presentation.cpp").read_text(encoding="utf-8")
    assert source.count(DISPLAY_COPY) == 1
    mutated = tmp_path / "ghost_presentation.cpp"
    mutated.write_text(source.replace(DISPLAY_COPY, OLD_OVERWRITE), encoding="utf-8")
    result = run_host(tmp_path, mutated)
    assert result.returncode != 0
    assert "clean stays clean with Ghosts in recording" in result.stderr, result.stdout + result.stderr


def test_staging_refuses_unqualified_source(tmp_path):
    stage = module("presentation_stage", "tools/stage_ghost_presentation.py")
    pinned = module("presentation_pinned", "tools/stage_link_witness.py")
    path = tmp_path / "src/FrameBuffer.cpp"
    path.parent.mkdir()
    path.write_text("void unrelated() {}", encoding="utf-8")
    with pytest.raises(ValueError, match="source anchor is not unique"):
        stage.edits(tmp_path, pinned.replace_once)
