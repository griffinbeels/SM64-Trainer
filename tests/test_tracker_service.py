# tests/test_tracker_service.py
import asyncio
from datetime import datetime, timezone

import pytest

from sm64_events.core.events import Event
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.storage.db import Database
from sm64_events.tracking.service import TrackerService

T0 = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)


def ev(type_, frame, payload=None):
    return Event(type=type_, frame=frame, timestamp_utc=T0, payload=payload or {})


def star(frame, course=2, star_id=2, igt=343):
    return ev("star_collected", frame,
              {"course_id": course, "star_id": star_id, "igt_frames": igt})


def make(tmp_path):
    db = Database(tmp_path / "t.db")
    svc = TrackerService(db, Broadcaster())
    asyncio.run(svc.start())
    return db, svc


class RecordingBroadcaster(Broadcaster):
    """Real broadcaster that also captures every published Event.
    Needed for segment_armed/segment_disarmed assertions: notices are
    broadcast-only and never reach the journal, so db.events() is blind
    to them."""

    def __init__(self):
        super().__init__()
        self.sent: list[Event] = []

    async def publish(self, event: Event) -> int:
        self.sent.append(event)
        return await super().publish(event)


def make_rec(tmp_path):
    db = Database(tmp_path / "t.db")
    bc = RecordingBroadcaster()
    svc = TrackerService(db, bc)
    asyncio.run(svc.start())
    return db, svc, bc.sent


def seed_id(db, name):
    """Resolve a seeded segment def's id by name — tests must not couple
    to autoincrement positions in the v4 migration seed list."""
    return next(d["id"] for d in db.segment_defs() if d["name"] == name)


def test_start_creates_session_and_journals_it(tmp_path):
    db, svc = make(tmp_path)
    assert svc.session_id == 1
    assert [e.type for e in db.events()] == ["session_started"]


def test_events_are_journaled_and_attempts_persisted(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1350)))
    attempts = db.attempts()
    assert len(attempts) == 1 and attempts[0].outcome == "success"
    types = [e.type for e in db.events()]
    assert "attempt_completed" in types          # derived event journaled too
    assert "target_changed" in types


def test_broadcaster_seq_returned_and_stored(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(star(900)))
    rows = db.events()
    assert all(r.seq > 0 for r in rows)
    assert len({r.seq for r in rows}) == len(rows)   # distinct seqs


def test_set_target_and_attribution(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.set_target(8, 2, strat_tag="carpetless"))
    assert svc.target == ("star", 8, 2) and svc.strat_tag == "carpetless"
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(ev("practice_reset", 1400, {"igt_frames_before": 380})))
    fails = [a for a in db.attempts() if a.outcome == "reset"]
    assert (fails[0].course_id, fails[0].star_id) == (8, 2)


def test_clear_reprojects_and_restore_undoes(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.set_target(8, 2))
    asyncio.run(svc.publish(star(900)))            # accidental WF grab
    grab_id = db.attempts()[0].id
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(ev("practice_reset", 1400, {"igt_frames_before": 380})))
    asyncio.run(svc.clear_attempt(grab_id, reason="accidental"))
    fails = [a for a in db.attempts() if a.outcome == "reset"]
    assert (fails[0].course_id, fails[0].star_id) == (8, 2)   # re-attributed
    assert svc.target == ("star", 8, 2)
    asyncio.run(svc.restore_attempt(grab_id))
    fails = [a for a in db.attempts() if a.outcome == "reset"]
    assert (fails[0].course_id, fails[0].star_id) == (2, 2)


def test_save_pb_inserts_row_and_journals(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1350)))
    aid = db.attempts()[0].id
    pb = asyncio.run(svc.save_pb(aid, "igt"))
    assert pb["frames"] == 343 and db.pbs()[0]["course_id"] == 2
    assert "pb_saved" in [e.type for e in db.events()]


def test_save_pb_rejects_missing_attempt(tmp_path):
    db, svc = make(tmp_path)
    with pytest.raises(LookupError):
        asyncio.run(svc.save_pb(999, "igt"))


def two_successes(db, svc):
    """Two successes on the same star: igt 343 then 350. Returns their ids."""
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1350, igt=343)))
    asyncio.run(svc.publish(ev("practice_reset", 1400, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1760, igt=350)))
    first = next(a.id for a in db.attempts() if a.igt_frames == 343)
    second = next(a.id for a in db.attempts() if a.igt_frames == 350)
    return first, second


def test_undo_pb_restores_previous_pb(tmp_path):
    db, svc = make(tmp_path)
    first, second = two_successes(db, svc)
    asyncio.run(svc.save_pb(first, "igt"))
    asyncio.run(svc.save_pb(second, "igt"))      # supersedes first
    out = asyncio.run(svc.undo_pb(second, "igt"))
    assert out["frames"] == 350
    assert out["restored_frames"] == 343 and out["restored_attempt_id"] == first
    [row] = db.pbs()
    assert row["attempt_id"] == first            # first is current again
    assert "pb_undone" in [e.type for e in db.events()]


def test_undo_pb_with_single_save_leaves_no_pb(tmp_path):
    db, svc = make(tmp_path)
    first, _ = two_successes(db, svc)
    asyncio.run(svc.save_pb(first, "igt"))
    out = asyncio.run(svc.undo_pb(first, "igt"))
    assert out["restored_frames"] is None and out["restored_attempt_id"] is None
    assert db.pbs() == []


