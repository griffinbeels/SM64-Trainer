"""Held and delayed pictures retain their identity through actual encoder cuts."""
from datetime import datetime, timezone
import json
import time

import av
import pytest
from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.extract import ClipExtractor
from sm64_events.replay.ffmpeg_sink import FfmpegAvSink
from sm64_events.replay.ledger import PictureLedger
from sm64_events.replay.ring import SegmentRing

from test_replay_picture_identity import encoder as encoder, picture, read_pictures
from test_replay_service import attempt, make_service


def wait_feeds(ledger, predicate):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        feeds = ledger.feeds_between(0, 1e12)
        if predicate(feeds):
            return feeds
        time.sleep(.02)
    raise AssertionError(f"encoder did not reach the requested feed: {feeds}")


def segment_pictures(path):
    try:
        return read_pictures(path)
    except IndexError:
        return []  # Record an audio-only tail in the diagnostic report too.


@pytest.fixture
def held_capture(tmp_path, encoder):
    ffmpeg, codec = encoder
    cfg = ReplayConfig(scratch_dir=tmp_path, fps=60, segment_s=1)
    ring = SegmentRing(retention_s=None, max_bytes=10**8)
    ledger = PictureLedger()
    sink = FfmpegAvSink(cfg, ring.add, ffmpeg=ffmpeg, codec=codec,
        on_fed=lambda tag, at, **clock: ledger.mark_fed(tag[1] if tag else None, at, **clock))
    origin = time.time()

    def capture(number, stamp):
        pixels = picture(number)
        assert ledger.observe(pixels, stamp, number + 100,
                              {"exact": True, "barcode": number})
        sink.submit(pixels, (number + 100, stamp))

    sink.start()
    try:
        capture(1, origin)
        held = wait_feeds(ledger, lambda feeds: sum(f["repeat"] for f in feeds) >= 3)
        repeats = [feed for feed in held if feed["repeat"]]
        # A captured picture can arrive after a heartbeat already placed a
        # later timestamp in the encoder. Its pixels must survive that nudge.
        capture(2, origin + .25)
        delayed = wait_feeds(ledger, lambda feeds: any(f["ts"] == origin + .25 for f in feeds))
        delayed_feed = next(f for f in delayed if f["ts"] == origin + .25)
        assert delayed_feed["pts"] > repeats[-1]["pts"]
        time.sleep(.04)
        capture(3, time.time())
        following = wait_feeds(ledger, lambda feeds: len(feeds) >= len(delayed) + 1)
        wait_feeds(ledger, lambda feeds: len(feeds) >= len(delayed) + 2)
    finally:
        sink.stop()
    return cfg, ring, ledger, repeats, delayed_feed, following[len(delayed)]


def test_cut_during_a_long_hold_and_after_a_delayed_capture(tmp_path, encoder, held_capture):
    ffmpeg, codec = encoder
    cfg, ring, ledger, repeats, delayed_feed, following_feed = held_capture
    origin = ledger.feeds_between(0, 1e12)[0]["at"]

    service = make_service(tmp_path / "service", [attempt()])
    service.recorder.ledger = ledger
    cuts = {
        # There is no new encoded picture in this interval, but the old
        # picture is visibly held through all of it. It is still footage.
        "inside-hold": (repeats[1]["at"] + .2, repeats[1]["at"] + .7),
        "delayed-capture": (repeats[-1]["at"] - .1, delayed_feed["at"] + .5),
        "single-delayed-picture": (delayed_feed["at"] + .00005, following_feed["at"] - .00005),
    }
    coverage = ring.coverage("video")
    segments = ring.covering("video", *coverage)
    report = {"origin": origin, "feeds": ledger.feeds_between(0, 1e12),
              "segments": [{"path": str(seg.path),
                            "start": seg.utc_start.timestamp() - origin,
                            "end": seg.utc_end.timestamp() - origin,
                            "pictures": segment_pictures(seg.path)} for seg in segments]}
    source = [row for segment in report["segments"] for row in segment["pictures"]]
    assert [round(t * 90000) for t, _ in source] == [f["pts"] for f in report["feeds"]]
    assert [n for _, n in source if n == 2] == [2], "the delayed picture survived exactly once"
    (tmp_path / "held-picture-report.json").write_text(json.dumps(report, indent=2))
    for label, (start, end) in cuts.items():
        res = ClipExtractor(cfg, codec, ffmpeg).extract(ring,
            datetime.fromtimestamp(start, timezone.utc), datetime.fromtimestamp(end, timezone.utc),
            tmp_path / f"{label}.mp4")
        decoded = read_pictures(res.path)
        assert decoded, label
        assert res.video_start_s < 1/90000, (label, res.video_start_s)
        assert res.start_utc.timestamp() <= start
        with av.open(str(res.path)) as movie:
            stream = movie.streams.video[0]
            actual_end = res.start_utc.timestamp() + float((stream.start_time + stream.duration) * stream.time_base)
            assert abs(actual_end - end) < 2/90000, (label, actual_end, end)
        if label == "inside-hold":
            assert {number for _, number in decoded} == {1}
        elif label == "single-delayed-picture":
            assert [number for _, number in decoded] == [2]
        else:
            assert [number for _, number in decoded if number != 1] == [2, 3]
        meta = {"start_utc": res.start_utc.isoformat(), "frame_times": res.frame_times}
        service._map_from_feeds(meta, res)
        valid = service._validated_meta(meta, attempt())
        assert valid["input_alignment"]["status"] == "source_linked", valid
        mapped = [meta["picture_ledger"][index]["barcode"] for index in valid["picture_rows"]]
        assert mapped == [number for _, number in decoded]
        report[label] = {"pictures": decoded, "source_pts": res.source_pts,
                         "mapped_barcodes": mapped, "start_utc": res.start_utc.isoformat()}
    (tmp_path / "held-picture-report.json").write_text(json.dumps(report, indent=2))
