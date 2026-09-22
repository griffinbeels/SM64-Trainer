from types import SimpleNamespace
import json

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


def test_optional_native_initialization_failure_keeps_video_recording(tmp_path):
    rec = make_recorder(tmp_path, FakeVideoSource(), FakeAudioSource(),
                        video_sink_factory=lambda *args: FakeAvSink())

    def unavailable(**kwargs):
        raise ValueError("unsupported native layout")

    rec._ghost_capture_factory = unavailable
    rec._begin_capture(WIN)
    assert rec._recording and rec.ghost_capture is None and rec.pose_archive is None
    rec.stop()


def test_partial_native_start_keeps_failed_owner_until_joined(tmp_path):
    class Partial(Source):
        def start(self):
            self.joined = False
            raise RuntimeError("native start failed after acquiring ownership")

    rec = make_recorder(tmp_path, FakeVideoSource(), FakeAudioSource(),
                        video_sink_factory=lambda *args: FakeAvSink())
    rec._ghost_capture_factory = Partial
    with pytest.raises(RuntimeError, match="cleanup pending"):
        rec._begin_capture(WIN)
    assert rec._capture_cleanup_failed and rec._rec_lock is not None
    owner = rec._capture_retained["poses"]
    assert not owner.archive.status()["closed"]
    owner.source.joined = True
    rec.stop(cleanup=False)
    assert owner.archive.status()["closed"] and rec._rec_lock is None
    rec.cleanup_scratch()
    assert not owner.archive.path.exists()


def test_service_preservation_uses_authoritative_attempt_instead_of_video_span(tmp_path):
    seen = []
    archive = SimpleNamespace(export_attempt=lambda *args, **kw: seen.append((args, kw)) or {"records": 3})
    row = attempt(end=110)
    row.segment_id, row.course_id, row.star_id, row.strat_tag = None, 24, 1, None
    service = ReplayService.__new__(ReplayService)
    service.recorder = SimpleNamespace(pose_archive=archive)
    service.tracker = SimpleNamespace(db=SimpleNamespace(
        events_between=lambda *args: pytest.fail("native export inferred an endpoint from video events")))
    assert service._preserve_ghost(row, tmp_path / "clip.ghost")[1] == {"records": 3}
    assert seen[-1][0] == (row, tmp_path / "clip.ghost")
    assert seen[-1][1] == {"target": {"kind": "star", "entity_key": "24-1", "strat": None}}
    row.segment_id = "segment-one"
    service._preserve_ghost(row, tmp_path / "clip.ghost")
    assert seen[-1][0][0] is row and "end_frame" not in seen[-1][1]


def native_service(tmp_path, *, rows=None):
    from datetime import datetime, timedelta
    from sm64_events.ghost.archive import PoseArchive
    from test_replay_service import attempt as replay_attempt, make_service
    sample = attempt()
    row = replay_attempt(anchor_frame=sample.anchor_frame, rta_frames=sample.rta_frames,
                         started_utc=sample.started_utc, ended_utc=sample.ended_utc,
                         native_clock_frame=sample.native_clock_frame,
                         native_clock_utc=sample.native_clock_utc,
                         native_clock_kind=sample.native_clock_kind, closed_frame=sample.closed_frame)
    service = make_service(tmp_path, [row], cov=(datetime.fromisoformat(row.started_utc) - timedelta(seconds=10),
                                               datetime.fromisoformat(row.ended_utc) + timedelta(seconds=10)))
    archive = PoseArchive(tmp_path / "poses.sqlite")
    archive.ingest(producer(), tuple(frame(n) for n in (1, 2, 3)) if rows is None else rows)
    service.recorder.pose_archive = archive
    return service, row, archive


def test_template_pose_preservation_never_extracts_video_and_survives_detach(tmp_path, monkeypatch):
    service, row, archive = native_service(tmp_path)
    monkeypatch.setattr(service, "_view", lambda *args: pytest.fail("pose preservation requested video"))
    service.compressor = SimpleNamespace(
        enqueue=lambda *args, **kwargs: pytest.fail("pose preservation queued video compression"))
    result = service.preserve_native(row.id)
    path, manifest = service.find_native(row.id)
    assert str(path) == result["path"] and path.parent.name == "native"
    assert manifest["attempt_id"] == row.id and manifest["records"] == 3
    assert service.extractor.calls == [] and not list(service.cfg.save_root.rglob("*.mp4"))
    archive.close()
    service.recorder.pose_archive = None
    assert service.preserve_native(row.id) == result


def test_native_lookup_requires_committed_matching_identity_and_hash(tmp_path):
    service, row, archive = native_service(tmp_path)
    service.preserve_native(row.id)
    path, manifest = service.find_native(row.id)
    metadata = path.with_suffix(".json")
    for key, value in (("session_id", row.session_id + 1), ("attempt_id", row.id + 1),
                       ("target", {}), ("file", "../other.ghost"), ("configuration", 999)):
        metadata.write_text(json.dumps({**manifest, key: value}), encoding="utf-8")
        assert service.find_native(row.id) is None
    metadata.unlink()
    assert service.find_native(row.id) is None  # binary alone is not a commit
    metadata.write_text(json.dumps(manifest), encoding="utf-8")
    payload = bytearray(path.read_bytes())
    payload[-1] ^= 1
    path.write_bytes(payload)
    assert service.find_native(row.id) is None
    archive.close()


def test_save_reuses_permanent_poses_without_deleting_original(tmp_path):
    from pathlib import Path
    from sm64_events.ghost.clip import check_manifest
    service, row, archive = native_service(tmp_path)
    queued = []

    def enqueue(path, **named):
        metadata = json.loads(path.with_suffix(".json").read_text())
        check_manifest(path.with_suffix(".ghost"), metadata["native_ghost"],
                       attempt=row, target=metadata["native_ghost"]["target"])
        queued.append((path, named))

    service.compressor = SimpleNamespace(enqueue=enqueue)
    original = service.preserve_native(row.id)
    assert queued == []
    original_path = Path(original["path"])
    archive.close()
    service.recorder.pose_archive = None
    saved = service.save(row.id)
    assert len(queued) == 1 and queued[0][0] == Path(saved["path"])
    assert queued[0][1]["attempt_id"] == row.id
    assert original_path.is_file()
    assert saved["native_ghost"]["clip_id"] == original["native_ghost"]["clip_id"]
    original_path.with_suffix(".json").unlink()
    path, manifest = service.find_native(row.id)
    assert path == Path(saved["path"]).with_suffix(".ghost")
    assert manifest["clip_id"] == original["native_ghost"]["clip_id"]


def test_pose_only_unavailable_is_explicit_without_video_fallback(tmp_path):
    from test_replay_service import attempt as replay_attempt, make_service
    service = make_service(tmp_path, [replay_attempt()])
    result = service.preserve_native(42)
    assert result["path"] is None and result["native_ghost"] is None
    assert "no native Mario poses" in result["native_ghost_reason"]
    assert service.extractor.calls == []


def test_pose_commit_protects_pruning_but_does_not_claim_video_availability(tmp_path):
    from sm64_events.replay.service import saved_attempt_ids
    service, row, archive = native_service(tmp_path)
    service.preserve_native(row.id)
    assert saved_attempt_ids(service.cfg.save_root) == {row.id}
    assert service.saved_attempt_ids() == set()
    path, _ = service.find_native(row.id)
    path.write_bytes(b"corrupt native recording")
    assert saved_attempt_ids(service.cfg.save_root) == set()
    archive.close()
