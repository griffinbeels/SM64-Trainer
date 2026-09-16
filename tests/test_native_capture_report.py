"""Native log reading is passive, identity scoped and honest about missing rows."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from native_capture_report import parse  # noqa: E402


def line(event, fields="", pid=42, epoch=7, observed=999):
    return f"2026-09-14T21:00:00Z pid={pid} epoch={epoch} observed_qpc={observed} event={event} {fields}"


def test_healthy_summary_reports_recent_stall_without_needing_failure():
    result = parse([
        line("gpu_summary", "frequency=10000000 previous_emit_ticks=50 dropped_records=0"),
        line("gpu_summary_phase", "phase=sample calls=2 total_ticks=330000 max_ticks=320000 max_started_qpc=123"),
        line("gpu_summary_cadence", "intervals=2 min_ticks=160000 max_ticks=500000"),
    ])["windows"][0]
    assert result["phases"]["sample"]["mean_ms"] == 16.5
    assert result["phases"]["sample"]["max_ms"] == 32
    assert result["previous_logging_ms"] == 0.005
    assert result["cadence"]["max_ms"] == 50
    assert "copy" in result["missing_phases"]
    assert all(value is None for value in result["resources"].values())


def test_resource_counts_preserve_zero_and_distinguish_unsupported_values():
    result = parse([
        line("gpu_summary", "snapshot_bytes=67108864 bridge_bytes=16777216 "
             "sample_calls=300 sample_reuses=0 publish_busy=invalid"),
    ])["windows"][0]["resources"]
    # `refused` (round 48) is absent from this older line: unsupported, not zero.
    assert result == dict(snapshot_bytes=67108864, bridge_bytes=16777216,
                          sample_calls=300, sample_reuses=0, publish_busy=None,
                          refused=None)


def test_old_fault_log_does_not_masquerade_as_healthy_window():
    assert parse([line("gpu_failure", "frequency=10000000"),
                  line("gpu_phase", "phase=sample calls=10 max_ticks=200000")])["windows"] == []


def test_does_not_join_stale_or_interleaved_samples_and_keeps_missing_unknown():
    rows = parse([
        line("gpu_summary", "frequency=invalid"),
        line("gpu_summary_phase", "phase=sample calls=2 max_ticks=300", epoch=6),
        line("gpu_summary_phase", "phase=copy calls=2 max_ticks=300", pid=43),
        line("gpu_summary_phase", "phase=take calls=0"),
    ])["windows"][0]
    assert list(rows["phases"]) == ["take"]
    assert rows["phases"]["take"]["mean_ms"] is None
    assert rows["phases"]["take"]["max_ms"] is None
    assert rows["cadence"] is None


def test_reader_retention_is_bounded():
    rows = parse(line("gpu_summary", f"frequency=10000000 observed_qpc={n}")
                 for n in range(150))["windows"]
    assert len(rows) == 120
    assert rows[0]["header"]["observed_qpc"] == "30"


def test_dropped_header_does_not_overwrite_previous_window():
    rows = parse([
        line("gpu_summary", "frequency=10000000"),
        line("gpu_summary_phase", "phase=sample calls=1 max_ticks=10"),
        line("gpu_summary_phase", "phase=sample calls=2 max_ticks=990", observed=1000),
        line("gpu_summary_cadence", "max_ticks=990", observed=1000),
        "pid=42 epoch=7 event=gpu_summary_phase phase=copy calls=1 max_ticks=10",
    ])["windows"][0]
    assert rows["phases"]["sample"]["calls"] == 1
    assert rows["cadence"] is None
    assert "copy" in rows["missing_phases"]
