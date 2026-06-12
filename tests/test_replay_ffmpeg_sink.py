# tests/test_replay_ffmpeg_sink.py
import io
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from sm64_events.replay.ffmpeg_sink import FfmpegVideoSink, parse_segment_csv
from sm64_events.replay.config import ReplayConfig


def test_spawn_args_pin_segment_and_quality_contract(tmp_path, monkeypatch):
    """Pins the ffmpeg arg contract; each flag broke live when wrong:
    no -g => NVENC default GOP (~250) gave the segment muxer (keyframe
    splits only) ONE giant segment; default bitrate (~2 Mbps) => visible
    grain; -muxdelay/-muxpreload/-reset_timestamps => the extractor's
    pts0-per-segment contract."""
    captured = {}

    class _FakeProc:
        def __init__(self):
            self.stdin = io.BytesIO()
            self.stdout = io.BytesIO()  # readline() -> b"" => readers exit
            self.stderr = io.BytesIO()

    def fake_popen(args, **kwargs):
        captured["args"] = args
        return _FakeProc()

    monkeypatch.setattr(
        "sm64_events.replay.ffmpeg_sink.subprocess.Popen", fake_popen)
    cfg = ReplayConfig(scratch_dir=tmp_path, fps=60, segment_s=2.0)
    sink = FfmpegVideoSink(cfg, lambda s: None, ffmpeg="ffmpeg")
    sink._spawn(320, 240)
    for t in sink._readers:  # EOF immediately against the fake pipes
        t.join(timeout=5)
    a = captured["args"]

    def after(flag):
        return a[a.index(flag) + 1]

    assert after("-g") == "120"            # fps*segment_s: IDR per segment
    assert after("-segment_time") == "2.0"
    assert after("-b:v") == "12M"
    assert after("-bf") == "0"
    assert after("-forced-idr") == "1"
    assert after("-muxdelay") == "0" and after("-muxpreload") == "0"
    assert after("-reset_timestamps") == "1"
    assert after("-segment_list") == "pipe:1"
    assert after("-pix_fmt") == "bgra" and after("-s") == "320x240"


T0 = datetime(2026, 6, 12, 1, 0, 0, tzinfo=timezone.utc)


def test_parse_segment_csv(tmp_path):
    (tmp_path / "video_00_000003.ts").write_bytes(b"x" * 99)
    seg = parse_segment_csv("video_00_000003.ts,6.000000,8.000000\n", T0, tmp_path)
    assert seg.kind == "video" and seg.size_bytes == 99
    assert seg.utc_start == T0 + timedelta(seconds=6)
    assert seg.utc_end == T0 + timedelta(seconds=8)
    assert parse_segment_csv("garbage\n", T0, tmp_path) is None
    assert parse_segment_csv("missing.ts,0,2\n", T0, tmp_path) is None


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg")
def test_ffmpeg_sink_produces_decodable_segments(tmp_path):
    import av
    cfg = ReplayConfig(scratch_dir=tmp_path, fps=60)
    segs = []
    sink = FfmpegVideoSink(cfg, segs.append, ffmpeg=shutil.which("ffmpeg"))
    sink.start()
    frame = np.zeros((240, 320, 4), dtype=np.uint8)
    t0 = time.monotonic()
    i = 0
    while time.monotonic() - t0 < 5.5:
        frame = frame.copy()
        frame[:, :, 0] = i % 256
        sink.submit(frame)
        i += 1
        time.sleep(1 / 120)
    sink.stop()
    assert len(segs) >= 2, f"expected >=2 segments, got {len(segs)}"
    first = segs[0]
    assert abs((first.utc_end - first.utc_start).total_seconds() - 2.0) < 0.2
    with av.open(str(first.path)) as c:
        v = c.streams.video[0]
        frames = list(c.decode(video=0))
        # exactly fps*segment_s frames, pts starting at ~0 (extractor contract)
        assert len(frames) == 120
        assert (v.start_time or 0) < 3000  # <~33ms in 90kHz ticks
