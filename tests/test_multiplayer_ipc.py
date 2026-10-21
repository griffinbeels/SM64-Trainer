"""The actual receiver mapping accepts only the existing local source capability."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]


def test_receiver_mapping_local_owner_replacement_and_receipt_age(tmp_path):
    if sys.platform != "win32":
        pytest.skip("Win32 receiver mapping")
    spec = importlib.util.spec_from_file_location("live_ipc_compiler", ROOT / "tools/build_plugin.py")
    compiler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compiler)
    native = ROOT / "plugin/gfxwrap"
    exe = tmp_path / "live_ipc.exe"
    compiler._cl(compiler.find_vcvars32(), ["/nologo", "/O2", "/W4", "/MT", "/EHsc", "/std:c++17",
        f"/I{native}", f"/I{ROOT / 'renderer/model'}", str(native / "ghost_live_ipc.cpp"),
        str(ROOT / "tests/native/ghost_live_ipc_host.cpp"), f"/Fe:{exe}", f"/Fo{tmp_path}\\",
        "/link", "/MANIFEST:EMBED", "/MANIFESTUAC:level='asInvoker'"], tmp_path)
    result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=15,
        check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] and report["checks"] >= 14
    assert report["header"] == 256 and report["update"] == 1692
