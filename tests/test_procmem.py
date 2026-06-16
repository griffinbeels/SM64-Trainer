"""Resource observability: the pure decision logic + the cheap samplers.

The probe functions degrade to 0/{} off-Windows, so shape assertions are
platform-tolerant; the pure helpers (assess_growth, top_type_growth,
resource_alarms) are exact everywhere — they carry the leak-attribution logic."""
import gc
import os

from sm64_events.core.procmem import (assess_growth, child_memory,
                                      dir_size_bytes, gc_summary, gpu_memory,
                                      handle_counts, named_process_memory,
                                      process_table, resource_alarms, sample,
                                      system_memory, top_type_growth,
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
        assert set(slot) == {"count", "rss_bytes", "private_bytes"}
        assert slot["count"] >= 1 and slot["rss_bytes"] > 0


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
