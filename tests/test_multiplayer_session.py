"""Shared native saved/live custody with the existing transparent model shell."""
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


def test_live_and_saved_native_sessions_share_one_model_and_cancel_independently(tmp_path):
    if sys.platform != "win32":
        pytest.skip("Win32 native model custody")
    spec = importlib.util.spec_from_file_location("live_session_build", ROOT / "tools/build_plugin.py")
    compiler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compiler)
    vcvars = compiler.find_vcvars32()
    assert vcvars, "x86 MSVC is required for the native session witness"
    native = ROOT / "plugin/gfxwrap"
    target = tmp_path / "ghost_live_session_host.exe"
    sources = ("ghost_playback.cpp", "ghost_origin.cpp", "ghost_annotation_assets.cpp",
               "ghost_live_codec.cpp", "ghost_live_input.cpp", "ghost_live_render.cpp", "ghost_live_session.cpp")
    compiler._cl(vcvars, ["/nologo", "/O2", "/W4", "/MT", "/EHsc", "/std:c++17", "/Gy",
        f"/I{native}", f"/I{ROOT / 'renderer/model'}", *[str(native / name) for name in sources],
        str(ROOT / "tests/native/ghost_live_session_host.cpp"), f"/Fe:{target}", f"/Fo{tmp_path}\\",
        "/link", "/OPT:REF", "/MANIFEST:EMBED", "/MANIFESTUAC:level='asInvoker'", *compiler.LIBS], tmp_path)
    raw = bytearray(frame())
    struct.pack_into("<H", raw, 112, 1)
    seed = tmp_path / "synthetic-live-packet.bin"
    seed.write_bytes(encode_pose(raw, participant_incarnation=bytes(range(16)), source_generation=1,
        elapsed_ns=0, continuity=1, rom_layout=1, rom_generation=3))
    result = subprocess.run([str(target), str(seed)], cwd=tmp_path, capture_output=True,
        text=True, timeout=30, check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] and report["transparent_model_shell"]
    assert report["checks"] > 50 and report["models"] == 3
    assert report["saved"] == 16 and report["live"] == 3
    assert report["render_allocations"] == report["render_frees"] == 0
