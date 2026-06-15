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
    assert {"events", "sessions", "attempts", "pbs", "ui_state", "routes", "runs"} <= names
    assert db._conn.execute("PRAGMA user_version").fetchone()[0] == 8


def test_reopening_existing_db_is_idempotent(tmp_path):
    first = make_db(tmp_path)
    sid = first.insert_session("2026-06-10T12:00:00Z")
    first.close()
    db = make_db(tmp_path)  # second open: migrations must not re-run/crash
    assert db._conn.execute("PRAGMA user_version").fetchone()[0] == 8
    row = db._conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    assert row is not None and row["started_utc"] == "2026-06-10T12:00:00Z"


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
    db.replace_attempts([])
    assert db.attempts() == []


def test_pbs_and_ui_state(tmp_path):
    db = make_db(tmp_path)
    db.insert_pb(course_id=2, star_id=2, strat_tag=None, timer_mode="igt",
                 frames=343, attempt_id=10, saved_utc="2026-06-10T12:01:00Z")
    pbs = db.pbs()
    assert pbs[0]["frames"] == 343 and pbs[0]["timer_mode"] == "igt"
    assert db.get_state("stat_menu", default=[1]) == [1]
    db.set_state("stat_menu", [{"key": "best"}])
    assert db.get_state("stat_menu", default=None) == [{"key": "best"}]


def test_sessions_returns_newest_first_with_attempt_counts(tmp_path):
    from sm64_events.tracking.projection import Attempt
    db = make_db(tmp_path)
    s1 = db.insert_session("2026-06-10T10:00:00Z")
    s2 = db.insert_session("2026-06-10T11:00:00Z")
    # upsert two attempts under session 1
    for i, aid in enumerate([10, 11]):
        a = Attempt(id=aid, session_id=s1, course_id=2, star_id=2,
                    strat_tag=None, anchor_type="practice_reset",
                    anchor_frame=100 * (i + 1), outcome="success",
                    outcome_detail=None, igt_frames=343, rta_frames=350,
                    started_utc="2026-06-10T10:00:00Z",
                    ended_utc="2026-06-10T10:00:10Z",
                    cleared=False, cleared_reason=None)
        db.upsert_attempt(a)
    rows = db.sessions()
    # newest first
    assert rows[0]["id"] == s2 and rows[1]["id"] == s1
    assert rows[1]["attempts"] == 2
    assert rows[0]["attempts"] == 0


def test_delete_session_removes_events_and_row_leaves_others(tmp_path):
    db = make_db(tmp_path)
    s1 = db.insert_session("2026-06-10T10:00:00Z")
    s2 = db.insert_session("2026-06-10T11:00:00Z")
    db.append_event(s1, seq=1, event=ev())
    db.append_event(s1, seq=2, event=ev())
    db.append_event(s2, seq=1, event=ev())
    assert len(db.events()) == 3
    db.delete_session(s1)
    remaining = db.events()
    assert len(remaining) == 1 and remaining[0].session_id == s2
    # session row gone
    row = db._conn.execute("SELECT * FROM sessions WHERE id=?", (s1,)).fetchone()
    assert row is None
    # session 2 still there
    row2 = db._conn.execute("SELECT * FROM sessions WHERE id=?", (s2,)).fetchone()
    assert row2 is not None


# -- migrations v2+v3: dust-trick counts (Phase 2) ----------------------------

def test_migrations_add_dust_trick_columns(tmp_path):
    db = make_db(tmp_path)
    cols = {r["name"] for r in db._conn.execute("PRAGMA table_info(attempts)")}
    assert {"rollouts_total", "rollouts_dustless",
            "jumps_total", "jumps_dustless"} <= cols


