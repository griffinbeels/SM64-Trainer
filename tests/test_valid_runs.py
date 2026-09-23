"""A VALID run (task 0144, projection caveat 21) -- his decisions of 2026-09-23.

* Save-state loads no longer split attempts: one practice-log entry per full
  reset, running through every load to the finish, marked invalid.
* A star is valid only from a full reset at the course's own start -- not a
  reset inside a subarea -- with no load before the finish.
* A segment is valid when no load happens between its own start and finish;
  a reset or load starts one only where that segment starts.
* History safety: a session journaled before the rules (no `attempt_rules` on
  its `session_started`) projects exactly as it always has.
"""
from sm64_events.storage.db import EventRow
from sm64_events.tracking.projection import ATTEMPT_RULES, project, replay
from sm64_events.tracking.segments import SegmentDef

W = "2026-09-23T12:00:00Z"
WF, WF_COURSE = 24, 2
LLL, LLL_COURSE = 22, 7
THI = 13


def jev(id, type, frame, payload=None, session_id=1, utc=W):
    return EventRow(id=id, session_id=session_id, seq=id, type=type,
                    frame=frame, wall_time_utc=utc, payload=payload or {})


def session(id, rules=ATTEMPT_RULES, session_id=1):
    payload = {"session_id": session_id}
    if rules is not None:
        payload["attempt_rules"] = rules
    return jev(id, "session_started", 0, payload, session_id=session_id)


def enter(id, frame, level, area=1):
    """A course entry as the detectors journal it: the level edge, its
    establishing area edge, then Usamune's own counter zero ~47 frames later."""
    return [jev(id, "level_changed", frame, {"from": 6, "to": level}),
            jev(id + 1, "area_changed", frame, {"level": level, "from": area, "to": area}),
            reset(id + 2, frame + 47, area=area)]


def reset(id, frame, area=1, **extra):
    return jev(id, "practice_reset", frame,
               {"igt_frames_before": 300, "mario_acted": True, "area": area,
                "prev_area": area, "area_load": False, "teleport": False,
                "loads_journaled": True, **extra})


def load(id, frame, restored=181, signature="igt_rewound", area=1, **extra):
    return jev(id, "state_loaded", frame,
               {"igt_frames_restored": restored, "igt_frames_before": 353,
                "load_signature": signature, "mario_acted": True,
                "area": area, "prev_area": area, "area_load": False,
                "teleport": False, "loads_journaled": True, **extra})


def area_load(id, frame, area=2):
    return jev(id, "practice_reset", frame,
               {"igt_frames_before": 200, "mario_acted": True, "area": area,
                "prev_area": 1, "area_load": True, "teleport": False,
                "loads_journaled": True})


def grab(id, frame, course=WF_COURSE, star_id=1, igt=500):
    return jev(id, "star_collected", frame,
               {"course_id": course, "star_id": star_id, "igt_frames": igt,
                "igt_timed_at": "xcam"})


def stars(attempts):
    return [a for a in attempts if a.segment_id is None]


# --- a load runs THROUGH the attempt ------------------------------------------

def test_a_load_mid_run_no_longer_splits_the_attempt():
    """His TTC loop, under the new rules: one reset, two loads, one finish --
    ONE row, opened by the reset, counting both loads, and not valid."""
    attempts = project([
        session(1), *enter(2, 1000, WF),
        load(10, 1400), load(11, 1600), grab(12, 1900)])
    assert len(attempts) == 1
    a = attempts[0]
    assert (a.id, a.outcome, a.anchor_type) == (4, "success", "practice_reset")
    assert (a.state_loads, a.first_load_frame) == (2, 1400)
    assert (a.valid, a.invalid_reason) == (False, "save_state")


def test_a_clean_run_from_the_course_start_is_valid():
    a, = project([session(1), *enter(2, 1000, WF), grab(10, 1900)])
    assert (a.valid, a.invalid_reason, a.state_loads) == (True, None, 0)
    assert (a.start_level, a.start_area) == (WF, 1)


