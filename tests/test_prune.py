# tests/test_prune.py
"""The startup prune of unlabelled attempts (tracking/prune.py, task 0076).

Three layers: the pure rule, the replay branch that applies a journaled
prune, and the TrackerService.start() shell that decides and journals one.
"""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from sm64_events.core.events import Event
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.storage.db import Database
from sm64_events.tracking.projection import Attempt, replay
from sm64_events.tracking.prune import PRUNE_EVENT, prunable_ids, unlabelled
from sm64_events.tracking.service import BROADCAST_ONLY, TrackerService

T0 = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)


def ev(type_, frame, payload=None):
    return Event(type=type_, frame=frame, timestamp_utc=T0, payload=payload or {})


def attempt(attempt_id, *, course=None, star=None, segment=None, strat=None):
    return Attempt(id=attempt_id, session_id=1, course_id=course, star_id=star,
                   segment_id=segment, strat_tag=strat,
                   anchor_type="practice_reset", anchor_frame=1000,
                   outcome="reset", outcome_detail=None,
                   igt_frames=None, rta_frames=300,
                   started_utc="2026-06-10T12:00:00Z",
                   ended_utc="2026-06-10T12:00:10Z",
                   cleared=False, cleared_reason=None)


# -- the rule -----------------------------------------------------------------

def test_an_attempt_naming_no_star_and_no_segment_is_unlabelled():
    assert unlabelled(attempt(1))


def test_an_attempt_with_an_entity_but_no_strategy_is_unlabelled():
    assert unlabelled(attempt(1, course=2, star=2))
    assert unlabelled(attempt(2, segment=7))
    # an empty tag is the same nothing a null one is
    assert unlabelled(attempt(3, course=2, star=2, strat=""))


def test_an_attempt_with_an_entity_and_a_strategy_is_kept():
    assert not unlabelled(attempt(1, course=2, star=2, strat="Cannonless"))
    assert not unlabelled(attempt(2, segment=7, strat="Standard"))


def test_a_saved_attempt_is_protected_even_though_it_is_unlabelled():
    rows = [attempt(1, course=2, star=2), attempt(2), attempt(3, segment=7)]
    assert prunable_ids(rows, protected=set()) == [1, 2, 3]
    assert prunable_ids(rows, protected={2}) == [1, 3]


# -- the replay branch --------------------------------------------------------

class Row:
    """Minimal EventRow stand-in: replay() only reads .type and .payload for
    the compensating-event branches."""

    def __init__(self, type_, payload):
        self.type, self.payload = type_, payload


def test_replay_drops_exactly_the_ids_a_prune_event_names(tmp_path):
    db = Database(tmp_path / "t.db")
    svc = TrackerService(db, Broadcaster())
    asyncio.run(svc.start())
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(ev("practice_reset", 1400, {"igt_frames_before": 380})))
    before, _ = replay(db.events())
    assert len(before) == 1
    doomed = before[0].id

    db.append_event(svc.session_id, 99,
                    ev(PRUNE_EVENT, 0, {"attempt_ids": [doomed]}))
    after, _ = replay(db.events())
    assert after == []

    # ...and an id it does NOT name survives, so this is a list and not a rule
    db2 = Database(tmp_path / "u.db")
    svc2 = TrackerService(db2, Broadcaster())
    asyncio.run(svc2.start())
    asyncio.run(svc2.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc2.publish(ev("practice_reset", 1400, {"igt_frames_before": 380})))
    db2.append_event(svc2.session_id, 99,
                     ev(PRUNE_EVENT, 0, {"attempt_ids": [doomed + 10_000]}))
    kept, _ = replay(db2.events())
    assert len(kept) == 1


