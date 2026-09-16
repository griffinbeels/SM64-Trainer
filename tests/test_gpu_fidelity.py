"""Actual GPU delivery compared with the existing bottom-up BGR materialization."""
import importlib.util
import os
import subprocess
import uuid
from pathlib import Path

import numpy as np
import pytest
from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.replay.pixels import to_bgra_top_down

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "plugin/gfxwrap"
pytestmark = pytest.mark.skipif(os.environ.get("SM64_TEST_GPU_BRIDGE") != "1",
    reason="explicit same-adapter NVIDIA hardware witness: set SM64_TEST_GPU_BRIDGE=1")


def bgr_fixture(width, height):
    """Independent source-coordinate raster; no bridge/shader code is imported."""
    rgb = np.empty((height, width, 3), dtype=np.uint8)
    rgb[:] = (37, 83, 151)
    rgb[:16, :16] = (255, 0, 0)
    rgb[:16, width-16:] = (0, 255, 0)
    rgb[height-16:, :16] = (0, 0, 255)
    rgb[height-16:, width-16:] = (255, 255, 0)
    rgb[:, 0] = (11, 29, 43)
    rgb[:, -1] = (53, 71, 89)
    rgb[0, :] = (101, 127, 149)
    rgb[-1, :] = (173, 197, 229)
    colors = [(0, 0, 0), (16, 16, 16), (40, 100, 220), (220, 100, 40),
              (127, 127, 127), (235, 235, 235), (255, 255, 255), (17, 201, 93)]
    for index, color in enumerate(colors):
        rgb[80:144, 48+64*index:96+64*index] = color
    rgb[200:232] = (np.arange(width, dtype=np.uint16) % 256).astype(np.uint8)[None, :, None]
    return rgb[:, :, ::-1].copy()


@pytest.fixture(scope="module")
def toolchain():
    spec = importlib.util.spec_from_file_location("fidelity_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vc32 = build.find_vcvars32()
    if vc32 is None or not vc32.with_name("vcvars64.bat").exists():
        pytest.skip("x86/x64 MSVC unavailable")
    return build, vc32


@pytest.mark.parametrize("width,height,mutation", [
    (640, 480, None), (641, 481, None),
    (641, 481, "OMIT_FLIP"), (641, 481, "WRONG_ODD_ROW"), (641, 481, "SWAP_RB"),
])
def test_gpu_top_down_exact_pixels(tmp_path, toolchain, width, height, mutation):
    build, vc32 = toolchain
    flags = [flag for flag in build.COMMON_FLAGS if not flag.startswith("/std:")]
    flags += ["/std:c++17", "/EHsc", f"/I{SOURCE}", f"/I{ROOT / 'plugin/gfxwrap'}"]
    if mutation:
        flags += [f"/DGPU_FIDELITY_{mutation}"]
    executables = []
    for label, vcvars in [("host", vc32), ("consumer", vc32.with_name("vcvars64.bat"))]:
        exe = tmp_path / f"fidelity_{label}.exe"
        sources = [str(SOURCE / "gpu_bridge.cpp"), str(SOURCE / f"gpu_fidelity_{label}.cpp")]
        if label == "host":
            sources += [str(ROOT / "plugin/gfxwrap/gl_snapshot.cpp")]
        build._cl(vcvars, flags + sources + [f"/Fe:{exe}", f"/Fo{tmp_path}\\", "/link",
            *build.LIBS, "d3d11.lib", "dxgi.lib"], tmp_path)
        executables.append(exe)
    prefix = "Local\\sm64-fidelity-" + uuid.uuid4().hex
    with (tmp_path / "producer.log").open("w") as log:
        producer = subprocess.Popen([str(executables[0]), prefix, str(width), str(height)],
            stdout=log, stderr=subprocess.STDOUT, cwd=tmp_path, **quiet_spawn_kwargs())
        try:
            consumer = subprocess.run([str(executables[1]), prefix], capture_output=True,
                text=True, timeout=30, cwd=tmp_path, **quiet_spawn_kwargs(), check=False)
            (tmp_path / "consumer.log").write_text(consumer.stdout + consumer.stderr, encoding="utf-8")
            assert consumer.returncode == 0, consumer.stdout + consumer.stderr
            assert producer.wait(timeout=15) == 0, (tmp_path / "producer.log").read_text()
        finally:
            if producer.poll() is None:
                producer.terminate()
                producer.wait(timeout=5)
    assert "bits=64 bytes=1228800" in consumer.stdout
    rgba = np.fromfile(tmp_path / "delivered.rgba", dtype=np.uint8).reshape(480, 640, 4)
    actual = rgba[:, :, [2, 1, 0, 3]]
    bottom_up = bgr_fixture(width, height)
    expected = to_bgra_top_down(bottom_up)[:height & ~1, :width & ~1]
    assert np.all(actual[:, :, 3] == 255)
    if mutation is None:
        assert np.array_equal(actual, expected), np.argwhere(actual != expected)[:8]
    else:
        assert not np.array_equal(actual, expected), "the fidelity oracle missed the injected violation"
        if mutation == "OMIT_FLIP":
            broken = np.concatenate([bottom_up[:480, :640], np.full((480, 640, 1), 255, np.uint8)], axis=2)
        elif mutation == "WRONG_ODD_ROW":
            broken = to_bgra_top_down(bottom_up[:480])[:, :640]
        else:
            broken = expected[:, :, [2, 1, 0, 3]]
        assert np.array_equal(actual, broken), "negative control did not reach its intended failure"
    (tmp_path / "oracle.txt").write_text(
        f"source={width}x{height} output=640x480 bytes={actual.nbytes} mutation={mutation} "
        f"mismatched_bytes={np.count_nonzero(actual != expected)} alpha=opaque\n", encoding="utf-8")
