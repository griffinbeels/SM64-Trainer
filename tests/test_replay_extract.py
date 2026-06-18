"""Extract a clip from A+V MPEG-TS ring segments (single-mux architecture).

Fixtures build REAL combined audio+video segments with the ffmpeg binary —
the exact format the FfmpegAvSink emits in production (one continuous encode,
segment muxer slicing it, reset_timestamps per segment). The extractor cuts a
sub-span with the ffmpeg binary too (concat + accurate seek + re-encode video
/ copy audio / faststart), so these tests need ffmpeg on disk; they skip with
a clear reason when it is absent (the production app always bundles it).
"""
import shutil
import subprocess
from datetime import datetime, timedelta, timezone

import av
import pytest

from sm64_events.core.paths import bundled_ffmpeg
from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.extract import ClipExtractor
from sm64_events.replay.ring import SegmentInfo, SegmentRing

T0 = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
CFG = ReplayConfig(fps=60)
SEG_S = 2.0


def _ffmpeg() -> str:
    ff = bundled_ffmpeg() or shutil.which("ffmpeg")
    if not ff:
        pytest.skip("ffmpeg binary not available")
    return ff


def build_av_buffer(tmp_path, seconds=8, t0=T0, hole_at=None, hole_s=0.0):
    """Real combined A+V segments via the ffmpeg binary. Returns a SegmentRing
    of kind='video' segments (audio lives INSIDE each .ts). hole_at (a segment
    index) shifts that segment and all later ones `hole_s` seconds later,
    simulating an idle-discard coverage hole in the ring."""
    ff = _ffmpeg()
    buf = tmp_path / "buf"
    buf.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        ff, "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=60:duration={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-g", "120", "-force_key_frames", "expr:gte(t,n_forced*2)",
        "-c:a", "aac", "-ar", "48000",
        "-f", "segment", "-segment_time", "2", "-segment_format", "mpegts",
        "-reset_timestamps", "1",
        str(buf / "seg_%04d.ts"),
    ], check=True, capture_output=True)
    ring = SegmentRing(retention_s=None, max_bytes=10**9)
    for i, p in enumerate(sorted(buf.glob("seg_*.ts"))):
        shift = hole_s if (hole_at is not None and i >= hole_at) else 0.0
        start = t0 + timedelta(seconds=i * SEG_S + shift)
        ring.add(SegmentInfo(path=p, kind="video", utc_start=start,
                             utc_end=start + timedelta(seconds=SEG_S),
                             size_bytes=p.stat().st_size))
    return ring


def _stream_kinds(path):
    with av.open(str(path)) as c:
        return {s.type for s in c.streams}


def _durations(path):
    """(video_seconds, audio_seconds) by decoding — ground truth, not metadata."""
    with av.open(str(path)) as c:
        vframes = sum(1 for _ in c.decode(video=0))
    with av.open(str(path)) as c:
        asamp = sum(f.samples for f in c.decode(audio=0))
    return vframes / 60.0, asamp / 48000.0


def test_extract_produces_scrubbable_av_mp4(tmp_path):
    ring = build_av_buffer(tmp_path, seconds=8)
    ex = ClipExtractor(cfg=CFG, codec="libx264")
    out = tmp_path / "clip.mp4"
    res = ex.extract(ring, T0 + timedelta(seconds=2), T0 + timedelta(seconds=6), out)
    assert res.truncated is False
    assert abs(res.duration_s - 4.0) < 0.2
    assert _stream_kinds(out) == {"video", "audio"}
    # faststart: moov atom must precede mdat for instant browser scrubbing
    head = out.read_bytes()[:8192]
    assert head.find(b"moov") != -1 and (
        head.find(b"mdat") == -1 or head.find(b"moov") < head.find(b"mdat"))


def test_extract_av_stay_in_sync(tmp_path):
    """The whole point: audio and video durations match within a frame —
    they were muxed on ONE clock at capture, so a cut can't shear them."""
    ring = build_av_buffer(tmp_path, seconds=8)
    ex = ClipExtractor(cfg=CFG, codec="libx264")
    out = tmp_path / "clip.mp4"
    ex.extract(ring, T0 + timedelta(seconds=2), T0 + timedelta(seconds=6), out)
    vsec, asec = _durations(out)
    assert abs(vsec - 4.0) < 0.2
    assert abs(asec - 4.0) < 0.2
    assert abs(vsec - asec) < 0.1


def test_extract_preserves_real_audio_content(tmp_path):
    """The captured 440 Hz tone must survive the cut as real, non-silent
    audio — proving the clip carries the segments' own audio, not a
    zero-filled placeholder."""
    import numpy as np
    ring = build_av_buffer(tmp_path, seconds=8)
    ex = ClipExtractor(cfg=CFG, codec="libx264")
    out = tmp_path / "clip.mp4"
    ex.extract(ring, T0 + timedelta(seconds=2), T0 + timedelta(seconds=6), out)
    with av.open(str(out)) as c:
        chunks = [f.to_ndarray() for f in c.decode(audio=0)]
    mono = np.concatenate(
        [x[0] if x.ndim == 2 else x for x in chunks]).astype(np.float64)
    assert mono.size > 0, "no audio decoded"
    rms = np.sqrt(np.mean((mono / (np.abs(mono).max() + 1e-9)) ** 2))
    assert rms > 0.3, f"audio is silent/placeholder (RMS {rms:.3f})"
    seg = mono[4096:4096 + 96000]
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
    peak = np.fft.rfftfreq(len(seg), 1 / 48000)[np.argmax(spec)]
    assert abs(peak - 440.0) < 3.0, f"tone came back at {peak:.1f} Hz"


def test_no_footage_raises(tmp_path):
    ring = SegmentRing(retention_s=None, max_bytes=10**9)
    ex = ClipExtractor(cfg=CFG, codec="libx264")
    with pytest.raises(ValueError):
        ex.extract(ring, T0, T0 + timedelta(seconds=1), tmp_path / "c.mp4")


def test_sub_frame_span_raises(tmp_path):
    ring = build_av_buffer(tmp_path, seconds=8)
    ex = ClipExtractor(cfg=CFG, codec="libx264")
    with pytest.raises(ValueError):
        ex.extract(ring, T0 + timedelta(seconds=2),
                   T0 + timedelta(seconds=2, microseconds=5), tmp_path / "c.mp4")


def test_clamps_and_flags_truncation(tmp_path):
    ring = build_av_buffer(tmp_path, seconds=8)
    ex = ClipExtractor(cfg=CFG, codec="libx264")
    out = tmp_path / "clip.mp4"
    res = ex.extract(ring, T0 - timedelta(seconds=10), T0 + timedelta(seconds=2), out)
    assert res.truncated is True
    assert abs(res.duration_s - 2.0) < 0.2


def test_hole_truncates_to_contiguous_run_and_stays_synced(tmp_path):
    """A span crossing an idle-discard hole is clamped to the contiguous run
    containing the start, marked truncated, and the result stays A/V-synced —
    NEVER concatenated across the hole (which would collapse wall time)."""
    # segments 0,1 then a 5 s hole before 2,3 : run containing T0 = [0s, 4s)
    ring = build_av_buffer(tmp_path, seconds=8, hole_at=2, hole_s=5.0)
    ex = ClipExtractor(cfg=CFG, codec="libx264")
    out = tmp_path / "clip.mp4"
    res = ex.extract(ring, T0, T0 + timedelta(seconds=8), out)
    assert res.truncated is True
    assert abs(res.duration_s - 4.0) < 0.2   # clamped to the first run
    vsec, asec = _durations(out)
    assert abs(vsec - asec) < 0.1
