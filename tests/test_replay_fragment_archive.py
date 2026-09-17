"""Actual mux bytes/packet clocks survive indexed, leased prefix publication."""
import io
import json

import av
import pytest

from test_replay_fragment_producer import continuing_output
from test_replay_picture_identity import encoder as encoder, read_pictures
from sm64_events.replay.fragmentindex import fragment_samples, tracks_of
from sm64_events.replay.fragments import FragmentReader
from sm64_events.replay.fragmentstore import FragmentArchive
from sm64_events.replay.media import MediaRun
from sm64_events.replay.ring import SegmentRing


def units_of(data):
    reader = FragmentReader()
    units = reader.feed(data)
    reader.finish()
    return units


@pytest.fixture
def recording(tmp_path, encoder):
    ffmpeg, codec = encoder
    continuing_output(tmp_path, ffmpeg, codec)
    return (tmp_path / "live.mp4").read_bytes()


def test_index_matches_independent_demuxed_packet_clock_and_bytes(recording):
    units = units_of(recording)
    tracks = tracks_of(units[0])
    actual = []
    for unit in units:
        if unit.kind == "media":
            for sample in fragment_samples(unit, tracks):
                track = tracks[sample.track_id]
                actual.append((track.kind, sample.pts, sample.dts, sample.duration, track.timescale,
                               sample.key, recording[sample.offset:sample.offset + sample.size]))
    with av.open(io.BytesIO(recording)) as source:
        expected = [(packet.stream.type, packet.pts, packet.dts, packet.duration, packet.time_base.denominator,
                     packet.is_keyframe, bytes(packet)) for packet in source.demux() if packet.size]
    assert sorted(actual) == sorted(expected)
    first = next(unit for unit in units if unit.kind == "media")
    sample_count = len(fragment_samples(first, tracks))
    with pytest.raises(ValueError, match="sample limit"):
        fragment_samples(first, tracks, max_samples=sample_count - 1)
    assert len(fragment_samples(first, tracks, max_samples=sample_count)) == sample_count


def test_read_existing_prefix_without_copy_or_tail_then_lease_and_eviction(tmp_path, recording, monkeypatch):
    root = tmp_path / "archive"
    ring = SegmentRing(None, 10**8, scratch_root=root)
    archive = FragmentArchive(root, ring, MediaRun("independent run", 1000), extent_bytes=4096)
    units = units_of(recording)
    archive.feed(units[0].data)
    media = [unit for unit in units if unit.kind == "media"]
    for unit in media[:10]:
        archive.feed(unit.data)
    # A manifest is a selection of existing bytes, not another encoded file.
    with archive.read(0, 45000) as (pictures, chunks):
        prefix = b"".join(chunks)
    assert not archive.closed and pictures[0].pts == 0
    with av.open(io.BytesIO(prefix)) as source:
        assert next(source.decode(video=0)).pts == 0
    with pytest.raises(LookupError, match="tail"):
        with archive.read(0, 180000):
            pass
    for unit in media[10:]:
        archive.feed(unit.data)
    archive.finish()
    assert archive.error is None
    assert len(list(root.iterdir())) < len(media), "one file per fragment defeats shared storage"
    before = sorted((path.name, path.stat().st_size) for path in root.iterdir())
    from pathlib import Path

    opens = []
    original_open = Path.open

    def counted_open(path, *args, **kwargs):
        if path.parent == root:
            opens.append(path)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counted_open)
    with archive.read(0, 170000) as (pictures, chunks):
        retained = b"".join(chunks)
        assert sorted((path.name, path.stat().st_size) for path in root.iterdir()) == before
    assert len(opens) == len(set(opens)), "open each selected extent once, not every fragment"
    with archive.read(0, 170000) as (_, abandoned):
        assert next(abandoned)  # initialization
        assert next(abandoned)  # suspend inside an open file, like a cancelled HTTP read
        ring.set_limits(None, 0)
        assert ring.total_bytes > 0
    assert ring.total_bytes == 0
    with pytest.raises(LookupError):
        with archive.read(0, 45000):
            pass
    assert list(abandoned) == [], "no further disk access after releasing the read lease"
    output = tmp_path / "read.mp4"
    output.write_bytes(retained)
    decoded = read_pictures(output)
    assert [number for _, number in decoded[:3]] == [0, 1, 2]
    assert [round(time * 90000) for time, _ in decoded][:len(pictures)] == [s.pts for s in pictures]
    (tmp_path / "archive-report.json").write_text(json.dumps({"files": before,
        "selected_pictures": len(pictures), "selected_bytes": len(retained)}), encoding="utf-8")


def test_truncated_stream_fails_closed_and_releases_writer(tmp_path, recording):
    root = tmp_path / "failed"
    ring = SegmentRing(None, 10**8, scratch_root=root)
    archive = FragmentArchive(root, ring, MediaRun("failure", 1000))
    units = units_of(recording)
    archive.feed(units[0].data + units[1].data)
    archive.feed(units[2].data[:-1])
    archive.finish()
    assert archive.closed and "unfinished" in archive.error
    assert not ring.protected_paths()
    with pytest.raises(ValueError, match="closed"):
        archive.feed(units[2].data[-1:])
    ring.set_limits(None, 0)
    assert ring.total_bytes == 0


def test_malformed_unit_closes_archive_without_accepting_later_data(tmp_path, recording):
    root = tmp_path / "invalid"
    ring = SegmentRing(None, 10**8, scratch_root=root)
    archive = FragmentArchive(root, ring, MediaRun("invalid", 1000))
    units = units_of(recording)
    archive.feed(units[0].data + units[1].data)
    with pytest.raises(ValueError, match="box order"):
        archive.feed(units[0].data)
    assert archive.closed and archive.error and not ring.protected_paths()
