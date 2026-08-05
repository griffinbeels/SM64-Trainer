import json
from datetime import datetime, timezone

from sm64_events.core.events import Event
from sm64_events.storage.db import MIGRATIONS, Database

T0 = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)


def make_db(tmp_path) -> Database:
    return Database(tmp_path / "t.db")


def ev(type_="star_collected", frame=100, payload=None) -> Event:
    return Event(type=type_, frame=frame, timestamp_utc=T0, payload=payload or {})


def test_migrations_set_user_version_and_create_tables(tmp_path):
    db = make_db(tmp_path)
    names = {r["name"] for r in db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"events", "sessions", "attempts", "pbs", "ui_state", "routes", "runs",
            "comparisons"} <= names
    assert db._conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)


def test_reopening_existing_db_is_idempotent(tmp_path):
    first = make_db(tmp_path)
    sid = first.insert_session("2026-06-10T12:00:00Z")
    first.close()
    db = make_db(tmp_path)  # second open: migrations must not re-run/crash
    assert db._conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
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


def test_current_pb_filters_by_strategy(tmp_path):
    """current_pb(strat_tag=...) returns the latest PB achieved WITH that
    strategy only — the per-strategy ranking lookup. Without the filter it
    returns the overall latest (strategy-blind)."""
    db = make_db(tmp_path)
    db.insert_pb(course_id=2, star_id=2, strat_tag="A", timer_mode="igt",
                 frames=343, attempt_id=10, saved_utc="2026-06-10T12:01:00Z")
    db.insert_pb(course_id=2, star_id=2, strat_tag="B", timer_mode="igt",
                 frames=350, attempt_id=11, saved_utc="2026-06-10T12:02:00Z")
    # unfiltered → overall latest (strat B, saved last)
    assert db.current_pb(2, 2, "igt")["frames"] == 350
    # each strategy resolves to its OWN time, never the other's
    assert db.current_pb(2, 2, "igt", strat_tag="A")["frames"] == 343
    assert db.current_pb(2, 2, "igt", strat_tag="B")["frames"] == 350
    # a strategy with no saved PB → None (the user is unranked on it)
    assert db.current_pb(2, 2, "igt", strat_tag="C") is None


def test_a_pb_on_a_hidden_attempt_does_not_count(tmp_path):
    """A pb row carries its own frames, so one whose attempt is hidden keeps
    GRADING unless a reader skips it. Both readers do, and the rule is a read
    filter rather than a delete because hiding is reversible: the projector
    auto-clears a success outside its star's validity bounds, and re-widening
    the bounds must bring the save back with the attempt."""
    from sm64_events.tracking.projection import Attempt
    db = make_db(tmp_path)

    def attempt(aid, igt, cleared):
        return Attempt(id=aid, session_id=1, course_id=2, star_id=2,
                       strat_tag="fast", anchor_type="practice_reset",
                       anchor_frame=500, outcome="success", outcome_detail=None,
                       igt_frames=igt, rta_frames=igt + 7,
                       started_utc="2026-06-10T12:00:00Z",
                       ended_utc="2026-06-10T12:00:12Z",
                       cleared=cleared, cleared_reason="auto: too fast" if cleared else None)

    db.replace_attempts([attempt(10, 350, False), attempt(11, 343, False)])
    for aid, frames in ((10, 350), (11, 343)):
        db.insert_pb(course_id=2, star_id=2, strat_tag="fast", timer_mode="igt",
                     frames=frames, attempt_id=aid,
                     saved_utc="2026-06-10T12:01:00Z")
    assert db.current_pb(2, 2, "igt")["frames"] == 343         # latest save wins
    assert [r["frames"] for r in db.pbs()] == [350, 343]

    # hide attempt 11: its save stops counting, 10's is current again
    db.replace_attempts([attempt(10, 350, False), attempt(11, 343, True)])
    assert db.current_pb(2, 2, "igt")["frames"] == 350
    assert db.current_pb(2, 2, "igt", strat_tag="fast")["frames"] == 350
    assert [r["frames"] for r in db.pbs()] == [350]
    # ...and the row is still THERE, which is what makes it reversible
    assert len(db._conn.execute("SELECT id FROM pbs").fetchall()) == 2

    # un-hide it (a widened time filter, applied by the next reprojection)
    db.replace_attempts([attempt(10, 350, False), attempt(11, 343, False)])
    assert db.current_pb(2, 2, "igt")["frames"] == 343
    # a row that was never tied to an attempt always counts
    db.replace_attempts([])
    db.insert_pb(course_id=8, star_id=1, strat_tag=None, timer_mode="igt",
                 frames=400, attempt_id=None, saved_utc="2026-06-10T12:02:00Z")
    assert db.current_pb(8, 1, "igt")["frames"] == 400


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
    assert db._conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
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
    # v4 seeds `level_enter to=17` and v21 repairs it on the way
    # through: LBLJ ends on the ENTRANCE TOUCH since 2026-08-04 (task
    # 0081), so a FRESH db lands on the repaired shape too. Reading
    # the post-migration state is the point -- v4's own literal is
    # not what any db ever runs with.
    assert lblj["end_triggers"] == [{"type": "entrance_touched", "to": 17}]


