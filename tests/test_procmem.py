"""Memory/disk observability: the pure decision logic + the cheap samplers."""
import gc

from sm64_events.core.procmem import (MemoryMonitor, assess_growth,
                                       dir_size_bytes, gc_summary, sample)

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
    full = sample(tmp_path, count_objects=True)
    assert full["objects"] > 0
    assert full["scratch_bytes"] == 150


def test_dir_size_tolerates_missing_dir(tmp_path):
    assert dir_size_bytes(tmp_path / "nope") == 0
    assert dir_size_bytes(tmp_path) == 0     # empty dir


def test_gc_summary_reflects_threshold():
    # gen-2 threshold pinned high (the _gcwatch fingerprint) is visible here
    assert gc_summary()["threshold"] == list(gc.get_threshold())


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


def test_monitor_latest_starts_empty_and_baseline_is_first_sample():
    mon = MemoryMonitor(interval_s=0.0)
    assert mon.latest == {}
    # one manual sample mimics what run() does on its first pass
    mon.latest = sample(count_objects=True)
    assert "rss_bytes" in mon.latest and "objects" in mon.latest
