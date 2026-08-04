# tests/test_tracker_service.py
import asyncio
import json
from datetime import datetime, timezone

import pytest

from sm64_events.core.events import Event
from sm64_events.ranks.standards import RankStandards
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.storage.db import Database
from sm64_events.tracking.service import TrackerService

T0 = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)


def ev(type_, frame, payload=None):
    return Event(type=type_, frame=frame, timestamp_utc=T0, payload=payload or {})


def star(frame, course=2, star_id=2, igt=343):
    # `igt_timed_at` is not decoration here: a star_collected payload WITHOUT
    # it replays as the grab quantity (projection.py — its absence is exactly
    # what a pre-2026-08-01 row means), and a grab-timed star cannot be saved
    # as a PB. This helper stands for an ordinary modern grab, so it says so.
    return ev("star_collected", frame,
              {"course_id": course, "star_id": star_id, "igt_frames": igt,
               "igt_timed_at": "xcam"})


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
    """The derived pair reaches the BROADCAST, never the journal: both
    restate something already stored, so journaling them was pure noise in a
    file whose value is being readable (service.py::BROADCAST_ONLY)."""
    db, svc, sent = make_rec(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1350)))
    attempts = db.attempts()
    assert len(attempts) == 1 and attempts[0].outcome == "success"
    broadcast = [e.type for e in sent]
    assert "attempt_completed" in broadcast and "target_changed" in broadcast
    journaled = [e.type for e in db.events()]
    assert "attempt_completed" not in journaled
    assert "target_changed" not in journaled


def test_attach_db_upgrades_broadcast_only_to_full_tracking(tmp_path):
    """A server that lost the instance-lock race starts with db=None
    (broadcast-only). attach_db is the self-heal: once the lock frees, the
    service must open a session, journal, and project exactly as if the db
    had been there from the start — and broadcast session_started so the
    UI refetches the view (live incident 2026-07-23: post-update page
    stuck on 'loading…')."""
    bc = RecordingBroadcaster()
    svc = TrackerService(None, bc)
    asyncio.run(svc.start())
    asyncio.run(svc.publish(star(900)))       # degraded: broadcast, no journal
    assert svc.session_id is None

    db = Database(tmp_path / "t.db")
    asyncio.run(svc.attach_db(db))

    assert svc.db is db
    assert svc.session_id == 1
    assert [e.type for e in db.events()] == ["session_started"]
    assert any(e.type == "session_started" for e in bc.sent)
    # Full pipeline live post-attach: journal + projection + persistence.
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1350)))
    attempts = db.attempts()
    assert len(attempts) == 1 and attempts[0].outcome == "success"


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
    # Labelled so the restart's prune leaves it alone -- what is under test
    # here is the WIPE surviving a restart, not the prune (tracking/prune.py).
    asyncio.run(svc.set_target(2, 2, "Cannonless"))
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
    """History is rebuilt from the journal; the live FOCUS is not.

    This asserted `target == ("star", 2, 2)` until 2026-08-01, i.e. it pinned
    the bug he reported — reopening the app with the star he practiced last
    time still selected. What a restart must rebuild is what he EARNED (the
    attempt, its strategy); what it must not rebuild is where he was pointed."""
    db, svc = make(tmp_path)
    # The strategy is load-bearing, not decoration: the startup prune deletes
    # an attempt that never said what it was practice FOR (tracking/prune.py),
    # so "survives a restart" now means "was labelled".
    asyncio.run(svc.set_strat(2, 2, "Cannonless"))   # the star actually grabbed
    asyncio.run(svc.set_target(8, 2))
    asyncio.run(svc.publish(star(900)))
    db2 = Database(tmp_path / "t.db")
    svc2 = TrackerService(db2, Broadcaster())
    asyncio.run(svc2.start())
    assert svc2.session_id == 2
    assert svc2.target is None
    assert [a.outcome for a in db2.attempts()] == ["success"]


def test_degraded_mode_without_db_still_broadcasts(tmp_path):
    svc = TrackerService(None, Broadcaster())
    asyncio.run(svc.start())
    asyncio.run(svc.publish(star(900)))   # must not raise
    assert svc.session_id is None


def test_reproject_emits_target_changed_when_target_reverts(tmp_path):
    db, svc, sent = make_rec(tmp_path)
    asyncio.run(svc.set_target(8, 2))
    asyncio.run(svc.publish(star(900)))            # target moves to (2,2)
    grab_id = db.attempts()[0].id
    asyncio.run(svc.clear_attempt(grab_id, reason="accidental"))
    assert svc.target == ("star", 8, 2)
    tc = [e for e in sent if e.type == "target_changed"]
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


def _create_hundred_coin_segment(svc, course_id, other_star=0, enabled=True):
    """A minimal stand-in for one of the seeded HUNDRED_COIN_EXITS rows
    (tools/corpus_movements.py) -- same shape (a single star_grabbed(course,
    6) start clause) without needing the whole bundled corpus reconciled."""
    return asyncio.run(svc.create_segment({
        "name": f"100c {course_id}",
        "start_triggers": [{"type": "star_grabbed", "course": course_id, "star": 6}],
        "end_triggers": [{"type": "star_grabbed", "course": course_id,
                          "star": other_star}],
        "enabled": enabled}))


def test_100_coin_star_pick_commits_as_a_plain_star_target(tmp_path):
    """spec 2026-07-28-multi-step-segments, 'the 100-coin star IS the
    segment': star_id 6 no longer redirects to a segment target on pick --
    it commits exactly like any other star. The retired redirect
    (`_hundred_coin_redirect`) existed only so the practice card could show
    the family's real attempts/strat/rank, which lived on the segment; those
    now attribute directly to this star (tracking/projection.py's
    seg_closed reattribution, segments.hundred_coin_entity), so a plain
    star target already shows the same thing with no indirection."""
    db, svc = make(tmp_path)
    asyncio.run(svc.request_target("star", course_id=2, star_id=6))
    assert svc.target == ("star", 2, 6)


def test_100_coin_star_pick_stays_plain_even_with_a_matching_segment(tmp_path):
    """A HUNDRED_COIN_EXIT-shaped def existing (enabled or not, either the
    old grab-starts shape or the reshaped waypoint one) must not change
    target-PICKING at all any more -- only attempt ATTRIBUTION reads the
    def's shape now, and that happens in projection.py, not here."""
    db, svc = make(tmp_path)
    _create_hundred_coin_segment(svc, course_id=2)
    asyncio.run(svc.request_target("star", course_id=2, star_id=6))
    assert svc.target == ("star", 2, 6)


def test_numbered_star_picks_are_plain_stars_too(tmp_path):
    """Stars 0-5 are untouched by this family end to end, same as before."""
    db, svc = make(tmp_path)
    _create_hundred_coin_segment(svc, course_id=2)
    asyncio.run(svc.request_target("star", course_id=2, star_id=0))
    assert svc.target == ("star", 2, 0)


def test_100_coin_star_pick_strategy_lands_on_the_star(tmp_path):
    """A strategy passed alongside the pick lands on the star's OWN memory
    (strat_by_star) -- there is no segment redirect left to carry it to."""
    db, svc = make(tmp_path)
    asyncio.run(svc.request_target("star", course_id=2, star_id=6,
                                   strat_tag="Coin Route A"))
    assert svc.target == ("star", 2, 6)
    assert svc.strat_by_star[(2, 6)] == "Coin Route A"


def test_death_event_flows_to_death_attempt(tmp_path):
    db, svc, sent = make_rec(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000,
                               {"igt_frames_before": 0, "mario_acted": True})))
    asyncio.run(svc.publish(ev("death", 1300,
                               {"cause": "drowning", "igt_frames": 290, "level": 9})))
    [a] = db.attempts()
    assert a.outcome == "death" and a.outcome_detail == "drowning"
    assert "attempt_completed" in [e.type for e in sent]


