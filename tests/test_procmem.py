"""Resource observability: the pure decision logic + the cheap samplers.

The probe functions degrade to 0/{} off-Windows, so shape assertions are
platform-tolerant; the pure helpers (assess_growth, top_type_growth,
resource_alarms) are exact everywhere — they carry the leak-attribution logic."""
import gc
import os
import weakref

import pytest

from sm64_events.core.procmem import (MachineSnapshot, ProcInfo,
                                      address_space, assess_growth,
                                      child_memory, descendants,
                                      dir_size_bytes, gc_summary, gpu_memory,
                                      handle_counts,
                                      image_is_large_address_aware,
                                      machine_snapshot, machine_summary,
                                      named_process_memory, named_processes,
                                      private_bytes, process_table,
                                      resource_alarms, sample, system_memory,
                                      thread_cpu, thread_group,
                                      top_type_growth, tree_summary,
                                      type_histogram)

_GiB = 1024 ** 3


def test_rss_or_zero_and_gc_summary_shape():
    snap = sample()
    assert snap["rss_bytes"] >= 0            # >0 on Windows, 0 if unavailable
    assert set(snap["gc"]) == {"counts", "threshold", "frozen"}
    assert len(snap["gc"]["counts"]) == 3 and len(snap["gc"]["threshold"]) == 3


def test_object_count_and_scratch_are_opt_in(tmp_path):
    (tmp_path / "a.ts").write_bytes(b"x" * 100)
    (tmp_path / "b.pcm").write_bytes(b"y" * 50)
    bare = sample()
    assert "objects" not in bare and "scratch_bytes" not in bare
    assert "private_bytes" not in bare and "system" not in bare  # resources opt-in
    full = sample(tmp_path, count_objects=True)
    assert full["objects"] > 0
    assert full["scratch_bytes"] == 150


def test_resources_opt_in_shapes():
    snap = sample(resources=True)
    assert snap["private_bytes"] >= 0           # >0 on Windows
    assert snap["threads"] >= 1                 # at least this thread
    assert isinstance(snap["system"], dict)     # {} off-Windows


def test_histogram_and_children_opt_in():
    snap = sample(histogram=True, children_of=os.getpid())
    assert snap["objects"] > 0
    # our own heap obviously contains dicts
    assert snap["types"]["builtins.dict"] > 0
    c = snap["children"]
    assert isinstance(c, dict)
    if c:  # populated on Windows
        assert set(c) >= {"count", "rss_bytes", "private_bytes", "by_name"}


def test_type_histogram_counts_a_known_object():
    class _Marker:
        pass
    keep = [_Marker() for _ in range(7)]      # noqa: F841 — held so they live
    hist = type_histogram()
    name = f"{_Marker.__module__}.{_Marker.__qualname__}"
    assert hist[name] == 7


def test_sample_releases_heap_references_before_blocking_probes(monkeypatch):
    from sm64_events.core import procmem

    class Marker:
        pass

    witnesses = []

    def snapshot():
        marker = Marker()
        witnesses.append(weakref.ref(marker))
        return [marker]

    def processes(**_kwargs):
        assert witnesses and witnesses[0]() is None
        return {}

    monkeypatch.setattr(procmem.gc, "get_objects", snapshot)
    monkeypatch.setattr(procmem, "process_table", processes)
    result = procmem.sample(histogram=True, children_of=os.getpid())
    assert result["objects"] == 1
    assert sum(result["types"].values()) == 1


def test_failed_histogram_releases_snapshot_even_while_traceback_is_retained(monkeypatch):
    from sm64_events.core import procmem
    from sm64_events.core.heap_observation import heap_lifetime_lock

    class Marker:
        pass

    witnesses = []

    def snapshot():
        marker = Marker()
        witnesses.append(weakref.ref(marker))
        return [marker]

    def failed_histogram(objects):
        assert objects
        raise ValueError("injected histogram failure")

    monkeypatch.setattr(procmem.gc, "get_objects", snapshot)
    monkeypatch.setattr(procmem, "type_histogram", failed_histogram)
    with pytest.raises(ValueError, match="injected histogram failure") as caught:
        procmem.sample(histogram=True)
    assert caught.value.__traceback__ is not None
    assert witnesses[0]() is None, "the failed observer's traceback retains the resource"
    assert heap_lifetime_lock.acquire(blocking=False), "the failed observer left shutdown blocked"
    heap_lifetime_lock.release()


