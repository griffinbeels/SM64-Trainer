"""Reject misleading measurements and preserve ownership at profiler boundaries."""
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from contextlib import nullcontext

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import profile_capture as capture  # noqa: E402
import profile_report as report  # noqa: E402


def test_default_process_sampling_does_not_enumerate_system_threads(monkeypatch):
    def forbidden():
        pytest.fail("thread count queries can enumerate system process information on Windows")

    process = SimpleNamespace(
        pid=42, cpu_percent=lambda: 3.0, is_running=lambda: True,
        oneshot=nullcontext, create_time=lambda: 123.0, name=lambda: "fixture",
        io_counters=lambda: SimpleNamespace(read_bytes=5, write_bytes=6),
        memory_info=lambda: SimpleNamespace(rss=7), num_threads=forbidden,
    )
    module = SimpleNamespace(Process=lambda pid: process, cpu_percent=lambda: 1.0,
                             virtual_memory=lambda: SimpleNamespace(used=8), Error=OSError)
    monkeypatch.setitem(sys.modules, "psutil", module)
    sampled = capture.SystemSampler([42]).sample()
    assert sampled["processes"] == [dict(pid=42, created=123.0, name="fixture",
                                       cpu_percent=3.0, rss_bytes=7, threads=None,
                                       read_bytes=5, write_bytes=6)]


@pytest.mark.parametrize("url", ["https://localhost:8065", "http://example.com:8065",
                                     "http://user@localhost:8065", "http://localhost",
                                     "http://localhost:8065/state", "http://localhost:8065?x=1"])
def test_capture_requires_explicit_local_origin(url):
    with pytest.raises(ValueError):
        capture.local_url(url)


def test_local_ipv6_and_redirect_refusal():
    assert capture.local_url("http://[::1]:8065/") == "http://[::1]:8065"
    with pytest.raises(ValueError, match="redirect"):
        capture.NoRedirect().redirect_request(None, None, None, None, None, "http://outside/")


def test_missing_is_not_zero_and_counter_reset_is_not_negative_work():
    assert report.distribution([]) is None
    assert report.distribution([1] * 90 + [200] * 10)["p95"] == 200
    assert report.counter_delta([10, 15, 2]) == {"delta": None, "resets": 1, "samples": 3}


def capture_folder(tmp_path):
    initial = {"session_id": "ours", "stages": {}, "counters": {}}
    stage = {"count": 100, "mean_ms": 2, "p50_ms": 1, "p95_ms": 8, "p99_ms": 16, "max_ms": 20,
             "quantile_method": "histogram_upper_bound"}
    final = {"session_id": "ours", "stages": {"encode": stage}, "counters": {"drops": 3}}
    meta = {"version": 1, "complete": True, "errors": [], "profile_session": "ours",
            "initial_profile": initial, "final_profile": final, "workload": {"scenario": "same"},
            "machine": {"host": "same"}, "sampling": {"seconds": 2}, "wake_excess_ms": [1, 2]}
    (tmp_path / "capture.json").write_text(json.dumps(meta), encoding="utf-8")
    samples = [{"elapsed_s": i, "profile": final, "replay": {}, "observer_ms": 1,
                "system": {"cpu_percent": 5, "memory_used_bytes": 100, "processes": []}}
               for i in (0, 1)]
    (tmp_path / "samples.jsonl").write_text("\n".join(map(json.dumps, samples)), encoding="utf-8")
    return meta, samples


def test_report_uses_final_histogram_not_average_of_snapshots(tmp_path):
    capture_folder(tmp_path)
    result = report.summarize(tmp_path)
    assert result["valid"], result["issues"]
    assert result["stages"]["encode"]["count"] == 100
    assert result["counters"]["drops"]["delta"] == 3
    assert result["gpu"] is None
    assert report.compare(result, result)["comparable"]


@pytest.mark.parametrize("field", ["workload", "machine", "sampling", "version"])
def test_comparison_rejects_configuration_changes(tmp_path, field):
    capture_folder(tmp_path)
    before = report.summarize(tmp_path)
    after = copy.deepcopy(before)
    after["metadata"][field] = "different"
    compared = report.compare(before, after)
    assert not compared["comparable"]
    assert compared["changes"] == {}


def test_restart_missing_snapshot_and_reset_invalidate_comparison(tmp_path):
    _meta, samples = capture_folder(tmp_path)
    samples[0]["profile"] = {"session_id": "other", "counters": {"drops": 9}, "stages": {}}
    (tmp_path / "samples.jsonl").write_text("\n".join(map(json.dumps, samples)), encoding="utf-8")
    result = report.summarize(tmp_path)
    assert not result["valid"]
    assert result["counters"]["drops"]["resets"] == 1
    assert any("session changed" in issue for issue in result["issues"])


