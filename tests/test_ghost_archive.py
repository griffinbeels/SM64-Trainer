from contextlib import closing
import sqlite3
import threading
import time

import pytest

from ghost_native_fixture import attempt, frame, producer
from sm64_events.detectors.anchors import NEAR_ZERO_IGT
from sm64_events.ghost.archive import (
    ARCHIVE_MAX_BYTES, ARCHIVE_MAX_RECORDS, ARCHIVE_RETENTION_S, COMMIT_ROWS, COMMIT_S,
    DANCE_ENTRY_FRAMES, DANCE_TAIL_MAX_FRAMES, SCHEMA_VERSION, PoseArchive, stored_record,
)
from sm64_events.ghost.recording import PoseRecording
from sm64_events.ghost.native_layout import POSE_AT
from sm64_events.memory.addresses import (
    ACT_JUMBO_STAR_CUTSCENE, ACT_STAR_DANCE_EXIT, ACT_STAR_DANCE_NO_EXIT, ACT_STAR_DANCE_WATER,
)
from sm64_events.ghost.clip import (
    CLOSING_TRANSITION_MAX, MAX_RECORDS, RECORD_BYTES, ClipError, attempt_origin, check_manifest, read_clip,
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


@pytest.mark.parametrize("dance", [DANCE, ACT_STAR_DANCE_WATER])
@pytest.mark.parametrize("late", [1, DANCE_ENTRY_FRAMES])
def test_a_grab_straight_into_the_dance_keeps_it_when_the_close_is_a_frame_early(tmp_path, dance, late):
    """His SSL "Shining Atop the Pyramid" PB (attempt 5386): walking at the
    close (821), dancing from 822, and export dropped the whole dance. A
    ground or swimming grab enters the dance with no fall first, and the
    attempt can close up to DANCE_ENTRY_FRAMES before the first dance row."""
    walking, swimming = 0x04000440, 0x300024D1
    before = swimming if dance == ACT_STAR_DANCE_WATER else walking
    archive = PoseArchive(tmp_path / "poses.sqlite")
    rows = [frame(1), frame(2), *(frame(n, action=before) for n in range(3, 3 + late)),
            *(frame(n, action=dance) for n in range(3 + late, 8 + late)), frame(8 + late, action=IDLE)]
    archive.ingest(producer(), tuple(rows))
    row, path, target = attempt(), tmp_path / "direct.ghost", {"kind": "star"}
    row.timed_at = "xcam"  # the tracker closed it at the x-cam
    manifest = archive.export_attempt(row, path, target=target)
    clip = check_manifest(path, manifest, attempt=row, target=target)
    assert manifest["tail_records"] == 4 + late and clip.dance_tail
    assert tuple(f.raw for f in clip.frames) == tuple(rows[:-1])
    assert clip.frames[3 + late - 1].live_action == dance  # every dance row, from its first
    archive.close()


def test_a_bowser_key_segment_keeps_its_dance_like_a_star(tmp_path):
    """His Bowser 1 PB (attempt 80000006404, 2026-09-28): the key segment
    closed on idle frame 49272 and the key's exit dance began at 49273; a
    segment kept no tail, so the ghost vanished at the key. "The key should
    work the same way as a normal star.\""""
    archive = PoseArchive(tmp_path / "poses.sqlite")
    rows = [frame(1), frame(2), frame(3), *(frame(n, action=DANCE) for n in range(4, 9)), frame(9, action=IDLE)]
    archive.ingest(producer(), tuple(rows))
    row, path = attempt(kind="segment"), tmp_path / "key.ghost"
    row.segment_id, row.closed_by, row.timed_at = 8, "key_grabbed", None
    manifest = archive.export_attempt(row, path)
    clip = check_manifest(path, manifest, attempt=row, target=None)
    assert manifest["tail_records"] == 5 and clip.dance_tail
    assert tuple(f.raw for f in clip.frames) == tuple(rows[:-1])
    archive.close()


def test_the_grand_star_keeps_its_sequence_up_to_the_flight(tmp_path):
    """The Bowser 3 grand star closes on its touch, already in
    ACT_JUMBO_STAR_CUTSCENE; its tail keeps the fall, landing, raise and
    take-off and ends where the scripted flight into the credits begins."""
    def animated(raw, animation):
        raw = bytearray(raw)
        raw[POSE_AT + 30:POSE_AT + 32] = animation.to_bytes(2, "little")
        return bytes(raw)
    archive = PoseArchive(tmp_path / "poses.sqlite")
    rows = [frame(1), frame(2), frame(3, action=ACT_JUMBO_STAR_CUTSCENE),
            *(animated(frame(n, action=ACT_JUMBO_STAR_CUTSCENE), 0x25) for n in range(4, 8)),
            animated(frame(8, action=ACT_JUMBO_STAR_CUTSCENE), 0x2A)]  # MARIO_ANIM_WING_CAP_FLY
    archive.ingest(producer(), tuple(rows))
    row, path = attempt(kind="segment"), tmp_path / "grand.ghost"
    row.segment_id, row.closed_by, row.timed_at = 10, "key_grabbed", None
    manifest = archive.export_attempt(row, path)
    clip = check_manifest(path, manifest, attempt=row, target=None)
    assert manifest["tail_records"] == 4 and clip.dance_tail
    assert tuple(f.raw for f in clip.frames) == tuple(rows[:-1])
    archive.close()


@pytest.mark.parametrize("close_action,kind,segment_id", [(IDLE, "star", None), (DANCE, "segment", 5)])
def test_no_tail_unless_a_star_run_closes_at_its_dance(tmp_path, close_action, kind, segment_id):
    """A close that no dance follows within DANCE_ENTRY_FRAMES keeps no tail,
    and a segment never keeps one."""
    archive = PoseArchive(tmp_path / "poses.sqlite")
    after = 4 + DANCE_ENTRY_FRAMES  # the first dance row comes one frame too late
    archive.ingest(producer(), (frame(1), frame(2), frame(3, action=close_action),
                                *(frame(n, action=close_action) for n in range(4, after)),
                                *(frame(n, action=DANCE) for n in range(after, after + 5))))
    row, path = attempt(kind=kind), tmp_path / "plain.ghost"
    row.segment_id, row.timed_at = segment_id, "xcam"
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
    # A fake clock: wall-time bounds flaked under a loaded merge check (0.52 s vs 0.5 s).
    now, naps = [0.0], []

    def nap(seconds):
        naps.append(seconds)
        now[0] += seconds

    archive = PoseArchive(tmp_path / "poses.sqlite", dance_stall_s=0.2, clock=lambda: now[0], sleep=nap)
    archive.ingest(producer(), tuple(danced(dance_rows=2)))  # no end yet
    assert archive.export_attempt(attempt(), tmp_path / "stalled.ghost")["tail_records"] == 2
    assert 0.2 <= sum(naps) <= 0.3 + 1e-9  # the stall ends the wait, not the whole cap
    archive.close()
    naps.clear()
    assert archive.export_attempt(attempt(), tmp_path / "closed.ghost")["tail_records"] == 2
    assert naps == []  # a closed recording has nothing more coming


def test_an_attempt_closed_by_entering_a_level_ends_its_ghost_at_the_last_clocked_frame(tmp_path):
    """His MIPS Clip ends entering DDD: the close lands inside the level load,
    where the capture has no clock (the last 3 frames of all three of his runs,
    2026-09-24), and every save refused its ghost. The clip now ends on the last
    clocked frame and its manifest says how far before the close that was."""
    archive = PoseArchive(tmp_path / "poses.sqlite")
    archive.ingest(producer(), (frame(1), frame(2), frame(3), frame(4, clock_valid=0), frame(5, clock_valid=0)))
    row, path, target = attempt(end=105, kind="segment"), tmp_path / "entered.ghost", {"kind": "segment"}
    row.segment_id = 2
    manifest = archive.export_attempt(row, path, target=target)
    assert (manifest["records"], manifest["closing_transition"], manifest["last_global_timer"]) == (3, 2, 103)
    assert check_manifest(path, manifest, attempt=row, target=target).frames[-1].global_timer == 103
    for wrong in (1, 3, CLOSING_TRANSITION_MAX + 1, "2"):
        with pytest.raises(ClipError):
            check_manifest(path, {**manifest, "closing_transition": wrong}, attempt=row, target=target)
    # Consistent with the bytes and the attempt, but further than a level load.
    row.closed_frame = 103 + CLOSING_TRANSITION_MAX + 1
    with pytest.raises(ClipError, match="closing transition"):
        check_manifest(path, {**manifest, "closed_frame": row.closed_frame,
                              "closing_transition": CLOSING_TRANSITION_MAX + 1}, attempt=row, target=target)
    archive.close()


def test_a_close_further_than_a_level_load_from_any_clock_still_refuses(tmp_path):
    archive = PoseArchive(tmp_path / "poses.sqlite")
    rows = [frame(1), frame(2)] + [frame(n, clock_valid=0, qpc=1000 + n) for n in range(3, 4 + CLOSING_TRANSITION_MAX)]
    archive.ingest(producer(), tuple(rows))
    last = 100 + 3 + CLOSING_TRANSITION_MAX
    with pytest.raises(ClipError, match="elapsed clock"):
        archive.export_attempt(attempt(end=last, end_utc=1800000002.0, kind="segment"), tmp_path / "far.ghost")
    archive.close()