def test_pipeline_survives_attempt_persist_failure(tmp_path):
    db, svc = make(tmp_path)
    # Labelled so the restart's prune leaves it alone; the self-heal is what
    # is under test (tracking/prune.py).
    asyncio.run(svc.set_target(2, 2, "Cannonless"))
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
    db, svc, sent = make_rec(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(ev("rollout", 1100,
                               {"dustless": True, "frames_late": 0, "level": 24})))
    asyncio.run(svc.publish(ev("rollout", 1200,
                               {"dustless": False, "frames_late": 2, "level": 24})))
    asyncio.run(svc.publish(star(1350)))
    a = db.attempts()[0]
    assert a.rollouts_total == 2 and a.rollouts_dustless == 1
    completed = [e for e in sent if e.type == "attempt_completed"]
    assert completed[-1].payload["rollouts_total"] == 2
    assert completed[-1].payload["rollouts_dustless"] == 1


def test_attempt_completed_carries_jump_counts(tmp_path):
    db, svc, sent = make_rec(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(ev("jump", 1100,
                               {"dustless": True, "frames_late": 0,
                                "landing_frames": 1, "kind": "double",
                                "level": 24})))
    asyncio.run(svc.publish(star(1350)))
    a = db.attempts()[0]
    assert a.jumps_total == 1 and a.jumps_dustless == 1
    completed = [e for e in sent if e.type == "attempt_completed"]
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


def test_segment_category_persists(tmp_path):
    db, svc, sent = make_rec(tmp_path)
    sid = asyncio.run(svc.create_segment({
        "name": "s", "start_triggers": [{"type": "spawned", "level": 16}],
        "end_triggers": [{"type": "level_enter", "to": 6}],
        "category": "Tricks"}))
    d = next(d for d in db.segment_defs() if d["id"] == sid)
    assert d["category"] == "Tricks"


def test_segment_waypoints_persist_via_create(tmp_path):
    db, svc, sent = make_rec(tmp_path)
    sid = asyncio.run(svc.create_segment({
        "name": "w", "start_triggers": [{"type": "level_exit", "from": 10}],
        "end_triggers": [{"type": "level_enter", "to": 7}],
        "waypoints": [[{"type": "level_enter", "to": 10}]]}))
    d = next(d for d in db.segment_defs() if d["id"] == sid)
    assert d["waypoints"] == [[{"type": "level_enter", "to": 10}]]


def test_update_segment_category_persists(tmp_path):
    db, svc, sent = make_rec(tmp_path)
    lblj = seed_id(db, "LBLJ")
    asyncio.run(svc.update_segment(lblj, {"category": "Main Categories"}))
    d = next(d for d in db.segment_defs() if d["id"] == lblj)
    assert d["category"] == "Main Categories"


def test_update_segment_marks_seed_dirty(tmp_path):
    """Editing a seeded segment flips seed_dirty so a future reconcile
    (tracking/defaults.py) never overwrites the user's change."""
    db, svc = make(tmp_path)
    lblj = seed_id(db, "LBLJ")
    assert next(d for d in db.segment_defs() if d["id"] == lblj)["seed_dirty"] == 0
    asyncio.run(svc.update_segment(lblj, {"name": "My LBLJ"}))
    assert next(d for d in db.segment_defs() if d["id"] == lblj)["seed_dirty"] == 1


def test_update_segment_does_not_dirty_a_user_created_segment(tmp_path):
    """No seed_key => nothing to protect; set_seed_dirty must not fire."""
    db, svc = make(tmp_path)
    sid = asyncio.run(svc.create_segment({
        "name": "U", "start_triggers": [{"type": "spawned", "level": 16}],
        "end_triggers": [{"type": "level_enter", "to": 6}]}))
    asyncio.run(svc.update_segment(sid, {"name": "U2"}))
    assert next(d for d in db.segment_defs() if d["id"] == sid)["seed_dirty"] == 0


def test_reset_segment_restores_seed_and_clears_dirty(tmp_path):
    db, svc = make(tmp_path)
    lblj = seed_id(db, "LBLJ")
    asyncio.run(svc.update_segment(lblj, {"name": "My LBLJ", "enabled": False}))
    assert next(d for d in db.segment_defs() if d["id"] == lblj)["seed_dirty"] == 1
    asyncio.run(svc.reset_segment(lblj))
    row = next(d for d in db.segment_defs() if d["id"] == lblj)
    assert row["name"] == "LBLJ" and row["seed_dirty"] == 0 and row["enabled"] is True
    assert row["start_triggers"] == [
        {"type": "level_enter", "to": 6, "from": 16},
        {"type": "attempt_anchor", "level": 6, "area": 1}]
    assert row["end_triggers"] == [{"type": "level_enter", "to": 17}]


def test_reset_user_created_segment_raises(tmp_path):
    db, svc = make(tmp_path)
    sid = asyncio.run(svc.create_segment({
        "name": "U", "start_triggers": [{"type": "spawned", "level": 16}],
        "end_triggers": [{"type": "level_enter", "to": 6}]}))
    with pytest.raises(LookupError):
        asyncio.run(svc.reset_segment(sid))


def test_reset_segment_unknown_id_raises(tmp_path):
    db, svc = make(tmp_path)
    with pytest.raises(LookupError):
        asyncio.run(svc.reset_segment(999))


def test_delete_segment_removes_def_and_reprojects(tmp_path):
    db, svc, sent = make_rec(tmp_path)
    lblj = seed_id(db, "LBLJ")
    asyncio.run(svc.delete_segment(lblj))
    assert all(d["id"] != lblj for d in db.segment_defs())
    assert any(e.type == "attempts_invalidated" for e in sent)
    with pytest.raises(LookupError):
        asyncio.run(svc.delete_segment(lblj))


# -- split_segment / merge_segments (Task 18, spec 2026-07-28-multi-step-
# segments) ------------------------------------------------------------------
# tracking/segments.py::split_definition/merge_definitions are pure and
# already tested directly (tests/test_segments.py, Task 17); these cover the
# SERVICE plumbing only -- looking an existing def up by id, inserting the
# result(s) as fresh rows, leaving the original(s) completely untouched, and
# mapping LookupError/ValueError the same way every other segment command
# does. Fixtures mirror test_segments.py's own (WF -> SSL / Basement,
# DDD -> BitFS) so a failure here is known NOT to be a pure-op regression.

def test_split_segment_creates_two_new_rows_and_keeps_the_original(tmp_path):
    db, svc, sent = make_rec(tmp_path)
    sid = asyncio.run(svc.create_segment({
        "name": "WF -> SSL", "match_mode": "loose",
        "start_triggers": [{"type": "level_exit", "from": 24}],
        "end_triggers": [{"type": "level_enter", "to": 8}],
        "waypoints": [[{"type": "area_enter", "level": 6, "area": 3}]]}))
    before = next(d for d in db.segment_defs() if d["id"] == sid)
    sent.clear()
    first_id, second_id = asyncio.run(svc.split_segment(
        sid, [{"type": "area_enter", "level": 6, "area": 3}],
        ("WF -> Basement", "Basement -> SSL")))
    rows = {d["id"]: d for d in db.segment_defs()}
    assert rows[sid] == before                        # original UNTOUCHED
    assert rows[first_id]["name"] == "WF -> Basement"
    assert rows[first_id]["start_triggers"] == before["start_triggers"]
    assert rows[first_id]["end_triggers"] == [
        {"type": "area_enter", "level": 6, "area": 3}]
    assert rows[first_id]["seed_key"] is None
    assert rows[second_id]["name"] == "Basement -> SSL"
    assert rows[second_id]["start_triggers"] == [
        {"type": "area_enter", "level": 6, "area": 3}]
    assert rows[second_id]["end_triggers"] == before["end_triggers"]
    assert rows[second_id]["seed_key"] is None
    assert any(e.type == "attempts_invalidated" for e in sent)


