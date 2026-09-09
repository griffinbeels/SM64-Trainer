from datetime import datetime, timedelta, timezone
from pathlib import Path
import threading

import pytest

from sm64_events.replay.ring import SegmentInfo, SegmentRing, effective_cap

T0 = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)


def seg(tmp_path, i, kind="video", length_s=2.0, size=100):
    p = tmp_path / f"{kind}_{i:06d}.bin"
    p.write_bytes(b"x" * size)
    start = T0 + timedelta(seconds=i * length_s)
    return SegmentInfo(path=p, kind=kind, utc_start=start,
                       utc_end=start + timedelta(seconds=length_s),
                       size_bytes=size)


def test_covering_selects_overlapping_only(tmp_path):
    ring = SegmentRing(retention_s=None, max_bytes=10**9)
    for i in range(5):
        ring.add(seg(tmp_path, i))
    got = ring.covering("video", T0 + timedelta(seconds=3),
                        T0 + timedelta(seconds=7))
    assert [s.path.name for s in got] == ["video_000001.bin", "video_000002.bin",
                                          "video_000003.bin"]


def test_late_old_segment_cannot_move_coverage_backward_or_evict_newer_footage(tmp_path):
    ring = SegmentRing(retention_s=None, max_bytes=250)
    oldest, middle, newest = [seg(tmp_path, i) for i in range(3)]
    for item in [oldest, newest, middle]:
        ring.add(item)
    assert ring.coverage("video") == (middle.utc_start, newest.utc_end)
    assert ring.covering("video", T0, newest.utc_end) == [middle, newest]
    assert not oldest.path.exists()
    assert middle.path.exists() and newest.path.exists()


def test_retention_evicts_and_deletes_files(tmp_path):
    ring = SegmentRing(retention_s=4.0, max_bytes=10**9)
    segs = [seg(tmp_path, i) for i in range(5)]
    for s in segs:
        ring.add(s)
    # newest end = T0+10 s; retention 4 s keeps segments ending after T0+6 s
    assert not segs[0].path.exists() and not segs[1].path.exists()
    assert not segs[2].path.exists()   # ends exactly at horizon -> evicted (<=)
    assert segs[3].path.exists()
    assert segs[4].path.exists()
    cov = ring.coverage("video")
    assert cov is not None and cov[1] == segs[4].utc_end


def test_disk_cap_evicts_oldest_regardless_of_retention(tmp_path):
    ring = SegmentRing(retention_s=None, max_bytes=250)
    segs = [seg(tmp_path, i, size=100) for i in range(4)]
    for s in segs:
        ring.add(s)
    assert ring.total_bytes <= 250
    assert not segs[0].path.exists() and segs[3].path.exists()


def test_effective_cap_configured_wins_when_disk_is_plentiful():
    # 1 TiB free, holding 1 GiB, configured 20 GiB -> configured wins
    assert effective_cap(20 * 1024**3, 1024**4, 1024**3,
                         margin_bytes=5 * 1024**3) == 20 * 1024**3


def test_effective_cap_disk_limits_below_configured():
    # only 8 GiB free, margin 5 GiB, holding 2 GiB -> grow to 2+(8-5)=5 GiB
    assert effective_cap(20 * 1024**3, 8 * 1024**3, 2 * 1024**3,
                         margin_bytes=5 * 1024**3) == 5 * 1024**3


def test_effective_cap_reclaims_when_free_below_margin():
    # free already under margin -> cap drops below current_total -> evict
    cap = effective_cap(20 * 1024**3, 1 * 1024**3, 4 * 1024**3,
                        margin_bytes=5 * 1024**3)
    assert cap == 4 * 1024**3 + (1 * 1024**3 - 5 * 1024**3)  # = 0


def test_disk_gate_caps_buffer_to_volume_minus_margin(tmp_path):
    """A configured cap far above the disk size must NOT fill the volume:
    eviction holds free space at the margin. Models a 500-byte volume whose
    only consumer is the buffer; margin 100 -> buffer parks at 400."""
    ring = SegmentRing(retention_s=None, max_bytes=10**9,
                       free_bytes_fn=lambda: max(0, 500 - ring.total_bytes),
                       disk_margin_bytes=100)
    segs = [seg(tmp_path, i, size=100) for i in range(6)]
    for s in segs:
        ring.add(s)
    assert ring.total_bytes == 400                 # volume(500) - margin(100)
    assert not segs[0].path.exists() and not segs[1].path.exists()
    assert segs[5].path.exists()                   # newest kept


def test_coverage_none_when_empty():
    ring = SegmentRing(retention_s=None, max_bytes=10**9)
    assert ring.coverage("video") is None


