from datetime import datetime, timedelta, timezone

from sm64_events.replay.ring import SegmentInfo, SegmentRing

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


def test_coverage_none_when_empty():
    ring = SegmentRing(retention_s=None, max_bytes=10**9)
    assert ring.coverage("video") is None


def test_audio_and_video_tracked_independently_for_query(tmp_path):
    ring = SegmentRing(retention_s=None, max_bytes=10**9)
    ring.add(seg(tmp_path, 0, kind="video"))
    ring.add(seg(tmp_path, 0, kind="audio"))
    assert len(ring.covering("audio", T0, T0 + timedelta(seconds=2))) == 1
    assert len(ring.covering("video", T0, T0 + timedelta(seconds=2))) == 1
