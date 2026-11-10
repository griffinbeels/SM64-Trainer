"""Compare the real compiled experimental header with the actual Python codec."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.multiplayer import protocol

ROOT = Path(__file__).resolve().parents[1]


def test_compiled_live_layout_matches_python(tmp_path):
    if sys.platform != "win32":
        pytest.skip("MSVC layout witness")
    spec = importlib.util.spec_from_file_location("build_plugin", ROOT / "tools/build_plugin.py")
    compiler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compiler)
    vcvars = compiler.find_vcvars32()
    assert vcvars, "x86 MSVC is required for the native receiver layout witness"
    target = tmp_path / "ghost_live_layout_host.exe"
    compiler._cl(vcvars, ["/nologo", "/W4", "/MT", "/EHsc", "/std:c++17",
        f"/I{ROOT / 'plugin/gfxwrap'}", f"/I{ROOT / 'renderer/model'}",
        str(ROOT / "tests/native/ghost_live_layout_host.cpp"),
        f"/Fe:{target}", f"/Fo{tmp_path}\\"], tmp_path)
    result = subprocess.run([str(target)], cwd=tmp_path, capture_output=True,
                            text=True, timeout=10, check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "header": protocol.HEADER.size, "packet": protocol.PACKET_BYTES,
        "core": 64, "held": 372, "pad": 416, "valid_fields": protocol.VALID_FIELDS,
        "version": protocol.VERSION, "peers": protocol.MAX_PEERS,
    }
