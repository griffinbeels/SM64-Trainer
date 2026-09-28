"""Build and run the renderer-thread capture witnesses (hidden, no emulator).

The x86 producer (`gpu_render_witness_host.cpp`) plays the renderer with every
capture step in its one GL context; the x64 consumer
(`gpu_render_witness_consumer.cpp`) plays the encoder helper, which creates the
bridge textures. Shared by test_gpu_bridge, test_gpu_fidelity and
test_gpu_selection.
"""
from __future__ import annotations

import importlib.util
import subprocess
import uuid
from pathlib import Path

import pytest
from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "plugin/gfxwrap"


def toolchain():
    spec = importlib.util.spec_from_file_location("render_witness_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vc32 = build.find_vcvars32()
    if vc32 is None or not vc32.with_name("vcvars64.bat").exists():
        pytest.skip("x86/x64 MSVC unavailable for the renderer-thread capture witness")
    return build, vc32


def _flags(build, defines):
    flags = [flag for flag in build.COMMON_FLAGS if not flag.startswith("/std:")]
    return flags + ["/std:c++17", "/EHsc", f"/I{SOURCE}", *(f"/D{d}" for d in defines)]


def build_producer(build, vc32, out: Path, defines=()):
    target = out / "render_witness_producer.exe"
    build._cl(vc32, _flags(build, defines) + [
        str(SOURCE / "gpu_render_witness_host.cpp"), str(SOURCE / "gpu_render.cpp"), str(SOURCE / "gl_snapshot.cpp"),
        f"/Fe:{target}", f"/Fo{out}\\", "/link", *build.LIBS], out)
    return target


def build_consumer(build, vc32, out: Path, codec=None):
    target = out / "render_witness_consumer.exe"
    defines, sources = [], [str(SOURCE / "gpu_render_witness_consumer.cpp")]
    extra = []
    if codec:
        defines = ["RENDER_WITNESS_NVENC"] + (["RENDER_WITNESS_AV1"] if codec == "av1" else [])
        sources.append(str(SOURCE / "gpu_bridge_encoder.cpp"))
        extra = [f"/I{SOURCE / 'vendor'}"]
    build._cl(vc32.with_name("vcvars64.bat"), _flags(build, defines) + extra + sources + [
        f"/Fe:{target}", f"/Fo{out}\\", "/link", *build.LIBS, "d3d11.lib", "dxgi.lib"], out)
    return target


def run_pair(producer: Path, consumer: Path, out: Path, geometry, mode="verify"):
    """geometry: source_w, source_h, crop_x, crop_y, snapshot_w, snapshot_h, pictures."""
    name = "Local\\sm64-render-witness-" + uuid.uuid4().hex
    with (out / "producer.log").open("w") as log:
        proc = subprocess.Popen([str(producer), "bridge", name, *map(str, geometry)], stdout=log,
                                stderr=subprocess.STDOUT, cwd=out, **quiet_spawn_kwargs())
        try:
            result = subprocess.run([str(consumer), name, mode], capture_output=True, text=True, cwd=out,
                                    timeout=60, **quiet_spawn_kwargs(), check=False)
            (out / "consumer.log").write_text(result.stdout + result.stderr, encoding="utf-8")
            code = proc.wait(timeout=30)
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)
    return result, code, (out / "producer.log").read_text(encoding="utf-8")
