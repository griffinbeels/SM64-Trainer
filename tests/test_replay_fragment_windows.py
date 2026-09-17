"""Idle gaps and retained GOP boundaries never collapse the source clock."""
import io

import av
from test_replay_picture_identity import encoder as encoder
from test_replay_fragment_connection import connected_capture
from sm64_events.replay.fragmentstore import FragmentArchive
from sm64_events.replay.ring import SegmentRing
from sm64_events.replay.virtualmp4 import VirtualMp4


def test_idle_hole_and_evicted_aac_dependency_keep_exact_windows(tmp_path, encoder):
    source, ledger, _ = connected_capture(tmp_path / "capture", encoder)
    end = ledger.feeds_between(0, 1e12)[-2]["pts"]
    with source.read(0, end) as (_, chunks):
        encoded = b"".join(chunks)
    root = tmp_path / "bounded"
    ring = SegmentRing(None, 10**8, scratch_root=root)
    archive = FragmentArchive(root, ring, source.run, extent_ticks=45000,
        discard=lambda start: 0.9 < start.timestamp()-source.run.origin_ts < 1.6)
    archive.feed(encoded)
    archive.finish()
    assert archive.error is None
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
