"""Periodic sampler + the JSONL persistence/record contract.

_tick() is exercised directly (sync) so the whole sample->log->alarm->persist
path is tested without the async loop or the live emulator."""
import json

from sm64_events.core.perfmon import (PerfMonitor, perf_record,
                                      start_new_session_log, write_perf_record)


def test_perf_record_trims_types_and_is_json_serialisable():
    snap = {
        "rss_bytes": 300 * 1024**2, "private_bytes": 250 * 1024**2,
        "objects": 12345, "handles": 900, "gdi_objects": 120,
        "user_objects": 80, "threads": 14, "gc": {"counts": [1, 2, 3]},
        "system": {"load_pct": 42}, "scratch_bytes": 5 * 1024**2,
        "children": {"count": 1, "rss_bytes": 400 * 1024**2, "by_name": {}},
        "types": {f"t{i}": i for i in range(100)},
    }
    growers = [{"type": "numpy.ndarray", "baseline": 10, "current": 99,
                "delta": 89}]
    rec = perf_record(snap, {"ring_bytes": 7}, uptime_s=61.4,
                      t_utc="2026-06-14T00:00:00+00:00", top_growers=growers)
    assert rec["rss_mib"] == 300.0 and rec["private_mib"] == 250.0
    assert rec["uptime_s"] == 61.4 and rec["handles"] == 900
    assert rec["gauges"] == {"ring_bytes": 7}
    assert rec["top_growers"] == growers
    assert len(rec["top_types"]) == 30          # trimmed from 100
    json.dumps(rec)                              # must not raise


def test_write_perf_record_appends_then_rotates(tmp_path):
    path = tmp_path / "perf.jsonl"
    # tiny cap so the third write trips rotation
    for i in range(3):
        write_perf_record(path, {"i": i}, max_bytes=10)
    assert path.exists()
    assert path.with_suffix(".jsonl.prev").exists()  # rotated at least once
    # the live file holds the most recent record
    last = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert last["i"] == 2


def test_write_perf_record_swallows_bad_path(tmp_path):
    # a path whose parent is a FILE can't be created — must not raise
    blocker = tmp_path / "afile"
    blocker.write_text("x")
    write_perf_record(blocker / "nope.jsonl", {"a": 1})  # no exception


def test_start_new_session_log_rotates_existing(tmp_path):
    p = tmp_path / "perf.jsonl"
    p.write_text("old-run\n", encoding="utf-8")
    start_new_session_log(p)
    assert not p.exists()                                    # current run starts fresh
    assert (tmp_path / "perf.jsonl.prev").read_text(encoding="utf-8") == "old-run\n"
    start_new_session_log(p)                                 # no file -> no error
    start_new_session_log(None)                              # disabled -> no error


def test_monitor_latest_starts_empty():
    assert PerfMonitor(perf_log_path=None).latest == {}


def test_tick_samples_persists_and_sets_baseline(tmp_path):
    path = tmp_path / "perf.jsonl"
    mon = PerfMonitor(scratch_dir=tmp_path, perf_log_path=path,
                      gauges=lambda: {"ring_bytes": 42}, interval_s=0.0)
    rec = mon._tick()
    assert rec["rss_mib"] >= 0 and rec["objects"] > 0
    assert rec["gauges"] == {"ring_bytes": 42}
    assert "rss_mib" in mon.latest and "top_types" not in mon.latest  # trimmed
    assert mon._baseline                         # first sample captured
    base_types = mon._baseline_types
    # a second tick appends and keeps the SAME baseline (growth reference)
    mon._tick()
    assert mon._baseline_types is base_types
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_tick_swallows_gauge_failure(tmp_path):
    def _boom():
        raise RuntimeError("gauge exploded")
    mon = PerfMonitor(scratch_dir=tmp_path, perf_log_path=tmp_path / "p.jsonl",
                      gauges=_boom)
    rec = mon._tick()                            # must not raise
    assert rec["gauges"] == {}