def test_set_limits_applies_live_and_evicts_immediately(tmp_path):
    """UI settings panel contract: shrinking a limit frees disk NOW, not at
    the next segment add."""
    ring = SegmentRing(retention_s=None, max_bytes=10**9)
    segs = [seg(tmp_path, i, size=100) for i in range(5)]
    for s in segs:
        ring.add(s)
    assert ring.total_bytes == 500

    ring.set_limits(retention_s=None, max_bytes=250)  # user shrank the cap
    assert ring.total_bytes <= 250
    assert not segs[0].path.exists() and not segs[1].path.exists()
    assert segs[4].path.exists()
    assert ring.max_bytes == 250 and ring.retention_s is None

    ring.set_limits(retention_s=4.0, max_bytes=250)   # now shrink retention
    # newest end = T0+10 s; horizon T0+6 s evicts segments ending <= +6 s
    assert ring.coverage("video")[0] >= T0 + timedelta(seconds=6)


def test_audio_and_video_tracked_independently_for_query(tmp_path):
    ring = SegmentRing(retention_s=None, max_bytes=10**9)
    ring.add(seg(tmp_path, 0, kind="video"))
    ring.add(seg(tmp_path, 0, kind="audio"))
    assert len(ring.covering("audio", T0, T0 + timedelta(seconds=2))) == 1
    assert len(ring.covering("video", T0, T0 + timedelta(seconds=2))) == 1


def clip(ring, tmp_path, group, start, size=100):
    paths = [tmp_path / f"{group}.mp4", tmp_path / f"{group}.json"]
    paths[0].write_bytes(b"v" * size)
    paths[1].write_bytes(b"{}")
    ring.register_temp(group, paths, T0 + timedelta(seconds=start),
                       T0 + timedelta(seconds=start + 2))
    return paths


def test_clips_sidecars_and_segments_share_oldest_source_budget(tmp_path):
    ring = SegmentRing(None, 250)
    old = seg(tmp_path, 0)
    ring.add(old)
    cached = clip(ring, tmp_path, "middle", 2)
    newest = seg(tmp_path, 2)
    ring.add(newest)
    assert not old.path.exists()
    assert all(path.exists() for path in cached)
    assert ring.total_bytes == 202
    ring.set_limits(None, 100)
    assert all(not path.exists() for path in cached)
    assert newest.path.exists()


def test_temp_files_alone_obey_free_floor(tmp_path):
    ring = SegmentRing(None, 1000, free_bytes_fn=lambda: 500 - ring.total_bytes,
                       disk_margin_bytes=100)
    oldest = clip(ring, tmp_path, "old", 0, 200)
    newest = clip(ring, tmp_path, "new", 2, 200)
    assert all(not path.exists() for path in oldest)
    assert all(path.exists() for path in newest)
    assert ring.total_bytes == 202


def test_source_lease_protects_existing_and_arriving_tail_across_threads(tmp_path):
    ring = SegmentRing(None, 1000)
    first, tail, later = [seg(tmp_path, i) for i in range(3)]
    ring.add(first)
    entered, release = threading.Event(), threading.Event()
    failures = []

    def reader():
        try:
            with ring.pin("video", T0, tail.utc_end) as source:
                assert source == [first]
                entered.set()
                assert release.wait(3)
                assert first.path.read_bytes() == b"x" * 100
                assert tail.path.read_bytes() == b"x" * 100
        except (AssertionError, OSError) as exc:
            failures.append(exc)

    thread = threading.Thread(target=reader)
    thread.start()
    try:
        assert entered.wait(3)
        ring.set_limits(None, 100)
        ring.add(tail)
        ring.add(later)
        assert first.path.exists() and tail.path.exists()
        assert not later.path.exists()
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive() and not failures
    assert not first.path.exists() and tail.path.exists()


def test_nested_temp_lease_reserves_creation_and_defers_delete(tmp_path):
    ring = SegmentRing(None, 0)
    with ring.pin_temp("save"):
        with ring.pin_temp("save"):
            paths = clip(ring, tmp_path, "save", 0)
            ring.forget_temp("save", delete=True)
        assert ring.total_bytes == 102
        assert all(path.exists() for path in paths)
    assert all(not path.exists() for path in paths)
    assert ring.total_bytes == 0


def test_growing_partial_remains_accounted_until_atomic_publication(tmp_path):
    ring = SegmentRing(None, 200)
    old = seg(tmp_path, 0)
    ring.add(old)
    partial, output = tmp_path / "clip.part", tmp_path / "clip.mp4"
    with ring.pin_temp("cut"):
        ring.register_temp("cut", [partial, output], T0, T0 + timedelta(seconds=2))
        partial.write_bytes(b"v" * 180)
        ring.maintain()
        assert ring.total_bytes == 180 and not old.path.exists()
        partial.replace(output)
        ring.maintain()
        assert ring.total_bytes == 180


