from contextlib import closing
import sqlite3
import threading
import time

import pytest

from ghost_native_fixture import attempt, frame, producer
from sm64_events.detectors.anchors import NEAR_ZERO_IGT
from sm64_events.ghost.archive import (
    ARCHIVE_MAX_BYTES, ARCHIVE_MAX_RECORDS, ARCHIVE_RETENTION_S, COMMIT_ROWS, COMMIT_S,
    DANCE_TAIL_MAX_FRAMES, SCHEMA_VERSION, PoseArchive, stored_record,
)
from sm64_events.ghost.recording import PoseRecording
from sm64_events.memory.addresses import ACT_STAR_DANCE_EXIT, ACT_STAR_DANCE_NO_EXIT
from sm64_events.ghost.clip import (
    MAX_RECORDS, RECORD_BYTES, ClipError, attempt_origin, check_manifest, read_clip,
)


@pytest.mark.parametrize("world_status", [3, 11])
def test_exact_attempt_export_survives_detach_and_includes_unencoded_invalid_rows(tmp_path, world_status):
    archive = PoseArchive(tmp_path / "poses.sqlite")
    rows = (frame(1, world=world_status), frame(2, world=world_status, valid=29),
            frame(3, world=world_status), frame(4, world=world_status))
    assert archive.ingest(producer(), rows)
    archive.close()
    assert not archive.ingest(producer(), (frame(5),))
    output = tmp_path / "saved.ghost"
    manifest = archive.export_attempt(attempt(), output, target={"kind": "star", "entity_key": "24-1"})
    assert (manifest["attempt_id"], manifest["session_id"], manifest["records"]) == (77, 9, 3)
    assert tuple(f.raw for f in read_clip(output).frames) == rows[:3]
    assert manifest["invalid_records"] == 1
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


def test_a_stale_occurrence_is_refused_across_batches_and_reopens(tmp_path):
    archive = PoseArchive(tmp_path / "poses.sqlite")
    archive.ingest(producer(), (frame(2),))
    for stale in (frame(1, qpc=1003), frame(3, qpc=1001)):  # an older occurrence, or time running back
        with pytest.raises(ClipError, match="stale"):
            archive.ingest(producer(), (stale,))
    archive.close()
    reopened = PoseArchive(tmp_path / "poses.sqlite")  # the last row comes from the file
    with pytest.raises(ClipError, match="stale"):
        reopened.ingest(producer(), (frame(1, qpc=1003),))
    assert reopened.ingest(producer(), (frame(3),)) and reopened.status()["records"] == 2
    reopened.close()


def test_archive_window_is_its_own_not_the_clip_cap(tmp_path):
    """A late save reaches back the whole retention window: the archive's
    ceiling is sized by bytes, independent of how many records one clip holds."""
    archive = PoseArchive(tmp_path / "poses.sqlite")
    assert archive.max_records == ARCHIVE_MAX_RECORDS > MAX_RECORDS
    assert ARCHIVE_MAX_RECORDS * RECORD_BYTES <= ARCHIVE_MAX_BYTES
    # Two hours of play at 30 Hz, and never less than before GC6 (200000 rows).
    assert archive.retention_s == ARCHIVE_RETENTION_S
    assert ARCHIVE_MAX_RECORDS >= max(ARCHIVE_RETENTION_S * 30, 200000)
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


@pytest.mark.parametrize("extra", [0, 1])
def test_star_export_requires_canonical_opener_but_segment_may_start_later(tmp_path, extra):
    archive = PoseArchive(tmp_path / "poses.sqlite")
    raw = NEAR_ZERO_IGT + extra
    archive.ingest(producer(), (frame(1, igt=raw), frame(2, igt=raw+1)))
    if extra:
        with pytest.raises(ClipError, match="near-zero"):
            archive.export_attempt(attempt(end=102), tmp_path / "star.ghost")
    else:
        assert archive.export_attempt(attempt(end=102), tmp_path / "star.ghost")["origin_raw"] == raw
    segment = archive.export_attempt(attempt(end=102, kind="segment"), tmp_path / "segment.ghost")
    assert segment["origin_raw"] == raw
    assert list(read_clip(tmp_path / "segment.ghost").canonical_frames()) == [0, 1]
    archive.close()