def test_a_refused_split_leaves_no_half_behind(tmp_path):
    """A 409 must write nothing. It wrote one row until 2026-07-29.

    `split_segment` did two sequential `_insert_definition` calls, each
    validating its own half at insert time, and `db.insert_segment_def`
    commits unconditionally. `split_definition` vets both halves only for
    UNFIREABILITY -- it says nothing about `name`, and `SegmentSplitBody`'s
    names are plain `str` with no `min_length`. So a blank second name got
    the first half committed, then raised, then surfaced as a clean 409 with
    an orphaned row left in the db that nothing would ever clean up.

    Both directions, because the bug is asymmetric by construction: a blank
    FIRST name always failed before anything was written and so was never
    the case that hurt. Only the second one could orphan.
    """
    db, svc, sent = make_rec(tmp_path)
    sid = asyncio.run(svc.create_segment({
        "name": "WF -> SSL", "match_mode": "loose",
        "start_triggers": [{"type": "level_exit", "from": 24}],
        "end_triggers": [{"type": "level_enter", "to": 8}]}))
    mid = [{"type": "area_enter", "level": 6, "area": 3}]
    before = [d["id"] for d in db.segment_defs()]

    for names in (("A", "   "), ("   ", "B"), ("A", "")):
        with pytest.raises(ValueError, match="name is required"):
            asyncio.run(svc.split_segment(sid, mid, names))
        assert [d["id"] for d in db.segment_defs()] == before, (
            f"names={names!r} left a row behind")

    # and the original is still intact and splittable with real names
    first_id, second_id = asyncio.run(
        svc.split_segment(sid, mid, ("WF -> Basement", "Basement -> SSL")))
    ids = [d["id"] for d in db.segment_defs()]
    assert sorted(ids) == sorted(before + [first_id, second_id])


def test_a_committed_split_row_is_independent_of_the_callers_objects(tmp_path):
    """Once `split_segment` returns, nothing the caller does to its own live
    objects can reach the persisted rows.

    **What this does NOT prove, despite an earlier name and docstring that
    claimed it** (Task 18 review addendum, 2026-07-29): it says nothing about
    whether `split_definition` aliases the caller's clause dicts or
    deep-copies them. It passes identically either way -- verified by
    mutation: replacing `list(d.start_triggers)` with
    `[dict(c) for c in d.start_triggers]` throughout the pure op, i.e.
    removing the aliasing outright, leaves this green. The assertion reads
    `db.segment_defs()`, a fresh SELECT + json.loads of a SQLite TEXT column
    that `insert_segment_def` wrote synchronously before this test line ever
    ran, so it sits downstream of a boundary EVERY implementation has already
    crossed. It was also carrying a "PREMISE" assertion using `==`, which
    proves value equality and not the shared identity it claimed.

    The aliasing question is settled STATICALLY and cannot be black-box
    tested: `_insert_definition`'s docstring plus the review's own sweep
    (zero in-place clause mutations anywhere in `src/`, and the JS editor
    replaces rather than mutates). An identity assertion would be worse than
    nothing here -- it would FAIL the day someone deep-copies, punishing the
    safest implementation.

    Kept because the weaker property is real and cheap to hold: it fails if
    the write path ever defers serialization (an async or batched insert
    holding the dict and dumping later), which is the one realistic way a
    committed row could start tracking a caller's later edits."""
    db, svc, sent = make_rec(tmp_path)
    sid = asyncio.run(svc.create_segment({
        "name": "WF -> SSL", "match_mode": "loose",
        "start_triggers": [{"type": "level_exit", "from": 24}],
        "end_triggers": [{"type": "level_enter", "to": 8}]}))
    current = next(d for d in svc._segment_defs if d.id == sid)
    first_id, second_id = asyncio.run(svc.split_segment(
        sid, [{"type": "area_enter", "level": 6, "area": 3}],
        ("WF -> Basement", "Basement -> SSL")))
    # Mutate the caller's own resident clause dict in place, after
    # split_segment has returned and both rows are already committed.
    current.start_triggers[0]["from"] = 999999
    # The already-committed rows (both the split half AND the untouched
    # original) must show the ORIGINAL value -- proving the mutation above
    # arrived too late to reach either one.
    rows_after = {d["id"]: d for d in db.segment_defs()}
    assert rows_after[first_id]["start_triggers"] == [
        {"type": "level_exit", "from": 24}]
    assert rows_after[sid]["start_triggers"] == [
        {"type": "level_exit", "from": 24}]


def test_a_committed_merge_row_is_independent_of_the_callers_objects(tmp_path):
    """Mirror of the split-side test above, with the same honest limits --
    read its docstring before trusting this one's name. It proves the merged
    row is durable and independent once `merge_segments` returns; it does not
    prove anything about whether `merge_definitions` aliases or copies."""
    db, svc, sent = make_rec(tmp_path)
    first_id = asyncio.run(svc.create_segment({
        "name": "WF -> Basement", "match_mode": "loose",
        "start_triggers": [{"type": "level_exit", "from": 24}],
        "end_triggers": [{"type": "area_enter", "level": 6, "area": 3}]}))
    second_id = asyncio.run(svc.create_segment({
        "name": "Basement -> SSL", "match_mode": "loose",
        "start_triggers": [{"type": "area_enter", "level": 6, "area": 3}],
        "end_triggers": [{"type": "level_enter", "to": 8}]}))
    resident_first = next(d for d in svc._segment_defs if d.id == first_id)
    new_id = asyncio.run(svc.merge_segments(first_id, second_id, "WF -> SSL"))
    resident_first.start_triggers[0]["from"] = 999999
    rows_after = {d["id"]: d for d in db.segment_defs()}
    assert rows_after[new_id]["start_triggers"] == [
        {"type": "level_exit", "from": 24}]
    assert rows_after[first_id]["start_triggers"] == [
        {"type": "level_exit", "from": 24}]


def test_split_segment_writes_default_strat_to_both_new_rows(tmp_path):
    """split_definition's own docstring says default_strat is inherited onto
    both halves; this pins that the SERVICE actually WRITES it through
    _insert_definition rather than silently dropping it the way routing
    through create_segment's own dict-based path would (see
    _insert_definition's docstring) -- default_strat is corpus-only and
    create_segment's dict never carries it, which is exactly why a naive
    reuse of that path would have been a silent regression here."""
    db, svc, sent = make_rec(tmp_path)
    sid = asyncio.run(svc.create_segment({
        "name": "WF -> SSL", "match_mode": "loose",
        "start_triggers": [{"type": "level_exit", "from": 24}],
        "end_triggers": [{"type": "level_enter", "to": 8}]}))
    db.update_segment_def(sid, default_strat="Standard")  # corpus-only field;
    # written directly like reconcile_defaults does, bypassing the API
    svc._segment_defs = svc._load_segment_defs()   # pick up the direct write
    first_id, second_id = asyncio.run(svc.split_segment(
        sid, [{"type": "area_enter", "level": 6, "area": 3}],
        ("WF -> Basement", "Basement -> SSL")))
    rows = {d["id"]: d for d in db.segment_defs()}
    assert rows[first_id]["default_strat"] == "Standard"
    assert rows[second_id]["default_strat"] == "Standard"


def test_split_segment_unknown_id_raises(tmp_path):
    db, svc = make(tmp_path)
    with pytest.raises(LookupError):
        asyncio.run(svc.split_segment(
            999, [{"type": "level_enter", "to": 6}], ("a", "b")))


def test_split_segment_propagates_the_pure_ops_unfireable_refusal(tmp_path):
    # Same collision test_segments.py's pure-op test uses: exiting Hazy Maze
    # Cave (level 7) lands directly in the castle basement in ONE
    # level_changed, so arming there and closing on a plain level_enter(to=6)
    # mid-point would arm and close on the same event.
    db, svc, sent = make_rec(tmp_path)
    sid = asyncio.run(svc.create_segment({
        "name": "x", "match_mode": "loose",
        "start_triggers": [{"type": "level_exit", "from": 7}],
        "end_triggers": [{"type": "level_enter", "to": 8}]}))
    before = len(db.segment_defs())
    with pytest.raises(ValueError, match="unfireable"):
        asyncio.run(svc.split_segment(
            sid, [{"type": "level_enter", "to": 6}],
            ("first half", "second half")))
    assert len(db.segment_defs()) == before   # nothing inserted on refusal


