"""SDK-free helper state, wire and parser checks; no live provider calls."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]


def test_eos_transport_core(tmp_path):
    if sys.platform != "win32":
        pytest.skip("MSVC EOS helper core witness")
    spec = importlib.util.spec_from_file_location("eos_core_compiler", ROOT / "tools/build_plugin.py")
    compiler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compiler)
    vcvars = compiler.find_vcvars64()
    assert vcvars, "x64 MSVC is required for the SDK-free EOS helper witness"
    target = tmp_path / "eos-core-test.exe"
    compiler._cl(vcvars, ["/nologo", "/O2", "/W4", "/WX", "/MT", "/EHsc", "/std:c++17",
        str(ROOT / "integrations/eos-transport/core_test.cpp"),
        f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", "/MANIFEST:EMBED",
        "/MANIFESTUAC:level='asInvoker'"], tmp_path)
    result = subprocess.run([str(target)], cwd=tmp_path, capture_output=True, text=True,
                            timeout=20, check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["test"] == "eos_transport_core" and report["passed"] is True