def test_an_attempt_still_open_when_the_prune_ran_survives_it(tmp_path):
    """The prune is retroactive, like a wipe — it names rows that had already
    closed. A run in flight closes afterwards and is post-prune data, or a
    restart would eat the attempt it interrupted."""
    db = Database(tmp_path / "t.db")
    svc = TrackerService(db, Broadcaster())
    asyncio.run(svc.start())
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(ev("practice_reset", 1400, {"igt_frames_before": 380})))
    first = replay(db.events())[0][0].id
    db.append_event(svc.session_id, 99,
                    ev(PRUNE_EVENT, 0, {"attempt_ids": [first]}))
    asyncio.run(svc.publish(ev("practice_reset", 2000, {"igt_frames_before": 380})))
    after, _ = replay(db.events())
    assert len(after) == 1 and after[0].id != first


# -- the startup shell --------------------------------------------------------

def unlabelled_history(tmp_path, name="t.db"):
    """A db whose previous session left two unlabelled resets behind."""
    db = Database(tmp_path / name)
    svc = TrackerService(db, Broadcaster())
    asyncio.run(svc.start())
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(ev("practice_reset", 1400, {"igt_frames_before": 380})))
    asyncio.run(svc.publish(ev("practice_reset", 1800, {"igt_frames_before": 380})))
    assert len(db.attempts()) == 2
    return db


def restart(db, saved_clip_ids=None):
    svc = TrackerService(db, Broadcaster())
    svc.saved_clip_ids = saved_clip_ids
    asyncio.run(svc.start())
    return svc


def test_startup_prunes_the_previous_sessions_unlabelled_attempts(tmp_path):
    db = unlabelled_history(tmp_path)
    restart(db)
    assert db.attempts() == []
    assert PRUNE_EVENT in [e.type for e in db.events()]


