"""Distinguish edit-list/decoder startup from changed encoded samples."""
from datetime import datetime, timezone
import hashlib
import json

import av
import numpy as np

from test_replay_fragment_archive import recording as recording
from test_replay_picture_identity import encoder as encoder
from test_replay_fragment_producer import decoded_audio
from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.extract import ClipExtractor
from sm64_events.replay.fragmentstore import FragmentArchive
from sm64_events.replay.media import MediaRun
from sm64_events.replay.ring import SegmentInfo, SegmentRing
from sm64_events.replay.virtualmp4 import VirtualMp4


def audio_packets(path):
    with av.open(str(path)) as source:
        return [{"pts": p.pts, "dts": p.dts, "duration": p.duration,
                 "sha": hashlib.sha256(bytes(p)).hexdigest(),
                 "side": [(d.data_type, bytes(d).hex()) for d in p.iter_sidedata()]}
                for p in source.demux(audio=0) if p.size]


def ignored_edit_audio(path):
    with av.open(str(path), options={"ignore_editlist": "1"}) as source:
        frames = list(source.decode(audio=0))
    return round(frames[0].pts*frames[0].time_base*48000), np.concatenate([f.to_ndarray() for f in frames], axis=1)


def difference(a, b, offset):
    first, x = a
    other, y = b
    low, high = max(first+offset, other), min(first+offset+x.shape[1], other+y.shape[1])
    assert high > low
    x, y = x[:,low-first-offset:high-first-offset], y[:,low-other:high-other]
    delta = x-y
    return {"first": first, "other_first": other, "overlap": high-low,
            "equal": np.array_equal(x,y), "max_abs": float(np.max(np.abs(delta))),
            "rms": float(np.sqrt(np.mean(delta*delta))), "different": int(np.count_nonzero(delta))}


def test_same_source_audio_decode_startup(tmp_path, recording, encoder):
    run = MediaRun("native", 1000)
    root = tmp_path / "archive"
    archive = FragmentArchive(root, SegmentRing(None, 10**8, scratch_root=root), run)
    archive.feed(recording)
    archive.finish()
    original, native, legacy = [tmp_path / f"{name}.mp4" for name in ("original","native","legacy")]
    original.write_bytes(recording)
    with archive.selection(90000, 170000) as (init, tracks, units, _):
        media = VirtualMp4(init, tracks, units, 90000, 170000)
        native.write_bytes(b"".join(media.chunks()))
    ring = SegmentRing(None, 10**8)
    utc = lambda stamp: datetime.fromtimestamp(stamp, timezone.utc)
    reference = tmp_path / "reference.ts"
    ring.add(SegmentInfo(reference, "video", utc(1000), utc(1002), reference.stat().st_size, (640,480), run))
    ClipExtractor(ReplayConfig(scratch_dir=tmp_path), encoder[1], encoder[0]).extract(ring, utc(1001), utc(1000+170000/90000), legacy)
    original_audio, native_audio, legacy_audio = [decoded_audio(path) for path in (original,native,legacy)]
    report = {"native_original": difference(native_audio,original_audio,48000),
              "legacy_original": difference(legacy_audio,original_audio,48000),
              "native_legacy": difference(native_audio,legacy_audio,0),
              "ignore_native_original": difference(ignored_edit_audio(native),ignored_edit_audio(original),0),
              "packets": {name: audio_packets(path) for name,path in (("original",original),("native",native),("legacy",legacy))}}
    (tmp_path / "audio-startup.json").write_text(json.dumps(report), encoding="utf-8")
    print(json.dumps({key:value for key,value in report.items() if key != "packets"}))

    assert report["native_legacy"]["equal"]
    assert report["ignore_native_original"]["equal"]
    assert report["packets"]["native"] == report["packets"]["legacy"]
