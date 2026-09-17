"""Bounded real-GPU sample witness against original full-frame selector input."""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import numpy as np
import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.replay.ledger import PictureLedger, SAMPLE_STRIDE
from sm64_events.replay.pixels import SampledPicture

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "tools/build_plugin.py").is_file())
SOURCE = ROOT / "plugin/gfxwrap"
pytestmark = pytest.mark.skipif(os.environ.get("SM64_TEST_GPU_SELECTION") != "1",
    reason="explicit bounded real-GPU selection witness: SM64_TEST_GPU_SELECTION=1")
TIMES = [0, .033, .066, .100, .110, .140, .150, .160, .200, .250, .300]
COUNTERS = [100, 101, 102, 103, 103, 104, 104, 105, 106, 107, 100]


def pictures(width, height):
    """CPU fixture only: actual original bottom-up RGBA bytes supplied to GL."""
    y, x = np.indices((height, width))
    top = np.stack([(17*x + 13*y + 23) % 256, (31*x + 3*y + 47) % 256,
                    (7*x + 19*y + 83) % 256, (x + 5*y) % 256], axis=-1).astype(np.uint8)
    # All corners, RGB primaries and grayscale have explicit asymmetric values.
    for i, color in enumerate([(255, 0, 0), (0, 255, 0), (0, 0, 255), (31, 31, 31)]):
        top[:8, i*8:(i+1)*8, :3] = color
    for (yy, xx), color in zip([(0, 0), (0, width-1), (height-1, 0), (height-1, width-1)],
                              [(13, 71, 191), (241, 29, 101), (89, 213, 7), (37, 59, 251)], strict=True):
        top[yy, xx, :3] = color
    result = [top.copy(), top.copy()]
    off = result[-1].copy(); off[1, 1, :3] ^= 0x33; result.append(off)
    on = off.copy(); on[8, 8, :3] ^= 0x55; result.append(on)
    folded = on.copy(); folded[16, 16, :3] ^= 0x77; result.append(folded)
    result.append(folded.copy())
    changed = folded.copy(); changed[24, 24, :3] ^= 0x11; result.append(changed)
    catchup = changed.copy(); catchup[32, 32, :3] ^= 0xEE; result.append(catchup)
    alpha_only = catchup.copy(); alpha_only[:, :, 3] ^= 0xFF; result.append(alpha_only)
    edge = alpha_only.copy(); edge[-1, -1, :3] ^= 0x99; result.append(edge)
    result.append(edge.copy())
    return [np.ascontiguousarray(p[::-1]) for p in result]


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory):
    directory = tmp_path_factory.mktemp("gpu-selection-input")
    values = {}
    for width, height in [(640, 480), (641, 481)]:
        frames = pictures(width, height); values[(width, height)] = frames
        with (directory / f"input_{width}x{height}.rgba").open("wb") as file:
            for frame in frames: file.write(frame.tobytes())
    return directory, values


