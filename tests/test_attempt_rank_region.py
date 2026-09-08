"""An imported ROM survives the rebuildable cache before any rank consumer reads it."""
import json
import sqlite3
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone

import pytest

from sm64_events.core.events import Event
from sm64_events.ranks import curves, history
from sm64_events.ranks.classify import display_cs
from sm64_events.ranks.standards import RankStandards
from sm64_events.storage.db import MIGRATIONS, Database, EventRow
from sm64_events.tracking import marelo
from sm64_events.tracking.projection import project
from sm64_events.tracking.segments import SEGMENT_ATTEMPT_OFFSET

T0 = datetime(2026, 9, 8, tzinfo=timezone.utc)


def _journal(kind="star"):
    identity = ({"course_id": 1, "star_id": 0} if kind == "star"
                else {"segment_id": 17})
    return [EventRow(
        index, 1, index, "time_imported", 0,
        (T0 + timedelta(seconds=index)).isoformat().replace("+00:00", "Z"),
        {**identity, "strat_tag": "Standard", "timer_mode": "igt" if kind == "star" else "rta",
         "frames": frames, "game_version": version, "platform": "n64",
         "imported_from": "sheet:Runner", "video": "https://example.org/original"})
        for index, (version, frames) in enumerate([("us", 900), ("jp", 450), ("us", 1800)], 1)]


def _standards(tmp_path, kind="star"):
    key = "star:1:0" if kind == "star" else "segment:17"
    path = tmp_path / "ranks.json"
    path.write_text(json.dumps({"version": 1, "entities": {key: {
        "clock": "igt" if kind == "star" else "rta",
        "strategies": {"Standard": {"Mario": 10, "Gold": 30}},
        "jp_strategies": {"Standard": {"Mario": 5, "Gold": 15}}}}}), encoding="utf-8")
    ranks = RankStandards(path)
    ranks.load()
    ranks.grading_version = "us"
    return ranks, key


def _rank_snapshot(attempts, ranks, key, mode="avg10"):
    def contextual_score(entity, frames, context):
        return curves.score_for(ranks.overall_curve(entity, context["game_version"]),
                                display_cs(frames))

    feed = marelo.successes_for(attempts, ranks.clock_for)
    series = history.history_series(
        feed, [{"need": 1, "candidates": [key]}],
        lambda *_: pytest.fail("history discarded the source ROM"), mode,
        context_scorer=contextual_score)
    return marelo.entity_scores(attempts, ranks, [key], mode), series


def _append_journal(db, rows):
    assert db.insert_session(T0.isoformat()) == 1
    for row in rows:
        assert db.append_event(1, row.seq, Event(
            type=row.type, frame=row.frame,
            timestamp_utc=datetime.fromisoformat(row.wall_time_utc), payload=row.payload)) == row.id


@pytest.mark.parametrize("kind", ["star", "segment"])
@pytest.mark.parametrize("writer", ["upsert", "replace"])
def test_projected_regions_reach_current_average_and_history_after_cache_reopen(
        tmp_path, kind, writer):
    path = tmp_path / "tracker.db"
    ranks, key = _standards(tmp_path, kind)
    db = Database(path)
    try:
        _append_journal(db, _journal(kind))
        projected = project(db.events())
        assert [attempt.game_version for attempt in projected] == ["us", "jp", "us"]
        expected = _rank_snapshot(projected, ranks, key)
        assert expected[0][key] == 45
        assert [point["marelo"] for point in expected[1]] == [45, 45, 45]
        # Sensitivity control: losing only the ROM stamp changes both consumers.
        lost_stamp = [replace(attempt, game_version=None) for attempt in projected]
        wrong = _rank_snapshot(lost_stamp, ranks, key)
        assert wrong[0] != expected[0] and wrong[1] != expected[1]
        if writer == "upsert":
            for attempt in projected:
                db.upsert_attempt(attempt)
        else:
            db.replace_attempts(projected)
        assert db.attempts() == projected
        assert _rank_snapshot(db.attempts(), ranks, key) == expected
    finally:
        db.close()

    db = Database(path)
    try:
        assert db.attempts() == projected
        assert _rank_snapshot(db.attempts(), ranks, key) == expected
        db.replace_attempts(project(db.events()))
        assert db.attempts() == projected
        assert _rank_snapshot(db.attempts(), ranks, key) == expected
    finally:
        db.close()


def _durable_rows(conn):
    return {table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id")]
            for table in ("events", "sessions", "pbs", "input_chunks")} | {
        "attempt_recordings": [tuple(row) for row in conn.execute(
            "SELECT * FROM attempt_recordings ORDER BY attempt_id")]}


