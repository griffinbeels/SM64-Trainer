"""Real SQLite fault boundaries, bounded input retries, and journal recovery."""
import asyncio
import sqlite3

import pytest

from sm64_events.inputs.frame import InputFrame
from sm64_events.inputs.observation import InputObservation
from sm64_events.inputs.store import ChunkWriter
from sm64_events.storage.db import Database
from sm64_events.tracking.projection import Projector, project, replay, time_corrections, warp_destinations
from sm64_events.tracking.runs import RunTracker
from sm64_events.tracking.segments import MatchContext, SegmentDef, SegmentEngine
from test_projection import jev, star as journal_star
from test_runs import CTX, Ev, STAR, att, started
from test_tracker_service import ev, make_rec, star

AT = "2026-09-14T12:00:00+00:00"
PAD = InputFrame(0, 0, 84, -84)


def test_failed_database_initialization_closes_connection(tmp_path, monkeypatch):
    opened = []
    connect = sqlite3.connect

    def record_connection(*args, **kwargs):
        connection = connect(*args, **kwargs)
        opened.append(connection)
        return connection

    def fail_migration(self):
        raise OSError("unavailable migration")

    monkeypatch.setattr(sqlite3, "connect", record_connection)
    monkeypatch.setattr(Database, "_migrate", fail_migration)
    with pytest.raises(OSError, match="unavailable migration"):
        Database(tmp_path / "initialization.db")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[0].execute("SELECT 1")


class CommitFault:
    """Fail before commit with a REAL pending SQLite transaction, once."""
    def __init__(self, connection):
        self.connection = connection
        self.armed = True

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def __enter__(self):
        return self

    def __exit__(self, *error):
        return self.connection.__exit__(*error)

    def commit(self):
        if self.armed:
            self.armed = False
            assert self.connection.in_transaction
            raise sqlite3.OperationalError("injected commit failure")
        return self.connection.commit()


@pytest.mark.parametrize("operation", ["input", "event", "attempt", "replace"])
def test_commit_failure_rolls_back_before_another_writer_can_commit(tmp_path, operation):
    db = Database(tmp_path / "rollback.db")
    session = db.insert_session(AT)
    attempt = project([journal_star(1, 350)])[0]
    if operation == "replace":
        db.upsert_attempt(attempt)
    real = db._conn
    db._conn = CommitFault(real)
    calls = {
        "input": lambda: db.inputs.append(session, [(1, PAD)], AT, AT),
        "event": lambda: db.append_event(session, 1, star(350)),
        "attempt": lambda: db.upsert_attempt(attempt),
        "replace": lambda: db.replace_attempts([]),
    }
    with pytest.raises(sqlite3.OperationalError, match="commit failure"):
        calls[operation]()
    assert not real.in_transaction
    # An unrelated write must not make the failed operation durable later.
    db.set_state("after_failure", True)
    with sqlite3.connect(tmp_path / "rollback.db") as observer:
        assert observer.execute("SELECT COUNT(*) FROM input_chunks").fetchone()[0] == 0
        assert observer.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
        assert observer.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == (operation == "replace")
    calls[operation]()
    table = {"input": "input_chunks", "event": "events", "attempt": "attempts", "replace": "attempts"}[operation]
    assert real.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == (operation != "replace")
    db.close()


def observation(source, sequence):
    return InputObservation(source, sequence, AT, AT)


def test_input_retry_is_paced_keeps_owner_and_preserves_missing_frames(tmp_path):
    db = Database(tmp_path / "input.db")
    first, second = db.insert_session(AT), db.insert_session(AT)
    now = [100.0]

    class Store:
        calls = 0
        unavailable = True

        def append(self, *args, **kwargs):
            self.calls += 1
            if self.unavailable:
                raise OSError("disk unavailable")
            db.inputs.append(*args, **kwargs)

    store = Store()
    writer = ChunkWriter(store, first, retry_clock=lambda: now[0])
    writer.FLUSH_FRAMES = 3
    for n in (1, 2):
        writer.add(n, PAD, observation=observation("poll:old", n))
    with pytest.raises(OSError):
        writer.add(3, PAD, observation=observation("poll:old", 3))
    for n in range(4, 904):
        with pytest.raises(OSError):
            writer.add(n, PAD, session_id=second, observation=observation("poll:new", n))
    assert store.calls == 1
    assert writer.health()["pending_frames"] == 3
    assert writer.health()["rejected_frames"] == 900
    writer.retry()  # paused-game heartbeat before the deadline does no work
    assert store.calls == 1
    now[0] += 0.25
    with pytest.raises(OSError):
        writer.retry()
    assert store.calls == 2
    store.unavailable = False
    now[0] += 0.5
    writer.retry()  # recovery works without another game frame
    writer.add(1, PAD, session_id=second, observation=observation("poll:new", 904))
    writer.close()
    chunks = db.inputs.chunks_between(AT, AT)
    assert [(c.session_id, [n for n, _ in c.frames]) for c in chunks] == [(first, [1, 2, 3]), (second, [1])]
    assert [c.observations[0].source_id for c in chunks] == ["poll:old", "poll:new"]
    assert writer.health()["error"] is None
    db.close()