def test_a_load_with_nothing_open_opens_a_run_that_cannot_be_valid():
    """Grab, load, grab again: the second grab's run began at the load."""
    attempts = project([
        session(1), *enter(2, 1000, WF), grab(10, 1900),
        load(11, 2000), grab(12, 2300)])
    first, second = attempts
    assert first.valid is True
    assert (second.id, second.anchor_type) == (11, "state_loaded")
    assert (second.valid, second.invalid_reason, second.state_loads) == (
        False, "save_state", 1)


def test_a_full_reset_after_loads_starts_a_valid_run_again():
    attempts = project([
        session(1), *enter(2, 1000, WF), load(10, 1400),
        reset(11, 1700), grab(12, 2100)])
    loaded, clean = attempts
    assert (loaded.outcome, loaded.invalid_reason) == ("reset", "save_state")
    assert (clean.id, clean.valid, clean.state_loads) == (11, True, 0)


def test_a_frame_counter_rewind_is_a_load_too():
    """PJ64's own savestate rewinds the frame counter: the load arrives at a
    LOWER frame, closes nothing, and the rta is unknowable rather than wrong."""
    a, = project([session(1), *enter(2, 1000, WF),
                  load(10, 900, signature="frame_counter_rewound"),
                  grab(11, 1500)])
    assert (a.id, a.valid, a.invalid_reason) == (4, False, "save_state")


def test_tricks_before_a_load_stay_with_the_run():
    a, = project([session(1), *enter(2, 1000, WF),
                  jev(10, "rollout", 1100, {"dustless": True, "landing_frames": 1}),
                  load(11, 1200),
                  jev(12, "rollout", 1300, {"dustless": False, "landing_frames": 2}),
                  grab(13, 1500)])
    assert (a.rollouts_total, a.rollouts_dustless) == (2, 1)


# --- where the opening reset landed -------------------------------------------

def test_a_reset_inside_a_subarea_is_not_a_course_start():
    """Enter LLL, drop into the volcano (the level's own load, not a retry),
    reset INSIDE it, grab: "resetting inside the volcano means it's valid for
    Inside the Volcano, but invalid for Elevator Tour"."""
    attempts = project([
        session(1), *enter(2, 1000, LLL),
        area_load(10, 1500, area=2),
        reset(11, 1800, area=2),
        grab(12, 2400, course=LLL_COURSE, star_id=2)])
    a = stars(attempts)[-1]
    assert (a.id, a.start_area) == (11, 2)
    assert (a.valid, a.invalid_reason) == (False, "subarea_reset")


def test_walking_into_a_subarea_keeps_the_course_start_run_valid():
    a, = project([
        session(1), *enter(2, 1000, LLL), area_load(10, 1500, area=2),
        grab(11, 2400, course=LLL_COURSE, star_id=2)])
    assert (a.id, a.valid) == (4, True)


def test_the_course_start_is_where_the_entry_landed():
    """THI's small painting lands on the tiny island, area 2: a reset there
    is a course start, not a subarea reset."""
    attempts = project([
        session(1), *enter(2, 1000, THI, area=2), reset(10, 1500, area=2),
        grab(11, 2000, course=11, star_id=1)])
    assert stars(attempts)[-1].valid is True


def test_a_run_nothing_reset_carries_on_from_before():
    a, b = project([session(1), *enter(2, 1000, WF), grab(10, 1900),
                    grab(11, 2500, star_id=2)])
    assert a.valid is True
    assert (b.valid, b.invalid_reason) == (False, "no_reset")


def test_a_run_opened_by_a_subarea_entry_is_not_a_reset():
    a, b = project([session(1), *enter(2, 1000, LLL),
                    grab(10, 1400, course=LLL_COURSE, star_id=1),
                    area_load(11, 1600, area=2),
                    grab(12, 2400, course=LLL_COURSE, star_id=2)])
    assert (b.id, b.invalid_reason) == (11, "no_reset")


# --- history safety -----------------------------------------------------------

