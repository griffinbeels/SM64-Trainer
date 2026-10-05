"""CPU-only calibration of native producer timing, independent of capture and polling."""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]


def test_callback_producer_observes_injected_stalls_and_preserves_unpolled_overflow_samples(tmp_path):
    spec = importlib.util.spec_from_file_location("callback_native_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    if not build.toolchain_available():
        pytest.skip("no x86 MSVC toolchain")
    target = tmp_path / "callback_cpu.exe"
    build._cl(build.find_vcvars32(), build.COMMON_FLAGS + [
        str(ROOT / "plugin/gfxwrap/callback_pacing_cpu_test.c"),
        f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", *build.LIBS], tmp_path)
    result = subprocess.run([str(target)], capture_output=True, text=True, timeout=10,
                            cwd=tmp_path, **quiet_spawn_kwargs(), check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    receipts = [json.loads(line) for line in result.stdout.splitlines()]
    assert receipts[0]["unpolled_callbacks"] == 8192
    assert receipts[1]["discarded_samples"] == 0 and receipts[1]["snapshot_busy"] >= 32
    assert receipts[2]["callbacks"] == 200000 and receipts[2]["mean_ns"] > 0
    lines = (tmp_path / "sm64_trainer_gfx.log").read_text(encoding="utf-8").splitlines()
    pacing = [line for line in lines if "event=callback_pacing " in line]
    assert len(pacing) == 18
    assert all("coverage=callback " in line and "build=" in line for line in pacing)
    injected = next(line for line in pacing if "callback=InjectedSleep " in line)
    assert "durations=4 " in injected and "gaps=3 " in injected
    assert "snapshot_busy=" in injected and "dropped_records=" in injected
    assert any("event=callback_progress " in line and "state=in_flight " in line for line in lines)
    spec = importlib.util.spec_from_file_location("callback_log_reader", ROOT / "tools/read_callback_pacing.py")
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    rows = list(reader.records(tmp_path / "sm64_trainer_gfx.log"))
    calibrated = next(row for row in rows if row.get("callback") == "InjectedSleep")
    assert calibrated["duration_max_ms"] >= 250
    assert len(calibrated["duration_bins"]) == 12 and sum(calibrated["duration_bins"]) == 4
    assert all(row["monitor_presentation"] == "unmeasured" for row in rows)
    baseline_target = tmp_path / "callback_baseline.exe"
    build._cl(build.find_vcvars32(), build.COMMON_FLAGS + [
        "/DCALLBACK_PACING_BASELINE_HOST", str(ROOT / "plugin/gfxwrap/callback_pacing_cpu_test.c"),
        f"/Fe:{baseline_target}", f"/Fo{tmp_path}\\", "/link", *build.LIBS], tmp_path)
    baseline = subprocess.run([str(baseline_target)], capture_output=True, text=True, timeout=10,
                              cwd=tmp_path, **quiet_spawn_kwargs(), check=False)
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    cost = json.loads(baseline.stdout)
    assert cost["callbacks"] == 200000 and cost["mean_ns"] > 0
    print(json.dumps({"producer_new_ns": receipts[2]["mean_ns"], "producer_baseline_ns": cost["mean_ns"],
                      "callbacks_per_batch": 200000}))


def test_bounded_reader_marks_missing_history(tmp_path):
    spec = importlib.util.spec_from_file_location("bounded_callback_reader", ROOT / "tools/read_callback_pacing.py")
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    target = tmp_path / "native.log"
    target.write_text("x" * 4096 + "\nevent=callback_progress coverage=callback callback=UpdateScreen "
                      "frequency=1000 age_ticks=100 state=no_new_callback\n", encoding="utf-8")
    row, = reader.records(target, max_bytes=1024)
    assert row["tail_truncated"] and row["age_ms"] == 100
    assert row["state"] == "no_new_callback" and row["monitor_presentation"] == "unmeasured"
    target.write_text("event=callback_progress coverage=callback callback=UpdateScreen "
                      "frequency=0 age_ticks=100 state=no_new_callback\n", encoding="utf-8")
    unknown, = reader.records(target)
    assert unknown["age_ms"] is None and unknown["frequency_valid"] is False
