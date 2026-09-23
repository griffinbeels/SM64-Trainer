"""Idle gaps and retained GOP boundaries never collapse the source clock."""
import io

import av
from test_replay_picture_identity import encoder as encoder
from test_replay_fragment_connection import connected_capture
from sm64_events.replay.fragmentstore import FragmentArchive
from sm64_events.replay.ring import SegmentRing
from sm64_events.replay.virtualmp4 import VirtualMp4


def bounded(root, source, encoded, discard=None):
    ring = SegmentRing(None, 10**8, scratch_root=root)
    archive = FragmentArchive(root, ring, source.run, extent_ticks=45000, discard=discard)
    archive.feed(encoded)
    archive.finish()
    assert archive.error is None
    return ring, archive


def test_idle_hole_and_evicted_aac_dependency_keep_exact_windows(tmp_path, encoder):
    source, ledger, _ = connected_capture(tmp_path / "capture", encoder)
    # The capture's own clock decides where its tracks and keyframes land: on a
    # loaded machine (the merge check) audio can stop before the last pictures
    # and a GOP can run long. Read to what every track published, and cut the
    # idle hole at the third extent this footage actually makes, rather than
    # at a wall-clock span that assumed a keyframe every 0.5 s.
    end = min(ledger.feeds_between(0, 1e12)[-2]["pts"], source.coverage()[1])
    with source.read(0, end) as (_, chunks):
        encoded = b"".join(chunks)
    _, probe = bounded(tmp_path / "probe", source, encoded)
    assert len(probe._extents) >= 4, "a hole needs retained footage on both sides"
    hole = probe._utc(probe._extents[2].start)
    ring, archive = bounded(tmp_path / "bounded", source, encoded, discard=lambda start: start == hole)
    # First query crosses discarded idle footage: its end must stop at that
    # hole. A later query starts in the retained run, without closing the gap.
    start, stop = archive.window(0, end)
    assert start == 0 and stop < end
    following_start, following_end = archive.window(stop + 1, end)
    assert following_start > stop and following_end > following_start
    with archive.selection(start, stop):
        pass
    with archive.selection(following_start, following_end) as (init, tracks, units, visible):
        media = VirtualMp4(init, tracks, units, visible[0].pts, following_end)
        output = b"".join(media.chunks())
    with av.open(io.BytesIO(output)) as decoded:
        ticks = [round(f.pts*f.time_base*90000) for f in decoded.decode(video=0)]
    assert ticks == media.frame_ticks
    # No whole-GOP history is available before this retained edge. Coverage
    # must begin at an actual picture where AAC is also available.
    ring.forget_temp(archive._extents[0].group, delete=True)
    low, high = archive.coverage()
    low, high = archive.window(low, high)
    with archive.selection(low, high) as (init, tracks, units, visible):
        media = VirtualMp4(init, tracks, units, visible[0].pts, high)
        assert b"".join(media.chunks())


def test_a_query_in_the_video_only_tail_before_a_hole_starts_after_it():
    """The shape a loaded merge check produced (2026-09-23), built exactly: the
    run before an idle hole ends its AAC before its video, so the first window
    stops where audio does and a query from the next tick lands in a tail no
    set of tracks covers. It used to raise "no complete replay interval"."""
    from pathlib import Path
    import threading
    from types import SimpleNamespace

    from sm64_events.replay.fragmentindex import Sample, Track
    from sm64_events.replay.fragmentstore import Published, _Extent

    video, audio = 1, 2

    def extent(group, start, samples):
        end = max(s.pts + s.duration for s in samples if s.track_id == video)
        return _Extent(Path(group), group, start, [Published(Path(group), group, 0, 1, 0, tuple(samples), start, end)],
                       [(start, 0)])

    archive = FragmentArchive.__new__(FragmentArchive)
    archive.tracks = {video: Track(video, "video", 90000, 0, 0, 0, 0), audio: Track(audio, "audio", 48000, 0, 0, 0, 0)}
    archive._lock, archive.ring, archive._prune_revision = threading.RLock(), SimpleNamespace(temporary_revision=0), 0
    archive._extents = [
        extent("before", 0, [Sample(video, 0, 0, 3000, 0, 1, True), Sample(audio, 0, 0, 1024, 0, 1, False)]),
        # the hole: 3000..9000
        extent("after", 9000, [Sample(video, 9000, 9000, 3000, 0, 1, True),
                               Sample(audio, 4800, 4800, 1024, 0, 1, False),
                               Sample(audio, 5824, 5824, 1024, 0, 1, False)])]
    start, stop = archive.window(0, 12000)
    assert (start, stop) == (0, 1920)  # AAC ends 1080 ticks before the picture does
    assert archive.window(stop + 1, 12000) == (9000, 12000)
    assert archive.window(2999, 12000) == (9000, 12000)