def old_session(start_id):
    """A session journaled BEFORE the rules: no attempt_rules, anchors without
    the detector's marker -- a load there splits the run, as it always has."""
    return [session(start_id, rules=None),
            jev(start_id + 1, "practice_reset", 1000, {"igt_frames_before": 0}),
            jev(start_id + 2, "state_loaded", 1400, {"igt_frames_restored": 181}),
            grab(start_id + 3, 1700)]


def test_a_session_journaled_before_the_rules_projects_as_it_always_has():
    old = project(old_session(1))
    assert [(a.id, a.outcome, a.anchor_type) for a in old] == [
        (2, "reset", "practice_reset"), (3, "success", "state_loaded")]
    assert {a.valid for a in old} == {None}


def test_a_new_session_after_an_old_one_leaves_the_old_rows_untouched():
    old = project(old_session(1))
    both = project([*old_session(1), session(20, session_id=2),
                    *[jev(e.id, e.type, e.frame, e.payload, session_id=2)
                      for e in enter(21, 5000, WF)],
                    jev(30, "state_loaded", 5400, load(0, 0).payload, session_id=2),
                    jev(31, "star_collected", 5700, grab(0, 0).payload, session_id=2)])
    assert both[:len(old)] == old
    assert [(a.id, a.state_loads) for a in both[len(old):]] == [(23, 1)]


def test_a_rules_two_session_does_not_judge_an_anchor_without_the_marker():
    """The session's rules AND the detector's own statement: an anchor that
    does not say loads are journaled opens a run nobody can judge."""
    a, = project([session(1), jev(2, "practice_reset", 1000,
                                  {"igt_frames_before": 0, "area": 1}),
                  grab(3, 1500)])
    assert a.valid is None


def test_the_replay_lead_in_floor_is_the_anchor_before():
    early = "2026-09-23T12:00:00Z"
    late = "2026-09-23T12:00:01Z"
    a, = [x for x in project([
        session(1), jev(2, "level_changed", 900, {"from": 6, "to": WF}),
        jev(3, "practice_reset", 950, {**reset(0, 0).payload, "mario_acted": False}, utc=early),
        jev(4, "practice_reset", 980, reset(0, 0).payload, utc=late),
        grab(5, 1400)]) if x.outcome == "success"]
    assert (a.id, a.prev_anchor_utc) == (4, early)


# --- segments -----------------------------------------------------------------

def seg(id, start, end_star=1):
    return SegmentDef(id=id, name=f"seg {id}", enabled=True,
                      start_triggers=[start],
                      end_triggers=[{"type": "star_grabbed", "course": WF_COURSE,
                                     "star": end_star}],
                      guards=[])


FROM_A_RESET = {"type": "attempt_anchor", "level": WF}
FROM_A_MOMENT = {"type": "moment_reached", "level": WF, "kind": "pole_grab"}


def segs(attempts, sid):
    return [a for a in attempts if a.segment_id == sid]


def test_a_segment_that_starts_on_a_reset_is_valid_from_a_load():
    """"For SUBSEGMENTS, it might be valid to start from a save state, because
    the subsection occurs AFTER the start of the save state" -- a definition
    that starts on a reset or load starts where the load lands."""
    attempts, _ = replay([session(1), *enter(2, 1000, WF),
                          load(10, 1400), grab(11, 1900)],
                         segments=[seg(1, FROM_A_RESET)])
    run = [a for a in segs(attempts, 1) if a.outcome == "success"]
    assert len(run) == 1
    assert (run[0].anchor_type, run[0].valid, run[0].state_loads) == (
        "state_loaded", True, 0)


