# tests/test_pending_target.py
"""A practice target picked for somewhere the player isn't standing is HELD
until they follow through (spec: user, 2026-07-26). Two layers are pinned
here: the pure commit/drop/hold decision, and the service wiring that turns a
stage_changed into one of those three outcomes."""
import asyncio
from datetime import datetime, timezone

import pytest

from sm64_events.core.events import Event
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.storage.db import Database
from sm64_events.tracking import pending_target as pending
from sm64_events.tracking.service import TrackerService

T0 = datetime(2026, 7, 26, 12, 0, 0, tzinfo=timezone.utc)

LLL, SSL, CASTLE = 13, 8, 6          # course ids; level 6 = Castle Inside
LEVEL_LLL, LEVEL_SSL = 22, 8         # gCurrLevelNum for those two courses


def stage(mode, course_id=None, level=None, area=1):
    return {"course_id": course_id, "level": level, "area": area, "mode": mode}


class RecordingBroadcaster(Broadcaster):
    def __init__(self):
        super().__init__()
        self.sent: list[Event] = []

    async def publish(self, event: Event) -> int:
        self.sent.append(event)
        return await super().publish(event)


def make(tmp_path):
    db = Database(tmp_path / "t.db")
    bc = RecordingBroadcaster()
    svc = TrackerService(db, bc)
    asyncio.run(svc.start())
    return db, svc, bc.sent


def enter(svc, mode, course_id=None, level=None, area=1):
    """Publish the broadcast-only stage_changed the detector would emit."""
    asyncio.run(svc.publish(Event(
        type="stage_changed", frame=0, timestamp_utc=T0,
        payload=stage(mode, course_id, level, area))))


def seed_id(db, name):
    return next(d["id"] for d in db.segment_defs() if d["name"] == name)


# ---- the pure decision ----------------------------------------------------

def test_a_star_belongs_to_its_own_course_only():
    assert pending.belongs_to_stage(stage("stars", SSL, LEVEL_SSL),
                                    "star", SSL, [], [])
    assert not pending.belongs_to_stage(stage("stars", LLL, LEVEL_LLL),
                                        "star", SSL, [], [])
    # no stage at all (emulator detached) is not "here"
    assert not pending.belongs_to_stage(None, "star", SSL, [], [])


def test_a_segment_belongs_by_start_level_or_start_subarea():
    # by level (the Bowser rows offer segments by level alone)
    assert pending.belongs_to_stage(stage("bowser_course", 16, 17),
                                    "segment", None, [17], [])
    # by [level, area] (the castle rows are subarea-scoped)
    assert pending.belongs_to_stage(stage("castle", None, 6, area=3),
                                    "segment", None, [], [[6, 3]])
    # the same segment in the WRONG castle subarea is not here
    assert not pending.belongs_to_stage(stage("castle", None, 6, area=1),
                                        "segment", None, [], [[6, 3]])


def test_entering_a_different_course_drops_but_the_castle_holds():
    # Reaching any course means passing through Castle Inside, so treating
    # the castle as a change of mind would drop every intent one room early.
    held = ("star", SSL, [], [])
    assert pending.resolve(stage("stars", SSL, LEVEL_SSL), *held) == pending.COMMIT
    assert pending.resolve(stage("stars", LLL, LEVEL_LLL), *held) == pending.DROP
    assert pending.resolve(stage("castle", None, 6), *held) == pending.HOLD
    assert pending.resolve(stage(None, None, 16), *held) == pending.HOLD
    assert pending.resolve(None, *held) == pending.HOLD


def test_an_arena_or_bowser_course_also_counts_as_committing_elsewhere():
    held = ("star", SSL, [], [])
    assert pending.resolve(stage("arena", None, 30), *held) == pending.DROP
    assert pending.resolve(stage("bowser_course", 16, 17), *held) == pending.DROP
    assert "castle" not in pending.COMMITMENT_MODES


# ---- the service wiring ---------------------------------------------------

def test_picking_a_star_in_another_course_holds_it(tmp_path):
    db, svc, sent = make(tmp_path)
    enter(svc, "stars", LLL, LEVEL_LLL)
    asyncio.run(svc.set_target(LLL, 1))
    result = asyncio.run(svc.request_target("star", course_id=SSL, star_id=2,
                                            strat_tag="carpetless"))
    assert result == {"pending": True}
    assert svc.target == ("star", LLL, 1)            # target has NOT moved
    held = svc.pending_target_payload()
    assert held["course_id"] == SSL and held["star_id"] == 2
    assert held["strat_tag"] == "carpetless"
    assert held["where"] == "Shifting Sand Land"     # named for display
    assert any(e.type == "target_pending" for e in sent)