def test_dir_size_tolerates_missing_dir(tmp_path):
    assert dir_size_bytes(tmp_path / "nope") == 0
    assert dir_size_bytes(tmp_path) == 0     # empty dir


def test_gc_summary_reflects_threshold():
    # gen-2 threshold pinned high (the _gcwatch fingerprint) is visible here
    assert gc_summary()["threshold"] == list(gc.get_threshold())


def test_system_memory_and_handle_counts_are_platform_tolerant():
    sysd = system_memory()
    assert isinstance(sysd, dict)
    if sysd:  # Windows
        assert 0 <= sysd["load_pct"] <= 100
        assert sysd["total_phys_bytes"] > 0
    h = handle_counts()
    assert isinstance(h, dict)
    if "handles" in h:  # Windows
        assert h["handles"] > 0


def test_child_memory_shape_for_no_children():
    # this pytest process has no relevant child; shape must still be sane
    c = child_memory(os.getpid())
    assert isinstance(c, dict)
    if c:
        assert c["count"] >= 0 and c["rss_bytes"] >= 0


def test_assess_growth_warns_only_above_floor_and_ratio():
    # doubled but tiny -> no alarm (floor not met)
    assert assess_growth(50 * 1024**2, 200 * 1024**2) is None
    # large but flat -> no alarm (ratio not met)
    assert assess_growth(3 * _GiB, 3 * _GiB) is None
    # doubled AND above floor -> alarm
    msg = assess_growth(2 * _GiB, 5 * _GiB)
    assert msg is not None and "leak" in msg
    # degenerate baselines never alarm
    assert assess_growth(0, 9 * _GiB) is None
    assert assess_growth(2 * _GiB, 0) is None


def test_top_type_growth_ranks_by_delta_and_drops_shrinkage():
    base = {"a": 1, "b": 5, "d": 100}
    cur = {"a": 10, "b": 5, "c": 3, "d": 40}
    rows = top_type_growth(base, cur, n=10)
    # b unchanged (delta 0) and d shrank are both excluded; a then c by delta
    assert [r["type"] for r in rows] == ["a", "c"]
    assert rows[0] == {"type": "a", "baseline": 1, "current": 10, "delta": 9}


def test_top_type_growth_respects_n():
    base = {}
    cur = {f"t{i}": i for i in range(1, 21)}
    assert len(top_type_growth(base, cur, n=5)) == 5


def test_resource_alarms_fire_per_class():
    # RSS relative: doubled and over floor
    msgs = resource_alarms({"rss_bytes": 2 * _GiB}, {"rss_bytes": 5 * _GiB})
    assert any("rss_bytes" in m for m in msgs)

    # handles relative: 9x and over 5000 floor
    msgs = resource_alarms({"handles": 1000}, {"handles": 9000})
    assert any("handles" in m for m in msgs)

    # GDI grew 4x but stays under the 3000 floor -> silent
    assert resource_alarms({"gdi_objects": 100}, {"gdi_objects": 400}) == []

    # child (ffmpeg) RSS is ABSOLUTE: alarms over the ceiling with NO baseline
    msgs = resource_alarms({}, {"children": {"rss_bytes": 3 * _GiB}})
    assert any("child_rss" in m for m in msgs)

    # system load is absolute pressure
    msgs = resource_alarms({}, {"system": {"load_pct": 95}})
    assert any("system memory load" in m for m in msgs)


def test_resource_alarms_quiet_when_flat_or_degenerate():
    flat = {"rss_bytes": 100 * 1024**2, "handles": 800, "threads": 12,
            "system": {"load_pct": 40}}
    assert resource_alarms(flat, flat) == []
    # zero/None current never divides or fires
    assert resource_alarms({"handles": 0}, {"handles": 0}) == []


