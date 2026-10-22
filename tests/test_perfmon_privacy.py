"""Normal monitoring retains app evidence without an unrelated app inventory."""
import json

from sm64_events.core import perfmon
from sm64_events.core.procmem import MachineSnapshot, ProcInfo


def snapshot(t):
    def process(pid, parent, name):
        return ProcInfo(pid, parent, name, 1, int(t * 1000000), 200, 100, 300, 2, 3, int(t), int(t * 100))
    procs = [process(10, 1, "SM64Trainer.exe"), process(20, 10, "ffmpeg.exe"),
             process(21, 10, "PrivateChildName.exe"), process(30, 1, "Project64.exe"),
             process(99, 1, "PrivateMedicalApp.exe")]
    return MachineSnapshot(t, {p.pid: p for p in procs}, {10: {100: (1, int(t * 100000))}},
                           (int(t * 10), int(t * 100), int(t * 2), int(t)))


def test_minute_stall_health_logs_and_history_exclude_unrelated_process_names(tmp_path, monkeypatch, caplog):
    snapshots = iter(snapshot(t) for t in (10.0, 20.0, 30.0))
    monkeypatch.setattr(perfmon, "machine_snapshot", lambda **kwargs: next(snapshots))
    monkeypatch.setattr(perfmon, "sample", lambda *args, **kwargs: {
        "rss_bytes": 100, "private_bytes": 200, "threads": 1, "gc": {"counts": [0, 0, 0]}})
    monkeypatch.setattr(perfmon, "address_space", lambda *args, **kwargs: {"vm_largest_free_mib": 250})
    monkeypatch.setattr(perfmon, "system_memory", lambda: {"load_pct": 40})
    path = tmp_path / "perf.jsonl"
    monitor = perfmon.PerfMonitor(self_pid=10, perf_log_path=path, stall_watch=False, stall_followup_s=0)
    monkeypatch.setattr(monitor, "_labeler", lambda: lambda *args: "replay-gpu-media")
    with caplog.at_level("INFO", logger="sm64.procmem"):
        monitor._tick()
        record = monitor._tick()
        stall = monitor._capture_stall(1.0)
    text = json.dumps([record, stall, monitor.latest]) + path.read_text() + caplog.text
    retained_names = [p.exe for history in monitor._recent for p in history.procs.values()]
    text += json.dumps(retained_names)
    assert "PrivateMedicalApp" not in text and "PrivateChildName" not in text
    assert record["machine"] == {"window_s": 10.0, "busy_pct": 90.0, "dpc_pct": 2.0, "interrupt_pct": 1.0}
    assert record["children"]["by_name"]["ffmpeg.exe"] == 100
    assert record["children"]["by_name"]["app-child"] == 100
    assert record["processes"]["Project64.exe"]["vm_largest_free_mib"] == 250
    assert "replay-gpu-media" in text
    assert "top_cpu" not in text and "top_private" not in text and "top_io" not in text
    assert stall["before"]["busy_pct"] == 90.0


def test_pure_record_builder_rejects_process_rankings_and_unknown_machine_fields():
    result = perfmon.perf_record({"machine": {
        "busy_pct": 80, "dpc_pct": 2, "interrupt_pct": 1, "window_s": 60,
        "top_cpu": [{"exe": "PrivateMedicalApp.exe"}], "machine_name": "PrivateName"}},
        {}, uptime_s=1, t_utc="2026-10-21T00:00:00Z", top_growers=[])
    assert result["machine"] == {"busy_pct": 80, "dpc_pct": 2, "interrupt_pct": 1, "window_s": 60}
    assert "Private" not in json.dumps(result)


def test_snapshot_unavailable_fallback_hides_unknown_child_images(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(perfmon, "machine_snapshot", lambda **kwargs: None)
    monkeypatch.setattr(perfmon, "sample", lambda *args, **kwargs: {"children": {
        "count": 2, "rss_bytes": 300, "private_bytes": 400,
        "by_name": {"ffmpeg.exe": 100, "PrivateChildName.exe": 200},
        "members": [{"exe": "PrivateChildName.exe", "rss_mib": 2, "path": "private"}]}})
    monitor = perfmon.PerfMonitor(self_pid=10, perf_log_path=tmp_path / "perf.jsonl", stall_watch=False)
    with caplog.at_level("INFO", logger="sm64.procmem"):
        record = monitor._tick()
    text = json.dumps([record, monitor.latest, monitor._baseline]) + caplog.text
    assert "PrivateChildName" not in text and "path" not in record["children"]["members"][0]
    assert record["children"]["by_name"] == {"ffmpeg.exe": 100, "app-child": 200}
    assert record["children"]["rss_bytes"] == 300