def test_undo_pb_rejects_attempt_that_is_not_current(tmp_path):
    # a newer save superseded this attempt's: undoing it must not delete
    # anything (its row is no longer what "current PB" points at)
    db, svc = make(tmp_path)
    first, second = two_successes(db, svc)
    asyncio.run(svc.save_pb(first, "igt"))
    asyncio.run(svc.save_pb(second, "igt"))
    with pytest.raises(ValueError):
        asyncio.run(svc.undo_pb(first, "igt"))
    assert len(db.pbs()) == 2                    # nothing deleted


def test_undo_pb_is_per_timer_mode(tmp_path):
    db, svc = make(tmp_path)
    first, _ = two_successes(db, svc)
    asyncio.run(svc.save_pb(first, "igt"))
    with pytest.raises(ValueError):              # no rta save to undo
        asyncio.run(svc.undo_pb(first, "rta"))
    assert len(db.pbs()) == 1


def test_undo_pb_rejects_missing_attempt_and_bad_mode(tmp_path):
    db, svc = make(tmp_path)
    with pytest.raises(LookupError):
        asyncio.run(svc.undo_pb(999, "igt"))
    with pytest.raises(ValueError):
        asyncio.run(svc.undo_pb(999, "lap"))     # mode checked first, like save_pb


def test_undo_pb_segment_is_kind_aware(tmp_path):
    # undoing a segment PB must not touch star rows (kind-aware keying:
    # segment rows match by segment_id, star rows by course+star)
    db, svc, sent = make_rec(tmp_path)
    lblj = seed_id(db, "LBLJ")
    asyncio.run(svc.publish(ev("practice_reset", 500, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(900)))
    star_aid = next(a.id for a in db.attempts() if a.segment_id is None)
    asyncio.run(svc.save_pb(star_aid, "rta"))
    asyncio.run(svc.publish(ev("level_changed", 1000, {"from": 16, "to": 6})))
    asyncio.run(svc.publish(ev("level_changed", 1085, {"from": 6, "to": 17})))
    seg_aid = next(a.id for a in db.attempts() if a.segment_id == lblj)
    asyncio.run(svc.save_pb(seg_aid, "rta"))
    out = asyncio.run(svc.undo_pb(seg_aid, "rta"))
    assert out["segment_id"] == lblj and out["restored_frames"] is None
    [row] = db.pbs()
    assert row["attempt_id"] == star_aid         # the star PB survived


# -- wipe_data ----------------------------------------------------------------

def success(svc, frame, course=2, star_id=2, igt=343):
    asyncio.run(svc.publish(ev("practice_reset", frame, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(frame + 350, course=course, star_id=star_id, igt=igt)))


def test_wipe_star_session_scope_spares_other_sessions_and_stars(tmp_path):
    db, svc = make(tmp_path)
    success(svc, 1000)                            # session 1, (2,2)
    asyncio.run(svc.new_session())
    success(svc, 5000, igt=350)                   # session 2, (2,2)
    success(svc, 6000, course=8, star_id=1)       # session 2, (8,1)
    asyncio.run(svc.wipe_data("star", course_id=2, star_id=2, scope="session"))
    keys = [(a.session_id, a.course_id, a.star_id) for a in db.attempts()
            if a.outcome == "success"]
    assert (1, 2, 2) in keys                      # other session survives
    assert (2, 8, 1) in keys                      # other star survives
    assert (2, 2, 2) not in keys                  # wiped
    wiped = [e for e in db.events() if e.type == "data_wiped"]
    assert wiped[-1].payload["session_id"] == 2   # journaled with a concrete id


def test_wipe_star_lifetime_wipes_history_and_pbs(tmp_path):
    db, svc = make(tmp_path)
    success(svc, 1000)
    asyncio.run(svc.new_session())
    success(svc, 5000, igt=350)
    success(svc, 6000, course=8, star_id=1, igt=500)
    a22 = next(a.id for a in db.attempts() if (a.course_id, a.star_id) == (2, 2))
    a81 = next(a.id for a in db.attempts() if (a.course_id, a.star_id) == (8, 1))
    asyncio.run(svc.save_pb(a22, "igt"))
    asyncio.run(svc.save_pb(a81, "igt"))
    asyncio.run(svc.wipe_data("star", course_id=2, star_id=2, scope="lifetime"))
    assert all((a.course_id, a.star_id) != (2, 2) for a in db.attempts())
    [pb] = db.pbs()                               # only the (8,1) pb remains
    assert (pb["course_id"], pb["star_id"]) == (8, 1)


def test_wipe_star_session_scope_pb_falls_back_to_prior_session(tmp_path):
    db, svc = make(tmp_path)
    success(svc, 1000)                            # s1: igt 343
    a1 = db.attempts()[0].id
    asyncio.run(svc.save_pb(a1, "igt"))
    asyncio.run(svc.new_session())
    success(svc, 5000, igt=330)                   # s2: faster
    a2 = next(a.id for a in db.attempts() if a.igt_frames == 330)
    asyncio.run(svc.save_pb(a2, "igt"))
    asyncio.run(svc.wipe_data("star", course_id=2, star_id=2, scope="session"))
    [pb] = db.pbs()                               # s2's save vanished with its attempt
    assert pb["attempt_id"] == a1                 # s1's PB is current again


def test_wipe_segment_lifetime_spares_star_data(tmp_path):
    db, svc, _ = make_rec(tmp_path)
    lblj = seed_id(db, "LBLJ")
    success(svc, 500)                             # star attempt
    asyncio.run(svc.publish(ev("level_changed", 1000, {"from": 16, "to": 6})))
    asyncio.run(svc.publish(ev("level_changed", 1085, {"from": 6, "to": 17})))
    seg_aid = next(a.id for a in db.attempts() if a.segment_id == lblj)
    asyncio.run(svc.save_pb(seg_aid, "rta"))
    asyncio.run(svc.wipe_data("segment", segment_id=lblj, scope="lifetime"))
    assert all(a.segment_id != lblj for a in db.attempts())
    assert any(a.segment_id is None for a in db.attempts())   # star attempt kept
    assert db.pbs() == []                          # segment pb gone


def test_wipe_star_spares_segment_data(tmp_path):
    db, svc, _ = make_rec(tmp_path)
    lblj = seed_id(db, "LBLJ")
    asyncio.run(svc.publish(ev("level_changed", 1000, {"from": 16, "to": 6})))
    asyncio.run(svc.publish(ev("level_changed", 1085, {"from": 6, "to": 17})))
    success(svc, 2000)
    asyncio.run(svc.wipe_data("star", course_id=2, star_id=2, scope="lifetime"))
    assert any(a.segment_id == lblj for a in db.attempts())   # segment survives


def test_wipe_survives_restart(tmp_path):
    db, svc = make(tmp_path)
    success(svc, 1000)
    asyncio.run(svc.wipe_data("star", course_id=2, star_id=2, scope="lifetime"))
    success(svc, 5000, igt=350)                   # fresh data after the wipe
    db2 = Database(tmp_path / "t.db")
    svc2 = TrackerService(db2, Broadcaster())
    asyncio.run(svc2.start())                     # replay applies the wipe event
    igts = [a.igt_frames for a in db2.attempts() if a.outcome == "success"]
    assert igts == [350]


def test_wipe_all_session_scope(tmp_path):
    db, svc = make(tmp_path)
    success(svc, 1000)                            # session 1
    asyncio.run(svc.new_session())
    asyncio.run(svc.publish(ev("practice_reset", 5000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(ev("practice_reset", 5500,                  # unassigned reset
                               {"igt_frames_before": 470, "mario_acted": True})))
    success(svc, 6000, igt=350)
    a2 = next(a.id for a in db.attempts() if a.igt_frames == 350)
    asyncio.run(svc.save_pb(a2, "igt"))
    asyncio.run(svc.wipe_data("all", scope="session"))
    assert [a.session_id for a in db.attempts()] == [1]      # s2 wiped clean
    assert db.pbs() == []                                    # s2's pb gone
    assert any(s["id"] == 2 for s in db.sessions())          # session row kept
    success(svc, 9000, igt=360)                              # still records
    assert any(a.session_id == 2 and a.igt_frames == 360 for a in db.attempts())


def test_wipe_all_lifetime_factory_resets_history(tmp_path):
    db, svc = make(tmp_path)
    success(svc, 1000)
    a1 = db.attempts()[0].id
    asyncio.run(svc.save_pb(a1, "igt"))
    asyncio.run(svc.new_session())
    success(svc, 5000, igt=350)
    defs_before = len(db.segment_defs())
    asyncio.run(svc.wipe_data("all", scope="lifetime"))
    assert db.attempts() == [] and db.pbs() == []
    assert [s["id"] for s in db.sessions()] == [svc.session_id]  # only active
    assert len(db.segment_defs()) == defs_before  # definitions are config, not history
    success(svc, 9000, igt=360)                   # tracking continues
    assert len(db.attempts()) == 1


def test_wipe_guards(tmp_path):
    db, svc = make(tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(svc.wipe_data("nonsense", scope="session"))
    with pytest.raises(ValueError):
        asyncio.run(svc.wipe_data("star", course_id=2, star_id=2, scope="weekly"))
    with pytest.raises(ValueError):
        asyncio.run(svc.wipe_data("star", course_id=2, scope="session"))
    with pytest.raises(ValueError):
        asyncio.run(svc.wipe_data("segment", scope="session"))


def test_new_session_closes_open_attempt_as_abandoned(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.new_session())
    assert svc.session_id == 2
    assert db.attempts()[0].outcome == "abandoned"


def test_restart_resumes_from_journal(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.set_target(8, 2))
    asyncio.run(svc.publish(star(900)))
    db2 = Database(tmp_path / "t.db")
    svc2 = TrackerService(db2, Broadcaster())
    asyncio.run(svc2.start())
    assert svc2.session_id == 2
    assert svc2.target == ("star", 2, 2)   # state rebuilt from journal


def test_degraded_mode_without_db_still_broadcasts(tmp_path):
    svc = TrackerService(None, Broadcaster())
    asyncio.run(svc.start())
    asyncio.run(svc.publish(star(900)))   # must not raise
    assert svc.session_id is None


def test_reproject_emits_target_changed_when_target_reverts(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.set_target(8, 2))
    asyncio.run(svc.publish(star(900)))            # target moves to (2,2)
    grab_id = db.attempts()[0].id
    asyncio.run(svc.clear_attempt(grab_id, reason="accidental"))
    assert svc.target == ("star", 8, 2)
    tc = [e for e in db.events() if e.type == "target_changed"]
    assert tc[-1].payload["course_id"] == 8 and tc[-1].payload["star_id"] == 2


def test_restore_unknown_attempt_raises_lookup_error(tmp_path):
    db, svc = make(tmp_path)
    with pytest.raises(LookupError):
        asyncio.run(svc.restore_attempt(999))


def test_set_target_registers_strategy(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.set_target(2, 4, strat_tag="owlless"))
    asyncio.run(svc.set_target(2, 4, strat_tag="owl"))
    asyncio.run(svc.set_target(2, 4, strat_tag="owlless"))   # no dup
    assert db.get_state("strategies", {}) == {"2:4": ["owlless", "owl"]}
    assert svc.strat_by_star[(2, 4)] == "owlless"


def test_death_event_flows_to_death_attempt(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000,
                               {"igt_frames_before": 0, "mario_acted": True})))
    asyncio.run(svc.publish(ev("death", 1300,
                               {"cause": "drowning", "igt_frames": 290, "level": 9})))
    [a] = db.attempts()
    assert a.outcome == "death" and a.outcome_detail == "drowning"
    types = [e.type for e in db.events()]
    assert "attempt_completed" in types


def test_pipeline_survives_attempt_persist_failure(tmp_path):
    db, svc = make(tmp_path)
    original = db.upsert_attempt
    db.upsert_attempt = lambda a: (_ for _ in ()).throw(RuntimeError("disk full"))
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1350)))           # must not raise
    db.upsert_attempt = original
    db2 = Database(tmp_path / "t.db")
    svc2 = TrackerService(db2, Broadcaster())
    asyncio.run(svc2.start())                      # replay self-heals
    assert any(a.outcome == "success" for a in db2.attempts())


