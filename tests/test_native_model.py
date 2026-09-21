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


def test_native_pose_runtime(tmp_path):
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
    evidence = []
    for label, supplied in fixtures:
        result = subprocess.run([str(target), supplied], cwd=tmp_path, capture_output=True,
                                text=True, timeout=30, **quiet_spawn_kwargs(), check=False)
        (tmp_path / f"{label}-witness.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
        assert result.returncode == 0, f"{label}: {result.stdout}{result.stderr}"
        report = json.loads(result.stdout)
        assert report["ok"] and report["vertices"] > 1800
        assert 0 < report["scratch_bytes"] <= 256 * 1024
        assert report["hidden_gl_pixels"] > 1000
        evidence.append({"fixture": label, "rom": Path(supplied).name,
                         "sha256": hashlib.sha256(Path(supplied).read_bytes()).hexdigest(),
                         "result": report})
    (tmp_path / "asset-witness.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")


def test_component_identity_covers_inputs_and_recipe(tmp_path, monkeypatch):
    builder = load_tool("build_model")
    inputs = json.loads((ROOT / "renderer/model/inputs.json").read_text(encoding="utf-8"))
    assert set(inputs) == {"libsm64", "mario_geo", "mario_model"}
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
    for changed in [recipe, copied / "model.h", copied / "model.c",
                    copied / "patches.py", copied / "inputs.json"]:
        before = changed.read_bytes()
        changed.write_bytes(before + b"\nchanged component input\n")
        assert builder.component_hash() != original, changed.name
        changed.write_bytes(before)
        assert builder.component_hash() == original
