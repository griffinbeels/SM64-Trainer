"""Actual reusable native Renderer/Input contract, with a transparent pose shell.

This fast witness opens no window, ROM, GPU, emulator, SDK or service. The real
ROM/GPU witness remains a separate root-owned integration gate.
"""
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys

import pytest

from ghost_native_fixture import frame
from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.multiplayer.protocol import encode_pose

ROOT = Path(__file__).resolve().parents[1]


def test_native_live_renderer_borrows_model_and_rechecks_every_included_seat(tmp_path):
    if sys.platform != "win32":
        pytest.skip("Win32 native renderer-owner witness")
    spec = importlib.util.spec_from_file_location("live_render_build", ROOT / "tools/build_plugin.py")
    compiler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compiler)
    vcvars = compiler.find_vcvars32()
    assert vcvars, "x86 MSVC is required for the native live renderer witness"
    native = ROOT / "plugin/gfxwrap"
    target = tmp_path / "ghost_live_render_host.exe"
    compiler._cl(vcvars, ["/nologo", "/O2", "/W4", "/MT", "/EHsc", "/std:c++17", "/Gy",
        f"/I{native}", f"/I{ROOT / 'renderer/model'}",
        str(native / "ghost_live_codec.cpp"), str(native / "ghost_live_input.cpp"),
        str(native / "ghost_live_render.cpp"), str(ROOT / "tests/native/ghost_live_render_host.cpp"),
        f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", "/OPT:REF", "/MANIFEST:EMBED",
        "/MANIFESTUAC:level='asInvoker'", *compiler.LIBS], tmp_path)
    raw = bytearray(frame())
    struct.pack_into("<H", raw, 112, 1)  # independently witnessed GC8 animTimer byte offset
    packet = encode_pose(raw, participant_incarnation=bytes(range(16)), source_generation=1,
        elapsed_ns=0, continuity=1, rom_layout=1, rom_generation=3)
    seed = tmp_path / "synthetic-live-packet.bin"
    seed.write_bytes(packet)
    result = subprocess.run([str(target), str(seed)], cwd=tmp_path, capture_output=True,
        text=True, timeout=30, check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] and report["transparent_model_shell"]
    assert not report["real_model"] and not report["gpu"]
    assert report["max_peers"] == 3 and report["model_calls"] > 10 and report["checks"] > 100
    assert report["render_allocations"] == report["render_frees"] == 0