def test_migration_v23_moves_a_frozen_lakitu_skip_to_the_door(tmp_path):
    """His instruction, 2026-08-05: "Lakitu should be determined by 'move it to
    the door' (when Mario touches the door)."

    The corpus already ends it on the door; a row he has EDITED carries
    `seed_dirty=1`, which blocks reconcile's update branch permanently, so
    only a migration reaches it. v4 seeds the old `level_enter to=6` shape and
    v23 repairs it on the way through, exactly as v21 does for LBLJ above --
    so a FRESH db lands on the repaired shape too, and reading the
    post-migration state is the point.
    """
    db = make_db(tmp_path)
    lakitu = next(d for d in db.segment_defs() if d["name"] == "Lakitu Skip")
    assert lakitu["end_triggers"] == [
        {"type": "moment_reached", "kind": "door_open",
         "level": 16, "ordinal": 1}]


def test_migration_v23_does_not_spend_his_own_edits(tmp_path):
    """The v21 rule, applied again: a repair may fix the thing it is about, it
    may NOT clear `seed_dirty` on the way past. Clearing it would hand the row
    back to reconcile, which would then discard every OTHER edit he made to
    it -- and this row is frozen precisely because he edited it."""
    db = make_db(tmp_path)
    lakitu = next(d for d in db.segment_defs() if d["name"] == "Lakitu Skip")
    other = next(d for d in db.segment_defs() if d["name"] == "LBLJ")
    # A fresh db has never been edited, so both read clean -- what this pins
    # is that v23 does not TOUCH the flag, which a fresh db can only show by
    # the repaired row being no different from an unrepaired one.
    assert lakitu["seed_dirty"] == other["seed_dirty"]
    # And it left the start alone. `spawned` in level 16 is the canonical
    # Lakitu-skip timing start (addresses.py, live-verified 2026-06-12); the
    # plan that said this had to move was corrected before it shipped.
    assert lakitu["start_triggers"] == [{"type": "spawned", "level": 16}]


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


def test_attempts_order_chronologically_across_both_id_namespaces(tmp_path):
    """Live report, spec 2026-07-28-multi-step-segments: a reattributed
    100-coin attempt keeps its SEGMENT-namespace id (jid + 10**10 * def_id,
    tracking/projection.py caveat 2/11) even though it is now a plain star
    attempt (segment_id=None) -- a huge number next to a native star
    attempt's small journal id for the SAME entity. Sorting by the raw `id`
    column stuck two real successes at the top of the practice log
    FOREVER while newer resets piled up underneath them (his own report).

    This is the shape no existing test could have covered: one entity
    (course 2, star 6) with BOTH a reattributed attempt (segment-namespace
    id, EARLIER in wall-clock/journal terms) and a native one (plain
    journal id, LATER) — db.attempts() must return them in JOURNAL order,
    not raw-id order, which for this pair is the OPPOSITE of numeric id
    order."""
    db = make_db(tmp_path)
    # Reattributed: journal id 22218, segment-namespaced as def_id=75 would
    # produce (75 * 10**10 + 22218) -- EARLIER in time, BIGGER raw id.
    reattributed = make_attempt(id=75 * 10**10 + 22218, course_id=2, star_id=6,
                                segment_id=None, rta_frames=2983, igt_frames=2983)
    # Native: journal id 22272 -- LATER in time, SMALLER raw id.
    native = make_attempt(id=22272, course_id=2, star_id=6, segment_id=None,
                          outcome="reset", rta_frames=None, igt_frames=None)
    db.replace_attempts([reattributed, native])
    ordered = db.attempts()
    assert [a.id for a in ordered] == [reattributed.id, native.id], (
        "db.attempts() must sort by journal_id (strips the segment "
        "namespace offset), not the raw id column -- raw-id order would "
        "put these in the OPPOSITE (wrong) sequence")


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
    assert db._conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
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
    assert db._conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
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
    assert db._conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
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
    # (a) version reflects only the successful prefix — the real migrations,
    # not the deliberately-broken one appended above
    assert check.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    # partial application rolled back: first statement did NOT stick
    names = {r[0] for r in check.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "extra" not in names
    check.close()
    # (b) the fixed entry then applies cleanly (no duplicate-table error)
    fixed = "CREATE TABLE extra (id INTEGER);"
    monkeypatch.setattr(db_mod, "MIGRATIONS", db_mod.MIGRATIONS[:-1] + [fixed])
    db = Database(path)
    # the real migrations plus the one this test appends
    assert db._conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS) + 1
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


# -- migration v9: routes.start_condition (per-route run-start condition) -----

def test_migration_v9_adds_start_condition_default_reset(tmp_path):
    db = make_db(tmp_path)
    rid = db.insert_route("R", [], "t")
    [row] = db.routes()
    assert row["start_condition"] == {"type": "reset_game"}   # default


