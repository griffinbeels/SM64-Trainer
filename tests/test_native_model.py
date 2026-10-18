"""Build and run the real Win32 model, using explicitly provided local ROM bytes."""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _native_pose_runtime(tmp_path, *, cpu_only=False):
    if sys.platform != "win32":
        pytest.skip("Win32 native component")
    rom = os.environ.get("SM64_MODEL_ROM")
    if not rom:
        pytest.skip("set SM64_MODEL_ROM to a locally supplied big-endian US ROM")
    seed = os.environ.get("SM64_MODEL_SEED_DIR")
    builder, compiler = load_tool("build_model"), load_tool("build_plugin")
    vcvars = compiler.find_vcvars32()
    assert vcvars, "x86 MSVC toolchain is required for the explicit native witness"
    library = builder.build_model(tmp_path / "model", vcvars32=vcvars,
                                  seed=Path(seed) if seed else None, testing=True)
    target = tmp_path / "model_host.exe"
    compiler._cl(vcvars, ["/nologo", "/O2", "/W4", "/MT", "/EHsc", "/std:c++17", "/Gy",
        f"/I{ROOT / 'renderer/model'}", str(ROOT / "tests/native/model_host.cpp"),
        str(library), f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", "/OPT:REF",
        "/MANIFEST:EMBED", "/MANIFESTUAC:level='asInvoker'", *compiler.LIBS], tmp_path)
    fixtures = [("native", rom)]
    practice = os.environ.get("SM64_MODEL_PRACTICE_ROM")
    if practice:
        fixtures.append(("practice", practice))
    # Usamune JP (SM64_USAMUNE_v1.93j.z64): the same model from its own offsets.
    japanese = os.environ.get("SM64_MODEL_JP_ROM")
    if japanese:
        fixtures.append(("jp", japanese))
    evidence = []
    for label, supplied in fixtures:
        args = [str(target), supplied, *(["--cpu-only"] if cpu_only else [])]
        result = subprocess.run(args, cwd=tmp_path, capture_output=True,
                                text=True, timeout=30, **quiet_spawn_kwargs(), check=False)
        (tmp_path / f"{label}-witness.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
        assert result.returncode == 0, f"{label}: {result.stdout}{result.stderr}"
        report = json.loads(result.stdout)
        assert report["ok"] and report["vertices"] > 1800
        assert 0 < report["scratch_bytes"] <= 256 * 1024
        assert report["cpu_only"] == cpu_only
        if cpu_only:
            assert report["hidden_gl_pixels"] == 0
        else:
            assert report["hidden_gl_pixels"] > 1000
        evidence.append({"fixture": label, "rom": Path(supplied).name,
                         "sha256": hashlib.sha256(Path(supplied).read_bytes()).hexdigest(),
                         "result": report})
    (tmp_path / "asset-witness.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    # Each cartridge's own blocks decode to the same art: a JP block read from
    # a wrong offset or shift would change it.
    assert len({item["result"]["atlas_fnv"] for item in evidence}) == 1, evidence
    # The real model through the real ghost_pose: Mario holding each kind the
    # ghost draws, under the F3D lighting, on every supplied ROM.
    held = tmp_path / "held_pose_host.exe"
    compiler._cl(vcvars, ["/nologo", "/O2", "/MT", "/EHsc", "/std:c++17",
        f"/I{ROOT / 'renderer/model'}", f"/I{ROOT / 'plugin/gfxwrap'}", str(ROOT / "tests/native/held_pose_host.cpp"),
        str(ROOT / "plugin/gfxwrap/ghost_pose.cpp"), str(library), f"/Fe:{held}", f"/Fo{tmp_path}\\",
        "/link", *compiler.LIBS], tmp_path)
    for label, supplied in fixtures:
        result = subprocess.run([str(held), supplied], cwd=tmp_path, capture_output=True,
                                text=True, timeout=30, **quiet_spawn_kwargs(), check=False)
        assert result.returncode == 0, f"{label} held poses: {result.stdout}{result.stderr}"


def test_native_pose_runtime(tmp_path):
    _native_pose_runtime(tmp_path)


def test_native_pose_cpu(tmp_path):
    """Real ROM geometry, without opening a GL context during live play."""
    _native_pose_runtime(tmp_path, cpu_only=True)


def test_component_identity_covers_inputs_and_recipe(tmp_path, monkeypatch):
    builder = load_tool("build_model")
    inputs = json.loads((ROOT / "renderer/model/inputs.json").read_text(encoding="utf-8"))
    # Every pinned input is staged, and every held model's input is pinned.
    held = {key for model in builder.HELD_MODELS.values() for key in (model.lists, *model.extra_lists, *model.inputs)}
    assert set(inputs) == {"libsm64", "mario_geo", "mario_model"} | held
    assert all(len(item["sha256"]) == 64 for item in inputs.values())
    copied = tmp_path / "model"
    copied.mkdir()
    for source in (ROOT / "renderer/model").iterdir():
        if source.is_file():
            (copied / source.name).write_bytes(source.read_bytes())
    recipe = tmp_path / "build_model.py"
    recipe.write_bytes((ROOT / "tools/build_model.py").read_bytes())
    monkeypatch.setattr(builder, "MODEL", copied)
    monkeypatch.setattr(builder, "__file__", str(recipe))
    original = builder.component_hash()
    for changed in [recipe, copied / "model.h", copied / "model.c", copied / "held.c",
                    copied / "patches.py", copied / "inputs.json"]:
        before = changed.read_bytes()
        changed.write_bytes(before + b"\nchanged component input\n")
        assert builder.component_hash() != original, changed.name
        changed.write_bytes(before)
        assert builder.component_hash() == original


def test_owl_texture_load_shorthand_reaches_its_atlas_cell():
    builder = load_tool("build_model")
    geo = "const GeoLayout hoot_geo[] = { GEO_DISPLAY_LIST(LAYER_OPAQUE, hoot_dl_05001B80), GEO_END() };"
    lists = """ALIGNED8 static const u8 hoot_texture_05000A20[] = { 0 };
const Gfx hoot_dl_05001B80[] = {
    gsDPLoadTextureBlock(hoot_texture_05000A20, G_IM_FMT_RGBA, G_IM_SIZ_16b, 32, 32, 0, G_TX_WRAP | G_TX_NOMIRROR, G_TX_WRAP | G_TX_NOMIRROR, 5, 5, G_TX_NOLOD, G_TX_NOLOD),
    gsSPEndDisplayList(),
};"""
    expanded = builder._expand_texture_blocks(lists)
    assert "gsDPLoadTextureBlock(" not in expanded
    assert builder._reached_textures(geo, "hoot_geo", expanded) == [("hoot_texture_05000A20", 32, 32)]
    assert "G_TX_RENDERTILE, 0, G_TX_WRAP | G_TX_NOMIRROR, 5" in expanded
    with pytest.raises(ValueError, match="Only RGBA16"):
        builder._expand_texture_blocks(lists.replace("G_IM_FMT_RGBA", "G_IM_FMT_IA"))


def test_selected_actor_graph_keeps_all_cases_and_only_reachable_lists():
    builder = load_tool("build_model")
    geo = """const GeoLayout actor_geo[] = {
    GEO_SWITCH_CASE(2, geo_switch_anim_state),
    GEO_DISPLAY_LIST(LAYER_OPAQUE, actor_dl_05000100),
    GEO_BRANCH_AND_LINK(actor_case), GEO_END() };
const GeoLayout actor_case[] = {
    GEO_DISPLAY_LIST(LAYER_OPAQUE, actor_dl_05000200), GEO_RETURN() };
const GeoLayout unused_geo[] = {
    GEO_DISPLAY_LIST(LAYER_OPAQUE, unused_metal_dl_03000100), GEO_END() };"""
    lists = """const Gfx actor_dl_05000100[] = { gsSPDisplayList(actor_dl_05000300), };
const Gfx actor_dl_05000200[] = { gsSPBranchList(actor_dl_05000300), };
const Gfx actor_dl_05000300[] = { gsSPEndDisplayList(), };
const Gfx unused_metal_dl_03000100[] = { unsupported_material(), };
"""
    selected = builder._selected_display_lists(geo, "actor_geo", lists)
    assert "unsupported_material" not in selected
    assert builder._reached_display_lists(geo, "actor_geo", selected) == {
        "actor_dl_05000100", "actor_dl_05000200", "actor_dl_05000300"}
    # A broken reached dependency is an error, never silently dropped.
    with pytest.raises(KeyError):
        builder._selected_display_lists(geo, "actor_geo", lists.replace(
            "const Gfx actor_dl_05000300[]", "const Gfx unrelated_dl_05000300[]"))