def test_active_capture_is_never_replaced(tmp_path, monkeypatch):
    workload = tmp_path / "workload.json"
    workload.write_text(json.dumps(dict.fromkeys(
        ("scenario", "rom", "save_state", "renderer", "resolution", "settings", "ambient"), "same")))
    calls = []

    def request(_origin, path, body=None):
        calls.append((path, body))
        return {"enabled": True}

    monkeypatch.setattr(capture, "request", request)
    args = SimpleNamespace(url="http://localhost:8065", workload=workload, seconds=30, interval=1)
    with pytest.raises(ValueError, match="already active"):
        capture.capture(args)
    assert calls == [("/health", None), (capture.PROFILE, None)]


def test_wpr_cleanup_can_only_target_owned_named_instance(tmp_path, monkeypatch):
    calls = []

    def command(args, timeout=15):
        calls.append(args)
        return "GeneralProfile First level triage\nGPU GPU activity" if "-profiles" in args else "-instancename"

    monkeypatch.setattr(capture.shutil, "which", lambda _name: "wpr.exe")
    monkeypatch.setattr(capture, "command", command)
    traces = capture.ExternalTraces(tmp_path)
    traces.start(1, True, None)
    assert traces.close() == []
    mutations = [c for c in calls if any(action in c for action in ("-start", "-stop", "-cancel"))]
    assert all(c[-2:] == ["-instancename", traces.instance] for c in mutations)
    assert all("-filemode" not in c for c in mutations)
    assert mutations[-1][1] == "-stop"
    assert len(traces.profiles) == 1 and traces.profiles[0].endswith("replay.wprp!Replay.Light")
    assert traces.profiles[0] in mutations[0]
    assert "GeneralProfile" not in mutations[0]
    import xml.etree.ElementTree as ET
    tree = ET.parse(traces.profiles[0].split("!")[0])
    collectors = tree.findall(".//SystemCollector") + tree.findall(".//EventCollector")
    total_kib = sum(int(row.find("BufferSize").attrib["Value"])
                    * int(row.find("Buffers").attrib["Value"]) for row in collectors)
    assert total_kib <= 128 * 1024
    assert traces.missing and "coverage" in traces.missing[0]


def test_failed_wpr_start_never_cancels_another_capture(tmp_path, monkeypatch):
    calls = []

    def command(args, timeout=15):
        calls.append(args)
        if "-start" in args:
            raise RuntimeError("foreign kernel collector busy")
        return "-instancename"

    monkeypatch.setattr(capture.shutil, "which", lambda _name: "wpr.exe")
    monkeypatch.setattr(capture, "command", command)
    traces = capture.ExternalTraces(tmp_path)
    with pytest.raises(RuntimeError):
        traces.start(1, True, None)
    traces.close()
    assert all(call[-2:] == ["-instancename", traces.instance]
               for call in calls if "-cancel" in call or "-stop" in call)


def test_graphics_generation_change_is_not_merged_into_one_histogram(tmp_path):
    _meta, samples = capture_folder(tmp_path)
    for i, sample in enumerate(samples):
        sample["replay"] = {"frame_source_health": {"graphics_profile": {
            "version": 1, "plugin_pid": 12, "generation": str(i), "metrics": {
                "gl_read_pixels": {"count": 1, "mean_ms": 7, "max_ms": 7}}}}}
    (tmp_path / "samples.jsonl").write_text("\n".join(map(json.dumps, samples)), encoding="utf-8")
    result = report.summarize(tmp_path)
    assert not result["valid"]
    assert result["graphics"]["metrics"]["gl_read_pixels"]["count"] == 1
    assert "Native graphics profile generation changed" in result["issues"]


def test_replay_loss_reset_and_backlog_remain_visible(tmp_path):
    _meta, samples = capture_folder(tmp_path)
    samples[0]["replay"] = {"grabs_skipped": 9, "encode_backlog": 3}
    samples[1]["replay"] = {"grabs_skipped": 1, "encode_backlog": 12}
    (tmp_path / "samples.jsonl").write_text("\n".join(map(json.dumps, samples)), encoding="utf-8")
    result = report.summarize(tmp_path)
    assert not result["valid"]
    assert result["replay"]["counters"]["grabs_skipped"]["delta"] is None
    assert result["replay"]["gauges"]["encode_backlog"]["max"] == 12
    assert result["coverage"]["native_graphics"] is False


def test_backend_histogram_definition_must_match(tmp_path):
    capture_folder(tmp_path)
    before = report.summarize(tmp_path)
    after = copy.deepcopy(before)
    after["histogram_definition"] = {"buckets_ms": [123]}
    assert not report.compare(before, after)["comparable"]
