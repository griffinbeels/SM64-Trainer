"""CPU-only calibration of native producer timing, independent of capture and polling."""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]


def assert_cpu_controls(rows, receipts):
    controls = {row["control"]: row for row in receipts if row["case"] == "cpu_calibration"}
    assert set(controls) == {"busy", "sleep", "mixed"}
    for control, receipt in controls.items():
        gap = next(row for row in rows if row.get("callback") == f"CPU{control.title()}Gap")
        duration = next(row for row in rows if row.get("callback") == f"CPU{control.title()}Duration")
        assert gap["gap_cpu_valid"] and gap["outside_cpu_valid"] and duration["duration_cpu_valid"]
        assert gap["gap_cpu_begin_qpc"] == receipt["gap_begin_qpc"] == int(gap["gap_max_started_qpc"])
        assert gap["gap_cpu_end_qpc"] == receipt["gap_end_qpc"]
        assert gap["gap_cpu_end_qpc"] - gap["gap_cpu_begin_qpc"] == int(gap["gap_max_ticks"])
        assert gap["outside_cpu_end_qpc"] == gap["gap_cpu_end_qpc"]
        assert gap["outside_thread_cycles"] == receipt["outside_cycles"]
        assert duration["duration_thread_cycles"] == receipt["duration_cycles"]
        assert duration["duration_cpu_100ns"] == receipt["duration_cpu_100ns"]
        assert gap["gap_cpu_tid"] == int(gap["tid"])
        assert gap["tid_role"] == "submitter"
        assert duration["duration_cpu_end_qpc"] - duration["duration_cpu_begin_qpc"] == int(duration["duration_max_ticks"])
    assert controls["busy"]["outside_cycles"] > controls["sleep"]["outside_cycles"] * 100
    assert controls["mixed"]["outside_cycles"] > controls["sleep"]["outside_cycles"] * 100


def test_callback_producer_observes_injected_stalls_and_preserves_unpolled_overflow_samples(tmp_path):
    spec = importlib.util.spec_from_file_location("callback_native_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    if not build.toolchain_available():
        pytest.skip("no x86 MSVC toolchain")
    target = tmp_path / "callback_cpu.exe"
    build._cl(build.find_vcvars32(), build.COMMON_FLAGS + [
        str(ROOT / "tests/native/callback_pacing_cpu_test.c"),
        f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", *build.LIBS], tmp_path)
    result = subprocess.run([str(target)], capture_output=True, text=True, timeout=10,
                            cwd=tmp_path, **quiet_spawn_kwargs(), check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    receipts = [json.loads(line) for line in result.stdout.splitlines()]
    assert receipts[0]["unpolled_callbacks"] == 8192
    assert receipts[1]["discarded_samples"] == 0 and receipts[1]["snapshot_busy"] >= 32
    assert receipts[2]["callbacks"] == 200000 and 0 < receipts[2]["mean_ns"] < 5000
    assert receipts[2]["cycle_reads"] == receipts[2]["time_reads"] == 400000
    lines = (tmp_path / "sm64_trainer_gfx.log").read_text(encoding="utf-8").splitlines()
    pacing = [line for line in lines if "event=callback_pacing " in line]
    assert len(pacing) == 26 and all(len(line.encode()) < 4096 for line in pacing)
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
    assert_cpu_controls(rows, receipts)
    final = [row for row in rows if row["event"] == "callback_final_window"]
    assert any(row["state"] == "unsent" and row["unsent_samples"] == "1" for row in final)
    assert any(row["state"] == "unavailable" and row["unsent_samples"] == "unknown" for row in final)
    assert any(row["state"] == "queued" and row["queued_samples"] == "1" for row in final)
    partial = next(row for row in rows if row.get("callback") == "UpdateScreen" and row["event"] == "callback_pacing")
    assert partial["samples"] == "1" and partial["duration_max_ms"] >= 550
    baseline_target = tmp_path / "callback_baseline.exe"
    build._cl(build.find_vcvars32(), build.COMMON_FLAGS + [
        "/DCALLBACK_PACING_BASELINE_HOST", str(ROOT / "tests/native/callback_pacing_cpu_test.c"),
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


@pytest.mark.parametrize("fields,valid,status,observed,cycles,milliseconds", [
    ("version=1", False, "no_sample", None, None, None),
    ("version=2 gaps=0 gap_cpu_valid=0 gap_cpu_reason=0 gap_cpu_100ns=0 gap_thread_cycles=0",
     False, "no_sample", 0, None, None),
    ("version=2 gap_cpu_valid=0 gap_cpu_reason=2 gap_cpu_error=5 gap_cpu_100ns=10 gap_thread_cycles=20",
     False, "query_failed", 10, None, None),
    ("version=2 gap_cpu_valid=1 gap_cpu_reason=0 gap_cpu_100ns=0 gap_thread_cycles=42000",
     True, "measured", 0, 42000, None),
    ("version=2 gap_cpu_valid=1 gap_cpu_reason=0 gap_cpu_100ns=156250 gap_thread_cycles=42000000",
     True, "measured", 156250, 42000000, 15.625),
])
def test_reader_preserves_cpu_unknown_invalid_and_quantized_zero(tmp_path, fields, valid, status,
                                                                observed, cycles, milliseconds):
    spec = importlib.util.spec_from_file_location("cpu_callback_reader", ROOT / "tools/read_callback_pacing.py")
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    target = tmp_path / "cpu.log"
    target.write_text(f"event=callback_pacing frequency=10000000 {fields}\n", encoding="utf-8")
    row, = reader.records(target)
    assert row["gap_cpu_valid"] is valid and row["gap_cpu_status"] == status
    assert row["gap_cpu_100ns"] == observed and row["gap_thread_cycles"] == cycles
    assert row["gap_cpu_ms"] == milliseconds
    assert row["gap_cpu_time_state"] == ("quantized" if milliseconds else "unresolved")
    assert not any(key.endswith("cycles_ms") or key == "wait_ms" for key in row)
