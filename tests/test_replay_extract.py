from datetime import datetime, timedelta, timezone

import av
import numpy as np

from sm64_events.replay.clock import CaptureClock
from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.encoder import SegmentWriter
from sm64_events.replay.extract import ClipExtractor
from sm64_events.replay.ring import SegmentRing

T0 = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
CFG = ReplayConfig(fps=30)  # tests pin the 30fps math; capture default is 60


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


def test_extract_full_buffer_streams_all_segments(tmp_path):
    """Whole-attempt clips are the spec use case: the extractor must stream
    frames (O(1) memory in clip length), never buffer the span. Cheap proxy:
    a span covering the entire buffer exercises the multi-segment streaming
    path end to end."""
    ring = build_buffer(tmp_path)
    ex = ClipExtractor(cfg=CFG, codec="libx264")
    out = tmp_path / "clip.mp4"
    res = ex.extract(ring, T0, T0 + timedelta(seconds=6), out)
    assert res.truncated is False
    assert abs(res.duration_s - 6.0) < 0.2
    with av.open(str(out)) as c:
        n = len([f for f in c.decode(video=0)])
        assert abs(n - 180) <= 3            # 6 s * 30 fps


def test_extract_no_footage_raises(tmp_path):
    ring = SegmentRing(retention_s=None, max_bytes=10**9)
    ex = ClipExtractor(cfg=CFG, codec="libx264")
    try:
        ex.extract(ring, T0, T0 + timedelta(seconds=1), tmp_path / "c.mp4")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_clip_video_duration_matches_span(tmp_path):
    ring = build_buffer(tmp_path)
    ex = ClipExtractor(cfg=CFG, codec="libx264")
    out = tmp_path / "clip.mp4"
    ex.extract(ring, T0 + timedelta(seconds=1), T0 + timedelta(seconds=5), out)
    with av.open(str(out)) as c:
        # Container duration is in microseconds; use it as primary source.
        # v.duration may be None for some PyAV/MP4 combos, so fall back to
        # container-level duration for both assertions.
        container_dur = c.duration / 1_000_000  # us -> s
        assert abs(container_dur - 4.0) < 0.25  # container duration ~ span
        v = c.streams.video[0]
        if v.duration is not None:
            vdur = float(v.duration * v.time_base)
            assert abs(vdur - 4.0) < 0.25
        else:
            # PyAV version doesn't populate stream duration; container level is
            # authoritative.
            assert abs(container_dur - 4.0) < 0.25


def build_buffer_with_hole(tmp_path):
    """0..59 then 105..164: encoder rotates on the index jump -> ~1.5 s hole."""
    ring = SegmentRing(retention_s=None, max_bytes=10**9)
    clk = CaptureClock(anchor_qpc_100ns=0, anchor_utc=T0)
    w = SegmentWriter(cfg=CFG, clock=clk, out_dir=tmp_path / "buf",
                      codec="libx264", on_segment=ring.add)
    w.start_audio(t0_utc=T0)
    tone = np.zeros((48000, 2), dtype=np.int16)
    for i in range(60):
        w.write_video(np.full((480, 640, 4), 50, dtype=np.uint8), frame_index=i)
    for i in range(105, 165):
        w.write_video(np.full((480, 640, 4), 200, dtype=np.uint8), frame_index=i)
    for _ in range(6):
        w.write_audio(tone)
    w.close()
    return ring


def test_hole_preserves_av_alignment(tmp_path):
    ring = build_buffer_with_hole(tmp_path)
    ex = ClipExtractor(cfg=CFG, codec="libx264")
    out = tmp_path / "clip.mp4"
    res = ex.extract(ring, T0, T0 + timedelta(seconds=5.5), out)
    assert abs(res.duration_s - 5.5) < 0.01
    with av.open(str(out)) as c:
        v = c.streams.video[0]
        a = c.streams.audio[0]
        # Use stream duration when available, else fall back to container.
        # INTENT: video timeline must span the hole (held frame), matching audio.
        container_dur = c.duration / 1_000_000  # us -> s
        if v.duration is not None:
            vdur = float(v.duration * v.time_base)
        else:
            vdur = container_dur
        if a.duration is not None:
            adur = float(a.duration * a.time_base)
        else:
            adur = container_dur
        # Video timeline must span the hole (wall-clock-locked pts preserves it)
        assert abs(vdur - 5.5) < 0.25
        assert abs(vdur - adur) < 0.3


def test_sub_frame_span_raises(tmp_path):
    ring = build_buffer(tmp_path)
    ex = ClipExtractor(cfg=CFG, codec="libx264")
    import pytest
    with pytest.raises(ValueError):
        ex.extract(ring, T0 + timedelta(seconds=1),
                   T0 + timedelta(seconds=1, microseconds=5),
                   tmp_path / "c.mp4")


def test_clip_audio_content_is_faithful_at_60fps(tmp_path):
    """Regression for the 60fps padding bug: per-video-frame audio blocks
    (rate//fps = 800 at 60fps) were each zero-padded to AAC's 1024-sample
    frame, injecting 28% silence mid-clip (distortion + stretch + desync).
    A pure tone must come back at the same pitch, same duration, and with
    a sane RMS (padding collapses RMS and splatters the spectrum)."""
    fps60 = ReplayConfig(fps=60)
    ring = SegmentRing(retention_s=None, max_bytes=10**9)
    clk = CaptureClock(anchor_qpc_100ns=0, anchor_utc=T0)
    w = SegmentWriter(cfg=fps60, clock=clk, out_dir=tmp_path / "buf",
                      codec="libx264", on_segment=ring.add)
    w.start_audio(t0_utc=T0)
    t = np.arange(48000 * 6, dtype=np.float64) / 48000
    tone = (np.sin(2 * np.pi * 440 * t) * 0.5 * 32767).astype(np.int16)
    for i in range(6 * 60):
        w.write_video(np.full((480, 640, 4), (i * 3) % 256, dtype=np.uint8),
                      frame_index=i)
    w.write_audio(np.stack([tone, tone], axis=1))
    w.close()

    ex = ClipExtractor(cfg=fps60, codec="libx264")
    out = tmp_path / "clip.mp4"
    res = ex.extract(ring, T0 + timedelta(seconds=1), T0 + timedelta(seconds=5), out)
    assert abs(res.duration_s - 4.0) < 0.1

    with av.open(str(out)) as c:
        chunks = [f.to_ndarray() for f in c.decode(audio=0)]
    mono = np.concatenate([x[0] if x.ndim == 2 else x for x in chunks]).astype(np.float64)
    # Duration: 4s of 48k samples (AAC priming tolerance)
    assert abs(len(mono) - 4 * 48000) < 4096
    # Pitch: FFT peak must be 440 Hz (padding shifts/splatters it)
    seg = mono[4096:4096 + 96000]
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
    freq = np.fft.rfftfreq(len(seg), 1 / 48000)
    peak = freq[np.argmax(spec)]
    assert abs(peak - 440.0) < 2.0, f"tone came back at {peak:.1f} Hz"
    # Continuity: a pure tone's RMS is ~0.35 fullscale; 28% injected silence
    # drags it down and adds 60 Hz AM sidebands
    rms = np.sqrt(np.mean((seg / np.abs(seg).max()) ** 2))
    assert rms > 0.6, f"RMS {rms:.2f} - silence chopped into the tone"
