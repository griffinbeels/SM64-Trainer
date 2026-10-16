"""Real default-drawable clean/display witness plus pinned staging contracts."""
import ast
import importlib.util
import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

from renderer_fixture import renderer_archive as renderer_archive
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


@pytest.mark.parametrize("restore_duplicate_copy", [False, True])
def test_cpu_presentation_custody_and_removed_work(tmp_path, restore_duplicate_copy):
    """Exercise shipped presentation logic without a WGL context or GPU calls."""
    build = module("presentation_cpu_build", "tools/build_plugin.py")
    vc = build.find_vcvars32()
    assert vc, "CPU presentation witness requires x86 MSVC"
    executable = tmp_path / "ghost_presentation_cpu_host.exe"
    presentation = SOURCE / "ghost_presentation.cpp"
    if restore_duplicate_copy:
        source = presentation.read_text(encoding="utf-8")
        anchor = "p->published=p->display_published={};\n    p->osd_window=true;"
        assert source.count(anchor) == 1
        presentation = tmp_path / "ghost_presentation.cpp"
        presentation.write_text(source.replace(anchor,
            "if(!p->saved && !save_back(p))return false;\n    p->osd_window=true;"), encoding="utf-8")
    flags = [flag for flag in build.COMMON_FLAGS if not flag.startswith("/std:")]
    build._cl(vc, flags + ["/std:c++17", "/EHsc", f"/I{SOURCE}",
        str(presentation), str(ROOT / "tests/native/ghost_presentation_cpu_host.cpp"),
        f"/Fe:{executable}", f"/Fo{tmp_path}\\", "/link", "/MANIFEST:EMBED", "/MANIFESTUAC:level='asInvoker'"], tmp_path)
    result = subprocess.run([str(executable)], capture_output=True, text=True, cwd=tmp_path,
        timeout=10, check=False, **quiet_spawn_kwargs())
    if restore_duplicate_copy:
        assert result.returncode != 0
        assert "plain OSD is not mirrored" in result.stderr
        return
    assert result.returncode == 0, result.stdout + result.stderr
    assert "plain_gl_calls=0 plain_copies=0 plain_osd_mirrors=0 ghost_copies=2 identity=qualified" in result.stdout
    assert "failed_display=clean_preserved" in result.stdout


def _function(source, signature):
    """Extract one actual function body; refuse an ambiguous source anchor."""
    assert source.count(signature) == 1, signature
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 1
    cursor = opening + 1
    while depth:
        depth += (source[cursor] == "{") - (source[cursor] == "}")
        cursor += 1
    return source[start:cursor]


def _actual_windows_staging(stage, function, start_marker, end_marker, source_tree):
    """Execute one unique production staging block against cached source."""
    owner = next(node for node in ast.parse(Path(stage.__file__).read_text()).body
                 if isinstance(node, ast.FunctionDef) and node.name == function)
    starts = [i for i, node in enumerate(owner.body) if ast.unparse(node) == start_marker]
    assert len(starts) == 1
    start = starts[0]
    end = next(i for i in range(start, len(owner.body)) if ast.unparse(owner.body[i]) == end_marker)
    output = {}
    namespace = {**vars(stage), "source": source_tree, "edited": output, "result": output}
    exec(compile(ast.Module(body=owner.body[start:end + 1], type_ignores=[]),
                 "actual_windows_staging", "exec"), namespace)
    return output["src/Graphics/OpenGLContext/windows/windows_DisplayWindow.cpp"].decode("utf-8")