def test_failed_derived_write_recovers_same_service_without_republishing(tmp_path, monkeypatch):
    import sm64_events.tracking.service as module
    now = [100.0]
    monkeypatch.setattr(module, "monotonic", lambda: now[0])
    db, service, sent = make_rec(tmp_path)
    original = db.upsert_attempt
    monkeypatch.setattr(db, "upsert_attempt", lambda _a: (_ for _ in ()).throw(OSError("disk full")))
    asyncio.run(service.publish(ev("practice_reset", 1000)))
    jid = asyncio.run(service.publish(star(1350)))
    assert jid is not None  # the raw journal row DID commit
    assert service.tracking_health()["state"] == "recovering"
    assert service.tracking_health()["phase"] == "projection"
    monkeypatch.setattr(db, "upsert_attempt", original)
    assert not asyncio.run(service.recover_tracking())
    now[0] += 0.25
    assert asyncio.run(service.recover_tracking())
    assert len(db.attempts()) == 1
    assert sum(e.type == "star_collected" for e in db.events()) == 1
    assert sum(e.type == "star_collected" for e in sent) == 1
    assert sum(e.type == "attempt_completed" for e in sent) == 0
    assert sum(e.type == "attempts_invalidated" for e in sent) == 1
    assert service.tracking_health()["recoveries"] == 1
    db.close()


def test_missing_journal_interval_is_closed_by_one_gap_before_new_events(tmp_path, monkeypatch):
    import sm64_events.tracking.service as module
    now = [100.0]
    monkeypatch.setattr(module, "monotonic", lambda: now[0])
    db, service, sent = make_rec(tmp_path)
    asyncio.run(service.publish(ev("practice_reset", 1000)))
    original = db.append_event
    monkeypatch.setattr(db, "append_event", lambda *_a: (_ for _ in ()).throw(OSError("disk full")))
    assert asyncio.run(service.publish(ev("practice_reset", 1200))) is None
    assert asyncio.run(service.publish(ev("mario_acted", 1250))) is None
    monkeypatch.setattr(db, "append_event", original)
    now[0] += 0.25
    asyncio.run(service.publish(star(1350)))
    rows = db.events()
    assert [r.type for r in rows] == ["session_started", "practice_reset", "tracking_gap", "star_collected"]
    [attempt] = db.attempts()
    assert attempt.anchor_type == "none" and attempt.rta_frames is None
    assert attempt.id == rows[-1].id  # no old anchor survived the gap
    assert replay(rows, segments=service.segment_defs)[0] == [attempt]
    assert sum(e.type == "practice_reset" for e in sent) == 2  # never retried
    assert sum(e.type == "tracking_gap" for e in sent) == 1
    assert service.tracking_health()["dropped_events"] == 2
    db.close()


def test_recovery_does_not_repeat_a_completion_already_published(tmp_path, monkeypatch):
    import sm64_events.tracking.service as module
    now = [100.0]
    monkeypatch.setattr(module, "monotonic", lambda: now[0])
    db, service, sent = make_rec(tmp_path)
    definition = SegmentDef(id=77, name="span", enabled=True,
        start_triggers=[{"type": "attempt_anchor", "level": 24}],
        end_triggers=[{"type": "star_grabbed", "course": 2, "star": 2}], guards=[])
    service._segment_defs = [definition]
    service._projector = Projector(segments=[definition])
    asyncio.run(service.publish(ev("level_changed", 900, {"from": 24, "to": 24})))
    asyncio.run(service.publish(ev("practice_reset", 1000)))
    original = db.upsert_attempt
    calls = []

    def fail_second(attempt):
        calls.append(attempt.id)
        if len(calls) == 2:
            raise OSError("second derived write failed")
        original(attempt)

    monkeypatch.setattr(db, "upsert_attempt", fail_second)
    asyncio.run(service.publish(star(1350)))
    assert len(calls) == 2
    assert sum(e.type == "attempt_completed" for e in sent) == 1
    now[0] += 0.25
    assert asyncio.run(service.recover_tracking())
    assert len(db.attempts()) == 2
    assert sum(e.type == "attempt_completed" for e in sent) == 1
    assert sum(e.type == "star_collected" for e in db.events()) == 1
    db.close()