def test_the_session_in_progress_is_never_pruned(tmp_path):
    """He still remembers what he is doing right now — the whole reason the
    rule is startup-only."""
    db = unlabelled_history(tmp_path)
    svc = restart(db)
    asyncio.run(svc.publish(ev("practice_reset", 3000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(ev("practice_reset", 3400, {"igt_frames_before": 380})))
    assert len(db.attempts()) == 1        # today's, unlabelled, still there


def test_the_prune_itself_refuses_the_session_it_is_running_in(tmp_path):
    """Calling it at startup is what makes it RARE; the session filter is
    what makes it safe. Driven directly, because at startup the live session
    has no attempts yet — so the whole-app path can never exercise this, and
    a prune moved to any other call site would silently eat live rows."""
    db = unlabelled_history(tmp_path)
    svc = restart(db)
    asyncio.run(svc.publish(ev("practice_reset", 3000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(ev("practice_reset", 3400, {"igt_frames_before": 380})))
    live = {a.id for a in db.attempts()}
    assert live, "fixture must leave an unlabelled row in the LIVE session"
    assert asyncio.run(svc._prune_unlabelled_attempts()) == 0
    assert {a.id for a in db.attempts()} == live


def test_a_second_start_prunes_nothing_and_journals_nothing(tmp_path):
    db = unlabelled_history(tmp_path)
    restart(db)
    before = len(db.events())
    restart(db)
    assert db.attempts() == []
    assert len([e for e in db.events() if e.type == PRUNE_EVENT]) == 1
    assert len(db.events()) == before + 1  # the second session_started, nothing else


def test_an_attempt_with_a_saved_pb_survives(tmp_path):
    db = unlabelled_history(tmp_path)
    spared = db.attempts()[0].id
    db.insert_pb(course_id=2, star_id=2, strat_tag=None, timer_mode="igt",
                 frames=343, attempt_id=spared, saved_utc="2026-06-10T12:00:00Z")
    restart(db)
    assert [a.id for a in db.attempts()] == [spared]
    # and the pb row it protects is still there -- delete_orphaned_pbs runs on
    # the same re-projection and would have taken it with the attempt
    assert db.pb_attempt_ids() == {spared}


def test_an_attempt_with_a_saved_clip_survives(tmp_path):
    db = unlabelled_history(tmp_path)
    spared = db.attempts()[1].id
    restart(db, saved_clip_ids=lambda: {spared})
    assert [a.id for a in db.attempts()] == [spared]


def test_an_unreadable_clip_directory_prunes_nothing(tmp_path):
    """Fail closed: an unknown protected set must never authorize a delete."""
    db = unlabelled_history(tmp_path)
    kept = {a.id for a in db.attempts()}
    def boom():
        raise OSError("save_root went away")
    restart(db, saved_clip_ids=boom)
    assert kept <= {a.id for a in db.attempts()}
    assert PRUNE_EVENT not in [e.type for e in db.events()]


def test_a_labelled_attempt_survives_a_prune_that_takes_its_neighbours(tmp_path):
    """The mixed case: unlabelled resets, then a target is picked and the
    same practice becomes labelled. Only the first half goes."""
    db = Database(tmp_path / "t.db")
    svc = TrackerService(db, Broadcaster())
    asyncio.run(svc.start())
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(ev("practice_reset", 1400, {"igt_frames_before": 380})))
    asyncio.run(svc.set_target(2, 2, "Cannonless"))
    asyncio.run(svc.publish(ev("practice_reset", 1800, {"igt_frames_before": 380})))
    asyncio.run(svc.publish(ev("practice_reset", 2200, {"igt_frames_before": 380})))
    before = db.attempts()
    labelled = {a.id for a in before if a.strat_tag == "Cannonless"}
    bare = {a.id for a in before if not a.strat_tag}
    assert labelled and bare, "fixture needs both kinds"
    restart(db)
    surviving = {a.id for a in db.attempts()}
    assert labelled <= surviving and not bare & surviving
    assert all(a.strat_tag for a in db.attempts())


# -- derived journal rows: never written, and swept if already there ----------

def test_derived_bookkeeping_reaches_the_browser_but_not_the_journal(tmp_path):
    db = Database(tmp_path / "t.db")
    svc = TrackerService(db, Broadcaster())
    asyncio.run(svc.start())
    asyncio.run(svc.set_target(2, 2, "Cannonless"))
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(ev("star_collected", 1350,
                               {"course_id": 2, "star_id": 2, "igt_frames": 343,
                                "igt_timed_at": "xcam"})))
    assert db.attempts(), "fixture must produce an attempt to report on"
    assert not [e for e in db.events() if e.type in BROADCAST_ONLY]


def test_startup_sweeps_derived_rows_written_before_the_rule_existed(tmp_path):
    """An existing install's journal is full of them; they go on first boot
    and the file shrinks, which is the whole point of the VACUUM."""
    db = Database(tmp_path / "t.db")
    svc = TrackerService(db, Broadcaster())
    asyncio.run(svc.start())
    # Fat payloads on purpose: the reclaim is measured in PAGES, and a
    # handful of tiny rows fit in the one page an empty db already has, so a
    # small fixture would pass with the VACUUM deleted.
    for i, type_ in enumerate(sorted(BROADCAST_ONLY) * 200):
        db.append_event(svc.session_id, 900 + i,
                        ev(type_, 0, {"n": i, "pad": "x" * 2000}))
    before = (tmp_path / "t.db").stat().st_size

    restart(db)          # through the real startup path, not the db method
    assert not [e for e in db.events() if e.type in BROADCAST_ONLY]
    assert (tmp_path / "t.db").stat().st_size < before, "VACUUM must reclaim"
    # ...and it reclaimed to the OS, not just into SQLite's own freelist
    free = db._conn.execute("PRAGMA freelist_count").fetchone()[0]
    assert free == 0


def test_the_sweep_leaves_every_other_event_type_alone(tmp_path):
    db = unlabelled_history(tmp_path)
    kept = [(e.id, e.type) for e in db.events()]
    assert db.purge_event_types(BROADCAST_ONLY) == 0
    assert [(e.id, e.type) for e in db.events()] == kept
