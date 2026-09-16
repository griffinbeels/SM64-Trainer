"""Real x86 GL producer -> x64 D3D11 consumer; test-only raw-pixel oracle."""
import importlib.util
import os
import subprocess
import uuid
from pathlib import Path

import pytest
from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "plugin/gfxwrap"
pytestmark = pytest.mark.skipif(os.environ.get("SM64_TEST_GPU_BRIDGE") != "1",
    reason="explicit same-adapter NVIDIA hardware witness: set SM64_TEST_GPU_BRIDGE=1")

@pytest.fixture(scope="module")
def native_build():
    spec = importlib.util.spec_from_file_location("bridge_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vc32 = build.find_vcvars32()
    if vc32 is None or not vc32.with_name("vcvars64.bat").exists():
        pytest.skip("x86/x64 MSVC unavailable for real GPU bridge witness")
    return build, vc32

@pytest.mark.parametrize("mode", ["pixels", "omit-copy", "nvenc"])
def test_gpu_bridge(tmp_path, native_build, mode):
    omit_copy = mode == "omit-copy"
    build, vc32 = native_build
    flags = [flag for flag in build.COMMON_FLAGS if not flag.startswith("/std:")]
    flags += ["/std:c++17", "/EHsc", f"/I{SOURCE}", f"/I{ROOT / 'plugin/gfxwrap'}"]
    producer = tmp_path / "gpu_bridge_producer.exe"
    consumer = tmp_path / "gpu_bridge_consumer.exe"
    for target, host, vc, extra in [
        (producer, "gpu_bridge_host.cpp", vc32, [str(ROOT / "plugin/gfxwrap/gl_snapshot.cpp")]),
        (consumer, "gpu_bridge_consumer.cpp", vc32.with_name("vcvars64.bat"), []),
    ]:
        defines = ["/DGPU_BRIDGE_OMIT_COPY"] if omit_copy else []
        if mode == "nvenc":
            defines += ["/DGPU_BRIDGE_LARGE"]
        if mode == "nvenc" and target == consumer:
            defines += ["/DGPU_BRIDGE_NVENC", f"/I{SOURCE / 'vendor'}"]
            extra += [str(SOURCE / "gpu_bridge_encoder.cpp")]
        build._cl(vc, flags + defines + [str(SOURCE / "gpu_bridge.cpp"), str(SOURCE / host), *extra,
            f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", *build.LIBS, "d3d11.lib", "dxgi.lib"], tmp_path)
    name = "Local\\sm64-gpu-bridge-" + uuid.uuid4().hex
    with (tmp_path / "producer.log").open("w") as producer_log:
        proc = subprocess.Popen([str(producer), name], stdout=producer_log, stderr=subprocess.STDOUT,
                                cwd=tmp_path, **quiet_spawn_kwargs())
        try:
            result = subprocess.run([str(consumer), name], capture_output=True, text=True,
                                    cwd=tmp_path, timeout=30, **quiet_spawn_kwargs(), check=False)
            (tmp_path / "consumer.log").write_text(result.stdout + result.stderr, encoding="utf-8")
            if omit_copy:
                assert result.returncode == 1 and "pixel mismatch" in result.stderr, result.stdout + result.stderr
            else:
                assert result.returncode == 0, result.stdout + result.stderr
                assert proc.wait(timeout=15) == 0
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)
    output = (tmp_path / "producer.log").read_text(encoding="utf-8")
    assert "render_progress=2000 full_refusals=2000" in output, output
    assert "worker_profile=core" in output
    assert "source_gpu_progress=completed consumer_still_stalled=yes" in output
    if not omit_copy:
        assert "producer passed: pictures=6" in output
        expected_bytes = 1843200 if mode == "nvenc" else 1152
        assert f"bits=64 pictures=6 bytes_checked={expected_bytes}" in result.stdout

    if mode == "nvenc":
        assert_nvenc_output(tmp_path, result.stdout)


def assert_nvenc_output(tmp_path, output):
    assert "drained submitted=6 completed=6 eos=yes" in output
    import av
    import csv
    import numpy as np
    with (tmp_path / "packets.csv").open() as source:
        packets = list(csv.DictReader(source))
    assert [int(row["pts"]) for row in packets] == [0, 3001, 3002, 90000, 91000, 180000]
    assert [int(row["duration"]) for row in packets] == [3001, 1, 86998, 1000, 89000, 9000]
    assert [int(row["idr"]) for row in packets] == [1, 0, 0, 1, 0, 1]
    with av.open(str(tmp_path / "witness.h264")) as video:
        frames = [frame.to_ndarray(format="rgb24") for frame in video.decode(video=0)]
    assert len(frames) == 6
    ys, xs = np.indices((240, 320)); xs = xs + 1; ys = 240 - 1 - ys + 2
    references = []
    for occurrence in range(1, 7):
        references.append(np.stack([(occurrence*29+xs*11+ys*3+17)&255,
            (occurrence*13+xs*7+ys*23+41)&255,
            (occurrence*19+xs*31+ys*5+71)&255], axis=-1).astype(float))
    distances = [[float(np.abs(frame.astype(float) - ref).mean()) for ref in references] for frame in frames]
    assert [int(np.argmin(row)) for row in distances] == [0, 1, 2, 3, 4, 5], distances
    (tmp_path / "decoded-marker-distances.txt").write_text(str(distances), encoding="utf-8")