def test_gpu_memory_shape_is_platform_tolerant():
    g = gpu_memory()
    assert isinstance(g, dict)
    if g:  # Windows with a DXGI adapter
        assert g["local_budget_bytes"] >= 0
        assert g["local_usage_bytes"] >= 0
        assert g["nonlocal_usage_bytes"] >= 0


def test_named_process_memory_matches_self():
    import sys
    exe = os.path.basename(sys.executable)        # python.exe — this process
    out = named_process_memory({exe})
    assert isinstance(out, dict)
    if out:  # Windows: at least this process matched
        slot = next(iter(out.values()))
        assert set(slot) == {"count", "rss_bytes", "private_bytes", "threads"}
        assert slot["count"] >= 1 and slot["rss_bytes"] > 0 and slot["threads"] >= 1


def test_named_process_memory_empty_for_unknown_name():
    assert named_process_memory({"definitely-not-a-real-process-xyz.exe"}) == {}


def test_process_table_one_pass_covers_children_and_named():
    import sys
    exe = os.path.basename(sys.executable)        # python.exe — this process
    pt = process_table(parent_pid=os.getpid(), names={exe})
    assert isinstance(pt, dict)
    if pt:  # Windows
        assert "children" in pt and "processes" in pt
        assert any(exe.lower() == k.lower() for k in pt["processes"])  # self matched


def test_process_table_empty_when_nothing_requested():
    assert process_table() == {}                  # no parent, no names -> no work


def test_sample_gpu_and_processes_opt_in():
    bare = sample()
    assert "gpu" not in bare and "processes" not in bare
    s = sample(gpu=True, processes={"python.exe"})
    assert isinstance(s["gpu"], dict) and isinstance(s["processes"], dict)


# -- the machine snapshot: fake tables for the arithmetic, this process for the
# probes. Times are the kernel's 100 ns ticks.
_S = 10_000_000
_MiB = 1024 ** 2


def _proc(pid, ppid, exe="x.exe", *, created=100, cpu=0, private=0, rss=0,
          threads=1, hard_faults=0, io=0):
    return ProcInfo(pid, ppid, exe, created, cpu, private, rss, 0, threads, 0,
                    hard_faults, io)


def _snap(t, procs, threads=None, cpus=(0, 0, 0, 0)):
    return MachineSnapshot(t, {p.pid: p for p in procs}, threads or {}, cpus)


