"""Actual x86 producer layout is readable by the x64 Python diagnostics consumer."""
import importlib.util
from pathlib import Path
import struct
import subprocess
import sys

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.replay.gpufielddiagnostics import (
    STARTUP, STARTUP_CONTEXT, STARTUP_CONTEXT_OFFSET, STARTUP_OFFSET, startup_receipt)

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
    assert struct.unpack_from("<5I", result.stdout) == (4096, 128, 256, 336, STARTUP_CONTEXT_OFFSET)
    assert STARTUP_CONTEXT_OFFSET == STARTUP_OFFSET + STARTUP.size
    assert len(result.stdout) == 20 + STARTUP.size + STARTUP_CONTEXT.size
    v1, appended = result.stdout[20:20 + STARTUP.size], result.stdout[20 + STARTUP.size:]
    receipt = startup_receipt(v1, appended)
    assert receipt["adapter_luid"] == [-1, 42]
    assert receipt["gl_vendor"] == "fixture AMD"
    assert receipt["missing_entry_point"] == "glFixture"
    assert receipt["nonce"] == struct.pack("<QQ", 5, 6).hex()
    # The appended receipt is decoded by position from the x86 producer's own layout.
    assert receipt["context_gl_vendor"] == "fixture context AMD"
    assert receipt["context_gl_version"] == "4.6 context fixture"
    assert receipt["context_identity"] == "context_created"
    assert (receipt["bindings_site"], receipt["bindings_gl_error"], receipt["bindings_flags"],
            receipt["bindings_invalidations"], receipt["bindings_after_ms"], receipt["bindings_contexts"]) == (
        14, 0x502, 1, 2, 30, 4)
