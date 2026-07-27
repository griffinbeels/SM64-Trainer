# tests/test_practicable.py
"""You practice what is in front of you (spec: user, 2026-07-27).

A target may only be set for something practicable where the player stands,
and stops being the target when they walk into a course that isn't its place.
Replaced tests/test_pending_target.py, which pinned the HELD-intent behaviour
this ruling removed.

Three layers: the pure predicate, the reader it depends on (the layer that was
actually broken), and the two enforcement points — setting, in the service, and
retiring, in the projector.
"""
import asyncio
import json
from datetime import datetime, timezone

import pytest

from sm64_events.core.events import Event
from sm64_events.core.paths import bundled_defaults_seed
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.storage.db import Database, EventRow
from sm64_events.tracking.defaults import reconcile_defaults
from sm64_events.tracking.practicable import practicable_here
from sm64_events.tracking.projection import Projector
from sm64_events.tracking.segments import (SegmentDef, segment_origin,
                                           stage_origin, star_origin,
                                           start_levels, start_origin)
from sm64_events.tracking.service import TrackerService

SEED = json.loads(bundled_defaults_seed().read_bytes().decode("utf-8"))

T0 = datetime(2026, 7, 27, 12, 0, 0, tzinfo=timezone.utc)

LEVEL_WF, LEVEL_CCM, LEVEL_SSL, LEVEL_CASTLE = 24, 5, 8, 6
COURSE_WF, COURSE_CCM = 2, 4


def stage(mode, course_id=None, level=None, area=1):
    return {"course_id": course_id, "level": level, "area": area, "mode": mode}


def make(tmp_path):
    """A service over the REAL bundled corpus — main.py reconciles it at
    startup, and the castle movements are the whole point here."""
    db = Database(tmp_path / "t.db")
    assert reconcile_defaults(db, SEED) == []
    svc = TrackerService(db, Broadcaster())
    asyncio.run(svc.start())
    return db, svc


def enter(svc, mode, course_id=None, level=None, area=1):
    """Publish the broadcast-only stage_changed the detector would emit."""
    asyncio.run(svc.publish(Event(
        type="stage_changed", frame=0, timestamp_utc=T0,
        payload=stage(mode, course_id, level, area))))


def seed_id(db, name):
    return next(d["id"] for d in db.segment_defs() if d["name"] == name)


# ---- the pure predicate ---------------------------------------------------

def test_you_can_practice_what_you_are_standing_in():
    assert practicable_here(stage("stars", COURSE_CCM, LEVEL_CCM),
                            star_origin(COURSE_CCM, 5))
    assert practicable_here(stage("castle", None, LEVEL_CASTLE, area=1), "6:1")


def test_you_cannot_practice_another_place():
    ccm = stage("stars", COURSE_CCM, LEVEL_CCM)
    assert not practicable_here(ccm, star_origin(COURSE_WF, 0))   # a WF star
    assert not practicable_here(ccm, "24")                        # from WF
    assert not practicable_here(ccm, "6:1")                       # the lobby


def test_a_course_is_one_place_however_many_areas_it_has():
    """CCM's slide is area 2 of level 5. A course must not split into two
    nodes, or walking into the slide would drop the star you're practising."""
    assert stage_origin(LEVEL_CCM, 1) == stage_origin(LEVEL_CCM, 2)
    assert stage_origin(LEVEL_CASTLE, 1) != stage_origin(LEVEL_CASTLE, 3)


def test_both_unknowns_permit_the_pick():
    # a definition that names no place at all
    assert practicable_here(stage("stars", COURSE_CCM, LEVEL_CCM), None)
    # no emulator attached / title screen: nothing to compare, nothing refused
    assert practicable_here(None, "24")
    assert practicable_here(stage(None), "24")


# ---- the reader (this is the layer that was broken) -----------------------

def test_every_seeded_definition_resolves_to_a_place():
    """THE regression guard. `start_levels` reads arm_level — where a trigger
    LEAVES Mario — and 50 of the 51 seeded `level_exit` clauses omit `to`, so
    54 of the 65 definitions resolved to nowhere and every consumer inherited
    the blind spot at once: the banner offered no castle movement, a picked
    one was held forever, and no movement target was ever retired (the live
    report of 2026-07-27, "ACTIVE SEGMENT  WF -> SSL" while inside CCM).
    Where a segment is practiced FROM is the question, and it has an answer
    for every one of them."""
    placeless = [row["name"] for row in SEED["segments"]
                 if start_origin(row["start_triggers"]) is None]
    assert placeless == []
    # and the old reader really is the broken one — if this ever starts
    # passing, arm_level has changed meaning and this file needs re-reading
    blind = [row["name"] for row in SEED["segments"]
             if not start_levels(row["start_triggers"])]
    assert len(blind) > 50


def test_a_user_override_moves_where_a_segment_is_practiced():
    triggers = [{"type": "level_exit", "from": LEVEL_WF}]
    assert segment_origin(21, triggers, {}) == "24"
    assert segment_origin(21, triggers, {"21": "6:3"}) == "6:3"


# ---- enforcement 1: setting the target ------------------------------------

def test_a_pick_from_another_course_is_refused(tmp_path):
    db, svc = make(tmp_path)
    enter(svc, "stars", COURSE_CCM, LEVEL_CCM)
    with pytest.raises(ValueError, match="standing in"):
        asyncio.run(svc.request_target("star", course_id=COURSE_WF, star_id=0))
    assert svc.target is None