def test_thread_cpu_names_each_thread_and_reports_what_exited():
    prev = _snap(0.0, [_proc(7, 1, cpu=10 * _S)], {7: {
        1: (5, 2 * _S), 2: (5, 1 * _S), 3: (5, 1 * _S), 4: (5, 3 * _S),
        9: (5, 1 * _S)}})
    curr = _snap(10.0, [_proc(7, 1, cpu=16 * _S)], {7: {
        1: (5, 4 * _S),            # MainThread: +2 s
        2: (5, 2 * _S),            # asyncio_0: +1 s
        3: (5, 2 * _S),            # asyncio_1: +1 s, grouped with asyncio_0
        4: (8, _S // 2),           # a NEW thread reusing tid 4: 0.5 s, not -2.5 s
        # tid 9 exited inside the window and took its CPU with it
    }})
    names = {1: "MainThread", 2: "asyncio_0", 3: "asyncio_1", 4: "replay-gpu-media"}
    out = thread_cpu(prev, curr, 7, lambda tid, created: names[tid])
    assert out["window_s"] == 10.0
    assert out["process_pct"] == 60.0                   # 6 s over 10 s
    assert out["threads"] == {"MainThread": 20.0, "asyncio": 20.0,
                              "replay-gpu-media": 5.0}
    assert out["unattributed_pct"] == 15.0              # 6 - 4.5 s named


def test_thread_cpu_needs_both_thread_tables():
    curr = _snap(1.0, [_proc(7, 1, cpu=_S)], {7: {1: (5, _S)}})
    assert thread_cpu(_snap(0.0, [_proc(7, 1)]), curr, 7, lambda *_: "x") == {}


def test_thread_group_collapses_pool_indices_only():
    assert thread_group("asyncio_3") == "asyncio"
    assert thread_group("ThreadPoolExecutor-0_2") == "ThreadPoolExecutor-0"
    assert thread_group("Thread-12 (run)") == "Thread-N (run)"
    assert thread_group("replay-gpu-media") == "replay-gpu-media"


def test_descendants_follow_grandchildren_and_skip_reused_pids():
    snap = _snap(0.0, [
        _proc(10, 1, "python.exe", created=100),                     # the server
        _proc(20, 10, "python.exe", created=200, private=_MiB),      # venv launcher
        _proc(30, 20, "python.exe", created=300, private=189 * _MiB,
              threads=39),                                           # encoder helper
        _proc(31, 30, "conhost.exe", created=310, private=2 * _MiB),
        _proc(40, 10, "stale.exe", created=50),   # older than pid 10: a reused ppid
        _proc(50, 1, "other.exe", created=120),
    ])
    members = descendants(snap, 10)
    assert {p.pid for p in members} == {20, 30, 31}
    tree = tree_summary(members)
    assert tree["count"] == 3 and tree["private_bytes"] == 192 * _MiB
    assert tree["threads"] == 41
    assert tree["members"][0] == {"exe": "python.exe", "pid": 30, "ppid": 20,
                                  "private_mib": 189.0, "rss_mib": 0.0,
                                  "threads": 39}


def test_descendants_keep_an_orphaned_helper_seen_before():
    # the launcher (pid 20) exited; its helper and the helper's child live on
    snap = _snap(0.0, [_proc(10, 1, created=100),
                       _proc(30, 20, "helper.exe", created=300),
                       _proc(31, 30, "conhost.exe", created=310)])
    assert descendants(snap, 10) == []
    assert {p.pid for p in descendants(snap, 10, {(30, 300)})} == {30, 31}
    assert descendants(snap, 10, {(30, 299)}) == []   # same pid, another process


def test_tree_rows_carry_cpu_over_the_window():
    prev = _snap(0.0, [_proc(10, 1), _proc(30, 10, cpu=_S)])
    curr = _snap(2.0, [_proc(10, 1), _proc(30, 10, cpu=2 * _S),
                       _proc(32, 10, "new.exe", created=150, cpu=_S // 2)])
    rows = {m["pid"]: m for m in tree_summary(descendants(curr, 10), prev,
                                              curr)["members"]}
    assert rows[30]["cpu_pct"] == 50.0      # 1 s over 2 s
    assert rows[32]["cpu_pct"] == 25.0      # born in the window: counts from 0


def test_machine_summary_ranks_the_window_not_the_lifetime():
    prev = _snap(0.0, [
        _proc(0, 0, "Idle", cpu=0),
        _proc(100, 4, "old.exe", cpu=500 * _S, private=900 * _MiB),
        _proc(200, 4, "busy.exe", cpu=10 * _S, hard_faults=10, io=0),
        _proc(300, 4, "reused.exe", created=100, cpu=50 * _S),
    ], cpus=(0, 0, 0, 0))
    curr = _snap(4.0, [
        _proc(0, 0, "Idle", cpu=12 * _S),
        _proc(100, 4, "old.exe", cpu=500 * _S + _S // 10, private=900 * _MiB),
        _proc(200, 4, "busy.exe", cpu=22 * _S, hard_faults=5010,
              io=64 * _MiB, private=50 * _MiB),
        _proc(300, 4, "reused.exe", created=999, cpu=2 * _S),  # a new process
        _proc(400, 4, "born.exe", created=500, cpu=_S),
    ], cpus=(12 * _S, 32 * _S, _S, _S // 2))  # 8 CPUs x 4 s; 12 s idle
    out = machine_summary(prev, curr, n=3)
    assert out["window_s"] == 4.0
    assert out["busy_pct"] == 62.5 and out["dpc_pct"] == 3.1
    assert out["interrupt_pct"] == 1.6
    assert [(r["exe"], r["cpu_pct"]) for r in out["top_cpu"]] == [
        ("busy.exe", 300.0), ("reused.exe", 50.0), ("born.exe", 25.0)]
    assert out["top_private"][0] == {"exe": "old.exe", "pid": 100,
                                      "private_mib": 900}
    assert out["top_hard_faults"] == [{"exe": "busy.exe", "pid": 200,
                                       "hard_faults": 5000}]
    assert out["top_io"] == [{"exe": "busy.exe", "pid": 200, "io_mib": 64.0}]
    assert all(r["exe"] != "Idle" for r in out["top_cpu"])


def test_named_processes_carry_threads_and_window_cpu():
    prev = _snap(0.0, [_proc(9, 1, "Project64.exe", cpu=_S)])
    curr = _snap(2.0, [_proc(9, 1, "Project64.exe", cpu=3 * _S, threads=31,
                             private=700 * _MiB)])
    out = named_processes(curr, {"project64.exe"}, prev)
    assert out == {"Project64.exe": {"count": 1, "rss_bytes": 0,
                                     "private_bytes": 700 * _MiB,
                                     "threads": 31, "cpu_pct": 100.0}}


def test_live_snapshot_sees_this_process_and_its_threads():
    snap = machine_snapshot(thread_pids=(os.getpid(),))
    if snap is None:
        pytest.skip("the process snapshot is Windows-only")
    me = snap.procs[os.getpid()]
    assert me.exe.lower().startswith("python")
    assert me.threads == len(snap.threads[os.getpid()]) >= 1
    import threading
    assert threading.get_native_id() in snap.threads[os.getpid()]
    assert abs(me.private_bytes - private_bytes()) < 64 * _MiB
    assert snap.cpus[1] > snap.cpus[0] > 0     # total time exceeds idle time


def test_live_tree_includes_a_grandchild(tmp_path):
    import subprocess
    import sys
    import time
    if machine_snapshot() is None:
        pytest.skip("the process snapshot is Windows-only")
    ready = tmp_path / "grandchild.pid"
    code = ("import subprocess, sys, time\n"
            "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
            f"open({str(ready)!r}, 'w').write(str(g.pid))\n"
            "g.wait()\n")
    child = subprocess.Popen([sys.executable, "-c", code],
                             creationflags=subprocess.CREATE_NO_WINDOW)
    grandchild = None
    try:
        for _ in range(200):
            if ready.exists() and ready.read_text():
                grandchild = int(ready.read_text())
                break
            time.sleep(0.02)
        assert grandchild is not None
        members = child_memory(os.getpid())["members"]
        assert grandchild in {m["pid"] for m in members}
        assert child.pid in {m["pid"] for m in members}
    finally:
        # /T ends the tree this test started (a venv launcher may sit between
        # each python and its interpreter); nothing else is below child.pid.
        subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"],
                       capture_output=True, check=False,
                       creationflags=subprocess.CREATE_NO_WINDOW)
        child.wait(5)


def test_address_space_walk_of_this_process_matches_its_virtual_size():
    snap = machine_snapshot()
    if snap is None:
        pytest.skip("the address-space walk is Windows-only")
    space = address_space(os.getpid())
    virtual = snap.procs[os.getpid()].virtual_bytes / _MiB
    assert space["vm_free_mib"] > 0
    assert 0 < space["vm_largest_free_mib"] <= space["vm_free_mib"]
    assert abs(space["vm_used_mib"] - virtual) <= 0.01 * virtual
    assert space["vm_regions"] > 10
    # a 64-bit process has no headroom question to answer
    assert address_space(os.getpid(), only_32bit=True) == {}


def _pe(tmp_path, characteristics):
    head = bytearray(512)
    head[:2] = b"MZ"
    head[0x3C:0x40] = (0x80).to_bytes(4, "little")          # e_lfanew
    head[0x80:0x84] = b"PE\0\0"
    head[0x96:0x98] = characteristics.to_bytes(2, "little")  # Characteristics
    path = tmp_path / f"img{characteristics:x}.exe"
    path.write_bytes(bytes(head))
    return str(path)


def test_large_address_aware_bit_decides_a_32bit_limit(tmp_path):
    # 0x0102 = EXECUTABLE_IMAGE | 32BIT_MACHINE, as Project64 1.6 ships
    assert image_is_large_address_aware(_pe(tmp_path, 0x0102)) is False
    assert image_is_large_address_aware(_pe(tmp_path, 0x0122)) is True
    assert image_is_large_address_aware(str(tmp_path / "missing.exe")) is False
