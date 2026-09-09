"""CPU-only capture ownership faults; never open a mapping, window or encoder."""
import threading
import io
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from sm64_events.replay import pluginsource as P
from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.recorder import ReplayRecorder
from sm64_events.replay.ring import SegmentInfo
from test_replay_recorder import FakeAudioSource, FakeAvSink, FakeVideoSource, WIN


class Stream:
    def __init__(self):
        self.want = False
        self.touches = 0
        self.seq = 0
        self.fail_wait = False
        self.graphics_profile = SimpleNamespace(refresh=lambda *_args: None)

    def header(self):
        return SimpleNamespace(write_seq=self.seq, alive=1, initiated=True, dropped=0)

    def set_want_frames(self, want):
        self.want = want

    def touch(self):
        self.touches += 1

    def wait(self, timeout):
        if self.fail_wait:
            raise OSError("reader lost its mapping")
        self.seq += 1


@pytest.mark.parametrize("fault", [False, True])
def test_probe_always_releases_temporary_demand(fault):
    stream = Stream()
    stream.fail_wait = fault
    if fault:
        with pytest.raises(OSError):
            P.pictures_flow(stream)
    else:
        assert P.pictures_flow(stream) == (True, None)
    assert not stream.want
    assert stream.touches > 0


def test_reader_failure_clears_demand_and_notifies_owner():
    stream = Stream()
    stream.fail_wait = True
    source = P.PluginVideoSource(stream, [], None)
    stopped = threading.Event()
    source.start(lambda *args: pytest.fail("no picture was published"), stopped.set)
    assert stopped.wait(1)
    assert not stream.want
    touches = stream.touches
    source.refresh_demand()
    source.stop()
    assert not stream.want and stream.touches == touches


def test_failed_reader_thread_start_leaves_no_demand(monkeypatch):
    stream = Stream()
    source = P.PluginVideoSource(stream, [], None)

    class FailedThread:
        def __init__(self, **kwargs):
            pass
        def start(self):
            raise RuntimeError("thread creation failed")

    monkeypatch.setattr(P.threading, "Thread", FailedThread)
    with pytest.raises(RuntimeError):
        source.start(lambda *args: None, lambda: None)
    source.stop()
    source.refresh_demand()
    assert not stream.want


def test_source_stopped_before_start_cannot_revive_capture():
    stream = Stream()
    source = P.PluginVideoSource(stream, [], None)
    source.request_stop()
    source.start(lambda *args: None, lambda: None)
    assert not stream.want and source._thread is None


def recorder(tmp_path, events, video_factory, sink=None, acquire=True):
    class Lock:
        def close(self):
            events.append("unlock")
    return ReplayRecorder(
        ReplayConfig(scratch_dir=tmp_path / "buffer"), lambda _: WIN,
        video_factory, lambda _: FakeAudioSource(), codec="libx264",
        video_sink_factory=lambda *args: sink or FakeAvSink(),
        recorder_lock_factory=lambda: Lock() if acquire else None,
        release_capture=lambda: events.append("release mapping"))


def test_factory_failure_releases_mapping_before_lock_once(tmp_path):
    events = []
    def fail(_):
        events.append("mapping created")
        raise OSError("factory failed after acquiring mapping")
    rec = recorder(tmp_path, events, fail)
    with pytest.raises(OSError):
        rec._begin_capture(WIN)
    assert events == ["mapping created", "release mapping", "unlock"]
    rec.stop()
    # Shutdown reacquires ownership only to clean the failed startup scratch.
    # The capture mapping still releases exactly once, before its original lock.
    assert events == ["mapping created", "release mapping", "unlock", "unlock"]
    rec.ledger.reset()


def test_failed_source_start_is_stopped_before_releasing_ownership(tmp_path):
    events = []
    class Partial(FakeVideoSource):
        def start(self, *args):
            events.append("demand enabled")
            raise RuntimeError("partial start")
        def stop(self):
            events.append("source stopped")
    rec = recorder(tmp_path, events, lambda _: Partial())
    with pytest.raises(RuntimeError):
        rec._begin_capture(WIN)
    assert events == ["demand enabled", "source stopped", "release mapping", "unlock"]
    rec.ledger.reset()


def test_viewer_only_never_releases_someone_elses_capture(tmp_path):
    events = []
    rec = recorder(tmp_path, events, lambda _: pytest.fail("factory called"), acquire=False)
    rec._begin_capture(WIN)
    rec.stop()
    assert events == []
    assert not (tmp_path / "buffer").exists()


def test_viewer_start_preserves_the_owners_existing_footage(tmp_path, monkeypatch):
    events, attempted = [], threading.Event()
    rec = recorder(tmp_path, events, lambda _: pytest.fail("factory called"), acquire=False)
    rec._cfg.scratch_dir.mkdir()
    sentinel = rec._cfg.scratch_dir / "owners-footage.ts"
    sentinel.write_bytes(b"must survive")
    def no_lock():
        attempted.set()
        return None
    rec._recorder_lock_factory = no_lock
    rec._codec = None
    monkeypatch.setattr("sm64_events.replay.recorder.pick_video_codec",
                        lambda: pytest.fail("viewer probed encoder"))
    rec.start()
    try:
        assert attempted.wait(1)
        assert sentinel.read_bytes() == b"must survive"
    finally:
        rec.stop()
    assert events == [] and sentinel.exists()


