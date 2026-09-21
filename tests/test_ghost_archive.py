import pytest

from ghost_native_fixture import attempt, frame, producer
from sm64_events.ghost.archive import PoseArchive
from sm64_events.ghost.clip import ClipError, read_clip


def test_exact_attempt_export_survives_detach_and_includes_unencoded_invalid_rows(tmp_path):
    archive = PoseArchive(tmp_path / "poses.sqlite")
    rows = (frame(1), frame(2, valid=13), frame(3), frame(4))
    assert archive.ingest(producer(), rows)
    archive.close()
    assert not archive.ingest(producer(), (frame(5),))
    output = tmp_path / "saved.ghost"
    manifest = archive.export_attempt(attempt(), output, target={"kind": "star", "entity_key": "24-1"})
    assert (manifest["attempt_id"], manifest["session_id"], manifest["records"]) == (77, 9, 3)
    assert tuple(f.raw for f in read_clip(output).frames) == rows[:3]
    assert archive.status()["closed"]


def test_grab_override_clamps_before_timed_end_and_both_endpoints_are_exact(tmp_path):
    archive = PoseArchive(tmp_path / "poses.sqlite")
    archive.ingest(producer(), (frame(1), frame(3), frame(4)))
    manifest = archive.export_attempt(attempt(end=104), tmp_path / "a.ghost", end_frame=103)
    assert manifest["last_global_timer"] == 103 and manifest["missing_occurrences"] == 1
    for run in (attempt(start=102), attempt(end=105)):
        with pytest.raises(ClipError, match="missing exact"):
            archive.export_attempt(run, tmp_path / "bad.ghost")
    archive.close()


def test_qpc_anchor_disambiguates_repeated_frames_but_overlap_is_refused(tmp_path):
    archive = PoseArchive(tmp_path / "poses.sqlite")
    rows = (frame(1), frame(2), frame(3))
    archive.ingest(producer(), rows)
    archive.ingest(producer(epoch=6, utc_anchor=1800000060.0), tuple(frame(n, epoch=6) for n in (1, 2, 3)))
    assert archive.export_attempt(attempt(), tmp_path / "a.ghost")["epoch"] == 5
    archive.ingest(producer(epoch=7), tuple(frame(n, epoch=7) for n in (1, 2, 3)))
    with pytest.raises(ClipError, match="ambiguous"):
        archive.export_attempt(attempt(), tmp_path / "bad.ghost")
    archive.close()


def test_stale_or_mixed_batch_commits_nothing(tmp_path):
    archive = PoseArchive(tmp_path / "poses.sqlite")
    archive.ingest(producer(), (frame(1),))
    with pytest.raises(ClipError, match="stale"):
        archive.ingest(producer(), (frame(2), frame(1)))
    with pytest.raises(ClipError, match="producer identity"):
        archive.ingest(producer(), (frame(2), frame(3, epoch=8)))
    assert archive.status()["records"] == 1
    archive.close()


def test_retention_and_record_cap_refuse_evicted_start(tmp_path):
    archive = PoseArchive(tmp_path / "poses.sqlite", max_records=2)
    archive.ingest(producer(), tuple(frame(n) for n in (1, 2, 3)))
    assert archive.status()["records"] == 2
    with pytest.raises(ClipError, match="missing exact"):
        archive.export_attempt(attempt(), tmp_path / "bad.ghost")
    archive.close()
    timed = PoseArchive(tmp_path / "timed.sqlite", retention_s=0.05)
    timed.ingest(producer(), (frame(1), frame(2), frame(10)))
    assert timed.status()["records"] == 1
    timed.close()


def test_sequence_order_is_unsigned_and_clock_reset_is_not_hidden(tmp_path):
    archive = PoseArchive(tmp_path / "poses.sqlite")
    first = 2**63
    archive.ingest(producer(), (frame(first, timer=101, igt=1, qpc=1001),
                                frame(first+1, timer=103, igt=0, qpc=1002)))
    with pytest.raises(ClipError, match="clock restarts"):
        archive.export_attempt(attempt(), tmp_path / "bad.ghost")
    archive.close()


def test_size_only_recording_retention_still_has_a_hard_pose_ceiling(tmp_path):
    archive = PoseArchive(tmp_path / "poses.sqlite", retention_s=None, max_records=2)
    archive.ingest(producer(), (frame(1, qpc=1000), frame(2, qpc=10000000)))
    assert archive.status()["records"] == 2  # more than two hours is retained
    archive.ingest(producer(), (frame(3, qpc=10000001),))
    assert archive.status()["records"] == 2  # record ceiling remains mandatory
    archive.close()