def test_v1_database_upgrades_in_place(tmp_path):
    # a real Phase 1 db (user_version=1) must gain the columns on open
    import sqlite3
    from sm64_events.storage.db import MIGRATIONS
    path = tmp_path / "t.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(MIGRATIONS[0])
    conn.execute("INSERT INTO sessions (started_utc) VALUES ('2026-06-10T12:00:00Z')")
    conn.execute("INSERT INTO attempts (id, session_id, anchor_type, outcome,"
                 " started_utc, ended_utc) VALUES (1, 1, 'practice_reset',"
                 " 'success', 's', 'e')")
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()
    db = Database(path)
    assert db._conn.execute("PRAGMA user_version").fetchone()[0] == 8
    assert db.attempts()[0].rollouts_total == 0   # backfilled default
    assert db.attempts()[0].jumps_total == 0


def test_attempts_round_trip_dust_trick_counts(tmp_path):
    from sm64_events.tracking.projection import Attempt
    db = make_db(tmp_path)
    a = Attempt(id=10, session_id=1, course_id=2, star_id=2, strat_tag=None,
                anchor_type="practice_reset", anchor_frame=500, outcome="success",
                outcome_detail=None, igt_frames=343, rta_frames=350,
                started_utc="2026-06-10T12:00:00Z", ended_utc="2026-06-10T12:00:12Z",
                cleared=False, cleared_reason=None,
                rollouts_total=5, rollouts_dustless=3,
                jumps_total=4, jumps_dustless=2)
    db.replace_attempts([a])
    assert db.attempts() == [a]


# -- migration v4: segment_defs, attempts.segment_id, kind-aware pbs ----------

def make_attempt(**overrides):
    """Factory that fills every Attempt field with defaults then applies overrides."""
    from sm64_events.tracking.projection import Attempt
    defaults = dict(
        id=1, session_id=1, course_id=2, star_id=1, strat_tag=None,
        anchor_type="practice_reset", anchor_frame=100,
        outcome="success", outcome_detail=None,
        igt_frames=300, rta_frames=310,
        started_utc="2026-06-11T00:00:00Z", ended_utc="2026-06-11T00:00:10Z",
        cleared=False, cleared_reason=None,
        rollouts_total=0, rollouts_dustless=0,
        jumps_total=0, jumps_dustless=0,
        segment_id=None,
    )
    defaults.update(overrides)
    return Attempt(**defaults)


def test_migration_v4_seeds_ten_segment_definitions(tmp_path):
    db = make_db(tmp_path)
    defs = db.segment_defs()
    assert len(defs) == 10
    lblj = next(d for d in defs if d["name"] == "LBLJ")
    assert lblj["enabled"] is True
    # warp-menu arming (live gate 2026-06-12): fresh DBs seed the
    # area-scoped attempt_anchor alongside the level edge
    assert lblj["start_triggers"] == [
        {"type": "level_enter", "to": 6, "from": 16},
        {"type": "attempt_anchor", "level": 6, "area": 1}]
    assert lblj["end_triggers"] == [{"type": "level_enter", "to": 17}]


def test_fresh_db_seeds_bowser3_ending_on_key_grabbed(tmp_path):
    # Regression: the ORIGINAL v4 seed (commit c9a03cd) ended Bowser 3 on
    # star_grabbed, which the grand star can NEVER fire (it enters
    # ACT_JUMBO_STAR_CUTSCENE -> key_grabbed, not star_collected — see
    # detectors/key.py). The segment armed but never completed (live report
    # 2026-06-12). 419c4e6 fixed the seed; this pins it so a future seed edit
    # can't silently re-break detection. (Existing-db repair: v6, below.)
    db = make_db(tmp_path)
    b3 = next(d for d in db.segment_defs() if d["name"] == "Bowser 3")
    assert b3["end_triggers"] == [{"type": "key_grabbed", "level": 34}]