def test_route_insert_with_explicit_start_condition(tmp_path):
    db = make_db(tmp_path)
    rid = db.insert_route("R", [], "t", start_condition={"type": "level_enter", "to": 9})
    row = next(r for r in db.routes() if r["id"] == rid)
    assert row["start_condition"] == {"type": "level_enter", "to": 9}
    db.update_route(rid, start_condition={"type": "reset_game"}, updated_utc="t2")
    assert db.routes()[0]["start_condition"] == {"type": "reset_game"}


# -- migrations v11/v12: waypoints/category/seed columns (default routes) ----

def test_segment_def_round_trips_waypoints_category_seed(tmp_path):
    db = make_db(tmp_path)
    sid = db.insert_segment_def(
        "SL->HMC", [{"type": "level_exit", "from": 10}],
        [{"type": "level_enter", "to": 7}], [], "2026-07-23T00:00:00Z",
        waypoints=[[{"type": "level_enter", "to": 10}]],
        category="Castle Movement", seed_key="seg:sl->hmc")
    row = next(r for r in db.segment_defs() if r["id"] == sid)
    assert row["waypoints"] == [[{"type": "level_enter", "to": 10}]]
    assert row["category"] == "Castle Movement"
    assert row["seed_key"] == "seg:sl->hmc"
    assert row["seed_dirty"] == 0
    db.set_seed_dirty("segment_defs", sid, 1)
    assert next(r for r in db.segment_defs() if r["id"] == sid)["seed_dirty"] == 1


def test_route_round_trips_category_seed(tmp_path):
    db = make_db(tmp_path)
    rid = db.insert_route("16 LBLJ", [], "2026-07-23T00:00:00Z",
                          category="Main Categories", seed_key="route:16-lblj")
    row = next(r for r in db.routes() if r["id"] == rid)
    assert row["category"] == "Main Categories"
    assert row["seed_key"] == "route:16-lblj"
    assert row["seed_dirty"] == 0


def test_retag_pbs_for_attempt_moves_the_row_to_the_new_strategy(tmp_path):
    """A pbs row snapshots strat_tag at save time and is NOT rebuilt from the
    journal, so reclassifying an attempt must carry its PB across — otherwise
    the old strategy keeps a PB that was never achieved with it."""
    db = make_db(tmp_path)
    db.insert_pb(course_id=2, star_id=2, strat_tag="Cannonless",
                 timer_mode="igt", frames=343, attempt_id=10,
                 saved_utc="2026-06-10T12:01:00Z")
    db.insert_pb(course_id=2, star_id=2, strat_tag="Cannonless",
                 timer_mode="igt", frames=350, attempt_id=11,
                 saved_utc="2026-06-10T12:02:00Z")
    db.retag_pbs_for_attempt(10, "Slide Kick")
    assert db.current_pb(2, 2, "igt", strat_tag="Slide Kick")["frames"] == 343
    assert db.current_pb(2, 2, "igt", strat_tag="Cannonless")["frames"] == 350
    # unlabeling is expressible too, and an attempt with no pb row is a no-op
    db.retag_pbs_for_attempt(10, None)
    assert db.current_pb(2, 2, "igt", strat_tag="Slide Kick") is None
    db.retag_pbs_for_attempt(999, "Whatever")
    assert len(db.pbs()) == 2


# -- migration v13: default_strat on a segment definition --------------------

def test_segment_def_round_trips_default_strat(tmp_path):
    """The seeded movements' "Standard" has to survive the db, and a def
    without one must read None rather than blowing up an older row."""
    db = make_db(tmp_path)
    sid = db.insert_segment_def(
        "BoB -> WF", [{"type": "level_exit", "from": 9}],
        [{"type": "level_enter", "to": 24}], [], "2026-07-24T00:00:00Z",
        default_strat="Standard")
    plain = db.insert_segment_def(
        "Mine", [{"type": "spawned"}], [{"type": "level_enter", "to": 6}],
        [], "2026-07-24T00:00:00Z")
    rows = {r["id"]: r for r in db.segment_defs()}
    assert rows[sid]["default_strat"] == "Standard"
    assert rows[plain]["default_strat"] is None
    db.update_segment_def(sid, default_strat="Blindfolded")
    assert next(r for r in db.segment_defs()
                if r["id"] == sid)["default_strat"] == "Blindfolded"


# -- migration v14: uploaded icons became bundled art -------------------------

def _v13_db_with_overrides(tmp_path, overrides):
    """A db one version behind, carrying `overrides`, then opened for real so
    the v14 entry runs against it."""
    import sqlite3
    path = tmp_path / "t.db"
    conn = sqlite3.connect(str(path))
    for script in MIGRATIONS[:13]:
        conn.executescript(script)
    conn.execute("INSERT INTO ui_state (key, value) VALUES ('icon_overrides', ?)",
                 (json.dumps(overrides),))
    conn.execute("PRAGMA user_version = 13")
    conn.commit()
    conn.close()
    db = Database(path)
    assert db._conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    return db.get_state("icon_overrides", {})


