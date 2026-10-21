"""Footage failures and switches cannot revoke the native pose owner.

Use the real recorder, archive and ghost publication; GPU/devices are inert.
"""
import pytest

from ghost_native_fixture import frame, producer
from test_ghost_recording import Source, native_service
from test_replay_startup_order import Audio, PairedVideo, WIN, make


def cycle(rec):
    rec._stop_event.clear()
    rec._wait_attach = lambda _: rec._stop_event.set()
    rec._run_attach_loop()


def test_disabled_footage_starts_poses_without_media_or_codec(tmp_path, monkeypatch):
    rec, video, audio, _, lease, *_ = make(tmp_path)
    rec._ghost_capture_factory = Source
    rec._codec = None
    monkeypatch.setattr("sm64_events.replay.recorder.pick_video_codec",
                        lambda: pytest.fail("ghost-only startup probed a video codec"))
    rec.set_recording_enabled(False)
    try:
        cycle(rec)
        source = rec.ghost_capture
        assert source.started and source.pid == WIN.pid
        source.emit(producer(), (frame(1),))
        assert rec.pose_archive.status()["records"] == 1
        assert video.starts == 0 and audio.on_pcm is None
        assert rec.status()["capture_state"] == "off" and not lease.closed
        assert not rec.cleanup_scratch()
    finally:
        rec.stop(cleanup=False)
    assert lease.closed and rec.pose_archive.status()["closed"]


def test_video_failure_and_off_on_keep_one_pose_archive_and_machine_lease(tmp_path):
    rec, video, *_ = make(tmp_path)
    rec._ghost_capture_factory = Source
    try:
        cycle(rec)
        source, archive, lease = rec.ghost_capture, rec.pose_archive, rec._rec_lock
        source.emit(producer(), (frame(1),))
        rec._source_stopped(video)
        rec._video_factory = lambda _: PairedVideo([])
        rec._audio_factory = lambda _: Audio([])
        cycle(rec)
        assert rec.ghost_capture is source and rec.pose_archive is archive
        rec.set_recording_enabled(False)
        cycle(rec)
        source.emit(producer(), (frame(2),))
        assert rec.status()["capture_state"] == "off"
        assert not lease.closed and not archive.status()["closed"]
        rec.set_recording_enabled(True)
        cycle(rec)
        source.emit(producer(), (frame(3),))
        assert rec.ghost_capture is source and rec._rec_lock is lease
        assert archive.status()["records"] == 3 and rec.status()["recording"]
    finally:
        rec.stop(cleanup=False)


def test_video_constructor_failure_does_not_close_ghosts(tmp_path):
    rec, *_ = make(tmp_path)
    rec._ghost_capture_factory = Source
    rec._video_factory = lambda _: (_ for _ in ()).throw(RuntimeError("GPU unavailable"))
    try:
        rec._ensure_pose_capture(WIN)
        source = rec.ghost_capture
        with pytest.raises(RuntimeError, match="GPU unavailable"):
            rec._begin_capture(WIN)
        source.emit(producer(), (frame(1),))
        assert rec.ghost_capture is source
        assert rec.pose_archive.status()["records"] == 1 and not rec._rec_lock.closed
    finally:
        rec.stop(cleanup=False)


def test_pose_only_attempt_commits_and_is_discoverable_without_video(tmp_path):
    service, row, archive = native_service(tmp_path)
    archive.close()
    rec, *_ = make(tmp_path / "capture")
    rec._ghost_capture_factory = Source
    rec.set_recording_enabled(False)
    service.recorder = rec
    try:
        cycle(rec)
        rec.ghost_capture.emit(producer(), tuple(frame(n) for n in (1, 2, 3)))
        result = service.preserve_native(row.id, wait_tail=False)
        assert result["native_ghost"] and result["native_ghost_reason"] is None
        found = service.find_native(row.id)
        assert found and found[0].is_file()
        assert not service.extractor.calls and service.find_saved(row.id) is None
    finally:
        rec.stop(cleanup=False)


def test_off_poses_follow_rom_retarget_and_window_loss(tmp_path):
    rec, *_ = make(tmp_path)
    rec._ghost_capture_factory = Source
    rec.set_recording_enabled(False)
    try:
        cycle(rec)
        first, first_archive = rec.ghost_capture, rec.pose_archive
        rec.retarget_capture()
        cycle(rec)
        assert rec.ghost_capture is not first and first_archive.status()["closed"]
        current = rec.pose_archive
        rec._window_finder = lambda _: None
        cycle(rec)
        assert rec.ghost_capture is None and current.status()["closed"]
        assert rec._rec_lock is None
    finally:
        rec.stop(cleanup=False)


def test_stop_during_native_factory_does_not_start_late_capture(tmp_path):
    from sm64_events.ghost.recording import PoseRecording
    owner = None
    def factory(**kwargs):
        owner.request_stop()
        return Source(**kwargs)
    owner = PoseRecording(tmp_path / "poses.sqlite", factory, producer_pid=WIN.pid, allowed=lambda: True)
    owner.start()
    assert not owner.source.started
    owner.source.emit(producer(), (frame(1),))
    assert owner.archive.status()["records"] == 0
    owner.stop()


def test_rom_retarget_during_media_close_is_not_lost(tmp_path):
    rec, video, *_ = make(tmp_path)
    rec._ghost_capture_factory = Source
    try:
        cycle(rec)
        first = rec.ghost_capture
        close = video.stop
        def stop():
            close()
            rec.retarget_capture()  # poller changes ROM while media closes
        video.stop = stop
        rec._video_factory = lambda _: PairedVideo([])
        rec._audio_factory = lambda _: Audio([])
        rec._source_stopped(video)
        cycle(rec)
        assert rec._pose_retarget
        cycle(rec)
        assert rec.ghost_capture is not first
    finally:
        rec.stop(cleanup=False)