# -- continue_session tests ---------------------------------------------------

def test_continue_session_routes_new_events_to_old_session(tmp_path):
    db, svc = make(tmp_path)
    # Build a star in session 1
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1350)))
    s1 = svc.session_id
    # Start session 2
    asyncio.run(svc.new_session())
    s2 = svc.session_id
    assert s2 == 2
    # Continue session 1: new events land in s1
    asyncio.run(svc.continue_session(s1))
    assert svc.session_id == s1
    asyncio.run(svc.publish(ev("practice_reset", 2000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(2400)))
    # All new journal rows after the continue belong to s1
    new_rows = [e for e in db.events() if e.type == "star_collected" and e.session_id == s1]
    assert len(new_rows) == 2
    # The new attempt's session_id matches s1
    success_attempts = [a for a in db.attempts() if a.outcome == "success" and a.session_id == s1]
    assert len(success_attempts) == 2


def test_continue_session_emits_session_started_with_resumed(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.new_session())
    s1 = 1
    asyncio.run(svc.continue_session(s1))
    journal = db.events()
    resumed_events = [e for e in journal
                      if e.type == "session_started" and e.payload.get("resumed") is True]
    assert len(resumed_events) == 1
    assert resumed_events[0].payload["session_id"] == s1


def test_continue_session_reopens_resumed_and_closes_left(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.new_session())            # ends session 1, opens session 2
    asyncio.run(svc.continue_session(1))      # resume session 1
    rows = {s["id"]: s for s in db.sessions()}
    assert rows[1]["ended_utc"] is None       # active again: reopened
    assert rows[2]["ended_utc"] is not None   # the session we left is closed


def test_continue_session_unknown_id_raises_lookup_error(tmp_path):
    db, svc = make(tmp_path)
    with pytest.raises(LookupError):
        asyncio.run(svc.continue_session(999))


def test_continue_session_active_is_noop(tmp_path):
    db, svc = make(tmp_path)
    active = svc.session_id
    before_count = len(db.events())
    result = asyncio.run(svc.continue_session(active))
    assert result == active
    # No new session_started event appended (no-op)
    after_count = len(db.events())
    assert after_count == before_count


# -- delete_session tests -----------------------------------------------------

def test_delete_session_removes_events_and_reprojects(tmp_path):
    db, svc = make(tmp_path)
    # Session 1: one success
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1350)))
    s1_attempt_count = len([a for a in db.attempts() if a.session_id == 1])
    # Session 2: another success (active)
    asyncio.run(svc.new_session())
    asyncio.run(svc.publish(ev("practice_reset", 5000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(5400)))
    # Delete session 1
    asyncio.run(svc.delete_session(1))
    # Session 1 events are gone
    s1_events = [e for e in db.events() if e.session_id == 1]
    assert s1_events == []
    # Session 1 attempts are gone from the cache
    s1_attempts = [a for a in db.attempts() if a.session_id == 1]
    assert s1_attempts == []
    # Session 2 attempts still intact
    s2_attempts = [a for a in db.attempts() if a.session_id == 2]
    assert len(s2_attempts) >= 1


def test_delete_active_session_raises_value_error(tmp_path):
    db, svc = make(tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(svc.delete_session(svc.session_id))


def test_delete_unknown_session_raises_lookup_error(tmp_path):
    db, svc = make(tmp_path)
    with pytest.raises(LookupError):
        asyncio.run(svc.delete_session(999))


def test_attempt_completed_carries_rollout_counts(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(ev("rollout", 1100,
                               {"dustless": True, "frames_late": 0, "level": 24})))
    asyncio.run(svc.publish(ev("rollout", 1200,
                               {"dustless": False, "frames_late": 2, "level": 24})))
    asyncio.run(svc.publish(star(1350)))
    a = db.attempts()[0]
    assert a.rollouts_total == 2 and a.rollouts_dustless == 1
    completed = [e for e in db.events() if e.type == "attempt_completed"]
    assert completed[-1].payload["rollouts_total"] == 2
    assert completed[-1].payload["rollouts_dustless"] == 1


def test_attempt_completed_carries_jump_counts(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(ev("jump", 1100,
                               {"dustless": True, "frames_late": 0,
                                "landing_frames": 1, "kind": "double",
                                "level": 24})))
    asyncio.run(svc.publish(star(1350)))
    a = db.attempts()[0]
    assert a.jumps_total == 1 and a.jumps_dustless == 1
    completed = [e for e in db.events() if e.type == "attempt_completed"]
    assert completed[-1].payload["jumps_total"] == 1
    assert completed[-1].payload["jumps_dustless"] == 1


# -- segment CRUD / target / broadcast tests (Task 12) -------------------------

def test_segment_crud_create_triggers_reprojection(tmp_path):
    db, svc, sent = make_rec(tmp_path)
    sid = asyncio.run(svc.create_segment({
        "name": "X", "start_triggers": [{"type": "spawned"}],
        "end_triggers": [{"type": "level_enter", "to": 6}], "guards": []}))
    assert any(e.type == "attempts_invalidated" for e in sent)
    assert any(d["id"] == sid and d["name"] == "X" for d in db.segment_defs())


def test_create_segment_invalid_definition_raises_before_insert(tmp_path):
    db, svc, sent = make_rec(tmp_path)
    before = len(db.segment_defs())
    with pytest.raises(ValueError):
        asyncio.run(svc.create_segment({
            "name": "X", "start_triggers": [{"type": "nope"}],
            "end_triggers": [], "guards": []}))
    assert len(db.segment_defs()) == before     # validate BEFORE insert


def test_update_segment_validates_merged_definition(tmp_path):
    db, svc, sent = make_rec(tmp_path)
    lblj = seed_id(db, "LBLJ")
    # partial patch must validate as the MERGED whole, not in isolation
    asyncio.run(svc.update_segment(lblj, {"enabled": False}))
    d = next(d for d in db.segment_defs() if d["id"] == lblj)
    assert d["enabled"] is False and d["name"] == "LBLJ"
    assert any(e.type == "attempts_invalidated" for e in sent)
    with pytest.raises(ValueError):
        asyncio.run(svc.update_segment(lblj, {"start_triggers": [{"type": "nope"}]}))
    with pytest.raises(LookupError):
        asyncio.run(svc.update_segment(999, {"enabled": False}))


def test_delete_segment_removes_def_and_reprojects(tmp_path):
    db, svc, sent = make_rec(tmp_path)
    lblj = seed_id(db, "LBLJ")
    asyncio.run(svc.delete_segment(lblj))
    assert all(d["id"] != lblj for d in db.segment_defs())
    assert any(e.type == "attempts_invalidated" for e in sent)
    with pytest.raises(LookupError):
        asyncio.run(svc.delete_segment(lblj))


def test_set_target_segment_round_trip(tmp_path):
    db, svc, sent = make_rec(tmp_path)
    lblj = seed_id(db, "LBLJ")
    asyncio.run(svc.set_target_segment(lblj))
    assert svc.target == ("segment", lblj)
    ts = next(e for e in sent if e.type == "target_set")
    assert ts.payload == {"kind": "segment", "segment_id": lblj}
    tc = [e for e in sent if e.type == "target_changed"]
    assert tc and tc[-1].payload["kind"] == "segment"
    assert tc[-1].payload["segment_id"] == lblj
    assert tc[-1].payload["segment_name"] == "LBLJ"
    assert tc[-1].payload["course_id"] is None           # shape stability: UI header keys off course_id
    with pytest.raises(LookupError):
        asyncio.run(svc.set_target_segment(9999))


def test_set_target_segment_with_strat_remembers_it(tmp_path):
    db, svc, sent = make_rec(tmp_path)
    lblj = seed_id(db, "LBLJ")
    asyncio.run(svc.set_target_segment(lblj, strat_tag="quickturn"))
    assert svc.target == ("segment", lblj) and svc.strat_tag == "quickturn"


def test_segment_attempt_completed_carries_segment_fields(tmp_path):
    db, svc, sent = make_rec(tmp_path)
    lblj = seed_id(db, "LBLJ")
    # seeded LBLJ: arms on grounds(16)->castle(6), ends on ->BitDW(17)
    asyncio.run(svc.publish(ev("level_changed", 1000, {"from": 16, "to": 6})))
    asyncio.run(svc.publish(ev("level_changed", 1085, {"from": 6, "to": 17})))
    done = [e for e in sent if e.type == "attempt_completed"
            and e.payload.get("kind") == "segment"]
    assert done and done[0].payload["segment_id"] == lblj
    assert done[0].payload["segment_name"] == "LBLJ"
    assert done[0].payload["rta_frames"] == 85
    assert done[0].payload["rta"] == "0'02\"83"
    armed = [e for e in sent if e.type == "segment_armed"]
    assert armed and armed[0].payload["segment_id"] == lblj
    # notices are broadcast-only: they must never reach the journal
    journaled = [e.type for e in db.events()]
    assert "segment_armed" not in journaled
    assert "segment_disarmed" not in journaled


def test_star_attempt_completed_carries_kind_star(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1350)))
    p = [e for e in db.events() if e.type == "attempt_completed"][-1].payload
    assert p["kind"] == "star"
    assert p["segment_id"] is None and p["segment_name"] is None