def test_v14_repoints_overrides_from_the_uploads_to_the_bundled_stems(tmp_path):
    """Three icons the user uploaded now ship in ui/assets/star_icons, and
    ui/entities.js hands them to the seeded definitions that should wear them
    by default. An override always WINS over a default, so the two entities
    that already named the uploaded copy would otherwise keep resolving
    `user:*.png` out of the data dir forever — editing the asset set alone only
    helps a fresh install (auto-memory: a seed fix needs its own repair
    migration)."""
    overrides = _v13_db_with_overrides(tmp_path, {
        "segment:1": "user:blj.png", "segment:3": "user:lakitu.png",
        "segment:8": "user:castle_movement.png",
        "segment:2": "mips1", "star:9:0": "user:mine.png"})
    assert overrides["segment:1"] == "blj"
    assert overrides["segment:3"] == "lakitu"
    assert overrides["segment:8"] == "castle_movement"
    # Everything else is untouched — including another user upload, which must
    # not move just because it is also an upload.
    assert overrides["segment:2"] == "mips1"
    assert overrides["star:9:0"] == "user:mine.png"


def test_v14_matches_the_whole_stored_value_not_a_prefix(tmp_path):
    """Guarded on the exact JSON string, quotes included: a DIFFERENT upload
    whose name merely starts the same way keeps its own art."""
    overrides = _v13_db_with_overrides(tmp_path, {
        "segment:4": "user:blj-of-my-own.png",
        "segment:5": "user:lakitu2.png"})
    assert overrides == {"segment:4": "user:blj-of-my-own.png",
                         "segment:5": "user:lakitu2.png"}


# -- migration v15: match_mode on a segment definition ------------------------

def test_segment_defs_round_trip_match_mode(tmp_path):
    db = make_db(tmp_path)
    sid = db.insert_segment_def(
        name="x", start_triggers=[{"type": "spawned"}],
        end_triggers=[{"type": "spawned"}], guards=[],
        created_utc="2026-07-28T00:00:00Z")
    # A bare insert_segment_def call defaults to strict (fix round, spec
    # 2026-07-28-multi-step-segments Item 0) — matching the column DEFAULT and
    # every existing row, so a fresh call at this layer agrees with a
    # migrated install regardless of when either happened. "Loose" is an
    # AUTHORING preference for a user-created segment, applied one layer up
    # (SegmentBody.match_mode / TrackerService.create_segment always passes it
    # explicitly), never a storage-layer default.
    assert next(r for r in db.segment_defs() if r["id"] == sid)["match_mode"] \
        == "strict"
    db.update_segment_def(sid, match_mode="loose")
    assert next(r for r in db.segment_defs() if r["id"] == sid)["match_mode"] \
        == "loose"


def test_existing_rows_migrate_to_strict(tmp_path):
    # v15 must not change how a single already-seeded definition matches:
    # the corpus converts row by row in Phase 7, never silently here.
    db = make_db(tmp_path)
    db._conn.execute("INSERT INTO segment_defs (name, enabled, start_triggers,"
                     " end_triggers, waypoints, guards, created_utc)"
                     " VALUES ('legacy',1,'[]','[]','[]','[]','2026-01-01')")
    db._conn.commit()
    assert next(r for r in db.segment_defs()
                if r["name"] == "legacy")["match_mode"] == "strict"


# -- migration v16: repair the Bowser pipe family stranded disabled ----------

_PIPE_FAMILY_SEED_KEYS = ("seg:bitdw-pipe", "seg:bitfs-pipe", "seg:bits-pipe",
                          "seg:reds->pipe:bitdw", "seg:reds->pipe:bitfs",
                          "seg:reds->pipe:bits")


def _v15_db_with_disabled_segments(tmp_path, *, dirty_pipe_family=True):
    """A db one version behind (v15, post the legacy-seed v12 seed_key
    backfill so the three pipe-only rows already carry their seed_key), with
    two of the pipe-family rows and one UNRELATED row left disabled the way
    the retired Bowser-banner exclusion (912466d) could have stranded them —
    a plain enabled=0 write that also dirties a seeded row. The reds->pipe
    siblings never come from a raw migration (they are bundled-seed content,
    reconcile_defaults's job) so they are inserted by hand here with their
    seed_key already set, matching what a reconciled real db looks like."""
    import sqlite3
    path = tmp_path / "t.db"
    conn = sqlite3.connect(str(path))
    for script in MIGRATIONS[:15]:          # bring the db up to v15
        conn.executescript(script)
    conn.execute("UPDATE segment_defs SET enabled=0"
                 + (", seed_dirty=1" if dirty_pipe_family else "")
                 + " WHERE seed_key='seg:bitdw-pipe'")
    conn.execute("INSERT INTO segment_defs (name, enabled, start_triggers,"
                 " end_triggers, waypoints, guards, created_utc, seed_key,"
                 " seed_dirty) VALUES (?,0,'[]','[]','[]','[]',?,?,?)",
                 ("BitFS — 8 Red Coins → Pipe", "2026-07-24T00:00:00Z",
                  "seg:reds->pipe:bitfs", 1 if dirty_pipe_family else 0))
    # An unrelated segment the user disabled on purpose (LBLJ, seeded and
    # untouched by the retired mechanism) — must survive the repair.
    conn.execute("UPDATE segment_defs SET enabled=0, seed_dirty=1"
                 " WHERE seed_key='seg:lblj'")
    conn.execute("PRAGMA user_version = 15")
    conn.commit()
    conn.close()
    return path


