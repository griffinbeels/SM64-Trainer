"""The ghost glue's capture-gateway reads per list and who owns each GL error.

ghost_draw.cpp is compiled whole against renderer shells (tests/native/
ghost_draw_shell.h) and a gateway that models link_dispatch's; no GL context.
Live, every glGetError waits for NVIDIA's driver thread (docs: What a drawn
ghost asks the driver). SM64_GHOST_DRAW_BASELINE=<git revision> also builds
that revision's glue and prints its numbers beside these, for a before/after.
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.ghost.native_layout import DRAW_LIST_GATES

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "plugin/gfxwrap"
SHELLED = ("Config.h", "FrameBuffer.h", "gSP.h", "gDP.h", "GBI.h", "GLFunctions.h", "Graphics/Context.h")


def build(tmp_path, glue_dir=NATIVE, baseline=False, failure=None):
    spec = importlib.util.spec_from_file_location("build_plugin", ROOT / "tools/build_plugin.py")
    compiler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compiler)
    vcvars = compiler.find_vcvars32()
    assert vcvars, "x86 MSVC toolchain is required for the ghost glue witness"
    shells = tmp_path / "shells"
    for name in SHELLED:
        (shells / name).parent.mkdir(parents=True, exist_ok=True)
        (shells / name).write_text('#include "ghost_draw_shell.h"\n', encoding="utf-8")
    target = tmp_path / "ghost_draw_host.exe"
    compiler._cl(vcvars, ["/nologo", "/O2", "/W4", "/MT", "/EHsc", "/std:c++17", "/Gy",
        *(["/DGHOST_DRAW_BASELINE"] if baseline else []),
        f"/I{glue_dir}", f"/I{shells}", f"/I{ROOT / 'tests/native'}", f"/I{NATIVE}", f"/I{ROOT / 'renderer/model'}",
        str(glue_dir / "ghost_draw.cpp"), str(ROOT / "tests/native/ghost_draw_host.cpp"),
        f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", "/OPT:REF",
        "/MANIFEST:EMBED", "/MANIFESTUAC:level='asInvoker'", *compiler.LIBS], tmp_path)
    result = subprocess.run([str(target)], cwd=tmp_path, capture_output=True, text=True,
                            timeout=30, check=False, **quiet_spawn_kwargs())
    if failure:
        assert result.returncode != 0 and failure in result.stderr, result.stdout + result.stderr
        return None
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def baseline(tmp_path, revision):
    glue = tmp_path / "baseline"
    glue.mkdir()
    for name in ("ghost_draw.cpp", "ghost_draw.h"):
        text = subprocess.run(["git", "show", f"{revision}:plugin/gfxwrap/{name}"], cwd=ROOT,
                              capture_output=True, check=True, **quiet_spawn_kwargs()).stdout
        (glue / name).write_bytes(text)
    return build(tmp_path / "before", glue, baseline=True)


def test_ghost_glue_gateway_reads_and_error_owners(tmp_path):
    if sys.platform != "win32":
        pytest.skip("Win32 renderer component")
    report = build(tmp_path)
    revision = os.environ.get("SM64_GHOST_DRAW_BASELINE")
    if revision:
        (tmp_path / "before").mkdir()
        before = baseline(tmp_path, revision)
        print(json.dumps({"before": {key: before[key] for key in ("finished", "drawn", "shapes")},
                          "after": {key: report[key] for key in ("finished", "drawn", "shapes")}}))
    assert report["ok"] and not report["baseline"] and report["driver_queries"] == 0
    assert report["transitions"] == {
        "initial_prewarm": True, "drawable_rebuilds": 0, "extent_prepares": 1,
        "generation_rebuilds": 1, "foreign_deletes": 0,
        "stale_images_refused": True, "revoked_token_refused": True,
        "loss_foreign_deletes": 0, "owned_loss_destroys": 1,
        "missed_loss_foreign_restores": 0,
    }
    # A list whose plan chose no ghost (finished, faded, suspended, out of its
    # scene) costs what no ghost costs: live, his LLL lists after his 7-2 run
    # mirrored 24 layer passes and read 48 errors each.
    assert report["finished"] == {"reads": 0, "counted": 0, "layer_passes": 0, "hud_mirrors": 0, "drawn": 0}
    # A drawn list reads once around the seal and its HUD mirrors, and twice
    # per mirrored surface (both of its passes), whatever its HUD draw count.
    drawn = report["drawn"]
    assert drawn == {"reads": 26, "counted": 26, "layer_passes": 24, "hud_mirrors": 27, "drawn": 1}
    for surfaces, huds, reads in report["shapes"]:
        assert reads == 2 + 2 * surfaces, (surfaces, huds, reads)
    s = report["scenarios"]
    # The renderer's error before a surface is its own: kept for its next read.
    assert s["renderer_before_surface"] == {
        "drawn": 0, "kept": "renderer", "pending": "none", "nominal": False, "drained_layer": 0,
        "drained_renderer": 0, "broken": 0, "scope_open": False}
    # The layer's own error is drained: the layer goes, the ghost and the
    # nominal state stay.
    assert s["layer_gl"] == {"drawn": 1, "kept": "none", "pending": "none", "nominal": True, "drained_layer": 1,
                             "drained_renderer": 0, "broken": 1, "scope_open": False}
    # Any error in the display scope, the renderer's between HUD draws or
    # after them included, cancels the image before it is ready and is kept.
    for name, kept in (("batch", "ghost"), ("renderer_between_huds", "renderer"), ("hud_gl", "ghost"),
                       ("renderer_after_huds", "renderer")):
        assert s[name] == {"drawn": 0, "kept": kept, "pending": "none", "nominal": False, "drained_layer": 0,
                           "drained_renderer": 0, "broken": 0, "scope_open": False}, name
    # A surface whose upstream draw throws still closes its one scope.
    assert s["throw_in_layer"]["scope_open"] is False and s["throw_in_layer"]["drawn"] == 1
    # The ghost's own error costs the source one invalidation; once the source
    # rebuilt its witness the ghost asks that context for nothing more (no
    # draw, no layer pass, no gateway read) and draws again in the next one.
    assert s["ghost_steps_aside"] == {
        "clean_drawn": 1, "faulted_drawn": 0, "kept": True, "after_drawn": 0, "after_reads": 0,
        "after_layers": 0, "again_reads": 0, "nominal": True, "next_context_drawn": 1}
    # Each way a list ends publishes its own gate (ghost_draw_facts.h): the
    # AMD tester's reports (2026-10-30) could not say which step refused.
    gates = {name: DRAW_LIST_GATES[code] for name, code in report["gates"].items()}
    assert gates == {name: name for name in (
        "drew", "no_plan", "no_model", "other_thread", "no_clean_consumer", "no_sample", "batch",
        "target_changed", "hud", "no_seal", "stale", "prepare", "no_compositor", "create_scope",
        "no_extent", "close_dirty", "faulted")}


def test_a_silent_batch_refusal_negative_control(tmp_path):
    """Restoring the silent exit a refused batch took before 2026-10-30 (an
    MSAA target looked like a list that never sealed) turns the witness red."""
    if sys.platform != "win32":
        pytest.skip("Win32 renderer component")
    source = (NATIVE / "ghost_draw.cpp").read_text(encoding="utf-8")
    anchor = "    } else { ghost_facts::refuse(GDF_LIST_BATCH); cancel(); }\n"
    assert source.count(anchor) == 1
    (tmp_path / "ghost_draw.cpp").write_text(source.replace(anchor, "    } else cancel();\n"), encoding="utf-8")
    build(tmp_path, tmp_path, failure="a refused batch names itself and why")


def test_old_scope_teardown_before_identity_negative_control(tmp_path):
    if sys.platform != "win32":
        pytest.skip("Win32 renderer component")
    source = (NATIVE / "ghost_draw.cpp").read_text(encoding="utf-8")
    anchor = "    if (identified && same_context(identity, owner)) {"
    assert source.count(anchor) == 1
    (tmp_path / "ghost_draw.cpp").write_text(source.replace(anchor,
        "    close_layer(); cancel(); ghost_compositor::clear_layer(compositor);\n" + anchor), encoding="utf-8")
    build(tmp_path, tmp_path, failure="begin abandons old display scopes without foreign restoration")
