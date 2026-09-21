import pytest

from ghost_native_fixture import attempt, frame, producer
from sm64_events.ghost.archive import PoseArchive
from sm64_events.ghost.clip import ClipError, attempt_origin, check_manifest, read_clip


def test_exact_attempt_export_survives_detach_and_includes_unencoded_invalid_rows(tmp_path):
    archive = PoseArchive(tmp_path / "poses.sqlite")
    rows = (frame(1), frame(2, valid=29), frame(3), frame(4))
    assert archive.ingest(producer(), rows)
    archive.close()
    assert not archive.ingest(producer(), (frame(5),))
    output = tmp_path / "saved.ghost"
    manifest = archive.export_attempt(attempt(), output, target={"kind": "star", "entity_key": "24-1"})
    assert (manifest["attempt_id"], manifest["session_id"], manifest["records"]) == (77, 9, 3)
    assert tuple(f.raw for f in read_clip(output).frames) == rows[:3]
    assert archive.status()["closed"]


def test_owner_origin_and_actual_closing_frame_replace_anchor_duration_arithmetic(tmp_path):
    archive = PoseArchive(tmp_path / "poses.sqlite")
    archive.ingest(producer(), (frame(1), frame(3), frame(4)))
    row = attempt(end=103)
    row.anchor_frame, row.rta_frames = 12, 999
    manifest = archive.export_attempt(row, tmp_path / "a.ghost")
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


def test_sequence_order_is_unsigned_and_timing_discontinuity_is_not_hidden(tmp_path):
    archive = PoseArchive(tmp_path / "poses.sqlite")
    first = 2**63
    archive.ingest(producer(), (frame(first, timer=101, igt=1, qpc=1001),
                                frame(first+1, timer=103, igt=0, qpc=1002, continuity=10)))
    with pytest.raises(ClipError, match="continuity"):
        archive.export_attempt(attempt(), tmp_path / "bad.ghost")
    archive.close()


def test_rebased_segment_trims_pre_origin_and_uses_explicit_owner_kind(tmp_path):
    archive = PoseArchive(tmp_path / "poses.sqlite")
    archive.ingest(producer(), tuple(frame(n, elapsed=90+n) for n in (1, 2, 3, 4)))
    row = attempt(start=103, end=104, kind="segment")
    row.anchor_frame, row.segment_id = 101, None  # reattributed star may be SegmentEngine-owned
    result = archive.export_attempt(row, tmp_path / "segment.ghost", target={"kind": "star"})
    clip = read_clip(tmp_path / "segment.ghost")
    assert [frame.sequence for frame in clip.frames] == [3, 4]
    assert list(clip.canonical_frames()) == [0, 1]
    assert result["native_clock_kind"] == "segment" and result["clock_kind"] == 2
    archive.close()


@pytest.mark.parametrize("changes", [{"elapsed": 2}, {"continuity": 10}])
def test_same_producer_endpoint_with_distinct_timing_coordinates_is_ambiguous(tmp_path, changes):
    archive = PoseArchive(tmp_path / "poses.sqlite")
    archive.ingest(producer(), (frame(1), frame(2, timer=101, **changes), frame(3)))
    with pytest.raises(ClipError, match="ambiguous"):
        archive.export_attempt(attempt(), tmp_path / "bad.ghost")
    archive.close()


def test_identical_endpoint_coordinates_choose_first_including_refused_pose(tmp_path):
    archive = PoseArchive(tmp_path / "poses.sqlite")
    rows = (frame(1, valid=29), frame(2, timer=101, elapsed=1), frame(3))
    archive.ingest(producer(), rows)
    archive.export_attempt(attempt(), tmp_path / "exact.ghost")
    clip = read_clip(tmp_path / "exact.ghost")
    assert clip.frames[0].sequence == 1 and not clip.canonical_frames()[1].playable
    archive.close()


def test_each_endpoint_uses_its_own_one_second_utc_window(tmp_path):
    archive = PoseArchive(tmp_path / "poses.sqlite")
    archive.ingest(producer(), (frame(1, qpc=1150), frame(2, timer=103, qpc=1300)))
    row = attempt(end_utc=1800000010.0)
    with pytest.raises(ClipError, match="missing exact"):
        archive.export_attempt(row, tmp_path / "bad.ghost")
    row.native_clock_utc = attempt(start_utc=1800000005.0).native_clock_utc
    assert archive.export_attempt(row, tmp_path / "good.ghost")["records"] == 2
    archive.close()


def test_missing_timing_inside_attempt_is_retained_but_cannot_export(tmp_path):
    archive = PoseArchive(tmp_path / "poses.sqlite")
    archive.ingest(producer(), (frame(1), frame(2, clock_valid=0), frame(3)))
    assert archive.status()["records"] == 3
    with pytest.raises(ClipError, match="elapsed clock"):
        archive.export_attempt(attempt(), tmp_path / "bad.ghost")
    archive.close()


@pytest.mark.parametrize("field,value", [
    ("native_clock_frame", 102), ("native_clock_utc", attempt(start_utc=1800000005.0).native_clock_utc),
    ("native_clock_kind", "segment"), ("closed_frame", 104),
])
def test_manifest_refuses_same_attempt_after_origin_reprojection(tmp_path, field, value):
    archive = PoseArchive(tmp_path / "poses.sqlite")
    archive.ingest(producer(), (frame(1), frame(2), frame(3)))
    row, path, target = attempt(), tmp_path / "saved.ghost", {"kind": "star"}
    manifest = archive.export_attempt(row, path, target=target)
    assert check_manifest(path, manifest, attempt=row, target=target).origin_global_timer == 101
    setattr(row, field, value)
    with pytest.raises(ClipError):
        check_manifest(path, manifest, attempt=row, target=target)
    archive.close()


@pytest.mark.parametrize("field,value", [
    ("native_clock_frame", None), ("native_clock_utc", "2026-01-01"),
    ("native_clock_kind", "unknown"), ("closed_frame", None),
])
def test_attempt_origin_never_falls_back_to_anchor_or_duration(field, value):
    row = attempt()
    setattr(row, field, value)
    with pytest.raises(ClipError):
        attempt_origin(row)


def test_size_only_recording_retention_still_has_a_hard_pose_ceiling(tmp_path):
    archive = PoseArchive(tmp_path / "poses.sqlite", retention_s=None, max_records=2)
    archive.ingest(producer(), (frame(1, qpc=1000), frame(2, qpc=10000000)))
    assert archive.status()["records"] == 2  # more than two hours is retained
    archive.ingest(producer(), (frame(3, qpc=10000001),))
    assert archive.status()["records"] == 2  # record ceiling remains mandatory
    archive.close()
