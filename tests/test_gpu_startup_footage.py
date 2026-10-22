"""Failed GPU startup to decoded replay through the normal recorder/sink.

Only window pixels, audio-device samples and native control/channel access are
fixtures. Actual source lifetime, fallback selection, FFmpeg, fragments, service
View and MP4 decoding run. No emulator or installed recording resource is used.
"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import shutil
import time

import av
import numpy as np
import pytest

from sm64_events.core.paths import bundled_ffmpeg
from sm64_events.replay.clock import CaptureClock, qpc_100ns
from sm64_events.replay.extract import ClipExtractor
from sm64_events.replay.ffmpeg_sink import FfmpegAvSink
from sm64_events.replay.gpucapture import GpuCapture
from sm64_events.replay.gpucapture_session import CaptureSession
from sm64_events.replay.gpusettings import GpuSettings
from sm64_events.replay.service import ReplayService
from test_gpu_startup_fallback import Demand, setup_factory
from test_replay_picture_identity import picture, read_pictures
from test_replay_recorder import FakeAudioSource, FakeVideoSource, WIN, make_recorder, wait_for
from test_replay_service import FakeTracker, attempt


def assert_playable_fallback(service, output):
    metadata = service.view(42)
    assert metadata["frame_map"] is None and metadata["input_span"] is None
    assert metadata["duration_s"] > 1 and metadata["frame_times"]
    with service.read_clip("clip_attempt_42.mp4") as media:
        output.write_bytes(b"".join(media.chunks()))
    markers = [marker for _, marker in read_pictures(output)]
    assert len(markers) >= 40 and markers == sorted(markers)
    assert all(0 <= marker < 60 for marker in markers)
    with av.open(str(output)) as media:
        assert sum(frame.samples for frame in media.decode(audio=0)) > 48000


def test_gpu_deadline_restarts_recorder_into_playable_unstamped_mp4(tmp_path, monkeypatch):
    ffmpeg = bundled_ffmpeg() or shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg required for the actual fallback recording")
    factory, _ = setup_factory(monkeypatch)
    desktop, audio, sources = FakeVideoSource(), FakeAudioSource(), []
    factory.desktop_factory = lambda win, fps: desktop

    def make_source(win):
        camera = factory.create(win)
        sources.append(camera)
        if isinstance(camera, GpuCapture):
            camera.settings = replace(GpuSettings(), startup_s=0.02)
            camera._demand_factory = Demand
            def session(owner, demand):
                def absent_channel(**_identity):
                    raise FileNotFoundError("no published picture channel")
                return CaptureSession(owner, demand, channel_factory=absent_channel)
            camera._session_factory = session
        return camera

    recorder = make_recorder(tmp_path, None, audio, video_sink_factory=lambda cfg, on_seg, codec:
                             FfmpegAvSink(cfg, on_seg, ffmpeg=ffmpeg, codec=codec))
    recorder._video_factory = make_source
    recorder._cfg = replace(recorder._cfg, save_root=tmp_path / "saved", pre_pad_s=0,
                            post_pad_s=0, extract_wait_s=0, segment_s=0.2)
    # Both fixtures use the real arrival clock, as device audio does in the app.
    first_tick = qpc_100ns()
    origin = datetime.now(timezone.utc)
    recorder._clock_factory = lambda: CaptureClock(first_tick, origin)
    try:
        recorder._begin_capture(WIN)
        assert wait_for(sources[0]._done.is_set)
        recorder._teardown_capture()
        assert recorder._capture_closed and not recorder._capture_cleanup_failed
        recorder._begin_capture(WIN)
        assert desktop.on_frame is not None and not isinstance(sources[-1], GpuCapture)
        for marker in range(60):
            desktop.on_frame(picture(marker), first_tick + round(marker * 1e7 / 30))
            audio.on_pcm(np.zeros((1600, 2), dtype=np.int16))
            time.sleep(1 / 30)
    finally:
        recorder.stop(cleanup=False)
    assert desktop.stopped and recorder._capture_closed
    assert len(sources) == 2 and factory.diagnostics()["first_failure"]["stage"] == "channel"
    cfg = recorder._cfg
    row = attempt(started_utc=(origin + timedelta(seconds=0.1)).isoformat(),
                  ended_utc=(origin + timedelta(seconds=1.8)).isoformat(), igt_frames=51)
    service = ReplayService(cfg, recorder, ClipExtractor(cfg, "libx264", ffmpeg), FakeTracker([row]))
    assert_playable_fallback(service, tmp_path / "fallback.mp4")