def test_segment_armed_broadcast_survives_recursive_publish(tmp_path):
    """One published event must close an attempt (attempt_completed fires via
    recursive _track publish) while the segment stays continuously armed —
    anchor closures emit NO armed/disarmed notices (attempt boundary, not a
    state change; live-gate amendment 2026-06-12).

    Sequence (seeded BitDW Pipe Entry — starts: level_enter to=17 OR
    attempt_anchor level=17; end: warp_entered level=17):

      1. level_changed {from:6,to:17}  -> arms the def via level_enter
      2. practice_reset @1100          -> closes the armed segment as
         outcome "reset" (attempt_completed -> publish -> _track recursion)
         AND re-arms the def silently in place (no notices — UI chip stays
         lit without flickering).  The attempt_completed fires THROUGH the
         recursive path.

    Verify: attempt_completed fires with outcome "reset"; no armed/disarmed
    notices at frame 1100; segment remains armed after the reset."""
    db, svc, sent = make_rec(tmp_path)
    bitdw = seed_id(db, "BitDW Pipe Entry")
    asyncio.run(svc.publish(ev("level_changed", 1000, {"from": 6, "to": 17})))
    asyncio.run(svc.publish(ev("practice_reset", 1100, {"igt_frames_before": 0})))
    completed = [e for e in sent if e.type == "attempt_completed"]
    assert completed and completed[-1].payload["outcome"] == "reset"  # recursion happened
    # anchor closure emits no notices — the segment never stops being armed
    notices_at_1100 = [e for e in sent
                       if e.type in ("segment_armed", "segment_disarmed")
                       and e.frame == 1100]
    assert notices_at_1100 == [], "anchor closure must not emit armed/disarmed notices"
    assert bitdw in svc.armed_segment_ids, "segment must remain armed after anchor closure"