def test_a_segment_restarted_by_a_load_elsewhere_in_its_area_is_not_valid():
    """A definition that starts on a pole grab: a load in the same area
    re-arms it at the load, which may be half-way up the level."""
    events = [session(1), *enter(2, 1000, WF),
              jev(10, "moment_reached", 1200,
                  {"kind": "pole_grab", "level": WF, "area": 1, "ordinal": 1}),
              load(11, 1500), grab(12, 1900)]
    attempts, _ = replay(events, segments=[seg(1, FROM_A_MOMENT)])
    run = [a for a in segs(attempts, 1) if a.outcome == "success"]
    assert len(run) == 1 and run[0].anchor_type == "state_loaded"
    assert (run[0].valid, run[0].invalid_reason) == (False, "save_state")


def test_a_clean_segment_run_is_valid():
    events = [session(1), *enter(2, 1000, WF),
              jev(10, "moment_reached", 1200,
                  {"kind": "pole_grab", "level": WF, "area": 1, "ordinal": 1}),
              grab(11, 1900)]
    attempts, _ = replay(events, segments=[seg(1, FROM_A_MOMENT)])
    run, = [a for a in segs(attempts, 1) if a.outcome == "success"]
    assert (run.valid, run.state_loads) == (True, 0)


def test_area_one_is_always_a_course_start():
    """Enter THI by the small painting, walk to the huge island, reset there:
    that is the big painting's own start, not a subarea (two such runs in his
    journal read as subarea resets under the entry-area-only rule)."""
    attempts = project([
        session(1), *enter(2, 1000, THI, area=2), area_load(10, 1300, area=1),
        reset(11, 1500, area=1), grab(12, 2000, course=11, star_id=1)])
    assert stars(attempts)[-1].valid is True


def test_a_segment_started_by_a_load_is_not_timed_from_the_restored_counter():
    """A load RESTORES Usamune's counter (181 here) rather than zeroing it, so
    a closing grab's 400 measures from the save, not from the load: the
    segment takes its own 219 frames instead. (segments.py's rule that an IGT
    may never exceed the span it covers is what holds this.)"""
    attempts, _ = replay([session(1), *enter(2, 1000, WF),
                          load(10, 1400, restored=181), grab(11, 1619, igt=400)],
                         segments=[seg(1, FROM_A_RESET)])
    run, = [a for a in segs(attempts, 1) if a.outcome == "success"]
    assert run.rta_frames == 219


def test_a_load_the_matcher_runs_through_still_invalidates_the_segment():
    """A load journaled on the save prompt's reload is an ECHO to the matcher
    (segments.py shape 4), so the segment runs straight through it -- and a
    load between its start and its finish is exactly what makes it invalid."""
    events = [session(1), *enter(2, 1000, WF),
              jev(10, "moment_reached", 1200,
                  {"kind": "pole_grab", "level": WF, "area": 1, "ordinal": 1}),
              load(11, 1500, save_pending=True), grab(12, 1900)]
    attempts, _ = replay(events, segments=[seg(1, FROM_A_MOMENT)])
    run, = [a for a in segs(attempts, 1) if a.outcome == "success"]
    assert (run.anchor_type, run.state_loads, run.first_load_frame) == (
        "moment_reached", 1, 1500)
    assert (run.valid, run.invalid_reason) == (False, "save_state")


def test_a_reset_after_a_load_keeps_an_attempt_he_played():
    """Review 2026-09-23: reset, play, load, reset without moving. The reset
    says "nothing since the last anchor" -- true of the stretch after the
    load, not of the attempt -- and the no-op discard used to drop the whole
    played attempt, replay and all."""
    attempts = project([
        session(1), *enter(2, 1000, WF),
        jev(10, "mario_acted", 1100),
        load(11, 1400, mario_acted=True),
        reset(12, 1500, mario_acted=False)])
    played, = [a for a in attempts if a.id == 4]
    assert (played.outcome, played.state_loads, played.invalid_reason) == (
        "reset", 1, "save_state")


def test_a_run_nobody_played_is_still_discarded_through_a_load():
    """The discard still does its job: reset, load, reset, never moving."""
    attempts = project([
        session(1), *enter(2, 1000, WF),
        load(11, 1400, mario_acted=False),
        reset(12, 1500, mario_acted=False)])
    assert [a.id for a in attempts] == []


