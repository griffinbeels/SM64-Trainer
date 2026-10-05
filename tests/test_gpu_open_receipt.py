"""Pure failed-Open formatter/retirement host; never builds or loads a GPU DLL."""
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]
CASES = {
    "unavailable_fields", "owned_fields_and_raw_maxima", "bounds_and_canaries",
    "failed_emit_still_retires_once", "truncated_format_skips_emit_but_preserves_retirement",
    "stage_labels",
}


def test_failed_open_receipt_format_and_retirement(tmp_path):
    if sys.platform != "win32":
        pytest.skip("Windows compiler host")
    spec = importlib.util.spec_from_file_location("receipt_host_build", ROOT / "tools/build_plugin.py")
    compiler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compiler)
    vcvars = compiler.find_vcvars64()
    assert vcvars, "x64 C++ compiler required for the inert scalar host"
    target = tmp_path / "encoder_open_receipt_host.exe"
    compiler._cl(vcvars, ["/nologo", "/O2", "/W4", "/MT", "/EHsc", "/std:c++17",
        f"/I{ROOT / 'plugin/gfxwrap'}", str(ROOT / "tests/native/gpu_encoder_open_receipt_host.cpp"),
        f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", "/MANIFEST:EMBED",
        "/MANIFESTUAC:level='asInvoker'"], tmp_path)
    result = subprocess.run([str(target)], cwd=tmp_path, capture_output=True, text=True,
                            encoding="utf-8", timeout=10, check=False, **quiet_spawn_kwargs())
    (tmp_path / "receipt-host.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    ran = {line.removeprefix("PASS ") for line in result.stdout.splitlines() if line.startswith("PASS ")}
    assert ran == CASES, result.stdout + result.stderr