def test_merge_segments_creates_one_new_row_and_keeps_both_inputs(tmp_path):
    db, svc, sent = make_rec(tmp_path)
    first_id = asyncio.run(svc.create_segment({
        "name": "WF -> Basement", "match_mode": "loose",
        "start_triggers": [{"type": "level_exit", "from": 24}],
        "end_triggers": [{"type": "area_enter", "level": 6, "area": 3}]}))
    second_id = asyncio.run(svc.create_segment({
        "name": "Basement -> SSL", "match_mode": "loose",
        "start_triggers": [{"type": "area_enter", "level": 6, "area": 3}],
        "end_triggers": [{"type": "level_enter", "to": 8}]}))
    before_first = next(d for d in db.segment_defs() if d["id"] == first_id)
    before_second = next(d for d in db.segment_defs() if d["id"] == second_id)
    sent.clear()
    new_id = asyncio.run(svc.merge_segments(first_id, second_id, "WF -> SSL"))
    rows = {d["id"]: d for d in db.segment_defs()}
    assert rows[first_id] == before_first     # both inputs UNTOUCHED
    assert rows[second_id] == before_second
    merged = rows[new_id]
    assert merged["name"] == "WF -> SSL"
    assert merged["start_triggers"] == before_first["start_triggers"]
    assert merged["end_triggers"] == before_second["end_triggers"]
    assert merged["waypoints"] == [before_second["start_triggers"]]
    assert merged["seed_key"] is None
    assert any(e.type == "attempts_invalidated" for e in sent)


def test_merge_segments_unknown_id_raises(tmp_path):
    db, svc = make(tmp_path)
    sid = asyncio.run(svc.create_segment({
        "name": "a", "start_triggers": [{"type": "level_exit", "from": 24}],
        "end_triggers": [{"type": "area_enter", "level": 6, "area": 3}]}))
    with pytest.raises(LookupError):
        asyncio.run(svc.merge_segments(sid, 999, "nope"))
    with pytest.raises(LookupError):
        asyncio.run(svc.merge_segments(999, sid, "nope"))


def test_merge_segments_propagates_the_pure_ops_do_not_meet_refusal(tmp_path):
    db, svc = make(tmp_path)
    first_id = asyncio.run(svc.create_segment({
        "name": "WF -> Basement", "match_mode": "loose",
        "start_triggers": [{"type": "level_exit", "from": 24}],
        "end_triggers": [{"type": "area_enter", "level": 6, "area": 3}]}))
    second_id = asyncio.run(svc.create_segment({
        "name": "DDD -> BitFS", "match_mode": "loose",
        "start_triggers": [{"type": "area_enter", "level": 26}],
        "end_triggers": [{"type": "level_enter", "to": 19}]}))
    before = len(db.segment_defs())
    with pytest.raises(ValueError, match="do not meet"):
        asyncio.run(svc.merge_segments(first_id, second_id, "nope"))
    assert len(db.segment_defs()) == before


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


def test_settle_frame_broadcasts_a_cancel_no_event_would_have_delivered(tmp_path):
    """The wire, not the rule (live report 2026-08-02). The engine decided this
    cancel at the move; until `settle_frame` existed the notice waited for the
    next journaled event, so a player standing still inside a course watched a
    dead movement claim ACTIVE SEGMENT for 27.7 s. Broadcast-only, like every
    other arm/disarm notice — the projector re-derives them on replay."""
    db, svc, sent = make_rec(tmp_path)
    # The shape of the shipped seg:wf->ssl row (this db carries only the legacy
    # ten; the 56 movements arrive via reconcile at startup). LOOSE on purpose:
    # a strict def is disarmed by the foreign level change itself, so it could
    # never show whether the topological verdict got delivered.
    wf_ssl = asyncio.run(svc.create_segment({
        "name": "WF → SSL", "match_mode": "loose", "guards": [],
        "start_triggers": [{"type": "level_exit", "from": 24}],
        "end_triggers": [{"type": "level_enter", "to": 8}]}))
    asyncio.run(svc.publish(ev("level_changed", 1000, {"from": 24, "to": 6})))
    asyncio.run(svc.publish(ev("area_changed", 1000,
                               {"level": 6, "from": 1, "to": 1})))
    asyncio.run(svc.publish(ev("mario_acted", 1005)))
    assert svc.armed_segment_ids == {wf_ssl}
    # Into the Bowser 1 arena, which no walk from the lobby reaches. No further
    # event: the clock alone has to deliver the verdict.
    asyncio.run(svc.publish(ev("level_changed", 2000, {"from": 6, "to": 30})))
    asyncio.run(svc.publish(ev("area_changed", 2000,
                               {"level": 30, "from": 1, "to": 1})))
    assert wf_ssl in svc.armed_segment_ids   # the arena's own fight arms too
    asyncio.run(svc.settle_frame(2001))
    assert wf_ssl not in svc.armed_segment_ids
    gone = [e for e in sent if e.type == "segment_disarmed"
            and e.payload["segment_id"] == wf_ssl]
    assert gone and gone[-1].payload["name"] == "WF → SSL"
    assert "segment_disarmed" not in [e.type for e in db.events()]


def test_star_attempt_completed_carries_kind_star(tmp_path):
    db, svc, sent = make_rec(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1350)))
    p = [e for e in sent if e.type == "attempt_completed"][-1].payload
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
                                "mode": "stars"})))
    # broadcast to clients...
    assert "stage_changed" in [e.type for e in sent]
    # ...but NEVER journaled (recomputable; no historical-query value)
    assert "stage_changed" not in [e.type for e in db.events()]
    # ...and cached for the session view's initial load
    assert svc.current_stage == {"course_id": 8, "level": 8, "area": 1,
                                 "mode": "stars"}


def test_current_stage_defaults_to_no_mode(tmp_path):
    db, svc = make(tmp_path)
    assert svc.current_stage == {"course_id": None, "level": None,
                                 "area": None, "mode": None}


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


def test_route_category_persists(tmp_path):
    db, svc = make(tmp_path)
    rid = asyncio.run(svc.create_route({"name": "r", "steps": [],
                                        "category": "Main Categories"}))
    d = next(r for r in db.routes() if r["id"] == rid)
    assert d["category"] == "Main Categories"


def test_update_route_category_persists(tmp_path):
    db, svc = make(tmp_path)
    rid = asyncio.run(svc.create_route({"name": "r", "steps": []}))
    asyncio.run(svc.update_route(rid, {"category": "Bowser"}))
    d = next(r for r in db.routes() if r["id"] == rid)
    assert d["category"] == "Bowser"


def test_update_route_does_not_dirty_a_user_created_route(tmp_path):
    """No seed_key => nothing to protect; set_seed_dirty must not fire."""
    db, svc = make(tmp_path)
    rid = asyncio.run(svc.create_route({"name": "r", "steps": []}))
    asyncio.run(svc.update_route(rid, {"name": "r2"}))
    assert next(r for r in db.routes() if r["id"] == rid)["seed_dirty"] == 0


def test_update_route_marks_seed_dirty(tmp_path):
    """A route carrying a seed_key (as a bundled route would) flips
    seed_dirty on edit, mirroring update_segment."""
    db, svc = make(tmp_path)
    rid = db.insert_route("Seeded Route", [], "2026-07-01T00:00:00Z",
                          seed_key="route:test")
    asyncio.run(svc.update_route(rid, {"name": "Renamed"}))
    assert next(r for r in db.routes() if r["id"] == rid)["seed_dirty"] == 1


def test_reset_route_restores_seed_and_clears_dirty(tmp_path, monkeypatch):
    """End-to-end reset_route: seed_key candidates resolve to the CURRENT
    segment_defs table (defaults.py's resolve_steps), exactly as reconcile
    does. The shipped defaults.seed.json ships routes: [] (no seeded route
    yet), so the seed corpus is injected here rather than depending on that
    file's contents."""
    db, svc = make(tmp_path)
    lblj = seed_id(db, "LBLJ")
    rid = db.insert_route("Custom Name", [
        {"need": 1, "candidates": [{"type": "segment", "segment_id": lblj}]}],
        "2026-07-01T00:00:00Z", seed_key="route:test")
    asyncio.run(svc.update_route(rid, {"name": "Custom Name"}))  # marks dirty
    assert next(r for r in db.routes() if r["id"] == rid)["seed_dirty"] == 1

    seed = {"segments": [], "routes": [{
        "seed_key": "route:test", "name": "Seed Name",
        "steps": [{"need": 1,
                   "candidates": [{"type": "segment", "seed_key": "seg:lblj"}]}],
        "start_condition": {"type": "reset_game"}, "category": "Main"}]}
    monkeypatch.setattr(svc, "_defaults_seed", lambda: seed)
    asyncio.run(svc.reset_route(rid))
    row = next(r for r in db.routes() if r["id"] == rid)
    assert row["name"] == "Seed Name" and row["seed_dirty"] == 0
    assert row["category"] == "Main"
    assert row["steps"] == [
        {"need": 1, "candidates": [{"type": "segment", "segment_id": lblj}]}]