def test_failed_segment_unlink_is_counted_retried_and_not_queryable(tmp_path, monkeypatch):
    ring = SegmentRing(None, 100)
    old, new = [seg(tmp_path, i) for i in range(2)]
    ring.add(old)
    unlink = Path.unlink

    def busy(path, *args, **kwargs):
        if path == old.path:
            raise PermissionError("sharing violation")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", busy)
    ring.add(new)  # no unlink error can terminate an encoder's segment callback
    assert ring.total_bytes == 100 and old.path.exists()
    assert ring.coverage("video") is None
    monkeypatch.setattr(Path, "unlink", unlink)
    ring.maintain()
    assert ring.total_bytes == 0 and not old.path.exists()


def test_partial_group_unlink_failure_retains_actual_remaining_bytes(tmp_path, monkeypatch):
    ring = SegmentRing(None, 1000)
    paths = clip(ring, tmp_path, "cut", 0)
    unlink = Path.unlink

    def busy(path, *args, **kwargs):
        if path == paths[0]:
            raise PermissionError("sharing violation")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", busy)
    ring.set_limits(None, 0)
    assert paths[0].exists() and not paths[1].exists()
    assert ring.total_bytes == 100
    monkeypatch.setattr(Path, "unlink", unlink)
    ring.maintain()
    assert ring.total_bytes == 0


def test_forgetting_a_saved_copy_never_deletes_it(tmp_path):
    ring = SegmentRing(None, 1000)
    paths = clip(ring, tmp_path, "promoted", 0)
    ring.forget_temp("promoted")
    ring.set_limits(None, 0)
    assert ring.total_bytes == 0
    assert all(path.exists() for path in paths)


def test_temp_registration_rejects_saved_files_outside_scratch(tmp_path):
    ring = SegmentRing(None, 1000, scratch_root=tmp_path / "scratch")
    saved = tmp_path / "saved.mp4"
    saved.write_bytes(b"keep")
    with pytest.raises(ValueError, match="inside scratch"):
        ring.register_temp("bad", [saved], T0, T0)
    assert saved.read_bytes() == b"keep"


def test_failed_eviction_callback_cannot_interrupt_capture(tmp_path):
    def failed(_):
        raise RuntimeError("archive unavailable")

    ring = SegmentRing(None, 0, on_evict=failed)
    source = seg(tmp_path, 0)
    ring.add(source)
    assert ring.total_bytes == 0 and not source.path.exists()


def test_inventory_counts_growing_encoder_archive_and_wal_without_double_count(tmp_path):
    ring = SegmentRing(None, 1000, scratch_root=tmp_path)
    source = seg(tmp_path, 0)
    ring.add(source)
    active = tmp_path / "active.ts"
    archive = tmp_path / "pictures.sqlite3"
    wal = tmp_path / "pictures.sqlite3-wal"
    for path, size in [(active, 150), (archive, 200), (wal, 75)]:
        path.write_bytes(b"x" * size)
    ring.maintain()
    assert ring.total_bytes == 525
    ring.set_limits(None, 400)
    assert not source.path.exists()
    assert all(path.exists() for path in (active, archive, wal))
    assert ring.total_bytes == 425  # mutable producer files may exceed cap
    active.write_bytes(b"x" * 200)
    wal.unlink()
    ring.maintain()
    assert ring.total_bytes == 400


def test_clear_defers_pinned_source_until_reader_finishes(tmp_path):
    ring = SegmentRing(None, 1000)
    source = seg(tmp_path, 0)
    ring.add(source)
    with ring.pin("video", source.utc_start, source.utc_end):
        ring.clear()
        assert source.path.exists()
    assert not source.path.exists() and ring.total_bytes == 0


def test_low_free_space_reports_pressure_only_after_eligible_eviction(tmp_path):
    free = [1000]
    ring = SegmentRing(None, 1000, free_bytes_fn=lambda: free[0], disk_margin_bytes=100)
    old, leased = [seg(tmp_path, i) for i in range(2)]
    ring.add(old)
    ring.add(leased)
    with ring.pin("video", leased.utc_start, leased.utc_end):
        free[0] = -100  # model an external consumer exhausting more than one segment
        ring.maintain()
        assert not old.path.exists() and leased.path.exists()
        assert ring.storage_pressure
        free[0] = 1000
        ring.maintain()
        assert not ring.storage_pressure


def test_configured_cap_does_not_pause_a_lease_on_a_plentiful_disk(tmp_path):
    ring = SegmentRing(None, 1000, free_bytes_fn=lambda: 10000, disk_margin_bytes=100)
    with ring.pin_temp("reader"):
        paths = clip(ring, tmp_path, "reader", 0)
        ring.set_limits(None, 0)
        assert all(path.exists() for path in paths)
        assert not ring.storage_pressure