@pytest.fixture(scope="module")
def build():
    spec = importlib.util.spec_from_file_location("selection_build", ROOT / "tools/build_plugin.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    vc = next((path for path in module.KNOWN_VCVARS if path.exists()), None)
    assert vc is not None, "explicit native witness needs the existing x86 MSVC compiler"
    # Reuse the build command owner while also suppressing background cursor feedback.
    def run(*args, **kwargs):
        kwargs.update(quiet_spawn_kwargs())
        return subprocess.run(*args, **kwargs)
    module.subprocess = SimpleNamespace(run=run)
    return module, vc


def full_frame_oracle(frame):
    # Independent oracle: full original image is flipped BEFORE the existing
    # selector samples it. No shader coordinates or GPU sample bytes are reused.
    expected = frame[::-1][:, :, [2, 1, 0, 3]].copy()
    expected[:, :, 3] = 255
    return expected


def selector_proof(width, height, frames, chunks):
    cpu = PictureLedger(); gpu = PictureLedger()
    assert SAMPLE_STRIDE == 8
    selected_cpu, selected_gpu = [], []
    for i, (frame, chunk) in enumerate(zip(frames, chunks, strict=True)):
        full = full_frame_oracle(frame)
        source = SampledPicture(width, height, chunk, SAMPLE_STRIDE)
        extras = {"occurrence": i+1, "pad": {"x": i, "buttons": ["A"] if i % 2 else ["B"]}}
        a = cpu.observe(full, 1000+TIMES[i], COUNTERS[i], extras)
        b = gpu.observe(source, 1000+TIMES[i], COUNTERS[i], extras)
        selected_cpu.append(a); selected_gpu.append(b)
        assert a == b
        assert list(cpu._rows) == list(gpu._rows)
        assert cpu._prev_sample == gpu._prev_sample
        assert cpu._prev_shape == gpu._prev_shape == (height, width, 4)
        if i == 4:
            # Folded candidate changes comparison but still retains picture 3.
            assert not b and gpu._rows[-1][2]["occurrence"] == 4
            assert gpu._prev_sample == chunk
            assert [n for n, selected in enumerate(selected_gpu) if selected] == [0, 3]
        if i == 5:
            assert COUNTERS[i] != gpu._rows[-1][1] and not b
    expected = [0, 3, 6, 7] + ([9] if width % 2 else [])
    assert [i for i, selected in enumerate(selected_cpu) if selected] == expected
    assert selected_cpu == selected_gpu
    return {"selected_occurrences": [i+1 for i in expected], "rows": list(gpu._rows),
            "decision_sequence": selected_gpu, "folded_baseline_kept_old_picture": True}


@pytest.mark.parametrize("mode", ["exact", "wrong-y", "crop-first", "rgba-bytes"])
def test_real_gpu_selection(tmp_path, fixtures, build, mode):
    input_dir, values = fixtures
    builder, vc = build
    defines = {"exact": [], "wrong-y": ["/DGPU_SELECTION_WRONG_Y"],
               "crop-first": ["/DGPU_SELECTION_CROP_FIRST"], "rgba-bytes": ["/DGPU_SELECTION_RGBA_BYTES"]}[mode]
    flags = [f for f in builder.COMMON_FLAGS if not f.startswith("/std:")]
    flags += ["/std:c++17", "/EHsc", f"/I{SOURCE}", f"/I{ROOT / 'plugin/gfxwrap'}"]
    target = tmp_path / "gpu_selection_host.exe"
    builder._cl(vc, flags + defines + [str(SOURCE / "gpu_selection.cpp"),
        str(SOURCE / "gpu_selection_host.cpp"), str(ROOT / "plugin/gfxwrap/gl_snapshot.cpp"),
        f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", *builder.LIBS], tmp_path)
    result = subprocess.run([str(target), str(input_dir), str(tmp_path)], cwd=tmp_path,
                            capture_output=True, text=True, timeout=30, **quiet_spawn_kwargs())
    (tmp_path / "host.log").write_bytes((result.stdout + result.stderr).encode())
    assert result.returncode == 0, result.stdout + result.stderr
    assert "selection witness completed" in result.stdout
    assert result.stdout.count("custody=yes guard_bytes=yes worker_only=yes") == 2
    receipt = {"mode": mode, "host": result.stdout, "dimensions": []}
    for (width, height), frames in values.items():
        actual = (tmp_path / f"samples_{width}x{height}.bgra").read_bytes()
        references = [full_frame_oracle(frame)[::8, ::8].tobytes() for frame in frames]
        expected = b"".join(references)
        detail = {"width": width, "height": height, "pictures": len(frames),
                  "expected_bytes": len(expected), "actual_bytes": len(actual)}
        if mode == "exact" or (mode == "crop-first" and width == 640):
            assert actual == expected
            per = len(references[0])
            assert f"bytes={per} pictures=11 reads=11 readback_bytes={per*11}" in result.stdout
            chunks = [actual[i*per:(i+1)*per] for i in range(len(frames))]
            detail.update(selector_proof(width, height, frames, chunks))
            detail["mismatches"] = 0
        else:
            assert actual != expected
            if mode == "crop-first":
                assert len(actual) == 80*60*4*11 < len(expected)
                detail["negative_detected"] = "odd original edge missing"
            else:
                assert len(actual) == len(expected)
                count = sum(a != b for a, b in zip(actual, expected, strict=True))
                assert count > 1000
                detail["mismatches"] = count
                detail["negative_detected"] = "exact byte oracle rejected mutation"
        receipt["dimensions"].append(detail)
    (tmp_path / "receipt.json").write_bytes(json.dumps(receipt, indent=2).encode())
