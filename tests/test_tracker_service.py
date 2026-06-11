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
    assert svc.target == (8, 2) and svc.strat_tag == "carpetless"
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
    assert svc.target == (8, 2)
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
    assert svc2.target == (2, 2)        # state rebuilt from journal


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
    assert svc.target == (8, 2)
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
