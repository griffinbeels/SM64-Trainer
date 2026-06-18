"""FfmpegAvSink — one ffmpeg muxes video (stdin) + audio (named pipe) on a
single wall-clock, emitting combined A+V MPEG-TS segments. This is the fix for
the two-clock A/V drift: video is wall-clock-stamped + CFR-locked, audio is
wall-clock-stamped + aresample=async-locked to the same master.
"""
import io
import shutil
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from sm64_events.core.paths import bundled_ffmpeg
from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.ffmpeg_sink import FfmpegAvSink, parse_segment_csv

T0 = datetime(2026, 6, 12, 1, 0, 0, tzinfo=timezone.utc)


def _ffmpeg():
    ff = bundled_ffmpeg() or shutil.which("ffmpeg")
    if not ff:
        pytest.skip("ffmpeg binary not available")
    return ff


def test_spawn_args_pin_av_single_mux_contract(tmp_path, monkeypatch):
    """Pins the ffmpeg arg contract — each flag is load-bearing for the
    single-clock sync model (see ffmpeg_sink docstring / the drift memory):
    wallclock BEFORE each input, cfr video, aresample=async audio, both
    streams mapped into A+V segments."""
    captured = {}

    class _FakeProc:
        def __init__(self):
            self.stdin = io.BytesIO()
            self.stdout = io.BytesIO()
            self.stderr = io.BytesIO()

    def fake_popen(args, **kwargs):
        captured["args"] = args
        return _FakeProc()

    monkeypatch.setattr(
        "sm64_events.replay.ffmpeg_sink.subprocess.Popen", fake_popen)
    monkeypatch.setattr(
        "sm64_events.replay.ffmpeg_sink._assign_kill_on_close", lambda p: None)
    cfg = ReplayConfig(scratch_dir=tmp_path, fps=60, segment_s=2.0)
    sink = FfmpegAvSink(cfg, lambda s: None, ffmpeg="ffmpeg")
    sink._spawn(320, 240)
    for t in sink._readers:
        t.join(timeout=5)
    a = captured["args"]

    # wallclock must appear before EACH -i (the entire sync model rests on it)
    inputs = [i for i, x in enumerate(a) if x == "-i"]
    assert len(inputs) == 2, "expected two inputs (video stdin + audio pipe)"
    wc = [i for i, x in enumerate(a) if x == "-use_wallclock_as_timestamps"]
    assert len(wc) == 2 and all(
        any(w < i and a[w + 1] == "1" for w in wc) for i in inputs)

    def after(flag):
        return a[a.index(flag) + 1]

    assert after("-fps_mode") == "cfr"
    assert "-vsync" not in a            # deprecated; must use -fps_mode
    assert "aresample=async=1" in " ".join(a)
    assert after("-segment_time") == "2.0"
    assert after("-reset_timestamps") == "1"
    # both streams mapped into the segments
    maps = [a[i + 1] for i, x in enumerate(a) if x == "-map"]
    assert any("v" in m for m in maps) and any("a" in m for m in maps)
    # video pipe is stdin; audio input is a windows named pipe
    assert "pipe:0" in a
    assert any(str(x).startswith(r"\\.\pipe") for x in a)


def test_parse_segment_csv_relative_to_origin(tmp_path):
    """utc is anchored once at the first frame; segment offsets are RELATIVE
    to the first segment's start, so the mapping is correct whether ffmpeg's
    CSV times are zero-based or wall-clock-epoch-based."""
    (tmp_path / "seg_000003.ts").write_bytes(b"x" * 99)
    # origin_s = 6.0 (first segment's start): this segment is the 2nd, at +2s
    seg = parse_segment_csv("seg_000003.ts,8.000000,10.000000\n",
                            T0, 6.0, tmp_path)
    assert seg.kind == "video" and seg.size_bytes == 99
    assert seg.utc_start == T0 + timedelta(seconds=2)
    assert seg.utc_end == T0 + timedelta(seconds=4)
    assert parse_segment_csv("garbage\n", T0, 0.0, tmp_path) is None
    assert parse_segment_csv("missing.ts,0,2\n", T0, 0.0, tmp_path) is None


@pytest.mark.skipif(bundled_ffmpeg() is None and shutil.which("ffmpeg") is None,
                    reason="no ffmpeg")
