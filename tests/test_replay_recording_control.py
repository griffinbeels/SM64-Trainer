"""Manual full-stop contracts, with inert video/audio/encoder boundaries.

No device, native mapping, GPU, app server or child encoder is opened.
"""
from dataclasses import replace
import json
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from sm64_events.replay.config import ReplayConfig, apply_settings_file, save_settings
from sm64_events.replay.service import ReplayService
from sm64_events.server.replay_api import create_replay_router
from test_replay_startup_order import Audio, HeldLock, PairedVideo, WIN, make, starting


def service(tmp_path, rec):
    cfg = replace(rec._cfg, settings_path=tmp_path / "settings.json",
                  save_root=tmp_path / "saved")
    rec._cfg = cfg
    return ReplayService(cfg, rec, None, None)


def client(svc):
    app = FastAPI()
    app.include_router(create_replay_router(svc))
    return TestClient(app)


@pytest.mark.parametrize("value", [0, 1, "false", "true", None, [], {}])
def test_recording_endpoint_requires_literal_boolean(tmp_path, value):
    rec, *_ = make(tmp_path)
    svc = service(tmp_path, rec)
    assert client(svc).put("/api/replay/recording", json={"enabled": value}).status_code == 422
    assert rec.recording_enabled and not svc.cfg.settings_path.exists()


@pytest.mark.parametrize("body", [{}, {"enabled": False, "extra": True}])
def test_recording_endpoint_rejects_missing_or_extra_keys(tmp_path, body):
    rec, *_ = make(tmp_path)
    assert client(service(tmp_path, rec)).put("/api/replay/recording", json=body).status_code == 422


def test_put_returns_during_blocked_startup_then_proves_full_stop(tmp_path):
    rec, video, audio, _, held, *_ = make(tmp_path, blocking=True)
    svc = service(tmp_path, rec)
    with starting(rec, audio) as (worker, errors):
        assert rec.status()["capture_state"] == "starting"
        result, done = [], threading.Event()

        def request():
            result.append(client(svc).put("/api/replay/recording", json={"enabled": False}))
            done.set()

        caller = threading.Thread(target=request, name="owned-recording-put", daemon=True)
        caller.start()
        try:
            assert done.wait(1), "switch waited on blocked source initialization"
            assert not audio.release.is_set() and worker.is_alive()
            body = result[0].json()
            assert result[0].status_code == 200
            assert body["enabled"] is True and body["recording_enabled"] is False
            assert body["capture_state"] == "stopping" and not body["recording"]
            assert apply_settings_file(svc.cfg).recording_enabled is False
        finally:
            audio.release.set()
            caller.join(3)
            worker.join(3)
        assert not caller.is_alive() and not worker.is_alive() and not errors
        assert video.starts == 0 and audio.stopped and video.stopped and held.closed
        assert rec.status()["capture_state"] == "off"


def test_rapid_off_on_retires_old_start_before_new_capture(tmp_path):
    rec, old_video, old_audio, _, old_lease, *_ = make(tmp_path, blocking=True)
    with starting(rec, old_audio) as (worker, errors):
        rec.set_recording_enabled(False)
        rec.set_recording_enabled(True)
        assert rec.status()["capture_state"] == "stopping"
        old_audio.release.set()
        worker.join(3)
        assert not worker.is_alive() and not errors
        assert old_video.starts == 0 and old_video.stopped and old_audio.stopped
        assert old_lease.closed and rec.status()["capture_state"] == "waiting"
        calls = []
        replacement, audio, lease = PairedVideo(calls), Audio(calls), HeldLock()
        rec._video_factory = lambda _: replacement
        rec._audio_factory = lambda _: audio
        rec._recorder_lock_factory = lambda: lease
        rec._begin_capture(WIN)
        assert replacement.starts == 1 and rec.status()["capture_state"] == "recording"


