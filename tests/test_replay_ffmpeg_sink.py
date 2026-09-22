"""FfmpegAvSink — one ffmpeg muxes video (stdin) + audio (named pipe) on a
single wall-clock, emitting combined A+V MPEG-TS segments. This is the fix for
the two-clock A/V drift: video is wall-clock-stamped + CFR-locked, audio is
wall-clock-stamped + aresample=async-locked to the same master.
"""
import io
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from sm64_events.core.paths import bundled_ffmpeg
from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.ffmpeg_sink import FfmpegAvSink, parse_segment_csv
from ffmpeg_spawn_fixture import capture_spawn

T0 = datetime(2026, 6, 12, 1, 0, 0, tzinfo=timezone.utc)


def _ffmpeg():
    ff = bundled_ffmpeg() or shutil.which("ffmpeg")
    if not ff:
        pytest.skip("ffmpeg binary not available")
    return ff


def test_spawn_args_pin_av_single_mux_contract(tmp_path, monkeypatch):
    """The CFR feed's contract (picture_feed=False): the pre-2026-09-02
    ring, still the shape the in-process fallback and older clips have."""
    """Pins the ffmpeg arg contract — each flag is load-bearing for the
    single-clock sync model (see ffmpeg_sink docstring / the drift memory):
    wallclock BEFORE each input, cfr video, aresample=async audio, both
    streams mapped into A+V segments."""
    cfg = ReplayConfig(scratch_dir=tmp_path, fps=60, segment_s=2.0,
                       picture_feed=False)
    captured = capture_spawn(monkeypatch, cfg)
    a = captured["args"]

    # Background spawn: no console window AND no busy-cursor feedback. The
    # cursor half is the flashing-mouse bug (2026-08-07): without
    # STARTF_FORCEOFFFEEDBACK every CreateProcess from the windowed exe shows
    # the "working in background" pointer ~2 s, and this child is the one that
    # respawns in a loop when it cannot encode.
    kw = captured["kwargs"]
    assert kw["creationflags"] == subprocess.CREATE_NO_WINDOW
    feedback_off = getattr(subprocess, "STARTF_FORCEOFFFEEDBACK", 0x80)
    assert kw["startupinfo"].dwFlags & feedback_off

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
    # The ring is the quality CEILING for every clip cut from it: it must
    # target a picture quality, not a bitrate that undershoots on easy scenes
    # and clips detail on hard ones (blurry-recording bug, 2026-07-23).
    assert after("-cq").isdigit()
    assert after("-b:v") == "0"     # a bitrate target would override -cq


@pytest.mark.parametrize("codec,quality_flag,idr_flag", [
    ("libx264", "-crf", None), ("h264_nvenc", "-cq", "-forced-idr"),
    ("h264_amf", "-qp_p", "-forced_idr"),
    ("h264_qsv", "-global_quality", "-forced_idr"),
])
def test_spawn_args_follow_the_picked_codec(tmp_path, monkeypatch, codec, quality_flag,
                                          idr_flag):
    """The sink encodes with pick_video_codec()'s answer, threaded through the
    recorder. Hardcoding h264_nvenc here was the flashing-mouse bug
    (2026-08-07): on a machine without an NVIDIA encoder the child died at
    birth and the respawn loop never ended."""
    cfg = ReplayConfig(scratch_dir=tmp_path, fps=60, segment_s=2.0)
    captured = capture_spawn(monkeypatch, cfg, codec)
    a = captured["args"]
    assert a[a.index("-c:v") + 1] == codec
    assert quality_flag in a
    assert a[a.index("-bf") + 1] == "0"
    if idr_flag:
        assert a[a.index(idr_flag) + 1] == "1"
    else:
        assert "-forced-idr" not in a and "-forced_idr" not in a