def test_v16_reenables_stranded_pipe_family_rows(tmp_path):
    """The two rows the retired exclusion left disabled (912466d, live report
    2026-07-29 — a disabled definition never arms, so both scenarios recorded
    silently nothing across a full practice session) come back enabled."""
    path = _v15_db_with_disabled_segments(tmp_path)
    db = Database(path)
    assert db._conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    rows = {r["seed_key"]: r for r in db.segment_defs()
            if r["seed_key"] in _PIPE_FAMILY_SEED_KEYS}
    assert rows["seg:bitdw-pipe"]["enabled"] is True
    assert rows["seg:reds->pipe:bitfs"]["enabled"] is True
    # v16 ITSELF left seed_dirty exactly as found (that repair was not a user
    # edit and was not supposed to change what reconcile does with these rows
    # next startup) -- but through the FULL migration chain to the current
    # version, v17 (below) now deliberately clears it: leaving it standing is
    # what froze these same six rows against every LATER corpus change, round
    # 2 item 5's own bug. A db landing on today's code sees BOTH repairs.
    assert rows["seg:bitdw-pipe"]["seed_dirty"] == 0
    assert rows["seg:reds->pipe:bitfs"]["seed_dirty"] == 0


def test_v16_leaves_an_unrelated_disabled_segment_disabled(tmp_path):
    """A user may have disabled something outside the Bowser pipe family on
    purpose (LBLJ here) — the repair must not silently re-enable their own
    choice just because it also carries seed_dirty=1."""
    path = _v15_db_with_disabled_segments(tmp_path)
    db = Database(path)
    lblj = next(r for r in db.segment_defs() if r["seed_key"] == "seg:lblj")
    assert lblj["enabled"] is False
    assert lblj["seed_dirty"] == 1


def test_v16_is_idempotent(tmp_path):
    """Re-running the migration set (a fresh Database() open against an
    already-migrated file) must not toggle anything a second time."""
    path = _v15_db_with_disabled_segments(tmp_path)
    Database(path).close()
    db = Database(path)          # re-open: v16's UPDATE runs again on replay?
    rows = {r["seed_key"]: r for r in db.segment_defs()
            if r["seed_key"] in _PIPE_FAMILY_SEED_KEYS}
    assert rows["seg:bitdw-pipe"]["enabled"] is True
    assert rows["seg:reds->pipe:bitfs"]["enabled"] is True


def test_fresh_install_has_no_stranded_rows_to_repair(tmp_path):
    """A brand-new db never runs an old mutual-exclusion write, so every
    pipe-family row starts (and stays) enabled — v16 has nothing to do on a
    fresh install, unlike a migrated one."""
    db = make_db(tmp_path)
    pipe_family = [r for r in db.segment_defs()
                   if r["seed_key"] in _PIPE_FAMILY_SEED_KEYS]
    # The legacy v4 seed only carries the three pipe-only rows; the
    # reds->pipe siblings are bundled-seed content that only exists once
    # reconcile_defaults has run, which is out of scope for the storage layer
    # alone — assert what IS seeded here stays enabled.
    assert pipe_family and all(r["enabled"] for r in pipe_family)


# -- migration v17: unfreeze the Bowser pipe family's seed_dirty flag --------
#
# Round 2, item 5, live report 2026-07-30/31: v16 repaired `enabled` on these
# six rows but, on explicit instruction, deliberately left seed_dirty=1
# standing. That instruction was right about not disguising a repair as a
# user edit and wrong about the consequence -- seed_dirty=1 blocks
# reconcile_defaults's update branch UNCONDITIONALLY, so these six rows were
# frozen against every future corpus change, not just v16's own field.
# seg:bitdw-pipe is the one that actually drifted (match_mode stuck at
# 'strict' instead of the seed's 'exclusive' -- the shape that lets grabbing
# the reds star cancel a no-reds attempt, which plain 'strict' cannot do).

def _v16_db_with_frozen_pipe_family(tmp_path):
    """A db at v16 with all SIX Bowser pipe-family rows carrying seed_dirty=1
    (as the retired mutual-exclusion toggle could have left any of them) and
    seg:bitdw-pipe's OWN match_mode stuck at the pre-v15 default 'strict' --
    reconcile never reached it to bring it to the seed's 'exclusive', exactly
    the live drift measured against this branch's own dev db (never the live
    file). The legacy three (seg:*-pipe) already exist from the v4/v12
    migrations; the reds->pipe siblings are bundled-seed-only content
    (reconcile_defaults's job), so they are inserted by hand here with their
    seed_key already set, matching what a reconciled real db looks like."""
    import sqlite3
    path = tmp_path / "t.db"
    conn = sqlite3.connect(str(path))
    for script in MIGRATIONS[:16]:          # bring the db up to v16
        conn.executescript(script)
    for abbrev, level, name in (("bitdw", 17, "BitDW — 8 Red Coins → Pipe"),
                                 ("bitfs", 19, "BitFS — 8 Red Coins → Pipe"),
                                 ("bits", 21, "BitS — 8 Red Coins → Pipe")):
        conn.execute(
            "INSERT INTO segment_defs (name, enabled, start_triggers,"
            " end_triggers, waypoints, guards, created_utc, seed_key,"
            " seed_dirty, match_mode) VALUES (?,1,?,?,'[]','[]',?,?,1,?)",
            (name, f'[{{"type":"level_enter","to":{level}}}]',
             f'[{{"type":"warp_entered","level":{level}}}]',
             "2026-07-24T00:00:00Z", f"seg:reds->pipe:{abbrev}", "strict"))
    conn.execute("UPDATE segment_defs SET seed_dirty=1, match_mode='strict'"
                 " WHERE seed_key IN ('seg:bitdw-pipe', 'seg:bitfs-pipe', 'seg:bits-pipe')")
    conn.execute("PRAGMA user_version = 16")
    conn.commit()
    conn.close()
    return path


