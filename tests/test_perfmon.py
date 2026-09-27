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
        "os_threads": 52, "cpu": {"process_pct": 12.5},
        "machine": {"top_cpu": [{"exe": "a.exe", "pid": 1, "cpu_pct": 90.0}]},
    }
    growers = [{"type": "numpy.ndarray", "baseline": 10, "current": 99,
                "delta": 89}]
    rec = perf_record(snap, {"ring_bytes": 7}, uptime_s=61.4,
                      t_utc="2026-06-14T00:00:00+00:00", top_growers=growers)
    assert rec["rss_mib"] == 300.0 and rec["private_mib"] == 250.0
    assert rec["uptime_s"] == 61.4 and rec["handles"] == 900
    assert rec["gauges"] == {"ring_bytes": 7}
    assert rec["os_threads"] == 52 and rec["cpu"] == {"process_pct": 12.5}
    assert rec["machine"]["top_cpu"][0]["exe"] == "a.exe"
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


def test_disabled_monitor_does_nothing(tmp_path):
    import asyncio
    path = tmp_path / "perf.jsonl"
    mon = PerfMonitor(perf_log_path=path, enabled=False)
    asyncio.run(mon.run())                       # returns at once, no sampling
    assert mon.latest == {} and not path.exists()


def test_tick_samples_persists_and_sets_baseline(tmp_path):
    path = tmp_path / "perf.jsonl"
    mon = PerfMonitor(scratch_dir=tmp_path, perf_log_path=path,
                      gauges=lambda: {"ring_bytes": 42}, interval_s=0.0)
    rec = mon._tick()
    assert rec["rss_mib"] >= 0 and rec["objects"] is None
    assert rec["sampling"]["mode"] == "light"
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


def test_light_monitor_never_walks_heap_disk_or_gpu(monkeypatch, tmp_path, caplog):
    from sm64_events.core import procmem

    def forbidden(*args, **kwargs):
        raise AssertionError("expensive probe during routine sampling")

    monkeypatch.setattr(procmem.gc, "get_objects", forbidden)
    monkeypatch.setattr(procmem, "gpu_memory", forbidden)
    monkeypatch.setattr(procmem, "dir_size_bytes", forbidden)
    with caplog.at_level("INFO", logger="sm64.procmem"):
        record = PerfMonitor(scratch_dir=tmp_path, perf_log_path=None, deep=False)._tick()
    assert record["objects"] is None and record["gpu"] is None
    assert record["scratch_mib"] is None and record["top_types"] == {}
    assert record["system"] is not None
    assert "gpu=unmeasured" in caplog.text and "scratch=unmeasured" in caplog.text


def test_deep_monitor_remains_available(monkeypatch, tmp_path):
    monkeypatch.setenv("SM64_PERFMON_DEEP", "1")
    record = PerfMonitor(scratch_dir=tmp_path, perf_log_path=None)._tick()
    assert record["objects"] > 0 and record["top_types"]
    assert record["sampling"]["mode"] == "deep"


def test_slow_probe_does_not_block_event_loop_and_gauges_stay_on_owner(monkeypatch):
    import asyncio
    import threading
    from sm64_events.core import perfmon

    entered, release = threading.Event(), threading.Event()
    owner = threading.get_ident()

    def collect():
        assert threading.get_ident() != owner
        entered.set()
        assert release.wait(2)
        return {}

    def gauges():
        assert threading.get_ident() == owner
        return {"owner": True}

    monitor = PerfMonitor(perf_log_path=None, gauges=gauges)
    monkeypatch.setattr(monitor, "_collect", collect)
    monkeypatch.setattr(perfmon, "start_new_session_log", lambda _: None)

    async def run():
        task = asyncio.create_task(monitor.run())
        try:
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(.005)
            assert entered.is_set()
            release.set()
            for _ in range(100):
                if monitor.latest:
                    break
                await asyncio.sleep(.005)
            assert monitor.latest["gauges"] == {"owner": True}
        finally:
            release.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(run())