def test_disabled_worker_skips_discovery_and_never_restarts_on_afk_or_reattach(tmp_path):
    rec, video, *_ = make(tmp_path)
    rec.set_recording_enabled(False)
    rec._window_finder = lambda _: pytest.fail("disabled recorder hunted a window")
    waits = []
    rec._wait_attach = lambda seconds: (waits.append(seconds), rec._stop_event.set())
    rec.set_session_paused(True)
    rec.set_player_active()
    rec._window_lost.set()
    rec._run_attach_loop()
    rec._begin_capture(WIN)
    assert waits and video.starts == 0 and rec._session_paused
    assert rec.status()["capture_state"] == "off"


def test_existing_attach_owner_wakes_to_close_all_sources(tmp_path, monkeypatch):
    rec, video, audio, _, lease, calls, *_ = make(tmp_path)
    poses = []

    class PoseOwner:
        def __init__(self, *args, allowed, **kwargs):
            self.allowed, self.archive, self.source = allowed, object(), None
            self.started, self.stopped = False, False
            poses.append(self)
        def start(self):
            self.started = True
        def request_stop(self):
            calls.append("pose-revoke")
        def stop(self):
            self.stopped = True

    monkeypatch.setattr("sm64_events.ghost.recording.PoseRecording", PoseOwner)
    rec._ghost_capture_factory = object()
    closed = threading.Event()
    close = lease.close
    lease.close = lambda: (close(), closed.set())
    rec.start()
    worker = rec._thread
    try:
        assert video.started.wait(1)
        rec.set_recording_enabled(False)
        assert closed.wait(1), "existing worker did not wake from its attach poll"
        assert rec._thread is worker and worker.is_alive()
        assert audio.stopped and video.stopped and poses[0].started and poses[0].stopped
        assert not poses[0].allowed()
        assert rec.status()["capture_state"] == "off"
    finally:
        rec.stop(cleanup=False)


@pytest.mark.parametrize("failing", ["audio", "poses", "lease"])
def test_failed_close_remains_stopping_and_blocks_replacement(tmp_path, failing):
    rec, video, audio, _, lease, *_ = make(tmp_path)
    rec._begin_capture(WIN)
    allowed = threading.Event()
    if failing == "poses":
        class Poses:
            def request_stop(self):
                pass
            def stop(self):
                if not allowed.is_set():
                    raise RuntimeError("fixture poses still running")
        owner, method = Poses(), "stop"
        rec._pose_owner = owner
    else:
        owner, method = (audio, "stop") if failing == "audio" else (lease, "close")
    original = getattr(owner, method)
    def close():
        if not allowed.is_set():
            raise RuntimeError(f"fixture {failing} still running")
        original()
    setattr(owner, method, close)
    rec.set_recording_enabled(False)
    try:
        rec._teardown_capture()
        status = rec.status()
        assert status["capture_state"] == "stopping" and not status["recording_enabled"]
        assert status["recovery"]["state"] == "cleanup_pending"
        assert failing in status["recovery"]["error"] or failing == "lease"
        assert not lease.closed
        rec.set_recording_enabled(True)
        rec._begin_capture(WIN)
        assert video.starts == 1, "failed owner was replaced before closure"
        rec.set_recording_enabled(False)
        allowed.set()
        rec._teardown_capture()
        assert lease.closed and rec.status()["capture_state"] == "off"
    finally:
        allowed.set()
        rec.stop(cleanup=False)


def test_manual_stop_preserves_buffer_saved_media_and_session_pause(tmp_path):
    rec, *_ = make(tmp_path)
    svc = service(tmp_path, rec)
    rec._begin_capture(WIN)
    sentinel = rec._cfg.scratch_dir / "retained-clip.mp4"
    sentinel.write_bytes(b"existing buffer")
    svc.cfg.save_root.mkdir()
    saved = svc.cfg.save_root / "attempt_0042_saved.mp4"
    saved.write_bytes(b"saved replay")
    rec.set_session_paused(True)
    svc.set_recording_enabled(False)
    rec._teardown_capture()
    assert rec._scratch_ready and rec._session_paused
    assert sentinel.read_bytes() == b"existing buffer" and saved.read_bytes() == b"saved replay"
    assert rec.reset_session_scratch()  # Existing session rotation must not re-enable capture.
    assert not rec.recording_enabled and rec._session_paused
    assert rec.status()["capture_state"] == "off"
    assert saved.read_bytes() == b"saved replay"
    rec.stop(cleanup=False)