def test_v17_clears_seed_dirty_on_exactly_the_six_pipe_family_rows(tmp_path):
    path = _v16_db_with_frozen_pipe_family(tmp_path)
    db = Database(path)
    assert db._conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    rows = {r["seed_key"]: r for r in db.segment_defs()
            if r["seed_key"] in _PIPE_FAMILY_SEED_KEYS}
    for key in _PIPE_FAMILY_SEED_KEYS:
        assert rows[key]["seed_dirty"] == 0, f"{key} still frozen"


def test_v17_leaves_an_unrelated_dirtied_segment_alone(tmp_path):
    """A user may have deliberately edited something outside the Bowser pipe
    family (LBLJ here) -- clearing ITS seed_dirty would silently discard a
    real edit at the next reconcile, exactly the risk the flag exists to
    prevent. Scoped to the six named seed_keys only."""
    import sqlite3
    path = _v16_db_with_frozen_pipe_family(tmp_path)
    conn = sqlite3.connect(str(path))
    conn.execute("UPDATE segment_defs SET seed_dirty=1 WHERE seed_key='seg:lblj'")
    conn.commit()
    conn.close()
    db = Database(path)
    lblj = next(r for r in db.segment_defs() if r["seed_key"] == "seg:lblj")
    assert lblj["seed_dirty"] == 1


def test_v17_unfreezes_reconcile_and_repairs_the_drifted_match_mode(tmp_path):
    """The consequence, not just the flag: once seed_dirty is cleared, the
    NEXT reconcile_defaults call (every real startup runs one) must bring
    seg:bitdw-pipe's match_mode to the seed's 'exclusive' -- proving the
    unfreeze actually reaches the field that was silently wrong, not merely
    that the flag itself flipped."""
    from sm64_events.tracking.defaults import reconcile_defaults
    path = _v16_db_with_frozen_pipe_family(tmp_path)
    db = Database(path)
    seed = {"segments": [
        {"seed_key": "seg:bitdw-pipe", "name": "BitDW Pipe Entry",
         "start_triggers": [{"type": "level_enter", "to": 17}],
         "end_triggers": [{"type": "warp_entered", "level": 17}],
         "match_mode": "exclusive"},
    ]}
    problems = reconcile_defaults(db, seed)
    assert problems == []
    row = next(r for r in db.segment_defs() if r["seed_key"] == "seg:bitdw-pipe")
    assert row["match_mode"] == "exclusive"


def test_v17_is_idempotent(tmp_path):
    """Re-running the migration set a second time must not error or toggle
    anything further -- seed_dirty is already 0, and setting 0 to 0 again is
    a no-op."""
    path = _v16_db_with_frozen_pipe_family(tmp_path)
    Database(path).close()
    db = Database(path)
    rows = {r["seed_key"]: r for r in db.segment_defs()
            if r["seed_key"] in _PIPE_FAMILY_SEED_KEYS}
    for key in _PIPE_FAMILY_SEED_KEYS:
        assert rows[key]["seed_dirty"] == 0


# -- migration v18: backfill untagged PBs on the 3 unambiguous segments ------
#
# Live report 2026-07-31: "Bowser 1 shows PB 0'26"30, but the rank display
# clearly shows Capless 5 -- this should never happen." Root cause: every
# legacy segment_def carried default_strat=NULL (v13 added the column with no
# repair), so their attempts recorded strat_tag=NULL from day one, and
# views.py's current_pbs_by_strat skips a PB with no strat_tag. Three of the
# seventeen entities the bug actually hit resolve unambiguously to ONE
# strategy in the bundled rank standards -- MIPS Clip and Bowser 1/2, all
# "Standard" -- so backfilling them is not a guess, exactly like giving them a
# default_strat going forward (tools/corpus_legacy.py). The other fourteen
# (ten stars plus BitDW/BitFS/BitS Pipe Entry and Bowser 3) stay untagged on
# purpose: they have real competing strategies, and this migration must never
# touch them.