def test_mux_initialization_failure_closes_its_child_and_leaves_no_feed(tmp_path, monkeypatch):
    from sm64_events.replay import ffmpeg_sink

    events = []
    class Child:
        stdin = io.BytesIO()
        stdout = io.BytesIO()
        stderr = io.BytesIO()
        def wait(self, timeout):
            events.append("waited")
            return 0

    child = Child()
    def broken_mux(*args):
        raise OSError("mux unavailable")

    monkeypatch.setattr(ffmpeg_sink.subprocess, "Popen", lambda *args, **kw: child)
    sink = FfmpegAvSink(ReplayConfig(scratch_dir=tmp_path), lambda seg: None,
                        on_fed=lambda *args, **kw: events.append("fed"))
    monkeypatch.setattr(sink, "_open_mux", broken_mux)
    monkeypatch.setattr(sink, "_respawn_delay", lambda: 0)
    assert sink._write_frame(np.zeros((96, 320, 4), np.uint8), (1, T0.timestamp())) is None
    assert sink._proc is None and child.stdin.closed
    assert events == ["waited"]


class _ReadsAudioOnlyAfterVideo:
    """A child that services the audio pipe only once its video stdin has
    ended -- what ffmpeg does when its scheduler waits on the video input. On
    a loaded runner the real one did, and stop() hung in the pipe's flush."""

    def __init__(self, args, **_kwargs):
        pipe = next(arg for arg in args if arg.startswith("\\\\.\\pipe\\"))
        self.video_ended, self.exited = threading.Event(), threading.Event()
        child = self

        class Stdin:
            closed = False

            def write(self, data):
                return len(data)

            def flush(self):
                pass

            def close(self):
                self.closed = True
                child.video_ended.set()

        self.stdin, self.stdout, self.stderr = Stdin(), io.BytesIO(), io.BytesIO()
        opened = threading.Event()

        def serve():
            with open(pipe, "rb", buffering=0) as audio:   # connects the pipe
                opened.set()
                self.video_ended.wait()
                try:
                    while audio.read(65536):
                        pass
                except OSError:
                    pass   # the sink disconnected its end: EOF
            self.exited.set()
        threading.Thread(target=serve, daemon=True).start()
        opened.wait(5)

    def poll(self):
        return 0 if self.exited.is_set() else None

    def wait(self, timeout=None):
        if not self.exited.wait(timeout):
            raise subprocess.TimeoutExpired("fake ffmpeg", timeout)
        return 0

    def kill(self):
        self.video_ended.set()


def test_stop_ends_the_video_before_flushing_audio_the_child_reads_last(tmp_path, monkeypatch):
    from sm64_events.replay import ffmpeg_sink
    monkeypatch.setattr(ffmpeg_sink.subprocess, "Popen", _ReadsAudioOnlyAfterVideo)
    monkeypatch.setattr(ffmpeg_sink, "_assign_kill_on_close", lambda proc: None)
    sink = FfmpegAvSink(ReplayConfig(scratch_dir=tmp_path, fps=30, picture_feed=False),
                        lambda seg: None, ffmpeg="ffmpeg", codec="libx264")
    sink.start()
    frame = np.zeros((240, 320, 4), np.uint8)
    for _ in range(15):
        sink.submit(frame)
        sink.submit_audio(np.zeros((1600, 2), np.int16).tobytes())
        time.sleep(1 / 30)
    stopper = threading.Thread(target=sink.stop, daemon=True)
    stopper.start()
    stopper.join(10)
    assert not stopper.is_alive(), "stop() is waiting on an audio flush the child will never read"


def test_respawn_backoff_scales_with_young_deaths_and_resets(tmp_path):
    """A child that dies young (encoder init failure, full disk) must not be
    respawned per write attempt — that ran 331 restarts in one sitting
    (2026-06-21) and flashes the busy cursor ~2 s per spawn. A healthy run
    resets the streak so a one-off death still recovers instantly."""
    cfg = ReplayConfig(scratch_dir=tmp_path, fps=60, segment_s=2.0)
    sink = FfmpegAvSink(cfg, lambda s: None, ffmpeg="ffmpeg")
    sink._spawned_at_mono = time.monotonic()        # just spawned, dying now
    assert [sink._respawn_delay() for _ in range(6)] == \
        [2.0, 4.0, 8.0, 16.0, 30.0, 30.0]
    sink._spawned_at_mono = time.monotonic() - 60   # child lived a minute
    assert sink._respawn_delay() == 0.0
    sink._spawned_at_mono = time.monotonic()
    assert sink._respawn_delay() == 2.0             # streak restarted


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