def test_a_load_that_opens_an_attempt_does_not_lend_it_the_last_ones_play():
    """Grab, load (the play before it belongs to the finished run), reset
    without moving: nothing was played in the attempt the load opened."""
    attempts = project([
        session(1), *enter(2, 1000, WF), grab(10, 1300),
        load(11, 1400, mario_acted=True),
        reset(12, 1500, mario_acted=False)])
    assert [a.id for a in attempts] == [4]


import pytest  # noqa: E402

from sm64_events.tracking.projection import Projector  # noqa: E402


@pytest.mark.parametrize("signature, frame, origin", [
    ("frame_counter_rewound", 900, (10, 900)),          # Project64 F7, backward
    ("frame_counter_skipped_ahead", 5000, (10, 5000)),  # F7 onto an earlier boot's state
    ("igt_rewound", 1400, (5, 1047)),                   # Usamune's own load: the reset stays
])
def test_a_full_ram_load_restarts_the_star_ghosts_clock(signature, frame, origin):
    """A Project64 load restores the game's own frame counter, so the star
    ghost times from the load, whichever way the counter moved (review
    2026-09-23: forward, the ghost stayed on the pre-load reset). A load
    Usamune does itself leaves the counter alone and the origin with it."""
    projector = Projector()
    for ev in [session(1), jev(2, "target_set", 990, {"course_id": WF_COURSE, "star_id": 1}),
               *[jev(e.id + 1, e.type, e.frame, e.payload) for e in enter(2, 1000, WF)],
               load(10, frame, signature=signature)]:
        projector.feed(ev)
    live = projector.native_ghost_origin("star", f"{WF_COURSE}-1")
    assert (live["arm_id"], live["frame"]) == origin


# --- round 10 (2026-09-23): loads inside engine-timed runs, and Bowser 3 -------

def hundred_coin_engine(id=71):
    """WF's 100-coin engine, as seeded: a star timed by a segment engine."""
    return SegmentDef(id=id, name="WF — 100 Coins → Exit", enabled=True,
                      start_triggers=[{"type": "level_enter", "to": WF},
                                      {"type": "attempt_anchor", "level": WF}],
                      waypoints=[[{"type": "star_grabbed", "course": WF_COURSE, "star": 6}]],
                      end_triggers=[{"type": "star_grabbed", "course": WF_COURSE, "star": s}
                                    for s in range(6)],
                      guards=[])


def hundred_coin_run(rules):
    target = jev(3, "target_set", 0, {"course_id": WF_COURSE, "star_id": 6})
    return [session(1, rules=rules), jev(2, "level_changed", 1000, {"from": 6, "to": WF}),
            jev(4, "area_changed", 1000, {"level": WF, "from": 1, "to": 1}), target,
            reset(5, 1047, mario_acted=False, acted_tracking=True), jev(6, "mario_acted", 1100),
            load(10, 1400), load(11, 1600), load(12, 1800),
            grab(20, 2200, star_id=6), grab(21, 2400, star_id=3)]


def hundred_coin_rows(attempts):
    return [a for a in attempts if (a.course_id, a.star_id) == (WF_COURSE, 6)]


def test_a_load_runs_through_a_100_coin_run_too():
    """His WF 100 Coins (round 10): the star is timed by a segment ENGINE,
    and each load closed the run as a reset and re-armed at the load -- ten
    reset rows for one run. Under rules 2 the engine runs through the loads
    like the star path does: one row, counting them, not valid."""
    attempts, _ = replay(hundred_coin_run(ATTEMPT_RULES), segments=[hundred_coin_engine()])
    run, = hundred_coin_rows(attempts)
    assert (run.outcome, run.state_loads, run.first_load_frame) == ("success", 3, 1400)
    assert (run.valid, run.invalid_reason) == (False, "save_state")