def test_save_pb_segment_requires_rta_and_inserts_segment_row(tmp_path):
    db, svc, sent = make_rec(tmp_path)
    lblj = seed_id(db, "LBLJ")
    asyncio.run(svc.publish(ev("level_changed", 1000, {"from": 16, "to": 6})))
    asyncio.run(svc.publish(ev("level_changed", 1085, {"from": 6, "to": 17})))
    aid = next(a.id for a in db.attempts() if a.segment_id == lblj)
    with pytest.raises(ValueError):
        asyncio.run(svc.save_pb(aid, "igt"))    # segments are RTA-only
    pb = asyncio.run(svc.save_pb(aid, "rta"))
    assert pb["frames"] == 85 and pb["segment_id"] == lblj
    row = db.pbs()[-1]
    assert row["segment_id"] == lblj
    assert row["course_id"] is None and row["star_id"] is None


def test_update_segment_reproject_diff_broadcasts_disarm(tmp_path):
    """Replay re-derives armed state silently: disabling an ARMED def must
    broadcast segment_disarmed (the reproject armed-set diff) or the UI
    badge keeps lying."""
    db, svc, sent = make_rec(tmp_path)
    bitdw = seed_id(db, "BitDW Pipe Entry")
    asyncio.run(svc.publish(ev("level_changed", 1000, {"from": 6, "to": 17})))
    assert any(e.type == "segment_armed" and e.payload["segment_id"] == bitdw
               for e in sent)
    asyncio.run(svc.update_segment(bitdw, {"enabled": False}))
    # frame 0 pins the notice to the reproject diff (live notices carry
    # the journal event's frame, 1000 here)
    assert any(e.type == "segment_disarmed" and e.frame == 0
               and e.payload["segment_id"] == bitdw for e in sent)