def _pinned_transition_tree(tmp_path, archive):
    """Apply the actual capture staging block to five verified upstream files."""
    pinned = json.loads((ROOT / "renderer/build-inputs.json").read_text())["upstream"]
    stage = module("transition_stage", "tools/stage_ghost_presentation.py")
    capture = module("transition_capture_stage", "tools/stage_link_witness.py")
    source_tree = tmp_path / "pinned"
    names = ("src/FrameBuffer.cpp", "src/FrameBuffer.h", "src/DisplayWindow.cpp", "src/TextDrawer.cpp",
             "src/Graphics/OpenGLContext/windows/windows_DisplayWindow.cpp")
    with zipfile.ZipFile(archive) as upstream:
        for name in names:
            path = source_tree / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(upstream.read(f"GLideN64-{pinned['commit']}/{name}"))
    # Execute the production capture staging statements for the pinned Windows
    # file, without staging/building the whole renderer or launching anything.
    windows = _actual_windows_staging(capture, "overlay", "key = GL + 'windows/windows_DisplayWindow.cpp'",
                                      "edited[key] = data", source_tree)
    resize = _function(windows, "bool DisplayWindowWindows::_resizeWindow()")
    assert resize.index("replay_gl::DrawableChanged();") < resize.index("SetWindowPos")
    assert resize.count("replay_gl::DrawableCommitted(hWnd,") == 2
    for signature in ("bool DisplayWindowWindows::_resizeWindow()", "void DisplayWindowWindows::_changeWindow()"):
        native_entry = _function(windows, signature)
        assert not any(call in native_entry for call in ("wglDeleteContext", "wglCreateContext", "wglMakeCurrent"))
    (source_tree / names[-1]).write_bytes(windows.encode("utf-8"))
    ghost = module("transition_ghost_stage", "tools/stage_ghost_renderer.py")
    # This block borrows the same checked replacement function as production.
    ghost.replace_once = capture.replace_once
    windows = _actual_windows_staging(ghost, "edits", "path = 'src/Graphics/OpenGLContext/windows/windows_DisplayWindow.cpp'",
                                      "result[path] = data", source_tree)
    for signature in ("bool DisplayWindowWindows::_start()", "void DisplayWindowWindows::_stop()"):
        loss = _function(windows, signature)
        assert loss.index("ghost_link::context_lost();") < loss.index("replay_gl::ContextLost();")
    (source_tree / names[-1]).write_bytes(windows.encode("utf-8"))
    return source_tree, stage, capture


@pytest.mark.parametrize("restore_regression", [None, "rebuild", "staging_stop"])
def test_cpu_actual_presentation_and_pinned_window_transitions(tmp_path, restore_regression, renderer_archive):
    """Actual lifecycle glue and staged resize/fullscreen entries, with CPU shells."""
    source_tree, stage, capture = _pinned_transition_tree(tmp_path, renderer_archive)
    display = stage.edits(source_tree, capture.replace_once)["src/DisplayWindow.cpp"].decode("utf-8")
    if restore_regression == "staging_stop":
        assert display.count("ghost_presentation::drawable_changing();") == 2
        display = display.replace("ghost_presentation::drawable_changing();", "ghost_presentation::context_stopping();")
    entries = [_function(display, signature) for signature in
               ("bool DisplayWindow::changeWindow()", "bool DisplayWindow::resizeWindow()", "void DisplayWindow::stop()")]
    (tmp_path / "actual_presentation_window.inc").write_text("\n".join(entries), encoding="utf-8")
    glue = (SOURCE / "ghost_presentation.cpp").read_text(encoding="utf-8")
    if restore_regression == "rebuild":
        anchor = "if(current && gamma_program && owns_context(now))"
        assert glue.count(anchor) == 1
        glue = glue.replace(anchor, "if(false)")
    functions = [_function(glue, signature) for signature in
                 ("bool identity_matches()", "bool owns_context(const renderer::Identity& now)",
                  "bool prepare_extent(uint32_t width,uint32_t height)", "void context_stopping()",
                  "void drawable_changing()", "void context_ready()")]
    (tmp_path / "actual_presentation_lifetime.inc").write_text("\n".join(functions), encoding="utf-8")
    build = module("presentation_transition_build", "tools/build_plugin.py")
    vc = build.find_vcvars32()
    assert vc, "CPU presentation transition witness requires x86 MSVC"
    executable = tmp_path / "ghost_presentation_transition_host.exe"
    (tmp_path / "GraphicsDrawer.h").write_text(
        "#pragma once\nstruct GraphicsDrawer { struct CopyRectParams {}; struct BlitOrCopyRectParams {}; };\n",
        encoding="utf-8")
    build._cl(vc, ["/nologo", "/O2", "/W4", "/MT", "/EHsc", "/std:c++17", "/DOS_WINDOWS", "/DSM64_GHOST_RENDERER",
        f"/I{tmp_path}", f"/I{SOURCE}", str(ROOT / "tests/native/ghost_presentation_transition_host.cpp"),
        f"/Fe:{executable}", f"/Fo{tmp_path}\\", "/link", "/MANIFEST:EMBED", "/MANIFESTUAC:level='asInvoker'"], tmp_path)
    result = subprocess.run([str(executable)], capture_output=True, text=True, cwd=tmp_path,
                            timeout=10, check=False, **quiet_spawn_kwargs())
    if restore_regression:
        assert result.returncode != 0
        failure = "stable setup allocates nothing" if restore_regression == "rebuild" else "drawable resize preserves context resources"
        assert failure in result.stderr
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert "resize=retained fullscreen=retained initial=prewarmed stale=refused failure=refused foreign=abandoned stop=destroyed" in result.stdout


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
    assert "plain_front=exact,no_copy" in result.stdout


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