def test_disabled_setting_survives_other_settings_and_server_restart(tmp_path):
    rec, *_ = make(tmp_path)
    svc = service(tmp_path, rec)
    assert svc.set_recording_enabled(False)["capture_state"] == "off"
    svc.update_settings(600, 3 * 1024**3, 1.0, 2.0)
    cfg = apply_settings_file(svc.cfg)
    assert not cfg.recording_enabled and cfg.retention_s == 600
    # A legacy caller that does not pass the switch also preserves it.
    save_settings(cfg.settings_path, None, 2 * 1024**3, 0.5, 2.0)
    cfg = apply_settings_file(cfg)
    from sm64_events.replay.recorder import ReplayRecorder
    restarted = ReplayRecorder(cfg, lambda _: pytest.fail("disabled restart discovery"),
                               lambda _: pytest.fail("disabled restart video"),
                               lambda _: pytest.fail("disabled restart audio"), codec="libx264")
    assert not restarted.recording_enabled and restarted.status()["capture_state"] == "off"
    restarted._wait_attach = lambda _: restarted._stop_event.set()
    restarted._run_attach_loop()
    assert not restarted._scratch_ready


def test_failed_persistence_leaves_requested_state_unchanged(tmp_path, monkeypatch):
    rec, *_ = make(tmp_path)
    svc = service(tmp_path, rec)
    def fail(*args, **kwargs):
        raise OSError("fixture cannot persist")
    monkeypatch.setattr("sm64_events.replay.service.save_settings", fail)
    with pytest.raises(OSError, match="cannot persist"):
        svc.set_recording_enabled(False)
    assert rec.recording_enabled and not svc.cfg.settings_path.exists()


def test_settings_writes_serialize_with_the_recording_request(tmp_path, monkeypatch):
    rec, *_ = make(tmp_path)
    svc = service(tmp_path, rec)
    entered, release = threading.Event(), threading.Event()
    real_save = save_settings
    writes, errors = [], []
    def save(*args, **kwargs):
        writes.append(kwargs["recording_enabled"])
        if len(writes) == 1:
            entered.set()
            assert release.wait(2)
        real_save(*args, **kwargs)
    monkeypatch.setattr("sm64_events.replay.service.save_settings", save)
    def call(fn):
        try:
            fn()
        except Exception as error:
            errors.append(error)
    disable = threading.Thread(target=lambda: call(lambda: svc.set_recording_enabled(False)))
    storage = threading.Thread(target=lambda: call(lambda: svc.update_settings(None, 3 * 1024**3)))
    disable.start()
    try:
        assert entered.wait(1)
        storage.start()
    finally:
        release.set()
        disable.join(3)
        if storage.ident is not None:
            storage.join(3)
    assert not disable.is_alive() and not storage.is_alive() and not errors
    assert writes == [False, False]
    assert json.loads(svc.cfg.settings_path.read_text())["recording_enabled"] is False


def test_legacy_and_invalid_storage_overlay_never_undo_valid_manual_disable(tmp_path):
    path = tmp_path / "settings.json"
    cfg = ReplayConfig(settings_path=path)
    path.write_text('{"retention_s": 600}')
    assert apply_settings_file(cfg).recording_enabled is True
    path.write_text('{"recording_enabled": false, "max_buffer_bytes": 1}')
    out = apply_settings_file(cfg)
    assert not out.recording_enabled and out.max_buffer_bytes == cfg.max_buffer_bytes


def test_capture_source_error_is_reported_as_error(tmp_path):
    rec, video, *_ = make(tmp_path)
    rec._begin_capture(WIN)
    try:
        video.error = "fixture capture failed"
        assert rec.status()["capture_state"] == "error"
        assert not rec.status()["recording"]
    finally:
        video.error = None
        rec.stop(cleanup=False)
