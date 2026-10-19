"""Actual x86 producer layout is readable by the x64 Python diagnostics consumer."""
import importlib.util
from pathlib import Path
import struct
import subprocess
import sys

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.replay.gpufielddiagnostics import STARTUP, startup_receipt

ROOT = Path(__file__).resolve().parents[1]


def test_optional_native_startup_receipt_preserves_existing_control_offsets(tmp_path):
    if sys.platform != "win32":
        pytest.skip("Windows x86 producer compiler")
    spec = importlib.util.spec_from_file_location("startup_layout_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vcvars = build.find_vcvars32()
    assert vcvars, "x86 compiler required"
    target = tmp_path / "startup-layout.exe"
    build._cl(vcvars, ["/nologo", "/O2", "/W4", "/MT", "/EHsc", "/std:c++17",
                      f"/I{ROOT / 'plugin/gfxwrap'}", str(ROOT / "tests/native/gpu_startup_layout_host.cpp"),
                      f"/Fe:{target}", f"/Fo{tmp_path}\\"], tmp_path)
    result = subprocess.run([str(target)], capture_output=True, timeout=5, check=True, **quiet_spawn_kwargs())
    assert struct.unpack_from("<4I", result.stdout) == (4096, 128, 256, 336)
    assert len(result.stdout) == 16 + STARTUP.size
    receipt = startup_receipt(result.stdout[16:])
    assert receipt["adapter_luid"] == [-1, 42]
    assert receipt["gl_vendor"] == "fixture AMD"
    assert receipt["missing_entry_point"] == "glFixture"
    assert receipt["nonce"] == struct.pack("<QQ", 5, 6).hex()
