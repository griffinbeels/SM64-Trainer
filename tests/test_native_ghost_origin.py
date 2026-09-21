"""The real bounded native origin ledger refuses guesses and ambiguous frames."""
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]


def test_native_origin_ledger(tmp_path):
    if sys.platform != "win32":
        pytest.skip("Win32 native ghost origin")
    spec = importlib.util.spec_from_file_location("build_plugin", ROOT / "tools/build_plugin.py")
    compiler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compiler)
    vcvars = compiler.find_vcvars32()
    assert vcvars, "x86 MSVC toolchain is required for this native witness"
    target = tmp_path / "ghost_origin.exe"
    compiler._cl(vcvars, ["/nologo", "/O2", "/W4", "/MT", "/EHsc", "/std:c++17",
        f"/I{ROOT / 'plugin/gfxwrap'}", str(ROOT / "plugin/gfxwrap/ghost_origin.cpp"),
        str(ROOT / "tests/native/ghost_origin_host.cpp"), f"/Fe:{target}", f"/Fo{tmp_path}\\",
        "/link", *compiler.LIBS], tmp_path)
    result = subprocess.run([str(target)], capture_output=True, text=True, timeout=10,
                            check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    assert "checks passed" in result.stdout
