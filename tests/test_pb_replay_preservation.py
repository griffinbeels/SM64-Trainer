"""The real, manually validated PB command preserves available video."""
import asyncio
from pathlib import Path

import pytest

from pb_commands import save_pb, undo_pb
from test_tracker_service import make, ev, star
from test_replay_service import FakeRecorder, FakeExtractor, T0
from sm64_events.main import _replay_service
from sm64_events.replay.config import ReplayConfig


def setup(tmp_path):
    db, tracker = make(tmp_path)
    recorder = FakeRecorder((T0, T0))
    config = ReplayConfig(save_root=tmp_path / "saved", scratch_dir=tmp_path / "scratch",
                          extract_wait_s=0)
    replay = _replay_service(config, recorder, "libx264", tracker)
    replay.extractor = FakeExtractor()
    asyncio.run(tracker.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(tracker.publish(star(1350)))
    return db, tracker, replay, db.attempts()[0].id


def test_completion_does_not_select_pb_but_manual_mark_saves_video(tmp_path):
    db, tracker, replay, aid = setup(tmp_path)
    assert not db.pbs() and not replay.extractor.calls
    result = save_pb(tracker, db, aid, "igt")
    assert result["frames"] == 343
    assert result["replay_save"]["status"] == "saved"
    saved = Path(result["replay_save"]["path"])
    assert saved.read_bytes() == b"mp4"
    assert saved.with_suffix(".json").is_file()
    undo_pb(tracker, db, aid, "igt")
    assert not db.pbs() and saved.is_file()


def test_video_failure_keeps_manual_pb_and_retry_preserves_video(tmp_path, monkeypatch):
    db, tracker, replay, aid = setup(tmp_path)
    original = replay.extractor.extract
    def fail(*_args):
        raise OSError("disk unavailable")
    monkeypatch.setattr(replay.extractor, "extract", fail)
    result = save_pb(tracker, db, aid, "igt")
    assert db.pbs()[0]["frames"] == 343
    assert result["replay_save"] == {"status": "failed", "message": "disk unavailable"}
    assert replay.status()["save_failures"][aid] == "disk unavailable"
    monkeypatch.setattr(replay.extractor, "extract", original)
    assert Path(replay.save(aid)["path"]).exists()
    assert replay.status()["save_failures"] == {}


def test_rejected_pb_never_preserves_media(tmp_path):
    _db, tracker, replay, aid = setup(tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(tracker.save_pb(aid, "igt"))  # No active strategy selected.
    assert not replay.extractor.calls


def test_only_explicit_session_switch_rotates_replay_lifetime(tmp_path):
    db, tracker, replay, aid = setup(tmp_path)
    rotations = []
    replay.recorder.reset_session_scratch = lambda: rotations.append(True)
    saved = Path(save_pb(tracker, db, aid, "igt")["replay_save"]["path"])
    first_session = tracker.session_id
    token = replay.review_session_token
    asyncio.run(tracker.publish(ev("practice_reset", 1400, {"igt_frames_before": 0})))
    assert rotations == []
    second_session = asyncio.run(tracker.new_session("Next practice"))
    assert second_session != first_session and rotations == [True]
    assert replay.review_session_token != token and saved.exists()
    asyncio.run(tracker.continue_session(second_session))
    assert rotations == [True]  # Re-selecting the current session is a no-op.
    asyncio.run(tracker.continue_session(first_session))
    assert rotations == [True, True] and saved.exists()
