from types import SimpleNamespace

import pytest

from ghost_native_fixture import attempt, frame, producer
from sm64_events.ghost.recording import PoseRecording
from sm64_events.replay.service import ReplayService
from test_replay_recorder import FakeAudioSource, FakeAvSink, FakeVideoSource, WIN, make_recorder


class Source:
    def __init__(self, *, on_batch, producer_pid):
        self.emit, self.pid = on_batch, producer_pid
        self.joined = True
        self.started = False

    def start(self):
        self.started = True

    def stop(self, timeout_s=3):
        return self.joined


def test_retired_owner_rejects_late_callbacks_and_preserves_unjoined_archive(tmp_path):
    owner = PoseRecording(tmp_path / "poses.sqlite", Source, producer_pid=123,
                          retention_s=300, allowed=lambda: True)
    owner.start()
    owner.source.emit(producer(), (frame(1),))
    owner.source.joined = False
    with pytest.raises(RuntimeError, match="cleanup pending"):
        owner.stop()
    assert not owner.archive.status()["closed"]
    owner.source.emit(producer(), (frame(2),))
    assert owner.archive.status()["records"] == 1
    owner.source.joined = True
    owner.stop()
    assert owner.archive.status()["closed"]


def test_recorder_lock_pause_and_cleanup_own_native_capture(tmp_path):
    rec = make_recorder(tmp_path, FakeVideoSource(), FakeAudioSource(),
                        video_sink_factory=lambda *args: FakeAvSink())
    rec._ghost_capture_factory = Source
    rec._begin_capture(WIN)
    source, lease, archive = rec.ghost_capture, rec._rec_lock, rec.pose_archive
    assert source.started and source.pid == WIN.pid
    source.emit(producer(), (frame(1),))
    rec.set_session_paused(True)
    source.emit(producer(), (frame(2),))
    assert rec.ghost_capture is source and archive.status()["records"] == 1
    rec.set_session_paused(False)
    source.emit(producer(), (frame(3),))
    source.joined = False
    rec._teardown_capture()
    assert rec._capture_cleanup_failed and not lease.closed
    assert not archive.status()["closed"]
    source.emit(producer(), (frame(4),))
    assert archive.status()["records"] == 2
    source.joined = True
    rec._teardown_capture()
    assert lease.closed and archive.status()["closed"]
    rec.stop()


def test_viewer_never_constructs_native_source_or_archive(tmp_path):
    rec = make_recorder(tmp_path, FakeVideoSource(), FakeAudioSource(),
                        recorder_lock_factory=lambda: None)
    rec._ghost_capture_factory = lambda **kwargs: pytest.fail("viewer requested native capture")
    rec._begin_capture(WIN)
    assert rec.pose_archive is None and rec.ghost_capture is None
    rec.stop()


def test_service_preservation_uses_grab_and_refuses_outside_attempt(tmp_path):
    seen = []
    archive = SimpleNamespace(export_attempt=lambda *args, **kw: seen.append(kw) or {"records": 3})
    row = attempt(end=110)
    row.segment_id, row.course_id, row.star_id, row.strat_tag = None, 24, 1, None
    event = SimpleNamespace(type="star_collected", payload={"course_id": 24, "star_id": 1, "grab_frame": 103})
    service = ReplayService.__new__(ReplayService)
    service.recorder = SimpleNamespace(pose_archive=archive)
    service.tracker = SimpleNamespace(db=SimpleNamespace(events_between=lambda *args: [event]))
    assert service._preserve_ghost(row, tmp_path / "clip.ghost")[1] == {"records": 3}
    assert seen[-1]["end_frame"] == 103
    event.payload["grab_frame"] = 111
    service._preserve_ghost(row, tmp_path / "clip.ghost")
    assert seen[-1]["end_frame"] == 110
    row.segment_id = "segment-one"
    event.payload["grab_frame"] = 103
    service._preserve_ghost(row, tmp_path / "clip.ghost")
    assert seen[-1]["end_frame"] == 110