def test_segment_def_crud_roundtrip(tmp_path):
    db = make_db(tmp_path)
    sid = db.insert_segment_def("Test", [{"type": "spawned"}],
                                [{"type": "level_enter", "to": 6}], [],
                                "2026-06-11T00:00:00Z")
    db.update_segment_def(sid, name="Test2", enabled=False)
    d = next(d for d in db.segment_defs() if d["id"] == sid)
    assert d["name"] == "Test2" and d["enabled"] is False
    db.delete_segment_def(sid)
    assert all(d["id"] != sid for d in db.segment_defs())


def test_attempts_roundtrip_preserves_segment_id(tmp_path):
    db = make_db(tmp_path)
    a = make_attempt(id=5, segment_id=3, course_id=None, star_id=None,
                     rta_frames=88)
    db.upsert_attempt(a)
    assert db.attempts()[0].segment_id == 3


def test_pb_accepts_segment_keying_and_null_course(tmp_path):
    db = make_db(tmp_path)
    db.insert_pb(course_id=None, star_id=None, strat_tag=None,
                 timer_mode="rta", frames=85, attempt_id=None,
                 saved_utc="2026-06-11T00:00:00Z", segment_id=1)
    row = db.pbs()[0]
    assert row["segment_id"] == 1 and row["course_id"] is None


def test_update_segment_def_unknown_field_raises_value_error(tmp_path):
    import pytest
    db = make_db(tmp_path)
    sid = db.insert_segment_def("Test", [{"type": "spawned"}],
                                [{"type": "level_enter", "to": 6}], [],
                                "2026-06-11T00:00:00Z")
    with pytest.raises(ValueError, match="unknown"):
        db.update_segment_def(sid, nonexistent_field="oops")


def test_v3_database_pb_rows_survive_v4_rebuild(tmp_path):
    # a real pre-segment db (user_version=3) must keep its PB rows — id,
    # frames, keying — through v4's pbs_v2 rebuild, gaining segment_id=NULL
    import sqlite3
    from sm64_events.storage.db import MIGRATIONS
    path = tmp_path / "t.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(MIGRATIONS[0])
    conn.executescript(MIGRATIONS[1])
    conn.executescript(MIGRATIONS[2])
    conn.execute("INSERT INTO pbs (id, course_id, star_id, timer_mode,"
                 " frames, saved_utc) VALUES (7, 2, 3, 'igt', 500,"
                 " '2026-06-10T12:00:00Z')")
    conn.execute("PRAGMA user_version = 3")
    conn.commit()
    conn.close()
    db = Database(path)
    assert db._conn.execute("PRAGMA user_version").fetchone()[0] == 8
    [row] = db.pbs()
    assert row["id"] == 7 and row["frames"] == 500
    assert row["course_id"] == 2 and row["star_id"] == 3
    assert row["segment_id"] is None


# -- migration v5: warp-menu arming — LBLJ gains the area-scoped anchor ------

def test_v5_updates_existing_v4_lblj_row_with_area_anchor(tmp_path):
    """An existing v4 db (created before the warp-menu amendment) carries
    the OLD LBLJ start_triggers; v5 must rewrite them in place.  (Fresh DBs
    get the new triggers straight from the edited v4 seed, so the
    pre-amendment shape is restored by hand here.)"""
    import sqlite3
    from sm64_events.storage.db import MIGRATIONS
    path = tmp_path / "t.db"
    conn = sqlite3.connect(str(path))
    for script in MIGRATIONS[:4]:
        conn.executescript(script)
    conn.execute("UPDATE segment_defs SET start_triggers="
                 "'[{\"type\":\"level_enter\",\"to\":6,\"from\":16}]'"
                 " WHERE id=1 AND name='LBLJ'")
    conn.execute("PRAGMA user_version = 4")
    conn.commit()
    conn.close()
    db = Database(path)
    assert db._conn.execute("PRAGMA user_version").fetchone()[0] == 8
    lblj = next(d for d in db.segment_defs() if d["name"] == "LBLJ")
    assert lblj["start_triggers"] == [
        {"type": "level_enter", "to": 6, "from": 16},
        {"type": "attempt_anchor", "level": 6, "area": 1}]