def test_cancel_waits_for_its_inflight_sample(monkeypatch):
    import asyncio
    import threading
    import pytest

    entered, release = threading.Event(), threading.Event()
    monitor = PerfMonitor(perf_log_path=None)

    def collect():
        entered.set()
        assert release.wait(2)
        return {}

    monkeypatch.setattr(monitor, "_collect", collect)

    async def run():
        task = asyncio.create_task(monitor.run())
        try:
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(.005)
            assert entered.is_set()
            task.cancel()
            await asyncio.sleep(.01)
            assert not task.done() and not monitor.latest
            task.cancel()
            await asyncio.sleep(.01)
            assert not task.done() and not monitor.latest
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert monitor.latest

    asyncio.run(run())


def _spin(seconds):
    import time
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        pass


def _windows_snapshot_or_skip():
    import pytest
    from sm64_events.core.procmem import machine_snapshot
    if machine_snapshot() is None:
        pytest.skip("the process snapshot is Windows-only")


def test_minute_record_attributes_process_cpu_to_named_threads():
    import threading
    _windows_snapshot_or_skip()
    mon = PerfMonitor(perf_log_path=None, stall_watch=False)
    first = mon._tick()
    assert first["cpu"] is None and first["machine"] is None  # no window yet
    done = threading.Event()

    def media():
        _spin(0.3)
        done.wait(5)

    worker = threading.Thread(target=media, name="replay-gpu-media")
    worker.start()
    try:
        _spin(0.1)
        rec = mon._tick()
    finally:
        done.set()
        worker.join()
    cpu = rec["cpu"]
    assert cpu["threads"]["replay-gpu-media"] >= 20       # ~0.3 s of ~0.4 s
    assert cpu["threads"]["MainThread"] > 0
    named = sum(cpu["threads"].values())
    assert abs(named - cpu["process_pct"]) <= 0.1 * cpu["process_pct"]
    assert rec["os_threads"] >= threading.active_count()
    assert rec["machine"]["top_cpu"] and "busy_pct" in rec["machine"]
    assert rec["stalls"] is None                          # the watch never ran
    json.dumps(rec)


def test_minute_record_carries_the_emulators_address_space(monkeypatch):
    import os
    import sys
    from sm64_events.core import perfmon
    _windows_snapshot_or_skip()
    exe = os.path.basename(sys.executable)
    fakes = iter([{"vm_largest_free_mib": 300.0, "vm_free_mib": 500.0},
                  {"vm_largest_free_mib": 145.5, "vm_free_mib": 306.0}]
                 + [{} for _ in range(64)])
    walked = []

    def space(pid, *, only_32bit):
        assert only_32bit                     # a 64-bit process is not walked
        walked.append(pid)
        return next(fakes)

    monkeypatch.setattr(perfmon, "address_space", space)
    rec = PerfMonitor(perf_log_path=None, stall_watch=False,
                      watch_processes=(exe,))._tick()
    slot = next(iter(rec["processes"].values()))
    assert os.getpid() in walked
    # several instances: the one closest to full is reported
    expected = 145.5 if len(walked) > 1 else 300.0
    assert slot["vm_largest_free_mib"] == expected
    assert slot["threads"] >= 1


def test_stall_watch_counts_every_late_wake_but_captures_once_per_cooldown():
    import threading
    from sm64_events.core.perfmon import StallWatch
    captured = []
    watch = StallWatch(captured.append, threading.Event(), threshold_s=0.5,
                       cooldown_s=30.0)
    assert not watch.observe(10.0, 0.2)       # ordinary scheduling jitter
    assert watch.observe(11.0, 1.2)
    assert not watch.observe(20.0, 3.5)       # counted, inside the cooldown
    assert watch.observe(41.5, 0.6)
    assert captured == [1.2, 0.6]
    assert watch.take_counts() == {"count": 3, "worst_late_ms": 3500}
    assert watch.take_counts() == {"count": 0, "worst_late_ms": 0}