def test_reproject_during_track_tail_abandons_stale_attempts(tmp_path):
    """Projector-identity race: a CRUD command awaited from INSIDE _track's
    attempt loop (modeling an API request landing while _track is
    suspended) swaps self._projector mid-tail. The replay already accounted
    for the in-flight journaled row, so the old tail must be ABANDONED —
    finishing it would upsert a stale segment attempt the replace_attempts
    just wiped.

    Construction: level_changed {6->17} arms BitDW Pipe Entry; the
    practice_reset @1100 closes it (closed=[seg reset attempt]) and emits
    attempt_completed via recursive publish. The broadcaster deletes the def
    upon the frame-1100 attempt_completed (kind=segment) — i.e. during the
    attempt loop, AFTER the notice drain. Without the identity guard the loop
    then upserts the stale seg attempt from the replaced projector back into
    the freshly re-projected table.

    Note: anchor closures emit no armed/disarmed notices (live-gate amendment
    2026-06-12), so the trigger is attempt_completed rather than segment_armed."""
    db = Database(tmp_path / "t.db")

    class DeleteOnCompleted(RecordingBroadcaster):
        def __init__(self):
            super().__init__()
            self.svc = None
            self.target_id = None
            self.fired = False

        async def publish(self, event: Event) -> int:
            seq = await super().publish(event)
            if (event.type == "attempt_completed"
                    and event.payload.get("kind") == "segment"
                    and event.frame == 1100
                    and not self.fired):
                self.fired = True
                await self.svc.delete_segment(self.target_id)
            return seq

    bc = DeleteOnCompleted()
    svc = TrackerService(db, bc)
    bc.svc = svc
    asyncio.run(svc.start())
    bc.target_id = seed_id(db, "BitDW Pipe Entry")
    asyncio.run(svc.publish(ev("level_changed", 1000, {"from": 6, "to": 17})))
    asyncio.run(svc.publish(ev("practice_reset", 1100, {"igt_frames_before": 0})))
    assert bc.fired
    # the stale tail was abandoned: no seg attempt re-upserted, no second
    # attempt_completed for the deleted def
    assert all(a.segment_id != bc.target_id for a in db.attempts())
    completed_for_target = [e for e in bc.sent
                            if e.type == "attempt_completed"
                            and e.payload.get("segment_id") == bc.target_id]
    # exactly one attempt_completed fires (the one that triggered the delete);
    # the tail abandonment prevents a second upsert+broadcast
    assert len(completed_for_target) == 1


def test_stage_changed_is_broadcast_only_and_cached(tmp_path):
    db, svc, sent = make_rec(tmp_path)
    asyncio.run(svc.publish(ev("stage_changed", 200,
                               {"course_id": 8, "level": 8, "area": 1,
                                "in_stage": True})))
    # broadcast to clients...
    assert "stage_changed" in [e.type for e in sent]
    # ...but NEVER journaled (recomputable; no historical-query value)
    assert "stage_changed" not in [e.type for e in db.events()]
    # ...and cached for the session view's initial load
    assert svc.current_stage == {"course_id": 8, "level": 8, "area": 1,
                                 "in_stage": True}