def test_av_sink_produces_synced_av_segments(tmp_path):
    """End to end: feed video frames + 48k PCM, get combined A+V segments
    whose audio and video durations match (one clock) and whose wall spans
    are ~2 s each."""
    import av
    cfg = ReplayConfig(scratch_dir=tmp_path, fps=60)
    segs = []
    sink = FfmpegAvSink(cfg, segs.append, ffmpeg=_ffmpeg())
    sink.start()
    frame = np.zeros((240, 320, 4), dtype=np.uint8)
    rate = 48000
    t0 = time.perf_counter()
    last_audio = t0
    i = 0
    phase = 0
    while time.perf_counter() - t0 < 6.0:
        frame = frame.copy()
        frame[:, :, 0] = i % 256
        sink.submit(frame)
        i += 1
        now = time.perf_counter()
        n = int(rate * (now - last_audio))
        if n > 0:
            idx = np.arange(phase, phase + n)
            tone = (8000 * np.sin(2 * np.pi * 440 * idx / rate)).astype(np.int16)
            sink.submit_audio(np.repeat(tone[:, None], 2, axis=1).tobytes())
            phase += n
            last_audio = now
        time.sleep(1 / 120)
    sink.stop()

    assert len(segs) >= 2, f"expected >=2 segments, got {len(segs)}"
    mid = segs[1]
    assert abs((mid.utc_end - mid.utc_start).total_seconds() - 2.0) < 0.2
    with av.open(str(mid.path)) as c:
        kinds = {s.type for s in c.streams}
        assert kinds == {"video", "audio"}, f"segment missing a stream: {kinds}"
    # A/V duration parity across the whole run (the sync guarantee)
    with av.open(str(mid.path)) as c:
        vframes = sum(1 for _ in c.decode(video=0))
    with av.open(str(mid.path)) as c:
        asamp = sum(f.samples for f in c.decode(audio=0))
    assert abs(vframes / 60.0 - asamp / 48000.0) < 0.15


def _sec_of_pcm(seconds, rate=48000):
    return np.zeros((int(rate * seconds), 2), dtype=np.int16).tobytes()


def test_audio_pacer_bridges_gaps_to_hold_realtime():
    """Regression for the choppy-video bug (2026-06-18): both pipes are
    wall-clock-stamped, so if the audio pipe falls behind the wall clock (the
    game goes quiet → WASAPI delivers nothing), ffmpeg blocks on audio and
    stops draining the VIDEO stdin, collapsing captured fps (live: 16.9 fed/s,
    >10000 duplicated frames → ~17 fps). The pacer must pad silence so the pipe
    stays at realtime through the gap. Deterministic: injected clock + writer."""
    from sm64_events.replay.ffmpeg_sink import AudioPacer
    clock = [0.0]
    written = []
    p = AudioPacer(48000, lambda: clock[0], written.append)
    # tick every 5 ms for 1 s; real audio arrives only for the first 0.2 s
    for k in range(200):
        clock[0] = k * 0.005
        if clock[0] < 0.2:
            p.feed(_sec_of_pcm(0.005))
        p.tick()
    total = sum(len(b) // 4 for b in written)
    # ~1 s of samples delivered despite audio stopping at 0.2 s (gap bridged)
    assert abs(total - 48000 * 0.995) < 48000 * 0.02
    assert total > 48000 * 0.5, "pipe fell far behind realtime — would starve ffmpeg"


def test_audio_pacer_does_not_overpad_when_audio_runs_ahead():
    """A burst of real audio ahead of the wall clock must NOT trigger silence
    padding (which would inflate/duplicate the track)."""
    from sm64_events.replay.ffmpeg_sink import AudioPacer
    clock = [0.0]
    written = []
    p = AudioPacer(48000, lambda: clock[0], written.append)
    p.feed(_sec_of_pcm(1.0))          # 1 s of audio delivered at t=0
    for k in range(100):              # tick across the next ~0.5 s
        clock[0] = k * 0.005
        assert p.tick() == 0          # already ahead → never pads
    assert sum(len(b) // 4 for b in written) == 48000  # only the real burst


def test_kill_on_close_job_reaps_child_when_handle_dies():
    """The orphan-ffmpeg backstop (live incident 2026-06-12: hung shutdown
    left ffmpeg recording into a dead terminal). Closing the job handle is
    exactly what the OS does to our handles when this process dies."""
    import ctypes
    import subprocess
    import sys

    from sm64_events.replay.ffmpeg_sink import _assign_kill_on_close

    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        job = _assign_kill_on_close(child)
        assert job is not None
        ctypes.windll.kernel32.CloseHandle(job)
        child.wait(timeout=5)
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.kill()
