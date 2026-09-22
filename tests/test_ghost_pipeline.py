"""Opt-in connected synthetic GPU proof; this is not a live GLideN64 benchmark."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.memory.layout import US

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = Path(os.environ.get("SM64_GHOST_PIPELINE_ROOT", ROOT)).resolve()
pytestmark = pytest.mark.skipif(os.environ.get("SM64_TEST_GHOST_PIPELINE") != "1",
    reason="explicit hidden connected native GPU witness: set SM64_TEST_GHOST_PIPELINE=1")


def tool(name):
    spec = importlib.util.spec_from_file_location(f"pipeline_{name}", COMPONENTS / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def layout_module():
    # A borrowed component tree must use its matching capture ABI. Keep the
    # normal test checkout's imports and queued tests entirely unchanged.
    path = COMPONENTS / "src/sm64_events/memory/ghost_layout.py"
    spec = importlib.util.spec_from_file_location("pipeline_ghost_layout", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def retain_artifacts(paths):
    evidence = os.environ.get("SM64_GHOST_PIPELINE_EVIDENCE")
    if evidence:
        destination = Path(evidence)
        destination.mkdir(parents=True, exist_ok=True)
        for path in paths:
            shutil.copyfile(path, destination / path.name)


def test_connected_capture_model_gpu_pipeline(tmp_path):
    if sys.platform != "win32":
        pytest.skip("Win32 hidden WGL pipeline")
    rom_name, seed_name = os.environ.get("SM64_MODEL_ROM"), os.environ.get("SM64_MODEL_SEED_DIR")
    assert rom_name and seed_name, "explicit pipeline needs a local ROM and cached pinned model inputs"
    rom, seed = Path(rom_name), Path(seed_name)
    digest = hashlib.sha1(rom.read_bytes()).hexdigest()
    assert digest == "9bef1128717f958171a4afac3ed78ee2bb4e86ce", "witness expects the verified big-endian US ROM"
    inputs = json.loads((COMPONENTS / "renderer/model/inputs.json").read_text(encoding="utf-8"))
    for item in inputs.values():
        cached = seed / item["filename"]
        assert cached.is_file() and hashlib.sha256(cached.read_bytes()).hexdigest() == item["sha256"], cached
    builder, compiler = tool("build_model"), tool("build_plugin")
    vcvars = compiler.find_vcvars32()
    assert vcvars, "x86 MSVC toolchain required"
    model_identity = builder.component_hash()
    library = builder.build_model(tmp_path / "model", seed=seed, vcvars32=vcvars)
    native = COMPONENTS / "plugin/gfxwrap"
    sources = [native / f"{name}.cpp" for name in
               ("ghost_capture", "ghost_root", "ghost_clock", "ghost_stream", "ghost_pass", "ghost_playback",
                "ghost_pose", "ghost_compositor", "ghost_presentation")]
    host = ROOT / "tests/native/ghost_pipeline_host.cpp"
    source_inputs = [*sources, host, Path(__file__), COMPONENTS / "tests/native/ghost_capture_host.cpp",
                     COMPONENTS / "tests/native/ghost_root_fixture.h",
                     COMPONENTS / "tests/native/ghost_compositor_host.cpp",
                     COMPONENTS / "src/sm64_events/memory/ghost_layout.py"]
    source_inputs += sorted(native.glob("ghost_*.h"))
    receipt = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in source_inputs}
    target = tmp_path / "ghost_pipeline_host.exe"
    compiler._cl(vcvars, ["/nologo", "/O2", "/W4", "/MT", "/EHsc", "/std:c++17", "/Gy",
        f"/I{native}", f"/I{COMPONENTS / 'renderer/model'}", f"/I{COMPONENTS / 'tests/native'}",
        *(str(path) for path in sources), str(host), str(library), f"/Fe:{target}", f"/Fo{tmp_path}\\",
        "/link", "/OPT:REF", "/MANIFEST:EMBED", "/MANIFESTUAC:level='asInvoker'",
        *compiler.LIBS], tmp_path)
    layout = tmp_path / "layout.bin"
    layout.write_bytes(layout_module().GhostLayout.from_layout(US).to_bytes())
    result = subprocess.run([str(target), str(layout), str(rom), str(tmp_path)], cwd=tmp_path,
        capture_output=True, text=True, timeout=60, check=False, **quiet_spawn_kwargs())
    (tmp_path / "pipeline-output.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
    retain_artifacts([tmp_path / "pipeline-output.txt"])
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest
               for path, digest in receipt.items()), "source changed during witness construction/run"
    assert builder.component_hash() == model_identity, "model source changed during witness"
    report.update(source_sha256=receipt, rom_sha1=digest, model=model_identity,
                  executable_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                  library_sha256=hashlib.sha256(library.read_bytes()).hexdigest(),
                  layout_sha256=hashlib.sha256(layout.read_bytes()).hexdigest(),
                  fixture="Synthetic swizzled RDRAM and terrain, real source clock and native components, hidden WGL.",
                  excludes="Live emulator, GLideN64 terrain/VI shader, video encoding, IPC transport.",
                  timing="Capture through presentation clean/display copies and glFinish; swap measured separately.")
    (tmp_path / "pipeline-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    from PIL import Image
    for path in tmp_path.glob("*.ppm"):
        with Image.open(path) as image:
            image.save(path.with_suffix(".png"))
    retain_artifacts([tmp_path / "pipeline-report.json", tmp_path / "pipeline-output.txt",
                      *tmp_path.glob("*.png")])
    assert report["ok"] and report["synthetic"] and not report["live_gliden64"]
    assert report["capture_version"] == 4 and report["clip_version"] == 3 and report["playback_version"] == 3
    assert report["follow_segment_origin"] and report["paused_igt_ignores_global_timer"]
    assert report["vertices"] > 1800 and report["silhouette_pixels"] > 1000
    assert report["outside_pixels_changed"] == report["alpha_errors"] == report["occlusion_errors"] == 0
    assert report["pipeline_gpu_completed"]["samples"] == report["including_swap"]["samples"] == 180
    print(f"Connected synthetic pipeline report: {tmp_path / 'pipeline-report.json'}")
    print(json.dumps({key: report[key] for key in
                     ("pipeline_gpu_completed", "blocking_swap", "including_swap", "gpu_renderer")}))
    # A functional pass cannot disguise exceeding the requested whole-frame budget.
    assert report["including_swap"]["over_budget"] == 0, report["including_swap"]