def _v17_db_with_untagged_pbs(tmp_path):
    """A db at v17 already carries all ten legacy segments (v4's INSERT + v12's
    seed_key backfill), including seg:mips-clip/bowser-1/bowser-2 (the three
    this migration targets) and seg:bitdw-pipe (unrelated, genuinely
    ambiguous) -- so this seeds untagged attempts/PBs onto those EXISTING
    rows rather than inserting new ones under the same seed_key (which would
    never happen on a real install and would make "the" row with that key
    ambiguous). A star (course/star, no segment_id) gets the same shape too,
    proving the segment_id-scoped migration cannot reach a star row even
    though NULL strat_tag looks identical there."""
    import sqlite3
    path = tmp_path / "t.db"
    conn = sqlite3.connect(str(path))
    for script in MIGRATIONS[:17]:          # bring the db up to v17
        conn.executescript(script)
    seg_ids = {r[1]: r[0] for r in conn.execute(
        "SELECT id, seed_key FROM segment_defs WHERE seed_key IN"
        " ('seg:mips-clip', 'seg:bowser-1', 'seg:bowser-2', 'seg:bitdw-pipe')")}
    assert len(seg_ids) == 4, seg_ids   # sanity: all four pre-exist from v4/v12
    aid = 1
    for seed_key in ("seg:mips-clip", "seg:bowser-1", "seg:bowser-2",
                     "seg:bitdw-pipe"):
        seg_id = seg_ids[seed_key]
        conn.execute(
            "INSERT INTO attempts (id, session_id, segment_id, strat_tag,"
            " anchor_type, outcome, rta_frames, started_utc, ended_utc)"
            " VALUES (?,1,?,NULL,'a','success',800,'t','t')", (aid, seg_id))
        conn.execute(
            "INSERT INTO pbs (segment_id, strat_tag, timer_mode, frames,"
            " attempt_id, saved_utc) VALUES (?,NULL,'rta',800,?,'t')",
            (seg_id, aid))
        aid += 1
    # A star PB/attempt, same untagged shape -- must be untouched (no
    # segment_id at all, so the migration's own subquery cannot match it).
    conn.execute(
        "INSERT INTO attempts (id, session_id, course_id, star_id, strat_tag,"
        " anchor_type, outcome, igt_frames, started_utc, ended_utc)"
        " VALUES (?,1,2,0,NULL,'a','success',800,'t','t')", (aid,))
    conn.execute(
        "INSERT INTO pbs (course_id, star_id, strat_tag, timer_mode, frames,"
        " attempt_id, saved_utc) VALUES (2,0,NULL,'igt',800,?,'t')", (aid,))
    conn.execute("PRAGMA user_version = 17")
    conn.commit()
    conn.close()
    return path


def test_v18_backfills_pbs_and_attempts_for_the_three_unambiguous_segments(tmp_path):
    path = _v17_db_with_untagged_pbs(tmp_path)
    db = Database(path)
    assert db._conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    seg_ids = {r["seed_key"]: r["id"] for r in db.segment_defs()
              if r["seed_key"] in ("seg:mips-clip", "seg:bowser-1", "seg:bowser-2")}
    for seed_key, seg_id in seg_ids.items():
        pb = db._conn.execute(
            "SELECT strat_tag FROM pbs WHERE segment_id=?", (seg_id,)).fetchone()
        attempt = db._conn.execute(
            "SELECT strat_tag FROM attempts WHERE segment_id=?", (seg_id,)).fetchone()
        assert pb["strat_tag"] == "Standard", seed_key
        assert attempt["strat_tag"] == "Standard", seed_key


def test_v18_leaves_ambiguous_and_star_rows_untagged(tmp_path):
    """BitDW Pipe Entry (real competing strategies) and the seeded star PB
    (no segment identity at all) must stay exactly as untagged as they
    started -- this migration only ever reaches the three named seed_keys."""
    path = _v17_db_with_untagged_pbs(tmp_path)
    db = Database(path)
    bitdw_id = next(r["id"] for r in db.segment_defs()
                    if r["seed_key"] == "seg:bitdw-pipe")
    pb = db._conn.execute(
        "SELECT strat_tag FROM pbs WHERE segment_id=?", (bitdw_id,)).fetchone()
    attempt = db._conn.execute(
        "SELECT strat_tag FROM attempts WHERE segment_id=?", (bitdw_id,)).fetchone()
    assert pb["strat_tag"] is None
    assert attempt["strat_tag"] is None
    star_pb = db._conn.execute(
        "SELECT strat_tag FROM pbs WHERE course_id=2 AND star_id=0").fetchone()
    star_attempt = db._conn.execute(
        "SELECT strat_tag FROM attempts WHERE course_id=2 AND star_id=0").fetchone()
    assert star_pb["strat_tag"] is None
    assert star_attempt["strat_tag"] is None


def test_v18_is_idempotent(tmp_path):
    """Re-running the migration set a second time must not error -- every
    matching row already carries 'Standard', so the NULL guard makes the
    second pass a no-op."""
    path = _v17_db_with_untagged_pbs(tmp_path)
    Database(path).close()
    db = Database(path)          # re-open: v18's UPDATEs run again on replay?
    seg_ids = {r["seed_key"]: r["id"] for r in db.segment_defs()
              if r["seed_key"] in ("seg:mips-clip", "seg:bowser-1", "seg:bowser-2")}
    for seed_key, seg_id in seg_ids.items():
        pb = db._conn.execute(
            "SELECT strat_tag FROM pbs WHERE segment_id=?", (seg_id,)).fetchone()
        assert pb["strat_tag"] == "Standard", seed_key