def _v35_database(path):
    rows = _journal()
    # Both actions are already folded into the cache; adding a column must not
    # replay or replace that attribution/visibility state as a side effect.
    rows.extend([
        EventRow(4, 1, 4, "attempt_strat_set", 0, "2026-09-08T00:00:04Z",
                 {"attempt_id": 2, "strat_tag": "Reviewed"}),
        EventRow(5, 1, 5, "attempt_cleared", 0, "2026-09-08T00:00:05Z",
                 {"attempt_id": 3, "reason": "warmup"})])
    rows.append(EventRow(6, 1, 6, "time_imported", 0, "2026-09-08T00:00:06Z",
                         {"course_id": 1, "star_id": 1, "strat_tag": "Standard",
                          "frames": 900, "timer_mode": "igt"}))
    attempts = project(rows)
    # A played segment in the other ID namespace must not inherit the ROM of
    # an unrelated import sharing its modulo journal id.
    attempts.append(replace(attempts[0], id=SEGMENT_ATTEMPT_OFFSET + 1,
                            course_id=None, star_id=None, segment_id=1,
                            igt_frames=None, rta_frames=900, game_version=None,
                            timed_by="rta", closed_by="level_enter"))
    with sqlite3.connect(path) as conn:
        for migration in MIGRATIONS[:35]:
            conn.executescript(migration)
        conn.execute("PRAGMA user_version=35")
        conn.execute("INSERT INTO sessions (id,started_utc) VALUES (1,?)", (T0.isoformat(),))
        for row in rows:
            conn.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?)", (
                row.id, row.session_id, row.seq, row.type, row.frame,
                row.wall_time_utc, json.dumps(row.payload)))
        columns = [row[1] for row in conn.execute("PRAGMA table_info(attempts)")]
        assert "game_version" not in columns
        for attempt in attempts:
            fields = asdict(attempt)
            conn.execute(f"INSERT INTO attempts ({','.join(columns)})"
                         f" VALUES ({','.join('?' for _ in columns)})",
                         tuple(fields[column] for column in columns))
        conn.execute("INSERT INTO pbs (course_id,star_id,strat_tag,timer_mode,frames,"
                     "attempt_id,saved_utc,imported_from,game_version)"
                     " VALUES (1,0,'Reviewed','igt',450,2,'saved','sheet:Runner','jp')")
        conn.execute("INSERT INTO attempt_recordings VALUES"
                     " (2,'https://example.org/edited',7,1)")
        conn.execute("INSERT INTO input_chunks"
                     " (id,session_id,start_frame,end_frame,started_utc,ended_utc,runs)"
                     " VALUES (1,1,100,101,'start','end',x'010203')")
        durable = _durable_rows(conn)
        cached = [tuple(row) for row in conn.execute("SELECT * FROM attempts ORDER BY id")]
    return attempts, durable, columns, cached


def test_v35_cache_backfills_only_exact_imports_without_rewriting_player_records(tmp_path):
    path = tmp_path / "legacy.db"
    projected, durable, columns, cached = _v35_database(path)
    ranks, key = _standards(tmp_path)
    for _ in range(2):
        db = Database(path)
        try:
            assert db._conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
            assert _durable_rows(db._conn) == durable
            assert [tuple(row) for row in db._conn.execute(
                f"SELECT {','.join(columns)} FROM attempts ORDER BY id")] == cached
            assert {attempt.id: attempt for attempt in db.attempts()} == {
                attempt.id: attempt for attempt in projected}
            assert _rank_snapshot(db.attempts(), ranks, key) == _rank_snapshot(projected, ranks, key)
            assert db.recording_link(2) == {"url": "https://example.org/edited", "revision": 7}
        finally:
            db.close()

    db = Database(path)
    try:
        rebuilt = project(db.events())
        db.replace_attempts(rebuilt)
        assert db.attempts() == rebuilt
        assert [attempt.game_version for attempt in rebuilt] == ["us", "jp", "us", None]
        assert _durable_rows(db._conn) == durable
        assert _rank_snapshot(db.attempts(), ranks, key) == _rank_snapshot(projected, ranks, key)
    finally:
        db.close()


def test_unstamped_imports_and_played_attempts_stay_unknown_in_cache(tmp_path):
    rows = _journal()
    for row in rows:
        row.payload.pop("game_version")
    path = tmp_path / "unknown.db"
    db = Database(path)
    try:
        _append_journal(db, rows)
        projected = project(db.events())
        db.replace_attempts(projected)
        assert [attempt.game_version for attempt in db.attempts()] == [None, None, None]
        db.upsert_attempt(replace(projected[0], timed_by="igt", closed_by="star_collected"))
        assert db.attempts()[0].game_version is None
    finally:
        db.close()
