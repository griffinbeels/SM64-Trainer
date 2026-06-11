import json
from datetime import datetime, timezone

from sm64_events.core.events import Event
from sm64_events.storage.db import Database

T0 = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)


def make_db(tmp_path) -> Database:
    return Database(tmp_path / "t.db")


def ev(type_="star_collected", frame=100, payload=None) -> Event:
    return Event(type=type_, frame=frame, timestamp_utc=T0, payload=payload or {})


def test_migrations_set_user_version_and_create_tables(tmp_path):
    db = make_db(tmp_path)
    names = {r["name"] for r in db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"events", "sessions", "attempts", "pbs", "ui_state"} <= names
    assert db._conn.execute("PRAGMA user_version").fetchone()[0] == 1


def test_reopening_existing_db_is_idempotent(tmp_path):
    make_db(tmp_path).close()
    db = make_db(tmp_path)  # second open: migrations must not re-run/crash
    assert db._conn.execute("PRAGMA user_version").fetchone()[0] == 1


def test_journal_append_and_read_back(tmp_path):
    db = make_db(tmp_path)
    sid = db.insert_session("2026-06-10T12:00:00Z")
    jid = db.append_event(sid, seq=7, event=ev(payload={"course_id": 2}))
    rows = db.events()
    assert len(rows) == 1 and rows[0].id == jid
    assert rows[0].session_id == sid and rows[0].seq == 7
    assert rows[0].type == "star_collected" and rows[0].frame == 100
    assert rows[0].payload == {"course_id": 2}
    assert rows[0].wall_time_utc == "2026-06-10T12:00:00Z"


def test_sessions_insert_and_end(tmp_path):
    db = make_db(tmp_path)
    sid = db.insert_session("2026-06-10T12:00:00Z", label="evening")
    db.end_session(sid, "2026-06-10T13:00:00Z")
    row = db._conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    assert row["label"] == "evening" and row["ended_utc"] == "2026-06-10T13:00:00Z"


def test_attempts_replace_and_read(tmp_path):
    from sm64_events.tracking.projection import Attempt
    db = make_db(tmp_path)
    a = Attempt(id=10, session_id=1, course_id=2, star_id=2, strat_tag=None,
                anchor_type="practice_reset", anchor_frame=500, outcome="success",
                outcome_detail=None, igt_frames=343, rta_frames=350,
                started_utc="2026-06-10T12:00:00Z", ended_utc="2026-06-10T12:00:12Z",
                cleared=False, cleared_reason=None)
    db.replace_attempts([a])
    assert db.attempts() == [a]
    b = a.__class__(**{**a.__dict__, "outcome": "reset"})
    db.upsert_attempt(b)
    assert db.attempts()[0].outcome == "reset"


def test_pbs_and_ui_state(tmp_path):
    db = make_db(tmp_path)
    db.insert_pb(course_id=2, star_id=2, strat_tag=None, timer_mode="igt",
                 frames=343, attempt_id=10, saved_utc="2026-06-10T12:01:00Z")
    pbs = db.pbs()
    assert pbs[0]["frames"] == 343 and pbs[0]["timer_mode"] == "igt"
    assert db.get_state("stat_menu", default=[1]) == [1]
    db.set_state("stat_menu", [{"key": "best"}])
    assert db.get_state("stat_menu", default=None) == [{"key": "best"}]
