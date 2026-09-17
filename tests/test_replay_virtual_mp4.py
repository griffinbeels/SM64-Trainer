"""Virtual native files retain encoded bytes, source ticks, pixels and AAC samples."""
import io
from datetime import datetime, timezone
import json

import av
import pytest

from test_replay_fragment_archive import recording as recording
from test_replay_picture_identity import encoder as encoder
from test_replay_fragment_producer import decoded_audio, decoded_video
from sm64_events.replay.fragmentstore import FragmentArchive
from sm64_events.replay.media import MediaRun
from sm64_events.replay.ring import SegmentRing, SegmentInfo
from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.extract import ClipExtractor
from test_replay_native_preroll import difference
from sm64_events.replay.virtualmp4 import VirtualMp4


@pytest.mark.parametrize("start,end", [(0, 170000), (1, 170000), (45000, 150012), (90000, 170000)])
def test_virtual_native_file_retains_picture_and_audio_identity(tmp_path, recording, encoder, start, end):
    root = tmp_path / "archive"
    archive = FragmentArchive(root, SegmentRing(None, 10**8, scratch_root=root), MediaRun("native", 1000))
    archive.feed(recording)
    archive.finish()
    before = sorted((p.name, p.stat().st_size) for p in root.iterdir())
    with archive.selection(start, end) as (init, tracks, units, _):
        virtual = VirtualMp4(init, tracks, units, start, end)
        assert sorted((p.name, p.stat().st_size) for p in root.iterdir()) == before
        data = b"".join(virtual.chunks())
        assert len(data) == virtual.size
        for low, high in [(0, 31), (len(virtual.header)-5, len(virtual.header)+5),
                          (virtual.size-37, virtual.size), (13, virtual.size-19)]:
            assert b"".join(virtual.chunks(low, high)) == data[low:high]
    output, original = tmp_path / "virtual.mp4", tmp_path / "original.mp4"
    output.write_bytes(data)
    original.write_bytes(recording)
    decoded = decoded_video(output)
    reference = [(pts-start, pixels) for pts, pixels in decoded_video(original) if start <= pts < end]
    assert decoded == reference
    assert [pts for pts, _ in decoded] == virtual.frame_ticks
    with av.open(io.BytesIO(data)) as source:
        actual_packets = [(p.stream.type, bytes(p)) for p in source.demux() if p.size]
    with av.open(io.BytesIO(recording)) as source:
        originals = {(p.stream.type, bytes(p)) for p in source.demux() if p.size}
    assert all(packet in originals for packet in actual_packets)
    # Both are independent valid decoder entry points. A native edit can
    # drop initial AAC priming packets, just like today's extractor. Keep an
    # exact comparison to that baseline instead of weakening a PCM tolerance.
    run = MediaRun("native", 1000)
    utc = lambda tick: datetime.fromtimestamp(1000 + tick/90000, timezone.utc)
    legacy_ring = SegmentRing(None, 10**8)
    witness = tmp_path / "reference.ts"
    legacy_ring.add(SegmentInfo(witness, "video", utc(0), utc(180000), witness.stat().st_size, (640,480), run))
    legacy = tmp_path / "legacy.mp4"
    ClipExtractor(ReplayConfig(scratch_dir=tmp_path), encoder[1], encoder[0]).extract(
        legacy_ring, utc(start), utc(end), legacy)
    audio = decoded_audio(output)
    full = difference(audio, decoded_audio(original), (start*48000+45000)//90000)
    current = difference(audio, decoded_audio(legacy), 0)
    assert full["equal"] or current["equal"], (full, current)
    (tmp_path / "virtual-report.json").write_text(json.dumps({"start": start, "end": end,
        "header_bytes": len(virtual.header), "file_bytes": virtual.size,
        "pictures": len(decoded), "audio_full_stream": full, "audio_current_extractor": current}), encoding="utf-8")


def test_unaligned_edit_is_rejected_instead_of_retiming_a_source_picture(tmp_path, recording):
    root = tmp_path / "archive"
    archive = FragmentArchive(root, SegmentRing(None, 10**8, scratch_root=root), MediaRun("native", 1000))
    archive.feed(recording)
    archive.finish()
    with archive.selection(45017, 150012) as (init, tracks, units, _):
        with pytest.raises(ValueError, match="start on a source picture"):
            VirtualMp4(init, tracks, units, 45017, 150012)