def test_reset_user_created_route_raises(tmp_path):
    db, svc = make(tmp_path)
    rid = asyncio.run(svc.create_route({"name": "r", "steps": []}))
    with pytest.raises(LookupError):
        asyncio.run(svc.reset_route(rid))


def test_reset_route_unknown_id_raises(tmp_path):
    db, svc = make(tmp_path)
    with pytest.raises(LookupError):
        asyncio.run(svc.reset_route(999))


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


def test_import_creates_missing_segment_carries_waypoints(tmp_path):
    # regression: resolve_import's embedded def carries waypoints (Task 10),
    # but the create-path here must actually forward it to insert_segment_def
    # or a fresh-install import silently strips them.
    db, svc = make(tmp_path)
    payload = {"kind": "sm64-route", "version": 1, "name": "Imp", "steps": [
        {"need": 1, "candidates": [{"type": "segment", "segment": {
            "name": "Waypointed Seg", "start_triggers": [{"type": "spawned"}],
            "end_triggers": [{"type": "level_enter", "to": 6}],
            "waypoints": [[{"type": "level_enter", "to": 10}]],
            "guards": []}}]}]}
    asyncio.run(svc.import_route(payload))
    new = next(d for d in db.segment_defs() if d["name"] == "Waypointed Seg")
    assert new["waypoints"] == [[{"type": "level_enter", "to": 10}]]


def test_export_unknown_route_raises(tmp_path):
    db, svc = make(tmp_path)
    with pytest.raises(LookupError):
        svc.export_route(999)