def test_a_slow_capture_is_not_counted_as_another_stall():
    import threading
    import time
    from sm64_events.core.perfmon import StallWatch
    offset = [0.0]
    stop = threading.Event()
    watch = StallWatch(lambda _late: time.sleep(0.8), stop, period_s=0.05,
                       threshold_s=0.5,
                       clock=lambda: time.perf_counter() + offset[0])
    watch.start()
    try:
        time.sleep(0.1)
        offset[0] += 1.0        # the next wake reads one second late
        time.sleep(1.2)         # the 0.8 s capture, then ordinary wakes
    finally:
        stop.set()
        watch.join(1.0)
    assert watch.take_counts()["count"] == 1


def test_a_failing_capture_does_not_stop_the_watch():
    import threading
    from sm64_events.core.perfmon import StallWatch

    def boom(_late):
        raise RuntimeError("snapshot exploded")

    watch = StallWatch(boom, threading.Event(), cooldown_s=0.0)
    assert watch.observe(1.0, 2.0) and watch.observe(2.0, 2.0)


def test_stall_baseline_is_the_last_snapshot_before_the_stall_began():
    from sm64_events.core.procmem import MachineSnapshot
    mon = PerfMonitor(perf_log_path=None, stall_watch=False)
    snaps = [MachineSnapshot(t, {}, {}, (0, 0, 0, 0)) for t in (10.0, 50.0, 70.0)]
    mon._recent.extend(snaps)
    assert mon._stall_baseline(60.0) is snaps[1]     # 70 s fell inside the stall
    assert mon._stall_baseline(5.0) is snaps[0]      # nothing older: the oldest


def test_a_real_stall_writes_a_record_naming_who_was_busy(tmp_path):
    """End to end: the interpreter is held for ~1 s (as the 4.8 s GC held it on
    09-25); the heartbeat wakes late and the log gains a stall record whose
    before-window names the thread that held it."""
    import asyncio
    import sys
    _windows_snapshot_or_skip()
    path = tmp_path / "perf.jsonl"
    mon = PerfMonitor(perf_log_path=path, interval_s=3600, stall_followup_s=0.2)
    mon._stalls._period = 0.1   # a wake is due early in the held second

    def stalls():
        if not path.exists():
            return []
        return [r for r in map(json.loads, path.read_text("utf-8").splitlines())
                if r.get("kind") == "stall"]

    async def run():
        task = asyncio.create_task(mon.run())
        try:
            for _ in range(300):
                if mon.latest:
                    break
                await asyncio.sleep(0.01)
            assert mon.latest, "the first minute sample never landed"
            old = sys.getswitchinterval()
            sys.setswitchinterval(1.0)       # this thread keeps the interpreter
            try:
                _spin(1.2)
            finally:
                sys.setswitchinterval(old)
            for _ in range(300):
                if stalls():
                    break
                await asyncio.sleep(0.01)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(run())
    found = stalls()
    assert len(found) == 1
    stall = found[0]
    assert stall["late_ms"] >= 500
    assert stall["before"]["self"]["threads"]["MainThread"] >= 50
    assert stall["before"]["top_cpu"] and stall["after"]["window_s"] > 0
    assert stall["system"] and stall["capture_ms"] > 0
    assert not mon._stalls.running           # shutdown ended the heartbeat


def test_real_recorder_gauges_do_not_enter_full_status_or_storage(monkeypatch, tmp_path):
    from test_replay_recorder import make_recorder, FakeVideoSource, FakeAudioSource
    from sm64_events.server.app import _create_monitor
    from types import SimpleNamespace

    class Poller:
        def perf_stats(self):
            return {"ticks": 17}

    class Replay:
        recorder = make_recorder(tmp_path, FakeVideoSource(), FakeAudioSource())
        cfg = SimpleNamespace(scratch_dir=tmp_path)

    def forbidden(*args):
        raise AssertionError("gauge entered storage")

    monkeypatch.setattr(Replay.recorder, "status", forbidden)
    monkeypatch.setattr(Replay.recorder.fragments, "coverage", forbidden)
    Replay.recorder._audio_mode = "process"
    monitor = _create_monitor(Poller(), Replay())
    assert monitor._read_gauges() == {"ticks": 17, "ring_bytes": 0, "idle": False,
                                       "recording": False, "audio_mode": "process"}