def test_walking_into_the_intended_course_commits_it_with_its_strategy(tmp_path):
    db, svc, sent = make(tmp_path)
    enter(svc, "stars", LLL, LEVEL_LLL)
    asyncio.run(svc.request_target("star", course_id=SSL, star_id=2,
                                   strat_tag="carpetless"))
    enter(svc, "castle", None, 6)                    # in transit — still held
    assert svc.pending_target_payload() is not None
    assert svc.target != ("star", SSL, 2)
    enter(svc, "stars", SSL, LEVEL_SSL)              # arrived
    assert svc.target == ("star", SSL, 2)
    assert svc.strat_tag == "carpetless"
    assert svc.pending_target_payload() is None


def test_walking_into_a_different_course_drops_the_intent(tmp_path):
    db, svc, sent = make(tmp_path)
    enter(svc, "stars", LLL, LEVEL_LLL)
    asyncio.run(svc.set_target(LLL, 1))
    asyncio.run(svc.request_target("star", course_id=SSL, star_id=2))
    enter(svc, "stars", 5, 5)                        # somewhere else entirely
    assert svc.pending_target_payload() is None
    assert svc.target == ("star", LLL, 1)            # normal detection carries on


def test_a_pick_you_can_practice_here_commits_immediately(tmp_path):
    db, svc, sent = make(tmp_path)
    enter(svc, "stars", LLL, LEVEL_LLL)
    result = asyncio.run(svc.request_target("star", course_id=LLL, star_id=3))
    assert result == {"pending": False}
    assert svc.target == ("star", LLL, 3)
    assert svc.pending_target_payload() is None


def test_with_no_live_stage_every_pick_commits(tmp_path):
    # Nothing is attached, so "did they follow through" has no answer —
    # holding here would strand the target until the emulator came up.
    db, svc, sent = make(tmp_path)
    assert svc.current_stage["mode"] is None
    result = asyncio.run(svc.request_target("star", course_id=SSL, star_id=2))
    assert result == {"pending": False} and svc.target == ("star", SSL, 2)


def test_repicking_the_current_target_from_elsewhere_is_not_a_journey(tmp_path):
    # Only the strategy can differ, so there is nothing to follow through on.
    db, svc, sent = make(tmp_path)
    asyncio.run(svc.set_target(SSL, 2))
    enter(svc, "stars", LLL, LEVEL_LLL)
    result = asyncio.run(svc.request_target("star", course_id=SSL, star_id=2,
                                            strat_tag="carpetless"))
    assert result == {"pending": False} and svc.strat_tag == "carpetless"


def test_a_segment_intent_commits_on_reaching_its_start_subarea(tmp_path):
    db, svc, sent = make(tmp_path)
    lblj = seed_id(db, "LBLJ")
    enter(svc, "stars", LLL, LEVEL_LLL)
    assert asyncio.run(svc.request_target("segment", segment_id=lblj)) \
        == {"pending": True}
    held = svc.pending_target_payload()
    assert held["segment_name"] == "LBLJ"
    # No `where` for a segment: its location is a set of start levels and
    # castle subareas, not one named place. Answering with the segment's own
    # name printed the same words twice in the chip while truncating the half
    # that identifies it (render, 2026-07-26).
    assert held["where"] is None
    enter(svc, "castle", None, 6, area=1)            # LBLJ starts in the lobby
    assert svc.target == ("segment", lblj)
    assert svc.pending_target_payload() is None


def test_an_unknown_segment_404s_instead_of_being_held(tmp_path):
    db, svc, sent = make(tmp_path)
    enter(svc, "stars", LLL, LEVEL_LLL)
    with pytest.raises(LookupError):
        asyncio.run(svc.request_target("segment", segment_id=9999))
    assert svc.pending_target_payload() is None


def test_a_second_pick_replaces_the_held_one(tmp_path):
    db, svc, sent = make(tmp_path)
    enter(svc, "stars", LLL, LEVEL_LLL)
    asyncio.run(svc.request_target("star", course_id=SSL, star_id=2))
    asyncio.run(svc.request_target("star", course_id=5, star_id=1))
    assert svc.pending_target_payload()["course_id"] == 5


def test_clearing_a_held_intent_leaves_the_target_alone(tmp_path):
    db, svc, sent = make(tmp_path)
    enter(svc, "stars", LLL, LEVEL_LLL)
    asyncio.run(svc.set_target(LLL, 1))
    asyncio.run(svc.request_target("star", course_id=SSL, star_id=2))
    asyncio.run(svc.clear_pending_target())
    assert svc.pending_target_payload() is None
    assert svc.target == ("star", LLL, 1)
    asyncio.run(svc.clear_pending_target())          # idempotent


def test_the_intent_is_never_journaled(tmp_path):
    # Live state like stage_changed: a replay must not resurrect an intent,
    # and target_pending carries no historical value.
    db, svc, sent = make(tmp_path)
    enter(svc, "stars", LLL, LEVEL_LLL)
    asyncio.run(svc.request_target("star", course_id=SSL, star_id=2))
    assert any(e.type == "target_pending" for e in sent)
    assert all(row.type != "target_pending" for row in db.events())