def test_import_rejects_bad_envelope(tmp_path):
    db, svc = make(tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(svc.import_route({"kind": "nope", "version": 1, "name": "x",
                                      "steps": [{"need": 1, "candidates": []}]}))


# -- select_route (Default Routes — foundation, Task 5) ------------------------

def test_select_route_journals_member_segment_ids(tmp_path):
    db, svc = make(tmp_path)
    lblj = seed_id(db, "LBLJ")
    rid = asyncio.run(svc.create_route({"name": "Sel", "steps": [
        {"need": 1, "candidates": [{"type": "segment", "segment_id": lblj}]},
        {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]}))
    asyncio.run(svc.select_route(rid))
    ev = db.events()[-1]
    assert ev.type == "route_selected"
    assert ev.payload == {"route_id": rid, "segment_ids": [lblj]}


def test_select_route_dedupes_repeated_segment_across_steps(tmp_path):
    db, svc = make(tmp_path)
    lblj = seed_id(db, "LBLJ")
    rid = asyncio.run(svc.create_route({"name": "Dup", "steps": [
        {"need": 1, "candidates": [{"type": "segment", "segment_id": lblj}]},
        {"need": 1, "candidates": [{"type": "segment", "segment_id": lblj}]}]}))
    asyncio.run(svc.select_route(rid))
    ev = db.events()[-1]
    assert ev.payload["segment_ids"] == [lblj]      # one entry, not two


def test_select_none_clears_active_route(tmp_path):
    db, svc = make(tmp_path)
    rid = asyncio.run(svc.create_route({"name": "R", "steps": [
        {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]}))
    asyncio.run(svc.select_route(rid))
    asyncio.run(svc.select_route(None))
    ev = db.events()[-1]
    assert ev.type == "route_selected"
    assert ev.payload == {"route_id": None, "segment_ids": []}


def test_select_route_unknown_id_raises(tmp_path):
    db, svc = make(tmp_path)
    with pytest.raises(LookupError):
        asyncio.run(svc.select_route(999))


def test_update_active_route_reemits_select_route_with_fresh_members(tmp_path):
    db, svc = make(tmp_path)
    lblj = seed_id(db, "LBLJ")
    rid = asyncio.run(svc.create_route({"name": "R", "steps": [
        {"need": 1, "candidates": [{"type": "segment", "segment_id": lblj}]}]}))
    asyncio.run(svc.select_route(rid))
    mips = seed_id(db, "MIPS Clip")
    asyncio.run(svc.update_route(rid, {"steps": [
        {"need": 1, "candidates": [{"type": "segment", "segment_id": lblj}]},
        {"need": 1, "candidates": [{"type": "segment", "segment_id": mips}]}]}))
    reselects = [e for e in db.events() if e.type == "route_selected"]
    assert len(reselects) == 2                      # initial select + re-emit on edit
    assert set(reselects[-1].payload["segment_ids"]) == {lblj, mips}


def test_update_route_that_is_not_active_does_not_reemit_select_route(tmp_path):
    db, svc = make(tmp_path)
    rid = asyncio.run(svc.create_route({"name": "R", "steps": [
        {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]}))
    # never selected via svc.select_route
    asyncio.run(svc.update_route(rid, {"name": "R2"}))
    assert not any(e.type == "route_selected" for e in db.events())


def test_name_only_edit_of_active_route_does_not_reemit_select_route(tmp_path):
    # FIX 3 (review, Low): isolate the re-emit guard's two clauses — a
    # name-only patch of the ACTIVE route must not re-emit route_selected
    # (only a `steps` change refreshes the member snapshot).
    db, svc = make(tmp_path)
    rid = asyncio.run(svc.create_route({"name": "R", "steps": [
        {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]}))
    asyncio.run(svc.select_route(rid))
    asyncio.run(svc.update_route(rid, {"name": "R renamed"}))
    reselects = [e for e in db.events() if e.type == "route_selected"]
    assert len(reselects) == 1                      # only the initial select_route


def test_deleting_the_active_route_clears_arming(tmp_path):
    # FIX 2 (review, Medium): deleting the currently-active route must clear
    # arming (a clearing route_selected {None, []}) so its segments don't
    # stay armed under in_active_route forever with no route to point at.
    db, svc = make(tmp_path)
    rid = asyncio.run(svc.create_route({"name": "R", "steps": [
        {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]}))
    asyncio.run(svc.select_route(rid))
    asyncio.run(svc.delete_route(rid))
    assert svc._projector.active_route_id() is None
    ev = db.events()[-1]
    assert ev.type == "route_selected"
    assert ev.payload == {"route_id": None, "segment_ids": []}


def test_deleting_a_non_active_route_does_not_touch_arming(tmp_path):
    db, svc = make(tmp_path)
    lblj = seed_id(db, "LBLJ")
    active_rid = asyncio.run(svc.create_route({"name": "Active", "steps": [
        {"need": 1, "candidates": [{"type": "segment", "segment_id": lblj}]}]}))
    other_rid = asyncio.run(svc.create_route({"name": "Other", "steps": [
        {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]}))
    asyncio.run(svc.select_route(active_rid))
    asyncio.run(svc.delete_route(other_rid))
    assert svc._projector.active_route_id() == active_rid
    assert not any(e.type == "route_selected" and e.payload["route_id"] is None
                  for e in db.events())


def test_active_route_survives_restart_and_reemits_on_edit(tmp_path):
    # FIX 1 (review, High): _active_route was in-memory only, so a service
    # restart lost track of which route was active even though the
    # projector correctly rebuilds route_segments from the journal on
    # replay. This proves the active-route id ALSO survives a restart (it
    # is now read from the projector, not a second field) and that editing
    # the still-active route's steps re-emits route_selected on the NEW
    # service instance.
    db_path = tmp_path / "t.db"
    db1 = Database(db_path)
    svc1 = TrackerService(db1, Broadcaster())
    asyncio.run(svc1.start())
    lblj = seed_id(db1, "LBLJ")
    rid = asyncio.run(svc1.create_route({"name": "R", "steps": [
        {"need": 1, "candidates": [{"type": "segment", "segment_id": lblj}]}]}))
    asyncio.run(svc1.select_route(rid))
    db1.close()

    # "Restart": a fresh service against the SAME db file replays the
    # journal, including the route_selected we just wrote.
    db2 = Database(db_path)
    svc2 = TrackerService(db2, Broadcaster())
    asyncio.run(svc2.start())
    assert svc2._projector.active_route_id() == rid

    mips = seed_id(db2, "MIPS Clip")
    asyncio.run(svc2.update_route(rid, {"steps": [
        {"need": 1, "candidates": [{"type": "segment", "segment_id": lblj}]},
        {"need": 1, "candidates": [{"type": "segment", "segment_id": mips}]}]}))
    reselects = [e for e in db2.events() if e.type == "route_selected"]
    assert len(reselects) == 2                      # original select + restart-safe re-emit
    assert set(reselects[-1].payload["segment_ids"]) == {lblj, mips}


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


def test_set_time_filter_reflags_history_and_clear_reverts(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1350, igt=150)))     # 5.00s: fine by default
    assert db.attempts()[0].cleared is False
    asyncio.run(svc.set_time_filter(2, 2, 180, None))  # min 6s
    a = db.attempts()[0]
    assert a.cleared is True and a.cleared_reason == "auto: below 6.00s min"
    asyncio.run(svc.clear_time_filter(2, 2))
    assert db.attempts()[0].cleared is False


def test_set_time_filter_validates_bounds(tmp_path):
    db, svc = make(tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(svc.set_time_filter(2, 2, 300, 300))   # max must exceed min
    with pytest.raises(ValueError):
        asyncio.run(svc.set_time_filter(2, 2, -1, None))


# -- purge_strategy (strategy-delete addendum, Task 8) ------------------------

def make_with_ranks(tmp_path):
    seed = {"version": 1, "entities": {
        "star:7:2": {"clock": "igt", "strategies": {"Standard": {"Mario": 11.76}}}}}
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(json.dumps(seed))
    ranks = RankStandards(tmp_path / "rs.json", seed_path=seed_path)
    ranks.load()
    db = Database(tmp_path / "t.db")
    svc = TrackerService(db, Broadcaster(), ranks=ranks)
    asyncio.run(svc.start())
    return db, svc


def test_purge_strategy_removes_custom_everywhere(tmp_path):
    db, svc = make_with_ranks(tmp_path)
    asyncio.run(svc.set_strat(7, 2, "logless"))            # registers + activates
    asyncio.run(svc.create_rank_strategy("star:7:2", "logless"))
    asyncio.run(svc.purge_strategy("star:7:2", "logless"))
    assert "logless" not in svc.ranks.strategies("star:7:2")
    assert "logless" not in db.get_state("strategies", {}).get("7:2", [])
    assert "logless" in db.get_state("deleted_strats", {}).get("star:7:2", [])
    assert svc.strat_by_star.get((7, 2)) is None           # strat_set null published


def test_purge_refuses_seeded_strategy(tmp_path):
    db, svc = make_with_ranks(tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(svc.purge_strategy("star:7:2", "Standard"))
    assert "Standard" in svc.ranks.strategies("star:7:2")  # untouched


def test_recreate_after_purge_clears_tombstone(tmp_path):
    db, svc = make_with_ranks(tmp_path)
    asyncio.run(svc.set_strat(7, 2, "logless"))
    asyncio.run(svc.purge_strategy("star:7:2", "logless"))
    asyncio.run(svc.set_strat(7, 2, "logless"))            # register path
    assert "logless" not in db.get_state("deleted_strats", {}).get("star:7:2", [])
    asyncio.run(svc.purge_strategy("star:7:2", "logless"))
    asyncio.run(svc.create_rank_strategy("star:7:2", "logless"))   # ranks path
    assert "logless" not in db.get_state("deleted_strats", {}).get("star:7:2", [])


def test_purge_segment_strategy_tombstones(tmp_path):
    db, svc = make_with_ranks(tmp_path)
    asyncio.run(svc.create_rank_strategy("segment:3", "fast"))
    asyncio.run(svc.purge_strategy("segment:3", "fast"))
    assert "fast" not in svc.ranks.strategies("segment:3")
    assert "fast" in db.get_state("deleted_strats", {}).get("segment:3", [])


# -- segment strategies (star parity: set_strat / registration / purge) -------

def test_set_strat_segment_registers_and_activates(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.set_strat_segment(1, "no bljs"))
    assert svc.strat_by_segment.get(1) == "no bljs"
    assert db.get_state("strategies", {}).get("seg:1") == ["no bljs"]
    asyncio.run(svc.set_strat_segment(1, None))       # explicit clear
    assert svc.strat_by_segment.get(1) is None
    assert db.get_state("strategies", {}).get("seg:1") == ["no bljs"]


def test_set_strat_segment_unknown_id_raises(tmp_path):
    db, svc = make(tmp_path)
    with pytest.raises(LookupError):
        asyncio.run(svc.set_strat_segment(9999, "x"))


def test_purge_segment_strategy_clears_active_and_registration(tmp_path):
    db, svc = make_with_ranks(tmp_path)
    asyncio.run(svc.set_strat_segment(1, "fast"))
    asyncio.run(svc.create_rank_strategy("segment:1", "fast"))
    asyncio.run(svc.purge_strategy("segment:1", "fast"))
    assert svc.strat_by_segment.get(1) is None         # strat_set null published
    assert "fast" not in db.get_state("strategies", {}).get("seg:1", [])


def test_set_attempt_strat_reclassifies_and_registers(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.set_target(2, 2, strat_tag="Cannonless"))
    asyncio.run(svc.publish(ev("practice_reset", 1000,
                               {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1350)))
    aid = db.attempts()[0].id
    assert db.attempts()[0].strat_tag == "Cannonless"
    asyncio.run(svc.set_attempt_strat(aid, "Slide Kick"))
    assert db.attempts()[0].strat_tag == "Slide Kick"
    assert "attempt_strat_set" in [e.type for e in db.events()]
    # the name is registered, so it survives in the section dropdown
    assert "Slide Kick" in db.get_state("strategies", {})["2:2"]
    # this is the entity's NEWEST attempt, so the active strategy follows
    # (user request 2026-07-24) — older rows stay history-only, see below
    assert svc.strat_by_star[(2, 2)] == "Slide Kick"


def test_set_attempt_strat_on_older_attempt_keeps_active_strat(tmp_path):
    """Only the NEWEST row moves the live picker; editing deeper history is
    a pure correction and leaves the active strategy alone."""
    db, svc = make(tmp_path)
    asyncio.run(svc.set_target(2, 2, strat_tag="Cannonless"))
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1350)))
    asyncio.run(svc.publish(ev("practice_reset", 2000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(2350)))
    older_id = min(row.id for row in db.attempts())
    asyncio.run(svc.set_attempt_strat(older_id, "Slide Kick"))
    assert next(row.strat_tag for row in db.attempts()
                if row.id == older_id) == "Slide Kick"
    assert svc.strat_by_star[(2, 2)] == "Cannonless"


def test_set_attempt_strat_null_unlabels_and_is_reversible(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.set_target(2, 2, strat_tag="Cannonless"))
    asyncio.run(svc.publish(ev("practice_reset", 1000,
                               {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1350)))
    aid = db.attempts()[0].id
    asyncio.run(svc.set_attempt_strat(aid, None))
    assert db.attempts()[0].strat_tag is None
    # un-labeling the newest row is not a strategy switch: None never
    # propagates to the active strategy
    assert svc.strat_by_star[(2, 2)] == "Cannonless"
    asyncio.run(svc.set_attempt_strat(aid, "Cannonless"))
    assert db.attempts()[0].strat_tag == "Cannonless"


def test_set_attempt_strat_moves_the_saved_pb(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.set_target(2, 2, strat_tag="Cannonless"))
    asyncio.run(svc.publish(ev("practice_reset", 1000,
                               {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1350)))
    aid = db.attempts()[0].id
    asyncio.run(svc.save_pb(aid, "igt"))
    asyncio.run(svc.set_attempt_strat(aid, "Slide Kick"))
    assert db.current_pb(2, 2, "igt", strat_tag="Slide Kick")["frames"] == 343
    assert db.current_pb(2, 2, "igt", strat_tag="Cannonless") is None


def test_set_attempt_strat_unknown_attempt_raises_lookup_error(tmp_path):
    db, svc = make(tmp_path)
    with pytest.raises(LookupError):
        asyncio.run(svc.set_attempt_strat(999, "Slide Kick"))


def test_set_attempt_strat_skips_registration_when_attempt_has_no_entity(tmp_path):
    """An attempt recorded with no star/segment target has no entity key to
    register a strategy name against, so reclassifying it must still retag
    the attempt without writing anything into the strategies dropdown state."""
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(ev("practice_reset", 1500, {"igt_frames_before": 480})))
    aid = db.attempts()[0].id
    assert db.attempts()[0].course_id is None                # unassigned attempt
    asyncio.run(svc.set_attempt_strat(aid, "Slide Kick"))
    assert db.attempts()[0].strat_tag == "Slide Kick"
    assert db.get_state("strategies", {}) == {}


def test_set_attempt_strat_reclassifies_segment_attempt_and_registers(tmp_path):
    # Every other set_attempt_strat test above seeds a STAR attempt, leaving
    # the segment branch (entity_key(None, None, segment_id) -> "seg:<id>")
    # untraced — the exact asymmetry CLAUDE.md domain rule 11 (star<->segment
    # parity) warns against. Mirrors test_undo_pb_segment_is_kind_aware's
    # setup: seed the "LBLJ" segment def, then walk its level_changed trigger
    # (16->6->17) to close a real segment attempt.
    db, svc = make(tmp_path)
    lblj = seed_id(db, "LBLJ")
    asyncio.run(svc.publish(ev("level_changed", 1000, {"from": 16, "to": 6})))
    asyncio.run(svc.publish(ev("level_changed", 1085, {"from": 6, "to": 17})))
    seg_aid = next(a.id for a in db.attempts() if a.segment_id == lblj)
    asyncio.run(svc.set_attempt_strat(seg_aid, "no bljs"))
    reclassified = next(a for a in db.attempts() if a.id == seg_aid)
    assert reclassified.strat_tag == "no bljs"
    assert "no bljs" in db.get_state("strategies", {})[f"seg:{lblj}"]
    # newest segment attempt -> the segment's active strategy follows too
    # (star<->segment parity for the newest-attempt rule)
    assert svc.strat_by_segment[lblj] == "no bljs"


def test_newest_attempt_id_ignores_the_segment_namespace_offset(tmp_path):
    """Spec 2026-07-28-multi-step-segments, live report (practice-log
    ordering): a reattributed 100-coin attempt keeps its SEGMENT-namespace id
    (arm.jid + SEGMENT_ATTEMPT_OFFSET*def_id, projection.py caveat 2/11) --
    a huge number that would win a raw max(row.id) over every NATIVE
    star-namespace attempt for the same entity regardless of which actually
    happened last. `_newest_attempt_id` must compare by journal_id instead,
    or reclassifying an OLD reattributed attempt would wrongly promote it to
    the active strategy while a genuinely newer reset sits unnoticed.

    Builds the mixed shape for real, through the actual reattribution engine
    (a HUNDRED_COIN_EXIT-shaped def for course 2, mirroring
    tests/test_projection.py's own `_hc_def`) rather than hand-inserting rows
    -- this is the one shape no existing test could have covered, since every
    other set_attempt_strat test seeds attempts from a single namespace."""
    db = Database(tmp_path / "t.db")
    db.insert_segment_def(
        "course 2 100 Coins -> Exit",
        start_triggers=[{"type": "level_enter", "to": 24},
                        {"type": "attempt_anchor", "level": 24}],
        end_triggers=[{"type": "star_grabbed", "course": 2, "star": s}
                      for s in range(6)],
        guards=[], waypoints=[[{"type": "star_grabbed", "course": 2, "star": 6}]],
        created_utc="2026-07-28T00:00:00Z")
    svc = TrackerService(db, Broadcaster())
    asyncio.run(svc.start())

    # Arm the 100-coin engine, grab the 100 coins (waypoint advance, silent),
    # then an exit star -- closes the engine, reattributing a SUCCESS to star
    # (2, 6) under a SEGMENT-namespace id. This is the OLDER event.
    asyncio.run(svc.publish(ev("level_changed", 900, {"from": 16, "to": 24})))
    asyncio.run(svc.publish(star(1000, course=2, star_id=6, igt=1000)))
    asyncio.run(svc.publish(star(1200, course=2, star_id=3, igt=1200)))
    hundred = next(a for a in db.attempts()
                   if a.course_id == 2 and a.star_id == 6 and a.segment_id is None)
    assert hundred.id >= 10**10, "must be the SEGMENT-namespace reattributed row"

    # A genuinely LATER native attempt on the same star entity, via the plain
    # target/anchor path -- a plain int id, chronologically newer but
    # numerically smaller than the reattributed row above. It is ABANDONED
    # (leaving the course) rather than a reset ON PURPOSE: a reset while a
    # 100-coin engine is armed is recorded by the ENGINE, and the plain
    # attempt for it is suppressed as a duplicate (projection.py::_close,
    # live report 2026-08-03). A foreign level change cancels a strict def
    # silently, so the plain row is the only one and this shape survives.
    asyncio.run(svc.set_target(2, 6, strat_tag="Cannonless"))
    asyncio.run(svc.publish(ev("practice_reset", 1400, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(ev("mario_acted", 1410, {})))
    asyncio.run(svc.publish(ev("level_changed", 1500, {"from": 24, "to": 16})))
    native_reset = next(a for a in db.attempts()
                        if a.course_id == 2 and a.star_id == 6
                        and a.outcome == "abandoned")
    assert native_reset.id < 10**10, "must be the plain journal-namespace row"
    assert native_reset.id < hundred.id          # raw id: reset LOOKS older
    from sm64_events.tracking.projection import journal_id
    assert journal_id(hundred.id) < journal_id(native_reset.id)  # actually isn't

    # Reclassifying the OLDER (reattributed) row must NOT move the active
    # strategy -- the reset that happened after it is still the top of the
    # log, exactly test_set_attempt_strat_on_older_attempt_keeps_active_strat's
    # rule, just with one row from each namespace.
    asyncio.run(svc.set_attempt_strat(hundred.id, "Slide Kick"))
    assert svc.strat_by_star[(2, 6)] == "Cannonless"

    # Reclassifying the actually-newest row (the native reset) DOES move it.
    asyncio.run(svc.set_attempt_strat(native_reset.id, "Slide Kick"))
    assert svc.strat_by_star[(2, 6)] == "Slide Kick"


def test_purge_refuses_a_segments_default_strategy(tmp_path):
    """Same protection a community-seeded strat gets, one layer down: the card
    for a defaulted segment hides its "no strategy" option, so tombstoning the
    default would leave the picker offering nothing pickable."""
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(json.dumps({"version": 1, "entities": {}}))
    ranks = RankStandards(tmp_path / "rs.json", seed_path=seed_path)
    ranks.load()
    db = Database(tmp_path / "t.db")
    db.update_segment_def(1, default_strat="Standard")   # before the defs load
    svc = TrackerService(db, Broadcaster(), ranks=ranks)
    asyncio.run(svc.start())
    with pytest.raises(ValueError, match="default strategy"):
        asyncio.run(svc.purge_strategy("segment:1", "Standard"))
    assert db.get_state("deleted_strats", {}) == {}
    # another strategy on the same segment is still purgeable
    asyncio.run(svc.set_strat_segment(1, "Blindfolded"))
    asyncio.run(svc.purge_strategy("segment:1", "Blindfolded"))
    assert "Blindfolded" in db.get_state("deleted_strats", {})["segment:1"]
    assert svc.strat_by_segment.get(1) == "Standard"   # falls back, not cleared


def test_deleting_a_session_takes_its_PBs_with_it(tmp_path):
    """A pb row carries its own `frames`, so one left behind by a deleted
    session keeps GRADING a time whose entire history is gone — an empty
    practice log under a real rank, with a live PB tag beside it (live report
    2026-07-27, after clearing session data to restart a progression).

    The previous pb row for that key restores automatically, which is the
    user's "I should now be ranked at whatever the next highest star is".
    """
    db, svc = make(tmp_path)
    # Session 1: a fast run, saved as the PB.
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1300)))
    fast = db.attempts()[-1]
    asyncio.run(svc.save_pb(fast.id, "igt"))
    assert [row["attempt_id"] for row in db.pbs()] == [fast.id]

    # Session 2 (active), so session 1 is deletable.
    asyncio.run(svc.new_session())
    asyncio.run(svc.publish(ev("practice_reset", 5000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(5500)))

    asyncio.run(svc.delete_session(1))
    surviving = [row["attempt_id"] for row in db.pbs()]
    assert fast.id not in surviving, (
        "the PB outlived the session that set it -- the rank would keep "
        "grading a time with no history behind it")


def test_deleting_a_session_leaves_another_sessions_PB_alone(tmp_path):
    """The mirror: only the deleted session's PBs go. A blanket wipe here
    would silently reset ranks the user never asked to touch."""
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1300)))
    asyncio.run(svc.new_session())
    asyncio.run(svc.publish(ev("practice_reset", 5000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(5400)))
    kept = db.attempts()[-1]
    asyncio.run(svc.save_pb(kept.id, "igt"))

    asyncio.run(svc.new_session())          # session 3, so session 1 is deletable
    asyncio.run(svc.delete_session(1))
    assert kept.id in [row["attempt_id"] for row in db.pbs()]


def test_orphaned_PBs_are_collected_on_reprojection(tmp_path):
    """The REPAIR half of the session/PB fix: rows orphaned by a delete or a
    wipe that predates the callers cleaning up after themselves. A pb row
    carries its own frames, so an orphan keeps GRADING — which is how a star
    kept reading a real rank with an empty practice log (live report
    2026-07-27) even after the delete path started tidying up.
    """
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1300)))
    attempt = db.attempts()[-1]
    asyncio.run(svc.save_pb(attempt.id, "igt"))
    assert db.pbs()

    # Simulate the damage: the attempt's journal slice is gone but its pb row
    # was left behind, exactly as an older delete_session would have left it.
    db.delete_session(attempt.session_id)
    assert db.pbs(), "the row should still be there before the repair runs"

    asyncio.run(svc._reproject())
    assert db.pbs() == [], "the orphaned PB survived a re-projection"


def test_a_live_PB_survives_the_orphan_sweep(tmp_path):
    """The mirror. A sweep that also took live rows would silently reset every
    rank in the app on the next re-projection."""
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1300)))
    attempt = db.attempts()[-1]
    asyncio.run(svc.save_pb(attempt.id, "igt"))
    asyncio.run(svc._reproject())
    assert [row["attempt_id"] for row in db.pbs()] == [attempt.id]


