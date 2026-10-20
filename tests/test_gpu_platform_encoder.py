"""Windows hardware path: independently decoded source IDs and exact archive ticks.

The fixture uploads its own synthetic source pictures. The shipped converter,
encoder and retained-picture operations use default D3D11 textures exclusively.
Enable explicitly; this does not inspect or use a live emulator or Trainer.
"""
from __future__ import annotations

import importlib.util
import io
import os
from pathlib import Path
import subprocess

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.environ.get("SM64_TEST_GPU_PLATFORM") != "1",
    reason="explicit isolated Windows hardware witness: SM64_TEST_GPU_PLATFORM=1")


@pytest.fixture(scope="module")
def native_host(tmp_path_factory):
    out = tmp_path_factory.mktemp("platform-encoder")
    spec = importlib.util.spec_from_file_location("platform_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vc64 = build.find_vcvars64()
    if vc64 is None:
        pytest.skip("x64 MSVC unavailable")
    source = ROOT / "plugin/gfxwrap"
    exe = out / "platform_encoder.exe"
    flags = [flag for flag in build.COMMON_FLAGS if not flag.startswith("/std:")]
    build._cl(vc64, flags + ["/std:c++17", "/EHsc", "/WX",
        str(source / "gpu_mf_encoder_host.cpp"), str(source / "gpu_mf_encoder.cpp"),
        f"/Fe:{exe}", f"/Fo{out}\\"], out)
    dll = build.build_encoder_helper(out, vc64)
    dll_exe = out / "platform_dll.exe"
    build._cl(vc64, flags + ["/std:c++17", "/EHsc", "/WX", "/DGPU_MF_USE_DLL",
        str(source / "gpu_mf_encoder_host.cpp"), f"/Fe:{dll_exe}", f"/Fo{out}\\"], out)
    return exe, dll_exe, dll


@pytest.mark.parametrize("mode", ["rgba", "bgra", "dll"])
def test_windows_gpu_encoder_exact_archive(native_host, tmp_path, mode):
    vendor = os.environ.get("SM64_TEST_GPU_PLATFORM_VENDOR", "1002")
    exe, dll_exe, dll = native_host
    command = [str(dll_exe), vendor, "rgba", str(dll)] if mode == "dll" else [str(exe), vendor, mode]
    result = subprocess.run(command, cwd=tmp_path,
        capture_output=True, text=True, timeout=20, check=False, **quiet_spawn_kwargs())
    (tmp_path / "hardware.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"vendor={vendor}" in result.stdout
    assert "noCPUreadback=yes" in result.stdout
    if mode == "dll":
        assert "realDLL shared textures exactLUID=yes backend=1" in result.stdout
    assert "closed=1 submitted=6 completed=6 copies=5 repeats=1" in result.stdout
    rows = [[int(word) for word in line.split(",")[1:]]
            for line in result.stdout.splitlines() if line.startswith("packet,")]
    pts = [0, 3001, 3002, 90000, 91000, 180000]
    durations = [3001, 1, 86998, 1000, 89000, 9000]
    assert [row[0] for row in rows] == list(range(1, 7))
    assert [row[1] for row in rows] == pts
    assert [row[2] for row in rows] == durations
    assert [row[3] for row in rows] == [1, 0, 0, 1, 0, 1]

    import av
    import numpy as np
    from sm64_events.replay.media import MediaRun
    from sm64_events.replay.packetmux import EncodedPicture, NativeFormat, PacketFragmentMux

    data = (tmp_path / "witness.h264").read_bytes()
    output = io.BytesIO()
    mux = PacketFragmentMux(output, NativeFormat("h264", 320, 240, 30),
        MediaRun("windows-gpu-witness", 1000.0), audio_rate=48000,
        audio_bitrate=160000, packet_limit=1 << 20, pcm_limit=384000)
    at = 0
    try:
        for occurrence, time, duration, key, size in rows:
            mux.write_video(EncodedPicture(occurrence, time, duration, bool(key), data[at:at + size]))
            at += size
        mux.close()
    finally:
        if not mux.closed:
            mux.abort()
    assert at == len(data)
    (tmp_path / "witness.mp4").write_bytes(output.getvalue())
    with av.open(io.BytesIO(output.getvalue())) as video:
        decoded = list(video.decode(video=0))
    assert [int(frame.pts * frame.time_base * 90000) for frame in decoded] == pts
    assert len(decoded) == 6
    ys, xs = np.indices((240, 320))
    references = [np.stack([(i * 31 + 17 + xs // 40 * 13) & 255,
        (ys // 30 * 25 + 33 + i * 11) & 255,
        (xs // 40 * 17 + ys // 30 * 7 + i * 19 + 70) & 255], axis=-1).astype(float)
        for i in range(6)]
    distances = [[float(np.abs(frame.to_ndarray(format="rgb24").astype(float) - ref).mean())
                  for ref in references] for frame in decoded]
    # Picture five deliberately repeats retained picture four after replacing
    # the source texture. A new source copy would fail this independent oracle.
    expected = [0, 1, 2, 3, 3, 5]
    assert [int(np.argmin(row)) for row in distances] == expected, distances
    assert max(row[index] for row, index in zip(distances, expected, strict=True)) < 6, distances
    (tmp_path / "decoded-distances.txt").write_text(str(distances), encoding="utf-8")


def test_windows_gpu_codec_refusal_is_typed(native_host, tmp_path):
    vendor = os.environ.get("SM64_TEST_GPU_PLATFORM_VENDOR", "1002")
    result = subprocess.run([str(native_host[0]), vendor, "av1"], cwd=tmp_path,
        capture_output=True, text=True, timeout=20, check=False, **quiet_spawn_kwargs())
    # This local AMD generation has no AV1 encoder. On a newer real adapter the
    # same route must encode successfully instead; never infer from its name.
    if result.returncode == 4:
        assert "error=8" in result.stdout, result.stdout + result.stderr
    else:
        assert result.returncode == 0, result.stdout + result.stderr
