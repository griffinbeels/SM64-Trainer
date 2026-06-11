from datetime import datetime, timedelta, timezone

import av
import numpy as np

from sm64_events.replay.clock import CaptureClock
from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.encoder import SegmentWriter
from sm64_events.replay.extract import ClipExtractor
from sm64_events.replay.ring import SegmentRing

T0 = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
CFG = ReplayConfig()


def build_buffer(tmp_path, seconds=6):
    """Real segments: video (frame index painted into pixels) + sine audio."""
    ring = SegmentRing(retention_s=None, max_bytes=10**9)
    clk = CaptureClock(anchor_qpc_100ns=0, anchor_utc=T0)
    w = SegmentWriter(cfg=CFG, clock=clk, out_dir=tmp_path / "buf",
                      codec="libx264", on_segment=ring.add)
    w.start_audio(t0_utc=T0)
    t = np.arange(48000, dtype=np.float32) / 48000
    tone = (np.sin(2 * np.pi * 440 * t) * 0.3 * 32767).astype(np.int16)
    sec = np.stack([tone, tone], axis=1)
    for i in range(seconds * 30):
        arr = np.full((480, 640, 4), (i * 4) % 256, dtype=np.uint8)
        w.write_video(arr, frame_index=i)
    for _ in range(seconds):
        w.write_audio(sec)
    w.close()
    return ring


def test_extract_produces_scrubbable_av_mp4(tmp_path):
    ring = build_buffer(tmp_path)
    ex = ClipExtractor(cfg=CFG, codec="libx264")
    out = tmp_path / "clip.mp4"
    res = ex.extract(ring, T0 + timedelta(seconds=1), T0 + timedelta(seconds=5), out)
    assert res.truncated is False
    assert abs(res.duration_s - 4.0) < 0.2
    with av.open(str(out)) as c:
        kinds = {s.type for s in c.streams}
        assert kinds == {"video", "audio"}
        n = len([f for f in c.decode(video=0)])
        assert abs(n - 120) <= 3            # 4 s * 30 fps
    # faststart: moov atom must precede mdat for instant browser scrubbing
    head = out.read_bytes()[:4096]
    assert head.find(b"moov") != -1 and (
        head.find(b"mdat") == -1 or head.find(b"moov") < head.find(b"mdat"))


def test_extract_clamps_and_flags_truncation(tmp_path):
    ring = build_buffer(tmp_path)
    ex = ClipExtractor(cfg=CFG, codec="libx264")
    out = tmp_path / "clip.mp4"
    res = ex.extract(ring, T0 - timedelta(seconds=10), T0 + timedelta(seconds=2), out)
    assert res.truncated is True
    assert abs(res.duration_s - 2.0) < 0.2


def test_extract_no_footage_raises(tmp_path):
    ring = SegmentRing(retention_s=None, max_bytes=10**9)
    ex = ClipExtractor(cfg=CFG, codec="libx264")
    try:
        ex.extract(ring, T0, T0 + timedelta(seconds=1), tmp_path / "c.mp4")
        assert False, "expected ValueError"
    except ValueError:
        pass