# ---------------------------------------------------------------------------
# A grab-timed star cannot be saved as a PB (2026-08-02): "these fake PBs
# (fake because only xcam timing is legal) just shouldn't be allowed". The
# button is drawn disabled from the SAME predicate (views._attempt_json's
# `pb_blocked_by`), but this is the door — a PB row keeps GRADING once it is
# in the table, and the API is reachable without the button.
# ---------------------------------------------------------------------------

def grab_timed_star(frame=1350, igt=343):
    return ev("star_collected", frame,
              {"course_id": 2, "star_id": 2, "igt_frames": igt,
               "igt_timed_at": "grab"})


def test_a_grab_timed_star_is_refused_as_a_pb(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(grab_timed_star()))
    aid = db.attempts()[0].id
    with pytest.raises(ValueError, match="grab_timed"):
        asyncio.run(svc.save_pb(aid, "igt"))
    assert db.pbs() == []


def test_the_other_clock_is_refused_too(tmp_path):
    # When the x-cam never happened the run's recorded END is the grab, so the
    # rta measures to the same illegal moment — allowing it would be the rule
    # with a hole in it.
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(grab_timed_star()))
    aid = db.attempts()[0].id
    with pytest.raises(ValueError, match="grab_timed"):
        asyncio.run(svc.save_pb(aid, "rta"))


