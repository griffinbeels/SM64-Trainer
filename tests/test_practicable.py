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
LEVEL_FILE_SELECT, LEVEL_GROUNDS = 1, 16   # both resolve to mode None
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
    # no emulator attached: nothing to compare against, nothing refused
    assert practicable_here(None, "24")
    # ...including the service's boot default, a stage naming no level yet
    assert practicable_here(stage(None), "24")


def test_a_mode_less_place_is_a_place_and_not_an_unknown():
    """`mode` None is the file select, a hub, a cap course — somewhere real
    with nothing to practice. It permitted EVERY pick until 2026-07-27 round
    two, which is the hole this closes: you could set a Whomp's Fortress
    target while standing on the castle grounds, the exact move the rule
    exists to stop. The player's PLACE is the question, and a level answers
    it whether or not the banner has a row for it."""
    for level in (LEVEL_FILE_SELECT, LEVEL_GROUNDS):
        here = stage(None, level=level)
        assert not practicable_here(here, star_origin(COURSE_WF, 0))
        assert not practicable_here(here, "24")
        assert practicable_here(here, None)   # a placeless def still may


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


def test_the_100_coin_star_commits_as_a_plain_star(tmp_path):
    """Against the REAL bundled corpus, not a hand-built stand-in: picking
    the 100-coin star as a STAR commits as a PLAIN star target (spec
    2026-07-28-multi-step-segments, "the 100-coin star IS the segment" --
    superseding the retired _hundred_coin_redirect, user ruling 2026-07-28).
    The practicability gate is unaffected either way: it resolves through
    star_origin(course, star), same as any other star, so standing in WF is
    sufficient regardless of what the underlying engine does."""
    db, svc = make(tmp_path)
    seed_id(db, "WF — 100 Coins → Exit")  # confirms the seeded def exists
    enter(svc, "stars", COURSE_WF, LEVEL_WF)
    asyncio.run(svc.request_target("star", course_id=COURSE_WF, star_id=6))
    assert svc.target == ("star", COURSE_WF, 6)


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


def test_a_pick_from_the_file_select_is_refused(tmp_path):
    """THE live report of 2026-07-27 round two: a new session, the game on its
    main screen, and the previous session's Lethal Lava Land star still
    reading ACTIVE TARGET — with a picker that would happily have set
    another. Nothing is practicable from the file select."""
    db, svc = make(tmp_path)
    enter(svc, None, level=LEVEL_FILE_SELECT)
    with pytest.raises(ValueError, match="standing in"):
        asyncio.run(svc.request_target("star", course_id=COURSE_WF, star_id=0))
    assert svc.target is None


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


# ---- a transition-triggered segment, picked where the transition LEFT you ---
# Live report 2026-08-01, standing in the Castle Lobby having just exited
# Whomp's Fortress, with "WF -> SSL" offered in the banner and refused on
# click: "The only time I would select this is when I just got out of Whomps
# and am in the lobby. I am not presently IN whomps, but rather I am coming
# *from* whomps. Instead of it being bound on 'you can't select something for
# a stage you're not in' it's rather 'you can't select something where you
# didn't satisfy the trigger condition'."

def test_a_segment_already_running_may_be_picked_wherever_it_got_to():
    """The place rule can NEVER accept this pick, and that is the premise
    error: the trigger IS leaving the course, so by the time it has fired the
    player is somewhere else by definition. Satisfying the trigger is the
    permission; the origin is only how you ask before it fires."""
    lobby = stage("castle", None, LEVEL_CASTLE, area=1)
    assert not practicable_here(lobby, "24")                    # from WF: no
    assert practicable_here(lobby, "24", running=True)           # ...unless running


def test_running_does_not_excuse_a_segment_that_has_not_started():
    lobby = stage("castle", None, LEVEL_CASTLE, area=1)
    assert not practicable_here(lobby, "24", running=False)


def test_standing_at_the_origin_still_permits_it_before_the_trigger_fires():
    """Both doors, not a replacement: a movement you have not started yet is
    still picked from the place it starts."""
    assert practicable_here(stage("stars", 2, 24), "24", running=False)


def test_the_movement_you_just_ran_out_of_a_course_can_be_picked_in_the_lobby(tmp_path):
    """END TO END on the real corpus, his exact live report (2026-08-01).

    He was AFK in the Castle Lobby having just left Whomp's Fortress. The
    banner offered "WF -> SSL" -- correctly, it was armed and running -- and
    clicking it came back "you can only practice what you are standing in --
    that one is in Whomp's Fortress". The banner and the server disagreed
    about the same segment on the same screen, and the server was the one
    that was wrong: that IS the only moment the pick makes sense."""
    db, svc = make(tmp_path)
    seg = seed_id(db, "WF \u2192 SSL")
    # A route is what makes the movement armable at all (its in_active_route
    # guard), and this is the route his own header named on the screenshot.
    route = next(r["id"] for r in db.routes()
                 if r["name"].startswith("16 Star — LBLJ"))
    asyncio.run(svc.select_route(route))
    enter(svc, "stars", COURSE_WF, LEVEL_WF)
    asyncio.run(svc.publish(Event(type="level_changed", frame=100, timestamp_utc=T0,
                                  payload={"from": LEVEL_CASTLE, "to": LEVEL_WF})))
    asyncio.run(svc.publish(Event(type="level_changed", frame=5000, timestamp_utc=T0,
                                  payload={"from": LEVEL_WF, "to": LEVEL_CASTLE})))
    enter(svc, "castle", None, LEVEL_CASTLE, area=1)
    # Not a precondition to skip past: if this stops being true the scenario
    # has evaporated and the assertion below would pass vacuously.
    assert seg in svc.armed_segment_ids
    asyncio.run(svc.request_target("segment", segment_id=seg))
    assert svc.target == ("segment", seg)


def test_a_segment_that_is_not_running_is_still_refused_from_elsewhere(tmp_path):
    """The other half, so the door did not simply come off its hinges."""
    db, svc = make(tmp_path)
    seg = seed_id(db, "WF \u2192 SSL")
    enter(svc, "castle", None, LEVEL_CASTLE, area=1)
    assert seg not in svc.armed_segment_ids
    with pytest.raises(ValueError, match="standing in"):
        asyncio.run(svc.request_target("segment", segment_id=seg))
    assert svc.target is None