def test_v18_never_overwrites_a_real_strat_tag(tmp_path):
    """A row already carrying a real (non-'Standard') strat_tag by the time
    v18 runs -- e.g. reconcile stamped default_strat and a later save tagged
    it with something else entirely -- must survive untouched. Guards on
    strat_tag IS NULL specifically so a genuine tag always wins."""
    import sqlite3
    path = tmp_path / "t.db"
    conn = sqlite3.connect(str(path))
    for script in MIGRATIONS[:17]:
        conn.executescript(script)
    cur = conn.execute(
        "INSERT INTO segment_defs (name, enabled, start_triggers,"
        " end_triggers, waypoints, guards, created_utc, seed_key, seed_dirty)"
        " VALUES ('MIPS Clip',1,'[]','[]','[]','[]','t','seg:mips-clip',0)")
    seg_id = cur.lastrowid
    conn.execute(
        "INSERT INTO attempts (id, session_id, segment_id, strat_tag,"
        " anchor_type, outcome, rta_frames, started_utc, ended_utc)"
        " VALUES (1,1,?,'Blindfolded','a','success',800,'t','t')", (seg_id,))
    conn.execute(
        "INSERT INTO pbs (segment_id, strat_tag, timer_mode, frames,"
        " attempt_id, saved_utc) VALUES (?,'Blindfolded','rta',800,1,'t')",
        (seg_id,))
    conn.execute("PRAGMA user_version = 17")
    conn.commit()
    conn.close()
    db = Database(path)
    pb = db._conn.execute(
        "SELECT strat_tag FROM pbs WHERE segment_id=?", (seg_id,)).fetchone()
    attempt = db._conn.execute(
        "SELECT strat_tag FROM attempts WHERE segment_id=?", (seg_id,)).fetchone()
    assert pb["strat_tag"] == "Blindfolded"
    assert attempt["strat_tag"] == "Blindfolded"


# -- empty-session purge ------------------------------------------------------

def _one_attempt(session_id: int, cleared: bool = False, attempt_id: int = 10):
    from sm64_events.tracking.projection import Attempt
    return Attempt(id=attempt_id, session_id=session_id, course_id=2, star_id=2,
                   strat_tag="fast", anchor_type="practice_reset",
                   anchor_frame=500, outcome="success", outcome_detail=None,
                   igt_frames=350, rta_frames=357,
                   started_utc="2026-06-10T12:00:00Z",
                   ended_utc="2026-06-10T12:00:12Z", cleared=cleared,
                   cleared_reason="auto: too fast" if cleared else None)


def test_delete_empty_sessions_drops_only_the_ones_with_no_attempts(tmp_path):
    db = make_db(tmp_path)
    practiced = db.insert_session("2026-06-10T12:00:00Z")
    empty = db.insert_session("2026-06-10T13:00:00Z")
    active = db.insert_session("2026-06-10T14:00:00Z")
    db.replace_attempts([_one_attempt(practiced)])
    assert db.delete_empty_sessions(active) == [empty]
    assert {s["id"] for s in db.sessions()} == {practiced, active}


def test_delete_empty_sessions_keeps_the_journal_slice(tmp_path):
    """The ROW goes, the events stay. Deleting a purged session's events
    would rewrite the replay of the sessions AROUND it (an attempts_pruned
    or attempt_cleared event recorded in a session that itself banked
    nothing still governs other sessions' attempts)."""
    db = make_db(tmp_path)
    empty = db.insert_session("2026-06-10T13:00:00Z")
    active = db.insert_session("2026-06-10T14:00:00Z")
    db.append_event(empty, seq=1, event=ev("level_changed", 900))
    db.replace_attempts([])
    assert db.delete_empty_sessions(active) == [empty]
    assert [e.session_id for e in db.events()] == [empty]


def test_delete_empty_sessions_spares_a_session_holding_a_cleared_attempt(tmp_path):
    """Cleared is not empty — an ignored attempt is still something he did,
    and it is one Restore click from counting again."""
    db = make_db(tmp_path)
    ignored = db.insert_session("2026-06-10T12:00:00Z")
    active = db.insert_session("2026-06-10T14:00:00Z")
    db.replace_attempts([_one_attempt(ignored, cleared=True)])
    assert db.delete_empty_sessions(active) == []


def test_delete_empty_sessions_never_reuses_a_purged_id(tmp_path):
    """AUTOINCREMENT, not plain rowid: the events left behind keep pointing
    at a number no future session can be handed."""
    db = make_db(tmp_path)
    empty = db.insert_session("2026-06-10T13:00:00Z")
    active = db.insert_session("2026-06-10T14:00:00Z")
    db.replace_attempts([])
    db.delete_empty_sessions(active)
    assert db.insert_session("2026-06-10T15:00:00Z") > active > empty