@pytest.mark.parametrize("changes", [
    {"elapsed": 2}, {"continuity": 10}, {"igt": 2}, {"origin": 2}, {"clock_valid": 7},
])
def test_same_producer_endpoint_with_distinct_timing_coordinates_is_ambiguous(tmp_path, changes):
    archive = PoseArchive(tmp_path / "poses.sqlite")
    duplicate = {"timer": 101, "igt": 1, "elapsed": 1, **changes}
    archive.ingest(producer(), (frame(1), frame(2, **duplicate), frame(3)))
    with pytest.raises(ClipError, match="ambiguous"):
        archive.export_attempt(attempt(), tmp_path / "bad.ghost")
    archive.close()


def test_identical_endpoint_coordinates_choose_first_including_refused_pose(tmp_path):
    archive = PoseArchive(tmp_path / "poses.sqlite")
    rows = (frame(1, valid=29), frame(2, timer=101, igt=1, elapsed=1), frame(3))
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


def test_the_two_hour_window_applies_whatever_the_video_keeps(tmp_path):
    """The recorder's video retention may be "whole session" (None); that once
    reached the archive, so only the ~499 MiB record ceiling bounded it."""
    with pytest.raises(ValueError, match="time bound"):
        PoseArchive(tmp_path / "unbounded.sqlite", retention_s=None)
    owner = PoseRecording(tmp_path / "owned.sqlite", None, producer_pid=1, allowed=lambda: True)
    assert owner.archive.retention_s == ARCHIVE_RETENTION_S
    hour = 30 * 3600  # qpc ticks at the fixture's 30 Hz
    owner.archive.ingest(producer(), (frame(1, qpc=1000), frame(2, qpc=1000 + hour),
                                      frame(3, qpc=1000 + 2 * hour + 30)))
    assert owner.archive.status()["records"] == 2  # the first is over two hours old
    owner.archive.close()


def test_rows_commit_in_batches_and_the_owner_reads_what_is_not_yet_committed(tmp_path):
    now = [0.0]
    archive = PoseArchive(tmp_path / "poses.sqlite", clock=lambda: now[0])

    def committed():
        with closing(sqlite3.connect(tmp_path / "poses.sqlite")) as reader:
            return reader.execute("SELECT COUNT(*) FROM poses").fetchone()[0]

    rows = [frame(n) for n in range(1, COMMIT_ROWS + 5)]
    for raw in rows[:3]:
        archive.ingest(producer(), (raw,))
    assert committed() == 0  # one ingest is no longer one fsync'd commit
    manifest = archive.export_attempt(attempt(), tmp_path / "pending.ghost")
    assert manifest["records"] == 3 and archive.status()["records"] == 3
    assert tuple(f.raw for f in read_clip(tmp_path / "pending.ghost").frames) == tuple(rows[:3])
    for raw in rows[3:COMMIT_ROWS]:
        archive.ingest(producer(), (raw,))
    assert committed() == COMMIT_ROWS  # the row count commits
    archive.ingest(producer(), (rows[COMMIT_ROWS],))
    now[0] += COMMIT_S
    archive.ingest(producer(), (rows[COMMIT_ROWS + 1],))
    assert committed() == COMMIT_ROWS + 2  # so does a second's age
    archive.ingest(producer(), (rows[COMMIT_ROWS + 2],))
    archive.close()
    assert committed() == COMMIT_ROWS + 3  # and close
    assert archive.export_attempt(attempt(), tmp_path / "closed.ghost")["records"] == 3