def test_a_pick_from_the_course_you_are_in_commits(tmp_path):
    db, svc = make(tmp_path)
    enter(svc, "stars", COURSE_CCM, LEVEL_CCM)
    asyncio.run(svc.request_target("star", course_id=COURSE_CCM, star_id=5))
    assert svc.target == ("star", COURSE_CCM, 5)


def test_a_movement_is_settable_from_the_course_it_starts_in(tmp_path):
    """The half that would be lost if the rule used the old reader: with
    `start_levels` answering nothing for WF -> SSL, "only what's here" would
    have made all 54 castle movements permanently unsettable."""
    db, svc = make(tmp_path)
    wf_ssl = seed_id(db, "WF → SSL")
    enter(svc, "stars", COURSE_WF, LEVEL_WF)
    asyncio.run(svc.request_target("segment", segment_id=wf_ssl))
    assert svc.target == ("segment", wf_ssl)


def test_a_movement_is_not_settable_from_somewhere_else(tmp_path):
    db, svc = make(tmp_path)
    wf_ssl = seed_id(db, "WF → SSL")
    enter(svc, "stars", COURSE_CCM, LEVEL_CCM)
    with pytest.raises(ValueError, match="standing in"):
        asyncio.run(svc.request_target("segment", segment_id=wf_ssl))
    assert svc.target is None


def test_repicking_the_current_target_elsewhere_still_sets_its_strategy(tmp_path):
    """A strategy edit goes through /api/target with the entity already
    targeted. Refusing it because the player has since walked off would be a
    second bug wearing the first one's clothes."""
    db, svc = make(tmp_path)
    enter(svc, "stars", COURSE_CCM, LEVEL_CCM)
    asyncio.run(svc.request_target("star", course_id=COURSE_CCM, star_id=5))
    enter(svc, "stars", COURSE_WF, LEVEL_WF)
    asyncio.run(svc.request_target("star", course_id=COURSE_CCM, star_id=5,
                                   strat_tag="Backflip WK"))
    assert svc.strat_by_star[(COURSE_CCM, 5)] == "Backflip WK"


def test_with_no_emulator_attached_anything_is_settable(tmp_path):
    """Reviewing with the game closed must not be a read-only mode."""
    db, svc = make(tmp_path)
    asyncio.run(svc.request_target("star", course_id=COURSE_WF, star_id=0))
    assert svc.target == ("star", COURSE_WF, 0)


# ---- enforcement 2: retiring the target -----------------------------------

WF_TO_SSL = SegmentDef(
    id=21, name="WF -> SSL", enabled=True,
    start_triggers=[{"type": "level_exit", "from": LEVEL_WF}],
    waypoints=[[{"type": "area_enter", "level": 6, "area": 3}]],
    end_triggers=[{"type": "level_enter", "to": LEVEL_SSL}], guards=[])

W = "2026-07-27T12:00:00Z"


def ev(id, type, payload):
    return EventRow(id=id, session_id=1, seq=id, type=type, frame=1000 + id,
                    wall_time_utc=W, payload=payload)


def targeted_projector():
    proj = Projector(segments=[WF_TO_SSL])
    proj.feed(ev(1, "target_set", {"kind": "segment", "segment_id": 21}))
    assert proj.target == ("segment", 21)
    return proj


def test_a_movement_target_retires_on_warping_into_another_course():
    """THE live report, from the target side: pick WF -> SSL, warp to CCM, and
    the practice page kept calling it the ACTIVE SEGMENT."""
    proj = targeted_projector()
    proj.feed(ev(2, "level_changed", {"from": LEVEL_WF, "to": LEVEL_CCM}))
    assert proj.target is None


def test_a_movement_target_survives_the_castle():
    """The castle is TRANSIT — every course is entered through it, so retiring
    there would drop the target one room before it could be practiced."""
    proj = targeted_projector()
    proj.feed(ev(2, "level_changed", {"from": LEVEL_WF, "to": LEVEL_CASTLE}))
    assert proj.target == ("segment", 21)


def test_a_movement_target_survives_re_entering_its_own_course():
    proj = targeted_projector()
    proj.feed(ev(2, "level_changed", {"from": LEVEL_WF, "to": LEVEL_CASTLE}))
    proj.feed(ev(3, "level_changed", {"from": LEVEL_CASTLE, "to": LEVEL_WF}))
    assert proj.target == ("segment", 21)


def test_a_placeless_segment_target_is_never_retired():
    anywhere = SegmentDef(id=7, name="from the top", enabled=True,
                          start_triggers=[{"type": "reset_game"}],
                          waypoints=[], guards=[],
                          end_triggers=[{"type": "level_enter", "to": 9}])
    proj = Projector(segments=[anywhere])
    proj.feed(ev(1, "target_set", {"kind": "segment", "segment_id": 7}))
    proj.feed(ev(2, "level_changed", {"from": LEVEL_WF, "to": LEVEL_CCM}))
    assert proj.target == ("segment", 7)


def test_retirement_follows_a_user_origin_override():
    proj = Projector(segments=[WF_TO_SSL], origin_overrides={"21": "5"})
    proj.feed(ev(1, "target_set", {"kind": "segment", "segment_id": 21}))
    # overridden to live in CCM, so walking into CCM must now KEEP it...
    proj.feed(ev(2, "level_changed", {"from": LEVEL_WF, "to": LEVEL_CCM}))
    assert proj.target == ("segment", 21)
    # ...and walking into WF, its derived origin, must retire it
    proj.feed(ev(3, "level_changed", {"from": LEVEL_CCM, "to": LEVEL_WF}))
    assert proj.target is None
