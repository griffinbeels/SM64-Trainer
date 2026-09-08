"""Optional host integration: owned ETW and Python sampling, never a live recorder."""
import ctypes
import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from profile_capture import ExternalTraces, tool_path  # noqa: E402
from profile_etl import export  # noqa: E402


@pytest.mark.skipif(os.name != "nt", reason="Windows ETW integration")
def test_installed_wpr_and_xperf_roundtrip(tmp_path):
    if not tool_path("wpr") or not tool_path("xperf"):
        pytest.skip("Windows Performance Toolkit is not installed")
    if not ctypes.windll.shell32.IsUserAnAdmin():
        pytest.skip("WPR recording needs an elevated host; unit ownership tests still apply")
    traces = ExternalTraces(tmp_path)
    try:
        traces.start(2, True, None)
        time.sleep(2)
    finally:
        errors = traces.close()
    assert errors == [], errors
    assert (tmp_path / "system.etl").stat().st_size > 0
    report = export(tmp_path / "system.etl", tmp_path / "analysis", actions=["tracestats", "profile"], timeout=30)
    assert report["complete"], report
    assert len(report["actions"]) == 2


def test_installed_python_stack_sampler_attaches_to_owned_test(tmp_path):
    if not tool_path("py-spy"):
        pytest.skip("Optional profiling dependency group is not installed")
    traces = ExternalTraces(tmp_path)
    try:
        traces.start(2, False, os.getpid())
        # Spend some time in Python as an independent stack-sampling witness.
        deadline = time.monotonic() + 2.5
        while time.monotonic() < deadline:
            sum(i * i for i in range(1000))
    finally:
        errors = traces.close()
    assert errors == [], errors
    profile = json.loads((tmp_path / "python-stacks.json").read_text())
    assert profile["profiles"], profile
    assert any(row.get("samples") for row in profile["profiles"]), profile