def test_a_failed_batch_leaves_the_earlier_uncommitted_ones(tmp_path):
    archive = PoseArchive(tmp_path / "poses.sqlite")
    archive.ingest(producer(), (frame(1), frame(2)))
    archive._db.execute("CREATE TEMP TRIGGER refuse BEFORE INSERT ON poses "
                        "WHEN NEW.timer = 103 BEGIN SELECT RAISE(ABORT, 'disk'); END")
    with pytest.raises(sqlite3.Error):
        archive.ingest(producer(), (frame(3),))
    archive._db.execute("DROP TRIGGER refuse")
    assert archive.status()["records"] == 2
    assert archive.ingest(producer(), (frame(3),))  # its sequence was never taken
    assert archive.export_attempt(attempt(), tmp_path / "a.ghost")["records"] == 3
    archive.close()


def test_rows_are_compressed_under_integer_keys_in_unsigned_order(tmp_path):
    archive = PoseArchive(tmp_path / "poses.sqlite")
    below = 2**63 - 1  # the signed boundary sits inside this run
    rows = tuple(frame(below + n, timer=101 + n, igt=1 + n, elapsed=1 + n, qpc=1001 + n) for n in (0, 1, 2))
    archive.ingest(producer(), rows)
    archive.export_attempt(attempt(), tmp_path / "a.ghost")
    assert tuple(f.raw for f in read_clip(tmp_path / "a.ghost").frames) == rows
    stored = archive._db.execute(
        "SELECT typeof(producer), typeof(sequence), length(data), data FROM poses ORDER BY sequence").fetchall()
    assert [row[:2] for row in stored] == [("integer", "integer")] * 3
    assert all(size < RECORD_BYTES // 2 and stored_record(data) == raw
               for (_, _, size, data), raw in zip(stored, rows, strict=True))
    archive.close()


def test_an_archive_from_another_schema_version_is_recreated(tmp_path):
    path = tmp_path / "poses.sqlite"
    with sqlite3.connect(path) as older:  # v1: hex text keys, raw records
        older.executescript("""
            CREATE TABLE producers (identity TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE poses (id INTEGER PRIMARY KEY, producer TEXT NOT NULL, sequence TEXT NOT NULL,
                captured REAL NOT NULL, timer INTEGER NOT NULL, valid INTEGER NOT NULL,
                data BLOB NOT NULL, UNIQUE(producer, sequence));""")
        older.execute("INSERT INTO poses VALUES (1,'x','0000000000000001',1.0,101,31,x'00')")
    older.close()
    archive = PoseArchive(path)
    assert archive.status()["records"] == 0
    assert archive._db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert archive.ingest(producer(), (frame(1), frame(2), frame(3)))
    assert archive.export_attempt(attempt(), tmp_path / "a.ghost")["records"] == 3
    archive.close()

# -- the dance tail: a star clip keeps Mario's grab and dance after x-cam ----

DANCE, IDLE = ACT_STAR_DANCE_EXIT, 0x0C400201


def danced(ends_with: dict | None = None, *, dance_rows: int = 5):
    """Attempt rows 1..3 closing on the dance's first frame (timer 103), then
    `dance_rows` more dance frames, then one row that ends the dance."""
    rows = [frame(1), frame(2), frame(3, action=DANCE)]
    rows += [frame(n, action=DANCE) for n in range(4, 4 + dance_rows)]
    after = 4 + dance_rows
    if ends_with is not None:
        rows.append(frame(after, **{"action": DANCE, **ends_with}))
    return rows


@pytest.mark.parametrize("ends_with", [
    {"action": IDLE},                  # a no-exit dance returns to idle
    {"elapsed": 8},                    # the exit warp freezes the game clock
    {"origin": 2},                     # a new star generation
    {"level": 16},                     # a scene change
    {"timer": 104, "elapsed": 9},      # a state load rewinds the global timer
])
def test_star_export_keeps_the_dance_until_it_ends_and_no_further(tmp_path, ends_with):
    archive = PoseArchive(tmp_path / "poses.sqlite")
    rows = danced(ends_with)
    archive.ingest(producer(), tuple(rows))
    row, path, target = attempt(), tmp_path / "danced.ghost", {"kind": "star"}
    manifest = archive.export_attempt(row, path, target=target)
    clip = check_manifest(path, manifest, attempt=row, target=target)
    assert manifest["tail_records"] == 5 and manifest["records"] == 8 and clip.dance_tail
    assert tuple(f.raw for f in clip.frames) == tuple(rows[:8])
    assert manifest["last_global_timer"] == 108  # the attempt itself still closes at 103
    for tail, reason in ((0, "dance tail"), (4, "effective origin")):
        with pytest.raises(ClipError, match=reason):
            check_manifest(path, {**manifest, "tail_records": tail}, attempt=row, target=target)
    archive.close()