def test_an_old_childs_final_segment_retains_its_own_media_clock(tmp_path):
    from sm64_events.replay.media import MediaRun

    path = tmp_path / "old.ts"
    path.write_bytes(b"old segment")
    old = MediaRun("old", T0.timestamp())
    received = []
    sink = FfmpegAvSink(ReplayConfig(scratch_dir=tmp_path), received.append)
    sink._anchor_utc = T0 + timedelta(seconds=50)
    sink._media_run = MediaRun("new", sink._anchor_utc.timestamp())
    proc = type("Child", (), {"stdout": io.BytesIO(b"old.ts,2,4\n")})()
    sink._segment_list_loop(proc, (320, 240), old)
    assert len(received) == 1
    assert received[0].media_run == old
    assert received[0].utc_start == T0 + timedelta(seconds=2)
    assert received[0].utc_end == T0 + timedelta(seconds=4)


@pytest.mark.skipif(bundled_ffmpeg() is None and shutil.which("ffmpeg") is None,
                    reason="no ffmpeg")
def test_av_sink_produces_synced_av_segments(tmp_path):
    """End to end: feed video frames + 48k PCM, get combined A+V segments
    whose audio and video durations match (one clock) and whose wall spans
    are ~2 s each."""
    import av
    # The CFR feed's own proof; the picture feed has its own in
    # test_replay_picture_feed.py (one frame per picture, ~30/s).
    cfg = ReplayConfig(scratch_dir=tmp_path, fps=60, picture_feed=False)
    segs = []
    # The codec the recorder would pick on THIS machine, never the
    # constructor's h264_nvenc default: without an NVIDIA encoder that child
    # dies at birth and the sink writes nothing (a GitHub runner, 2026-09-21).
    from sm64_events.replay.encoder import pick_video_codec
    ffmpeg = _ffmpeg()
    sink = FfmpegAvSink(cfg, segs.append, ffmpeg=ffmpeg, codec=pick_video_codec(ffmpeg))
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


def test_picture_audio_waits_between_batches_then_fills_real_silence():
    """Padding between valid callbacks used to displace their real PCM."""
    from sm64_events.replay.ffmpeg_sink import AudioPacer
    clock, padding, real = [0.0], [], []
    pacer = AudioPacer(48000, lambda: clock[0], padding.append,
                      write_at=lambda data, end: real.append((data, end)), idle_grace_s=.05)
    a, b = _sec_of_pcm(.01), _sec_of_pcm(.03)
    pacer.feed(a, ends_at=10.0)
    for tick in [.005, .015, .025, .035]:
        clock[0] = tick
        assert pacer.tick() == 0
    pacer.feed(b, ends_at=10.03)
    clock[0] = .075  # still inside the next batch's grace
    assert pacer.tick() == 0
    clock[0] = .1  # source really went quiet: catch up to the wall clock
    assert pacer.tick() == 48000 * .06
    assert real == [(a, 10.0), (b, 10.03)]  # every real byte and stamp survived
    assert sum(len(p) // 4 for p in padding) == 48000 * .06


def test_empty_audio_does_not_keep_a_silent_source_alive():
    from sm64_events.replay.ffmpeg_sink import AudioPacer
    clock, written = [0.0], []
    pacer = AudioPacer(48000, lambda: clock[0], written.append, idle_grace_s=.05)
    pacer.tick()
    clock[0] = .1
    pacer.feed(b"")
    assert pacer.tick() == 4800  # works even before the first real batch


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