# -- migration v6: grand-star repair — Bowser 3 ends on key_grabbed ----------

def test_v6_repairs_existing_bowser3_end_trigger(tmp_path):
    """An existing db seeded from the ORIGINAL v4 (commit c9a03cd) ended
    Bowser 3 on star_grabbed.  The grand star never enters a star-dance
    action (detectors/key.py) — it fires key_grabbed which='grand', never
    star_collected — so that segment armed but could never complete.  The
    seed was corrected in 419c4e6 for FRESH DBs but no repair shipped for
    existing ones; v6 is that repair.  (Fresh DBs already carry the fixed
    end trigger, so the pre-fix shape is restored by hand here.)"""
    import sqlite3
    from sm64_events.storage.db import MIGRATIONS
    path = tmp_path / "t.db"
    conn = sqlite3.connect(str(path))
    for script in MIGRATIONS[:5]:          # bring the db up to v5
        conn.executescript(script)
    conn.execute("UPDATE segment_defs SET end_triggers="
                 "'[{\"type\":\"star_grabbed\"}]'"
                 " WHERE id=10 AND name='Bowser 3'")
    conn.execute("PRAGMA user_version = 5")
    conn.commit()
    conn.close()
    db = Database(path)
    assert db._conn.execute("PRAGMA user_version").fetchone()[0] == 8
    b3 = next(d for d in db.segment_defs() if d["name"] == "Bowser 3")
    assert b3["end_triggers"] == [{"type": "key_grabbed", "level": 34}]


# -- migration v7: routes (ordered star/segment plans) -----------------------