def test_demand_stops_before_blocking_encoder_drain(tmp_path):
    events = []
    draining, drained = threading.Event(), threading.Event()
    class Video(FakeVideoSource):
        def request_stop(self):
            events.append("demand disabled")
        def stop(self):
            events.append("source stopped")
    class Sink(FakeAvSink):
        def stop(self):
            draining.set()
            assert drained.wait(2)
    rec = recorder(tmp_path, events, lambda _: Video(), sink=Sink())
    rec._begin_capture(WIN)
    stopping = threading.Thread(target=rec.stop)
    stopping.start()
    try:
        assert draining.wait(1)
        assert "demand disabled" in events and "source stopped" in events
        assert "release mapping" not in events
    finally:
        drained.set()
        stopping.join(2)
    assert not stopping.is_alive()
    assert events[-2:] == ["release mapping", "unlock"]
    rec.ledger.reset()


def test_stop_during_factory_cannot_start_a_late_source(tmp_path):
    events = []
    entered, finish = threading.Event(), threading.Event()
    video = FakeVideoSource()
    def factory(_):
        entered.set()
        assert finish.wait(2)
        return video
    rec = recorder(tmp_path, events, factory)
    starting = threading.Thread(target=rec._begin_capture, args=(WIN,))
    starting.start()
    assert entered.wait(1)
    # The public stop flag revokes future startup even if the factory is
    # outside the recorder's control. Teardown waits for its owned source.
    stopping = threading.Thread(target=rec.stop)
    stopping.start()
    assert rec._stop_event.wait(1)
    finish.set()
    starting.join(2)
    stopping.join(2)
    assert not starting.is_alive() and not stopping.is_alive()
    assert video.on_frame is None and video.stopped
    assert events == ["release mapping", "unlock"]
    rec.ledger.reset()


def test_new_sink_and_respawn_cannot_overwrite_retained_segments(tmp_path, monkeypatch):
    from sm64_events.replay import ffmpeg_sink as F

    paths = []
    class Thread:
        def __init__(self, **kwargs):
            pass
        def start(self):
            pass
        def is_alive(self):
            return False
    def popen(args, **kwargs):
        path = Path(args[-1].replace("%06d", "000000"))
        path.write_bytes(f"picture identity {len(paths)}".encode())
        paths.append(path)
        return SimpleNamespace(stdin=io.BytesIO(), stdout=io.BytesIO(), stderr=io.BytesIO())
    monkeypatch.setattr(F.subprocess, "Popen", popen)
    monkeypatch.setattr(F.threading, "Thread", Thread)
    monkeypatch.setattr(F, "_assign_kill_on_close", lambda _: None)
    monkeypatch.setattr(F.FfmpegAvSink, "_open_audio_pipe", lambda _: None)
    cfg = ReplayConfig(scratch_dir=tmp_path, picture_feed=False)
    first = F.FfmpegAvSink(cfg, lambda _: None, codec="libx264")
    second = F.FfmpegAvSink(cfg, lambda _: None, codec="libx264")
    first._spawn(320, 240)
    old_bytes = paths[0].read_bytes()
    second._spawn(320, 240)  # detach/reattach creates a separate sink
    second._spawn(640, 480)  # a resize still increments its respawn counter
    assert len(set(paths)) == 3
    assert paths[0].read_bytes() == old_bytes == b"picture identity 0"
    assert paths[1].name.endswith("_00_000000.ts")
    assert paths[2].name.endswith("_01_000000.ts")


def test_previous_owner_recovers_after_another_owner_reset_shared_scratch(tmp_path):
    events = []
    first = recorder(tmp_path, events, lambda _: FakeVideoSource())
    second = recorder(tmp_path, events, lambda _: FakeVideoSource())
    first._begin_capture(WIN)
    first_archive = first._cfg.scratch_dir / first._ledger_name
    old = first._cfg.scratch_dir / "old-segment.ts"
    old.write_bytes(b"old footage")
    first.ring.add(SegmentInfo(old, "video", datetime.fromtimestamp(1, timezone.utc),
                              datetime.fromtimestamp(2, timezone.utc), old.stat().st_size))
    first._teardown_capture()
    assert first_archive.exists()
    second._begin_capture(WIN)  # its first owned attach resets shared scratch
    second._teardown_capture()
    assert not first_archive.exists() and not old.exists()
    first._begin_capture(WIN)
    try:
        assert first.status()["recording"]
        assert first_archive.exists()
        assert first.ring.coverage("video") is None
    finally:
        first.stop()
        second.stop()
        first.ledger.reset()
        second.ledger.reset()