def test_the_dance_tail_is_bounded(tmp_path):
    archive = PoseArchive(tmp_path / "poses.sqlite")
    archive.ingest(producer(), tuple(danced(dance_rows=DANCE_TAIL_MAX_FRAMES + 20)))
    manifest = archive.export_attempt(attempt(), tmp_path / "long.ghost")
    assert manifest["tail_records"] == DANCE_TAIL_MAX_FRAMES
    assert manifest["last_global_timer"] == 103 + DANCE_TAIL_MAX_FRAMES
    archive.close()


@pytest.mark.parametrize("close_action,kind,segment_id", [(IDLE, "star", None), (DANCE, "segment", 5)])
def test_no_tail_unless_a_star_run_closes_inside_the_dance(tmp_path, close_action, kind, segment_id):
    archive = PoseArchive(tmp_path / "poses.sqlite")
    archive.ingest(producer(), (frame(1), frame(2), frame(3, action=close_action),
                                *(frame(n, action=DANCE) for n in range(4, 9))))
    row, path = attempt(kind=kind), tmp_path / "plain.ghost"
    row.segment_id = segment_id
    manifest = archive.export_attempt(row, path)
    assert manifest["tail_records"] == 0 and manifest["records"] == 3
    assert not check_manifest(path, manifest, attempt=row, target=None).dance_tail
    archive.close()


def test_the_hundred_coin_star_keeps_its_exit_star_dance(tmp_path):
    """The 100-coin star is a star run timed by its exit segment: it grabs the
    100-coin star mid-run and closes on the exit star, whose dance it keeps."""
    archive = PoseArchive(tmp_path / "poses.sqlite")
    rows = (frame(1), frame(2, action=ACT_STAR_DANCE_NO_EXIT), frame(3), frame(4, action=DANCE),
            *(frame(n, action=DANCE) for n in range(5, 8)), frame(8, action=IDLE))
    archive.ingest(producer(), rows)
    row, path = attempt(end=104, kind="segment"), tmp_path / "hundred.ghost"
    row.segment_id = None  # reattributed to the 100-coin star
    manifest = archive.export_attempt(row, path)
    clip = check_manifest(path, manifest, attempt=row, target=None)
    assert manifest["tail_records"] == 3 and clip.dance_tail and len(clip.frames) == 7
    archive.close()


def test_export_waits_for_the_dance_the_recorder_is_still_capturing(tmp_path):
    archive = PoseArchive(tmp_path / "poses.sqlite", dance_stall_s=2.0)
    rows = danced({"action": IDLE})
    archive.ingest(producer(), tuple(rows[:4]))  # closed on x-cam; one dance frame so far
    later = threading.Timer(0.3, lambda: archive.ingest(producer(), tuple(rows[4:])))
    later.start()
    begun = time.monotonic()
    manifest = archive.export_attempt(attempt(), tmp_path / "live.ghost")
    later.join()
    assert manifest["tail_records"] == 5 and 0.25 < time.monotonic() - begun < 2.0
    archive.close()


def test_a_stalled_or_closed_recording_keeps_the_dance_it_has(tmp_path):
    archive = PoseArchive(tmp_path / "poses.sqlite", dance_stall_s=0.2)
    archive.ingest(producer(), tuple(danced(dance_rows=2)))  # no end yet
    begun = time.monotonic()
    assert archive.export_attempt(attempt(), tmp_path / "stalled.ghost")["tail_records"] == 2
    assert time.monotonic() - begun < 1.5
    archive.close()
    begun = time.monotonic()
    assert archive.export_attempt(attempt(), tmp_path / "closed.ghost")["tail_records"] == 2
    assert time.monotonic() - begun < 0.5