def test_migration_v7_creates_routes_table(tmp_path):
    db = make_db(tmp_path)
    names = {r["name"] for r in db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "routes" in names


def test_route_crud_roundtrip(tmp_path):
    db = make_db(tmp_path)
    steps = [{"need": 1, "candidates": [{"type": "segment", "segment_id": 1}]},
             {"need": 2, "label": "Whomp's", "candidates": [
                 {"type": "star", "course": 2, "star": 0},
                 {"type": "star", "course": 2, "star": 1},
                 {"type": "star", "course": 2, "star": 2}]}]
    rid = db.insert_route("Standard", steps, "2026-06-14T00:00:00Z")
    [row] = db.routes()
    assert row["id"] == rid and row["name"] == "Standard"
    assert row["steps"] == steps                       # JSON round-trips
    assert row["created_utc"] == row["updated_utc"]
    db.update_route(rid, name="Standard v2", updated_utc="2026-06-14T01:00:00Z")
    row = db.routes()[0]
    assert row["name"] == "Standard v2"
    assert row["updated_utc"] == "2026-06-14T01:00:00Z"
    db.delete_route(rid)
    assert db.routes() == []


def test_update_route_unknown_field_raises(tmp_path):
    import pytest
    db = make_db(tmp_path)
    rid = db.insert_route("R", [], "2026-06-14T00:00:00Z")
    with pytest.raises(ValueError, match="unknown"):
        db.update_route(rid, bogus="x")


def test_update_delete_unknown_route_raises_lookup(tmp_path):
    import pytest
    db = make_db(tmp_path)
    with pytest.raises(LookupError):
        db.update_route(999, name="x")
    with pytest.raises(LookupError):
        db.delete_route(999)


def test_v6_leaves_user_customized_bowser3_untouched(tmp_path):
    """The repair is triple-guarded on the EXACT broken seed value, so a
    user who deliberately re-pointed Bowser 3's end trigger keeps it."""
    import sqlite3
    from sm64_events.storage.db import MIGRATIONS
    path = tmp_path / "t.db"
    conn = sqlite3.connect(str(path))
    for script in MIGRATIONS[:5]:
        conn.executescript(script)
    conn.execute("UPDATE segment_defs SET end_triggers="
                 "'[{\"type\":\"level_enter\",\"to\":6}]'"
                 " WHERE id=10 AND name='Bowser 3'")
    conn.execute("PRAGMA user_version = 5")
    conn.commit()
    conn.close()
    db = Database(path)
    b3 = next(d for d in db.segment_defs() if d["name"] == "Bowser 3")
    assert b3["end_triggers"] == [{"type": "level_enter", "to": 6}]


def test_failed_migration_rolls_back_schema_and_version(tmp_path, monkeypatch):
    # a crash mid-entry must roll back BOTH the partial schema changes and
    # the version write, so a fixed entry can later apply cleanly
    import sqlite3
    import pytest
    import sm64_events.storage.db as db_mod
    path = tmp_path / "t.db"
    Database(path).close()                       # all real migrations applied
    bad = "CREATE TABLE extra (id INTEGER); CREATE TABLE broken (oops"
    monkeypatch.setattr(db_mod, "MIGRATIONS", db_mod.MIGRATIONS + [bad])
    with pytest.raises(sqlite3.OperationalError):
        Database(path)
    check = sqlite3.connect(str(path))
    # (a) version reflects only the successful prefix
    assert check.execute("PRAGMA user_version").fetchone()[0] == 8
    # partial application rolled back: first statement did NOT stick
    names = {r[0] for r in check.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "extra" not in names
    check.close()
    # (b) the fixed entry then applies cleanly (no duplicate-table error)
    fixed = "CREATE TABLE extra (id INTEGER);"
    monkeypatch.setattr(db_mod, "MIGRATIONS", db_mod.MIGRATIONS[:-1] + [fixed])
    db = Database(path)
    assert db._conn.execute("PRAGMA user_version").fetchone()[0] == 9
    db.close()


# -- migration v8: runs (full-game run history) ------------------------------

def test_migration_v8_creates_runs_table(tmp_path):
    db = make_db(tmp_path)
    names = {r["name"] for r in db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "runs" in names


def _run_row(**o):
    d = dict(id=500, route_id=1, route_name="R", route_steps=[{"need": 1,
             "candidates": [{"type": "star", "course": 2, "star": 0}]}],
             mode="forgiving", status="finished", reached_step=1,
             total_ms=120000, start_offset_ms=1360,
             started_utc="2026-06-14T00:00:00Z", ended_utc="2026-06-14T00:02:00Z",
             is_pb=1, splits=[{"step_index": 0, "elapsed_ms": 120000}])
    d.update(o); return d


def test_run_insert_and_read(tmp_path):
    db = make_db(tmp_path)
    db.insert_run(_run_row())
    [r] = db.runs()
    assert r["id"] == 500 and r["status"] == "finished" and r["total_ms"] == 120000
    assert r["route_steps"][0]["need"] == 1            # JSON round-trips
    assert r["splits"][0]["elapsed_ms"] == 120000
    assert r["is_pb"] is True


def test_runs_filter_by_route_and_finished(tmp_path):
    db = make_db(tmp_path)
    db.insert_run(_run_row(id=1, route_id=1, status="finished"))
    db.insert_run(_run_row(id=2, route_id=1, status="aborted", is_pb=0))
    db.insert_run(_run_row(id=3, route_id=2, status="finished"))
    assert {r["id"] for r in db.runs(route_id=1)} == {1, 2}
    assert {r["id"] for r in db.runs(route_id=1, finished_only=True)} == {1}


def test_replace_runs_rebuilds_cache(tmp_path):
    db = make_db(tmp_path)
    db.insert_run(_run_row(id=9))
    db.replace_runs([])
    assert db.runs() == []


def test_run_settings_default_and_set(tmp_path):
    db = make_db(tmp_path)
    assert db.get_state("run_settings", {"start_offset_ms": 1360}) == {"start_offset_ms": 1360}
    db.set_state("run_settings", {"start_offset_ms": 2000})
    assert db.get_state("run_settings", {})["start_offset_ms"] == 2000