def test_a_100_coin_run_journaled_before_the_rules_splits_as_it_always_did():
    events = hundred_coin_run(None)
    for index, ev in enumerate(events):          # old journals: no marker, no signature
        if ev.type in ("practice_reset", "state_loaded"):
            events[index] = jev(ev.id, ev.type, ev.frame,
                                {k: v for k, v in ev.payload.items()
                                 if k not in ("loads_journaled", "load_signature")})
    attempts, _ = replay(events, segments=[hundred_coin_engine()])
    assert [a.outcome for a in hundred_coin_rows(attempts)].count("reset") >= 3
    assert "loaded" not in {a.outcome for a in attempts}


def test_a_segment_a_load_restarts_says_it_ended_on_a_load():
    """A plain segment still restarts at a load (a subsection may start from a
    save state); the attempt the load ended says so -- "it shouldn't be a
    *real* reset, it should be a 'LOADED SAVE STATE'" -- and never 'reset'."""
    events = [session(1), *enter(2, 1000, WF),
              jev(10, "moment_reached", 1200,
                  {"kind": "pole_grab", "level": WF, "area": 1, "ordinal": 1}),
              jev(11, "mario_acted", 1250),
              load(12, 1500), grab(13, 1900)]
    attempts, _ = replay(events, segments=[seg(1, FROM_A_MOMENT)])
    ended = [a for a in segs(attempts, 1) if a.outcome != "success"]
    assert [a.outcome for a in ended] == ["loaded"]


BOWSER_3 = 34


def bowser_3(id=10):
    return SegmentDef(id=id, name="Bowser 3", enabled=True,
                      start_triggers=[{"type": "level_enter", "to": BOWSER_3},
                                      {"type": "attempt_anchor", "level": BOWSER_3}],
                      end_triggers=[{"type": "key_grabbed", "level": BOWSER_3}],
                      guards=[])


def arena_reset(id, frame, acted=True):
    return jev(id, "practice_reset", frame,
               {"igt_frames_before": 300, "mario_acted": acted, "acted_tracking": True,
                "area": 1, "prev_area": 1, "area_load": False, "teleport": False,
                "loads_journaled": True})


def test_his_bowser_3_pb_keeps_the_fight_selected_and_its_resets_recorded():
    """Round 10, his sequence (journal ids 3903-3928): arriving in the arena
    auto-selects Bowser 3 (a HOOKED head); he wins, saves the PB, and resets
    to go again. The completed hook used to empty the hand: the ghost had no
    target ("Checking whether this has a run...") and every reset of the
    fight after it was untargeted and went unrecorded -- until his second PB,
    won with the hand empty, followed onto the fight."""
    events = [session(1), jev(2, "level_changed", 46575, {"from": 11, "to": BOWSER_3}),
              jev(3, "area_changed", 46575, {"level": BOWSER_3, "from": 1, "to": 1}),
              jev(4, "target_set", 0, {"kind": "segment", "segment_id": 10, "auto": True}),
              arena_reset(5, 46577, acted=False), jev(6, "mario_acted", 46614),
              jev(7, "key_grabbed", 48326, {"level": BOWSER_3, "which": "grand",
                                            "igt_frames": 1752}),
              arena_reset(8, 48689), jev(9, "mario_acted", 48853),
              arena_reset(10, 49603)]
    attempts, proj = replay(events, segments=[bowser_3()])
    assert proj.target == ("segment", 10)
    assert [a.outcome for a in segs(attempts, 10)] == ["success", "reset"]


def test_a_100_coin_run_picked_up_at_the_grab_was_not_started_by_a_reset():
    """The engine arms on the 100-coin grab itself when nothing armed it (his
    load loop): that run began at no reset, so it is not valid."""
    events = [session(1), jev(2, "area_changed", 900, {"level": WF, "from": 1, "to": 1}),
              grab(3, 1500, star_id=6), grab(4, 1700, star_id=3)]
    attempts, _ = replay(events, segments=[hundred_coin_engine()])
    run, = hundred_coin_rows(attempts)
    assert (run.anchor_type, run.valid, run.invalid_reason) == ("star_collected", False, "no_reset")