def test_current_stage_defaults_to_not_in_stage(tmp_path):
    db, svc = make(tmp_path)
    assert svc.current_stage == {"course_id": None, "level": None,
                                 "area": None, "in_stage": False}


# -- routes (Phase A) ---------------------------------------------------------

def test_create_route_persists_and_broadcasts(tmp_path):
    db, svc, sent = make_rec(tmp_path)
    lblj = seed_id(db, "LBLJ")
    rid = asyncio.run(svc.create_route({
        "name": "Test Route", "steps": [
            {"need": 1, "candidates": [{"type": "segment", "segment_id": lblj}]},
            {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]}))
    assert any(r["id"] == rid and r["name"] == "Test Route" for r in db.routes())
    assert any(e.type == "routes_changed" for e in sent)


def test_create_route_rejects_missing_segment(tmp_path):
    db, svc = make(tmp_path)
    with pytest.raises(LookupError):
        asyncio.run(svc.create_route({"name": "Bad", "steps": [
            {"need": 1, "candidates": [{"type": "segment", "segment_id": 99999}]}]}))


def test_create_route_rejects_invalid_definition(tmp_path):
    db, svc = make(tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(svc.create_route({"name": "", "steps": []}))


def test_update_and_delete_route(tmp_path):
    db, svc = make(tmp_path)
    rid = asyncio.run(svc.create_route({"name": "R", "steps": [
        {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]}))
    asyncio.run(svc.update_route(rid, {"name": "R2"}))
    assert next(r for r in db.routes() if r["id"] == rid)["name"] == "R2"
    asyncio.run(svc.delete_route(rid))
    assert all(r["id"] != rid for r in db.routes())
    with pytest.raises(LookupError):
        asyncio.run(svc.delete_route(rid))


def test_update_route_unknown_id_raises(tmp_path):
    db, svc = make(tmp_path)
    with pytest.raises(LookupError):
        asyncio.run(svc.update_route(999, {"name": "x"}))


def test_export_then_import_reuses_existing_segment(tmp_path):
    db, svc = make(tmp_path)
    lblj = seed_id(db, "LBLJ")
    rid = asyncio.run(svc.create_route({"name": "Exp", "steps": [
        {"need": 1, "candidates": [{"type": "segment", "segment_id": lblj}]},
        {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]}))
    payload = svc.export_route(rid)
    assert payload["kind"] == "sm64-route" and payload["version"] == 1
    assert payload["steps"][0]["candidates"][0]["segment"]["name"] == "LBLJ"
    preview = asyncio.run(svc.import_route(payload, dry_run=True))
    assert preview["reused"] == ["LBLJ"] and preview["created"] == []
    out = asyncio.run(svc.import_route(payload))
    imported = next(r for r in db.routes() if r["id"] == out["id"])
    assert imported["steps"][0]["candidates"][0] == {"type": "segment",
                                                     "segment_id": lblj}


def test_import_creates_missing_segment(tmp_path):
    db, svc = make(tmp_path)
    payload = {"kind": "sm64-route", "version": 1, "name": "Imp", "steps": [
        {"need": 1, "candidates": [{"type": "segment", "segment": {
            "name": "Brand New Seg", "start_triggers": [{"type": "spawned"}],
            "end_triggers": [{"type": "level_enter", "to": 6}], "guards": []}}]}]}
    before = len(db.segment_defs())
    out = asyncio.run(svc.import_route(payload))
    assert len(db.segment_defs()) == before + 1
    new = next(d for d in db.segment_defs() if d["name"] == "Brand New Seg")
    imported = next(r for r in db.routes() if r["id"] == out["id"])
    assert imported["steps"][0]["candidates"][0]["segment_id"] == new["id"]


def test_export_unknown_route_raises(tmp_path):
    db, svc = make(tmp_path)
    with pytest.raises(LookupError):
        svc.export_route(999)


def test_import_rejects_bad_envelope(tmp_path):
    db, svc = make(tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(svc.import_route({"kind": "nope", "version": 1, "name": "x",
                                      "steps": [{"need": 1, "candidates": []}]}))


# -- runs (Phase D) -----------------------------------------------------------

def _route_with(db, svc):
    seed_id(db, "LBLJ")  # ensure LBLJ seed is present (side-effect: confirms db seeded)
    return asyncio.run(svc.create_route({"name": "Run R", "steps": [
        {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]}))


def test_start_run_journals_and_arms(tmp_path):
    db, svc, sent = make_rec(tmp_path)
    rid = _route_with(db, svc)
    asyncio.run(svc.start_run(rid))
    ev_list = [e for e in db.events() if e.type == "run_started"]
    assert len(ev_list) == 1
    assert ev_list[-1].payload["route_id"] == rid
    assert ev_list[-1].payload["route_name"] == "Run R"
    assert ev_list[-1].payload["route_steps"][0]["need"] == 1
    assert ev_list[-1].payload["start_offset_ms"] == 1360       # default
    assert any(e.type == "run_started" for e in sent)           # broadcast too


def test_full_run_persists_finished_row(tmp_path):
    db, svc = make(tmp_path)
    rid = _route_with(db, svc)
    asyncio.run(svc.start_run(rid))
    asyncio.run(svc.publish(ev("game_reset", 0)))
    asyncio.run(svc.publish(star(900, course=2, star_id=0)))
    [run] = db.runs()
    assert run["status"] == "finished" and run["route_id"] == rid
    assert run["is_pb"] is True


def test_start_run_unknown_route_raises(tmp_path):
    db, svc = make(tmp_path)
    with pytest.raises(LookupError):
        asyncio.run(svc.start_run(99999))


def test_run_settings_get_and_update(tmp_path):
    db, svc = make(tmp_path)
    assert svc.run_settings()["start_offset_ms"] == 1360
    asyncio.run(svc.update_run_settings({"start_offset_ms": 2000}))
    assert svc.run_settings()["start_offset_ms"] == 2000
    with pytest.raises(ValueError):
        asyncio.run(svc.update_run_settings({"start_offset_ms": -5}))


def test_runs_rebuild_on_restart(tmp_path):
    db, svc = make(tmp_path)
    rid = _route_with(db, svc)
    asyncio.run(svc.start_run(rid))
    asyncio.run(svc.publish(ev("game_reset", 0)))
    asyncio.run(svc.publish(star(900, course=2, star_id=0)))
    db2 = Database(tmp_path / "t.db")
    svc2 = TrackerService(db2, Broadcaster())
    asyncio.run(svc2.start())                  # replay re-derives + replace_runs
    assert len(db2.runs()) == 1 and db2.runs()[0]["status"] == "finished"


def test_run_finished_not_journaled(tmp_path):
    """run_finished/run_aborted are broadcast-only: they must never appear in
    db.events() (they are derived and the projector ignores them on replay —
    like segment_armed/segment_disarmed)."""
    db, svc = make(tmp_path)
    rid = _route_with(db, svc)
    asyncio.run(svc.start_run(rid))
    asyncio.run(svc.publish(ev("game_reset", 0)))
    asyncio.run(svc.publish(star(900, course=2, star_id=0)))
    journaled_types = [e.type for e in db.events()]
    assert "run_finished" not in journaled_types
    assert "run_aborted" not in journaled_types
    assert "run_progress" not in journaled_types


def test_create_route_default_start_condition(tmp_path):
    db, svc = make(tmp_path)
    rid = asyncio.run(svc.create_route({"name": "R", "steps": [
        {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]}))
    assert next(r for r in db.routes() if r["id"] == rid)["start_condition"] == {"type": "reset_game"}


def test_start_run_includes_start_condition(tmp_path):
    db, svc, sent = make_rec(tmp_path)
    rid = asyncio.run(svc.create_route({"name": "R",
        "start_condition": {"type": "level_enter", "to": 9}, "steps": [
        {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]}))
    asyncio.run(svc.start_run(rid))
    ev_list = [e for e in db.events() if e.type == "run_started"][-1]
    assert ev_list.payload["start_condition"] == {"type": "level_enter", "to": 9}


# -- run pause/resume/reset (Phase E) -----------------------------------------

def test_pause_run_journals_run_paused(tmp_path):
    db, svc = make(tmp_path)
    rid = _route_with(db, svc)
    asyncio.run(svc.start_run(rid))
    asyncio.run(svc.pause_run())
    types = [e.type for e in db.events()]
    assert "run_paused" in types


def test_reset_run_journals_run_reset(tmp_path):
    db, svc = make(tmp_path)
    rid = _route_with(db, svc)
    asyncio.run(svc.start_run(rid))
    asyncio.run(svc.reset_run())
    types = [e.type for e in db.events()]
    assert "run_reset" in types


def test_editing_armed_route_steps_rearms_run(tmp_path):
    db, svc = make(tmp_path)
    rid = asyncio.run(svc.create_route({"name": "R", "steps": [
        {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]}))
    asyncio.run(svc.start_run(rid))                 # arms with course 2 star 0
    # edit the step to a different star
    asyncio.run(svc.update_route(rid, {"steps": [
        {"need": 1, "candidates": [{"type": "star", "course": 8, "star": 2}]}]}))
    # the LATEST run_started snapshot must reflect the edited step
    rs = [e for e in db.events() if e.type == "run_started"][-1]
    assert rs.payload["route_steps"][0]["candidates"][0] == {"type": "star", "course": 8, "star": 2}
    # and now grabbing course 8 star 2 (after a game_reset) finishes the run
    asyncio.run(svc.publish(ev("game_reset", 0)))
    asyncio.run(svc.publish(star(900, course=8, star_id=2)))
    assert any(r["status"] == "finished" and r["route_id"] == rid for r in db.runs())


def test_editing_unarmed_route_does_not_emit_run_started(tmp_path):
    db, svc = make(tmp_path)
    rid = asyncio.run(svc.create_route({"name": "R", "steps": [
        {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]}))
    # never armed -> editing must NOT emit run_started
    asyncio.run(svc.update_route(rid, {"name": "R2"}))
    assert not any(e.type == "run_started" for e in db.events())


def test_editing_armed_route_mid_run_voids_not_aborts(tmp_path):
    db, svc = make(tmp_path)
    rid = asyncio.run(svc.create_route({"name": "R", "steps": [
        {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]}))
    asyncio.run(svc.start_run(rid))
    asyncio.run(svc.publish(ev("game_reset", 0)))            # a run is now ACTIVE
    asyncio.run(svc.update_route(rid, {"steps": [
        {"need": 1, "candidates": [{"type": "star", "course": 8, "star": 2}]}]}))
    # the interrupted run is VOID — no aborted (or any) run row was saved by the edit
    assert db.runs() == []
    # the fresh snapshot has the edited step; grabbing it (after F1) finishes
    asyncio.run(svc.publish(ev("game_reset", 0)))
    asyncio.run(svc.publish(star(900, course=8, star_id=2)))
    assert [r["status"] for r in db.runs()] == ["finished"]
