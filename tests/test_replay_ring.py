from datetime import datetime, timedelta, timezone

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