def test_an_xcam_timed_star_still_saves(tmp_path):
    # The control: the refusal is about the QUANTITY, not about stars.
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(ev("star_collected", 1350,
                               {"course_id": 2, "star_id": 2,
                                "igt_frames": 343, "igt_timed_at": "xcam"})))
    aid = db.attempts()[0].id
    assert asyncio.run(svc.save_pb(aid, "igt"))["frames"] == 343


def test_the_view_and_the_server_agree_about_what_is_saveable(tmp_path):
    # One predicate, two consumers: a button that offers what save_pb refuses
    # is the drift this shares a door to prevent.
    from sm64_events.tracking.views import build_session_view
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(grab_timed_star()))
    view = build_session_view(db, svc, clock="igt")
    rows = [r for sec in view["stars"] for r in sec["attempts"]]
    assert [r["pb_blocked_by"] for r in rows] == ["grab_timed"]


def test_the_practice_log_row_carries_its_own_mark(tmp_path):
    """The badge beside the TIME, which is a different key from the one on the
    save button and outlives it: a row already saved as a PB draws Undo, and a
    cleared row draws no button at all, but both still print a number that
    measures the grab rather than the x-cam (2026-08-02)."""
    from sm64_events.tracking.views import build_session_view
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(grab_timed_star()))
    grabbed = [r for sec in build_session_view(db, svc, clock="igt")["stars"]
               for r in sec["attempts"]]
    assert [r["caveat"] for r in grabbed] == ["grab_timed"]

    # The control, and the alarm-fatigue clause: a legacy row is UNKNOWN, not
    # proof, and four fifths of his log is legacy.
    asyncio.run(svc.publish(ev("practice_reset", 2000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(ev("star_collected", 2350,      # no igt_timed_at
                               {"course_id": 2, "star_id": 2,
                                "igt_frames": 343, "igt_source": "result"})))
    legacy = [r for sec in build_session_view(db, svc, clock="igt")["stars"]
              for r in sec["attempts"] if r["id"] != grabbed[0]["id"]]
    assert [r["caveat"] for r in legacy] == [None]


def test_a_legacy_star_is_marked_but_still_saveable(tmp_path):
    """Absence of `igt_timed_at` is UNKNOWN, not proof (measured 2026-08-02).

    669 of his 670 legacy star rows took Usamune's OWN stored number, which is
    the legal x-cam quantity under STOP=Xcam and on any ground grab and the
    grab quantity under GrabX with a real fall — and the journal keeps no
    post-grab frames, so nothing can say which. Refusing them would delete
    almost his whole history's saveability on an assumption nobody measured.
    They stay MARKED, because an unverifiable time is what a caveat is for."""
    from sm64_events.tracking.caveats import caveats_for
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(ev("star_collected", 1350,      # no igt_timed_at
                               {"course_id": 2, "star_id": 2,
                                "igt_frames": 343, "igt_source": "result"})))
    attempt = db.attempts()[0]
    assert attempt.timed_at is None                    # unknown, not "grab"
    assert asyncio.run(svc.save_pb(attempt.id, "igt"))["frames"] == 343
    assert "grab_timed" in caveats_for({"strat_tag": "Owlless"}, attempt)


def test_a_segment_is_never_marked_grab_timed(tmp_path):
    # timed_at is None for every non-star closure, and "unknown" must not
    # become a mark about a moment segments do not have.
    from sm64_events.tracking.caveats import caveats_for
    from sm64_events.tracking.projection import Attempt
    seg = Attempt(id=1, session_id=1, course_id=None, star_id=None,
                  strat_tag="Standard", anchor_type="none", anchor_frame=None,
                  outcome="success", outcome_detail=None, igt_frames=None,
                  rta_frames=500, started_utc="", ended_utc="", cleared=False,
                  cleared_reason=None, segment_id=7)
    assert "grab_timed" not in caveats_for({"strat_tag": "Standard"}, seg)
