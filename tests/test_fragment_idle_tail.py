"""Retention boundaries and resume races using actual ring ownership."""
from types import SimpleNamespace as NS
from datetime import datetime, timezone

from sm64_events.replay.fragmentretention import IdleTail
from sm64_events.replay.media import MediaRun
from sm64_events.replay.ring import SegmentRing


def utc(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc)


def extent(ring, root, number, start, end):
    path = root / f"extent-{number}.bin"
    path.write_bytes(bytes([number]) * 1024)
    group = f"fragments:fixture:{number}"
    ring.register_temp(group, [path], utc(start), utc(end))
    return NS(group=group, path=path, start=start * 90000,
              units=[NS(end=end * 90000)])


def test_tail_retains_predecessor_and_old_active_history_through_new_idle_epoch(tmp_path):
    ring = SegmentRing(None, 10**7, scratch_root=tmp_path)
    window = [(utc(4), 3)]
    tail = IdleTail(MediaRun("fixture", 0), ring, lambda: window[0])
    active = extent(ring, tmp_path, 0, 0, 4)
    tail.maintain(4 * 90000, closed=active)
    idle = []
    for n in range(1, 11):
        item = extent(ring, tmp_path, n, n * 4, n * 4 + 4)
        idle.append(item)
        tail.maintain((n * 4 + 4) * 90000, closed=item)
    assert active.path.exists(), "idle trimming must not age out previous attempts"
    assert [e.start for e in idle if e.path.exists()] == [36 * 90000, 40 * 90000]
    window[0] = None
    tail.maintain(44 * 90000)
    preserved = [e.path for e in idle if e.path.exists()]
    window[0] = (utc(50), 0)
    mixed = extent(ring, tmp_path, 11, 48, 52)
    tail.maintain(52 * 90000, closed=mixed)
    for n in range(12, 30):
        item = extent(ring, tmp_path, n, n * 4 + 4, n * 4 + 8)
        tail.maintain((n * 4 + 8) * 90000, closed=item)
    assert all(p.exists() for p in preserved) and mixed.path.exists() and active.path.exists()
    assert len(tail._candidates) == 1


def test_expired_reader_lease_keeps_bytes_until_release(tmp_path):
    evicted = []
    ring = SegmentRing(None, 10**7, scratch_root=tmp_path,
                       on_temp_evict=lambda *event: evicted.append(event))
    tail = IdleTail(MediaRun("fixture", 0), ring, lambda: (utc(0), 0))
    first = extent(ring, tmp_path, 0, 0, 4)
    tail.maintain(4 * 90000, closed=first)
    with ring.pin_temp(first.group):
        second = extent(ring, tmp_path, 1, 4, 8)
        tail.maintain(8 * 90000, closed=second)
        assert first.path.read_bytes() == bytes([0]) * 1024
        assert first.group in ring.temporary_groups() and not evicted
    assert not first.path.exists() and first.group not in ring.temporary_groups()
    assert evicted == [(first.group, utc(0), utc(4))]


def test_resume_during_expiry_cannot_advance_frozen_cutoff(tmp_path):
    window = [(utc(0), 3)]
    ring = SegmentRing(None, 10**7, scratch_root=tmp_path)
    tail = IdleTail(MediaRun("fixture", 0), ring, lambda: window[0])
    items = [extent(ring, tmp_path, n, n * 4, (n + 1) * 4) for n in range(6)]
    for item in items:
        # Slow audio: video grows, but the published A/V frontier has not.
        tail.maintain(0, closed=item)
    original = ring.forget_temp
    def resume(group, delete=False):
        window[0] = None
        original(group, delete)
    ring.forget_temp = resume
    tail.maintain(24 * 90000)
    assert not items[0].path.exists()
    assert all(e.path.exists() for e in items[1:]), "stale idle pass continued after resume"
    tail.maintain(30 * 90000)
    assert not tail._candidates


def test_slow_track_frontier_holds_tail_and_missing_groups_drop_candidate_refs(tmp_path):
    ring = SegmentRing(None, 10**7, scratch_root=tmp_path)
    tail = IdleTail(MediaRun("fixture", 0), ring, lambda: (utc(0), 3))
    items = [extent(ring, tmp_path, n, n * 4, (n + 1) * 4) for n in range(4)]
    for item in items:
        tail.maintain(None, closed=item)
    assert all(e.path.exists() for e in items)
    tail.maintain(10 * 90000)
    assert all(e.path.exists() for e in items), "video frontier was used instead of delayed A/V"
    tail.maintain(12 * 90000)
    assert not items[0].path.exists() and all(e.path.exists() for e in items[1:])
    ring.forget_temp(items[1].group, delete=True)
    tail.prune(ring.temporary_groups())
    assert len(tail._candidates) == 2