def test_recovery_failure_stays_dirty_and_is_not_retried_on_every_frame(tmp_path, monkeypatch):
    import sm64_events.tracking.service as module
    now = [100.0]
    monkeypatch.setattr(module, "monotonic", lambda: now[0])
    db, service, _ = make_rec(tmp_path)
    original = db.replace_attempts
    calls = []

    def fail(_attempts):
        calls.append(1)
        raise OSError("replace unavailable")

    monkeypatch.setattr(db, "replace_attempts", fail)
    assert not asyncio.run(service.capture_gap("emulator lost"))
    for _ in range(100):
        assert not asyncio.run(service.recover_tracking())
    assert len(calls) == 1
    assert service.tracking_health()["error"]
    assert sum(e.type == "tracking_gap" for e in db.events()) == 1
    monkeypatch.setattr(db, "replace_attempts", original)
    now[0] += 0.25
    assert asyncio.run(service.recover_tracking())
    assert sum(e.type == "tracking_gap" for e in db.events()) == 1
    db.close()


def test_gap_silently_disarms_attempts_segments_and_runs_in_live_and_replay():
    definition = SegmentDef(id=77, name="span", enabled=True, start_triggers=[{"type": "attempt_anchor", "level": 24}],
        end_triggers=[{"type": "star_grabbed", "course": 2, "star": 2}], guards=[], match_mode="loose")
    engine = SegmentEngine([definition])
    opened = jev(1, "practice_reset", 1000)
    context = MatchContext(level=24, prev_level=None, num_stars=None)
    engine.feed(opened, context)
    assert engine.armed_ids() == {77}
    engine.feed(jev(2, "tracking_gap", 0), CTX)
    assert engine.armed_ids() == set()
    assert engine.feed(journal_star(3, 1300), CTX)[0] == []

    projector = Projector(segments=[definition])
    events = [jev(0, "level_changed", 900, {"from": 24, "to": 24}), opened,
              jev(2, "tracking_gap", 0), journal_star(3, 1300),
              jev(4, "practice_reset", 1000), jev(5, "tracking_gap", 0), journal_star(6, 1300)]
    live = [a for event in events for a in projector.feed(event)]
    rebuilt, _ = replay(events, segments=[definition])
    assert live == rebuilt
    assert all(a.outcome == "success" and a.anchor_type == "none" for a in live)
    assert all(a.segment_id is None and a.rta_frames is None for a in live)

    run = RunTracker()
    run.feed(started([{"need": 1, "candidates": [STAR]}]), [], CTX)
    run.feed(Ev("game_reset", id=1), [], CTX)
    assert run.active_run_view() is not None
    assert run.feed(Ev("tracking_gap", id=2), [], CTX) == []
    assert run.active_run_view() is None and run.finished_runs() == []
    assert run.feed(Ev("star_collected", id=3), [att(course=2, star=0)], CTX) == []
    run.feed(Ev("game_reset", id=4), [], CTX)
    assert run.feed(Ev("star_collected", id=5), [att(course=2, star=0)], CTX)[0].status == "finished"


def test_replay_prepasses_cannot_match_repeated_counters_across_a_gap():
    grab = journal_star(1, 100)
    corrected = jev(3, "star_time_corrected", 100,
                    {"course_id": 2, "star_id": 2, "igt_frames": 999})
    assert time_corrections([grab, corrected])
    assert not time_corrections([grab, jev(2, "tracking_gap", 0), corrected])
    warp = jev(1, "warp_entered", 100)
    level = jev(3, "level_changed", 105, {"from": 24, "to": 6})
    assert warp_destinations([warp, level]) == {1: 6}
    assert warp_destinations([warp, jev(2, "tracking_gap", 0), level]) == {}
