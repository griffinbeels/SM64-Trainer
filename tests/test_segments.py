import pytest

from sm64_events.storage.db import EventRow
from sm64_events.tracking.segments import (SEGMENT_ATTEMPT_OFFSET,
                                           MatchContext, SegmentDef,
                                           SegmentEngine,
                                           validate_definition, vocab)

W = "2026-06-11T12:00:00Z"


def jev(id, type, frame, payload=None, session_id=1):
    # local copy of test_projection.py's factory (tests/ is not a package)
    return EventRow(id=id, session_id=session_id, seq=id, type=type,
                    frame=frame, wall_time_utc=W, payload=payload or {})


def test_validate_accepts_a_seed_shaped_definition():
    validate_definition({
        "name": "LBLJ",
        "start_triggers": [{"type": "level_enter", "to": 6, "from": 16}],
        "end_triggers": [{"type": "level_enter", "to": 17}],
        "guards": []})  # no raise


def test_validate_rejects_unknown_trigger_type():
    with pytest.raises(ValueError, match="unknown trigger type"):
        validate_definition({"name": "x",
                             "start_triggers": [{"type": "nope"}],
                             "end_triggers": [{"type": "spawned"}],
                             "guards": []})


def test_validate_rejects_missing_required_param():
    with pytest.raises(ValueError, match="level_enter"):
        validate_definition({"name": "x",
                             "start_triggers": [{"type": "level_enter"}],
                             "end_triggers": [{"type": "spawned"}],
                             "guards": []})


def test_vocab_lists_triggers_guards_and_level_enum():
    v = vocab()
    keys = {t["key"] for t in v["triggers"]}
    assert {"level_enter", "level_exit", "area_enter", "warp_entered",
            "key_grabbed", "star_grabbed", "spawned",
            "attempt_anchor"} <= keys
    assert v["levels"]["17"] == "Bowser in the Dark World"
    assert {g["key"] for g in v["guards"]} == {"prev_level",
                                               "star_count_min",
                                               "star_count_max"}


def test_string_clause_raises_value_error_not_500():
    with pytest.raises(ValueError, match="must be a dict"):
        validate_definition({"name": "x", "start_triggers": ["level_enter"],
                             "end_triggers": [{"type": "spawned"}], "guards": []})


def test_non_list_guards_raises_value_error():
    with pytest.raises(ValueError, match="guards must be a list"):
        validate_definition({"name": "x",
                             "start_triggers": [{"type": "spawned"}],
                             "end_triggers": [{"type": "spawned"}],
                             "guards": "not a list"})


def test_all_db_seeds_pass_validate_definition(tmp_path):
    """Registry/seed agreement: seeds live as JSON in db.py MIGRATIONS while
    the vocabulary lives here — this is the only gate that catches a rename
    on either side."""
    from sm64_events.storage.db import Database
    db = Database(tmp_path / "t.db")
    defs = db.segment_defs()
    assert len(defs) == 10
    for d in defs:
        validate_definition({k: d[k] for k in
                             ("name", "start_triggers", "end_triggers",
                              "guards")})


# ---------------------------------------------------------------------------
# Task 10: SegmentEngine FSM tests
# ---------------------------------------------------------------------------

LBLJ = SegmentDef(id=1, name="LBLJ", enabled=True,
                  start_triggers=[{"type": "level_enter", "to": 6, "from": 16}],
                  end_triggers=[{"type": "level_enter", "to": 17}], guards=[])
PIPE = SegmentDef(id=5, name="BitDW Pipe Entry", enabled=True,
                  start_triggers=[{"type": "level_enter", "to": 17},
                                  {"type": "attempt_anchor", "level": 17}],
                  end_triggers=[{"type": "warp_entered", "level": 17}],
                  guards=[])


def ctx(level=None, prev_level=None, num_stars=None, area=None):
    return MatchContext(level=level, prev_level=prev_level,
                        num_stars=num_stars, area=area)


def lblj_arm(engine, jid=10, frame=1000):
    return engine.feed(jev(jid, "level_changed", frame,
                           {"from": 16, "to": 6}), ctx(level=6, prev_level=16))


def test_arm_then_end_is_a_success_with_rta_delta():
    e = SegmentEngine([LBLJ])
    lblj_arm(e)
    closed, _ = e.feed(jev(11, "level_changed", 1085, {"from": 6, "to": 17}),
                       ctx(level=17, prev_level=6))
    [a] = closed
    assert a.outcome == "success" and a.segment_id == 1
    assert a.rta_frames == 85 and a.igt_frames is None
    assert a.course_id is None and a.star_id is None
    assert a.id == 10 + SEGMENT_ATTEMPT_OFFSET * 1
    assert a.anchor_type == "level_changed"


def test_restart_anchors_rearm_without_recording_a_row():
    e = SegmentEngine([LBLJ])
    lblj_arm(e, jid=10, frame=1000)
    # walk out (silent disarm), walk back in (fresh arm at the new frame)
    closed, _ = e.feed(jev(11, "level_changed", 1200, {"from": 6, "to": 16}),
                       ctx(level=16, prev_level=6))
    assert closed == []
    lblj_arm(e, jid=12, frame=1300)
    closed, _ = e.feed(jev(13, "level_changed", 1390, {"from": 6, "to": 17}),
                       ctx(level=17, prev_level=6))
    assert closed[0].rta_frames == 90


def test_rearm_on_start_refire_restarts_the_timer():
    e = SegmentEngine([PIPE])
    e.feed(jev(20, "level_changed", 2000, {"from": 6, "to": 17}),
           ctx(level=17, prev_level=6))
    e.feed(jev(21, "practice_reset", 2500, {"igt_frames_before": 100}),
           ctx(level=17))                       # closes reset AND re-arms
    closed, _ = e.feed(jev(22, "warp_entered", 2600, {"level": 17, "area": 1,
                                                      "action": 0x1300}),
                       ctx(level=17))
    assert closed[0].rta_frames == 100          # timed from the reset, not entry


def test_practice_reset_closes_as_reset_then_rearms_via_attempt_anchor():
    e = SegmentEngine([PIPE])
    e.feed(jev(30, "level_changed", 3000, {"from": 6, "to": 17}),
           ctx(level=17, prev_level=6))
    closed, _ = e.feed(jev(31, "practice_reset", 3200,
                           {"igt_frames_before": 50}), ctx(level=17))
    [a] = closed
    assert a.outcome == "reset" and a.rta_frames == 200
    assert a.anchor_type == "level_changed"     # the attempt that FAILED was armed by entry


def test_afk_reset_discards_the_row_but_still_rearms():
    e = SegmentEngine([PIPE])
    e.feed(jev(40, "level_changed", 4000, {"from": 6, "to": 17}),
           ctx(level=17, prev_level=6))
    closed, _ = e.feed(jev(41, "practice_reset", 4500,
                           {"paused_frames_before": 200}), ctx(level=17))
    assert closed == []                          # AFK discard
    closed, _ = e.feed(jev(42, "warp_entered", 4600, {"level": 17, "area": 1,
                                                      "action": 0x1300}),
                       ctx(level=17))
    assert closed[0].rta_frames == 100           # re-armed by the reset anyway


def test_death_and_game_reset_close_with_their_outcomes():
    e = SegmentEngine([LBLJ])
    lblj_arm(e)
    closed, _ = e.feed(jev(11, "death", 1050, {"cause": "standing"}),
                       ctx(level=6))
    assert closed[0].outcome == "death"
    assert closed[0].outcome_detail == "standing"
    lblj_arm(e, jid=12, frame=2000)
    closed, _ = e.feed(jev(13, "game_reset", 2100, {}), ctx())
    assert closed[0].outcome == "hard_reset"


def test_foreign_level_change_disarms_silently():
    e = SegmentEngine([LBLJ])
    lblj_arm(e)
    closed, _ = e.feed(jev(11, "level_changed", 1500, {"from": 6, "to": 27}),
                       ctx(level=27, prev_level=6))
    assert closed == []
    closed, _ = e.feed(jev(12, "level_changed", 1600, {"from": 27, "to": 17}),
                       ctx(level=17, prev_level=27))
    assert closed == []                          # was not armed anymore


def test_establishing_level_event_from_equals_to_never_arms():
    e = SegmentEngine([LBLJ])
    closed, _ = e.feed(jev(10, "level_changed", 1000, {"from": 6, "to": 6}),
                       ctx(level=6, prev_level=6))
    assert e.armed_ids() == set()


def test_guards_reevaluate_on_every_arm():
    guarded = SegmentDef(id=2, name="g", enabled=True,
                         start_triggers=[{"type": "level_enter", "to": 6}],
                         end_triggers=[{"type": "level_enter", "to": 17}],
                         guards=[{"type": "prev_level", "level": 16}])
    e = SegmentEngine([guarded])
    e.feed(jev(10, "level_changed", 1000, {"from": 26, "to": 6}),
           ctx(level=6, prev_level=26))          # guard fails: from courtyard
    assert e.armed_ids() == set()
    e.feed(jev(11, "level_changed", 1100, {"from": 16, "to": 6}),
           ctx(level=6, prev_level=16))
    assert e.armed_ids() == {2}


def test_negative_rta_discards_and_disarms():
    e = SegmentEngine([LBLJ])
    lblj_arm(e, frame=5000)
    closed, _ = e.feed(jev(11, "level_changed", 100, {"from": 6, "to": 17}),
                       ctx(level=17, prev_level=6))
    assert closed == []
    assert e.armed_ids() == set()


def test_armed_disarmed_notices_for_live_broadcast():
    e = SegmentEngine([LBLJ])
    _, notices = lblj_arm(e)
    assert notices == [{"event": "segment_armed", "segment_id": 1,
                        "name": "LBLJ", "frame": 1000}]
    _, notices = e.feed(jev(11, "level_changed", 1500, {"from": 6, "to": 27}),
                        ctx(level=27, prev_level=6))
    assert notices[0]["event"] == "segment_disarmed"


def test_realistic_game_reset_records_hard_reset_with_unknowable_rta():
    # game_reset frames are boot-range (< 120, lifecycle.py) BY DEFINITION,
    # so the delta from any real arm frame is negative — the row must still
    # exist, with the time marked unknowable.
    e = SegmentEngine([LBLJ])
    lblj_arm(e)                                  # armed at frame 1000
    closed, _ = e.feed(jev(11, "game_reset", 50, {}), ctx())
    [a] = closed
    assert a.outcome == "hard_reset"
    assert a.rta_frames is None
    assert e.armed_ids() == set()


def test_session_started_while_armed_disarms_silently():
    e = SegmentEngine([LBLJ])
    lblj_arm(e)
    closed, notices = e.feed(jev(11, "session_started", 0, {}), ctx())
    assert closed == []
    assert e.armed_ids() == set()
    assert notices == [{"event": "segment_disarmed", "segment_id": 1,
                        "name": "LBLJ", "frame": 0}]


def test_state_loaded_closes_as_reset():
    e = SegmentEngine([PIPE])
    e.feed(jev(20, "level_changed", 2000, {"from": 6, "to": 17}),
           ctx(level=17, prev_level=6))
    closed, _ = e.feed(jev(21, "state_loaded", 2300,
                           {"igt_frames_restored": 0}), ctx(level=17))
    [a] = closed
    assert a.outcome == "reset" and a.rta_frames == 300
    assert e.armed_ids() == {5}                  # re-armed via attempt_anchor


def test_two_defs_armed_by_same_event_get_disjoint_ids():
    second = SegmentDef(id=2, name="Second", enabled=True,
                        start_triggers=[{"type": "level_enter", "to": 6,
                                         "from": 16}],
                        end_triggers=[{"type": "level_enter", "to": 17}],
                        guards=[])
    e = SegmentEngine([LBLJ, second])
    _, notices = lblj_arm(e)
    assert e.armed_ids() == {1, 2}
    assert [n["event"] for n in notices] == ["segment_armed",
                                             "segment_armed"]
    closed, _ = e.feed(jev(11, "level_changed", 1085, {"from": 6, "to": 17}),
                       ctx(level=17, prev_level=6))
    assert len(closed) == 2
    by_def = {a.segment_id: a for a in closed}
    assert (by_def[2].id - by_def[1].id) == SEGMENT_ATTEMPT_OFFSET * (2 - 1)


def test_success_emits_disarmed_notice():
    e = SegmentEngine([LBLJ])
    lblj_arm(e)
    _, notices = e.feed(jev(11, "level_changed", 1085, {"from": 6, "to": 17}),
                        ctx(level=17, prev_level=6))
    assert notices == [{"event": "segment_disarmed", "segment_id": 1,
                        "name": "LBLJ", "frame": 1085}]


def test_afk_constant_matches_projection():
    # the segment-side AFK threshold mirrors the star side — if projection's
    # constant moves, this is the gate that catches the drift
    from sm64_events.tracking.projection import PAUSE_DISCARD_FRAMES
    from sm64_events.tracking.segments import _AFK_PAUSE_FRAMES
    assert _AFK_PAUSE_FRAMES == PAUSE_DISCARD_FRAMES


def test_guard_failing_refire_keeps_original_arm():
    guarded = SegmentDef(id=3, name="g", enabled=True,
                         start_triggers=[{"type": "star_grabbed"}],
                         end_triggers=[{"type": "level_enter", "to": 17}],
                         guards=[{"type": "star_count_max", "n": 5}])
    e = SegmentEngine([guarded])
    e.feed(jev(30, "star_collected", 3000, {"course_id": 1, "star_id": 1}),
           ctx(level=9, num_stars=5))            # guard passes: armed
    assert e.armed_ids() == {3}
    e.feed(jev(31, "star_collected", 3500, {"course_id": 1, "star_id": 2}),
           ctx(level=9, num_stars=6))            # guard fails: NO re-arm, NO disarm
    assert e.armed_ids() == {3}
    closed, _ = e.feed(jev(32, "level_changed", 4000, {"from": 9, "to": 17}),
                       ctx(level=17, prev_level=9))
    assert closed[0].rta_frames == 1000          # timed from the ORIGINAL arm


# ---------------------------------------------------------------------------
# Load-echo guard (live gate 2026-06-12)
# Usamune resets IGT on every level load, so the anchor detector emits a
# synthetic practice_reset on the SAME global-timer frame as the level entry
# that armed the segment.  A same-frame anchor must be ignored completely.
# ---------------------------------------------------------------------------

def test_load_echo_anchor_does_not_close_a_fresh_arm():
    """Castle-entry LBLJ: practice_reset at frame 1000 == arm frame 1000
    is a load echo and must NOT close or disarm the segment."""
    e = SegmentEngine([LBLJ])
    e.feed(jev(10, "level_changed", 1000, {"from": 16, "to": 6}),
           ctx(level=6, prev_level=16))
    closed, notices = e.feed(jev(11, "practice_reset", 1000,
                                  {"igt_frames_before": 64}), ctx(level=6))
    assert closed == []
    assert e.armed_ids() == {1}
    disarmed = [n for n in notices if n["event"] == "segment_disarmed"]
    assert disarmed == []


def test_lblj_full_walk_with_load_echoes_records_one_clean_success():
    """Full LBLJ walk: castle-entry echo at 1000, BitDW entry echo at 1085.
    Only one closed attempt (success, rta 85); no reset rows."""
    e = SegmentEngine([LBLJ])
    # Castle entry arms LBLJ
    e.feed(jev(10, "level_changed", 1000, {"from": 16, "to": 6}),
           ctx(level=6, prev_level=16))
    # Load echo — same frame as arm — must be ignored
    e.feed(jev(11, "practice_reset", 1000, {"igt_frames_before": 64}),
           ctx(level=6))
    # BitDW entry closes LBLJ with success (end trigger)
    closed1, _ = e.feed(jev(12, "level_changed", 1085, {"from": 6, "to": 17}),
                         ctx(level=17, prev_level=6))
    # BitDW load echo — LBLJ is already disarmed; PIPE not in this engine
    closed2, _ = e.feed(jev(13, "practice_reset", 1085,
                             {"igt_frames_before": 64}), ctx(level=17))
    all_closed = closed1 + closed2
    assert len(all_closed) == 1
    [a] = all_closed
    assert a.outcome == "success" and a.rta_frames == 85


def test_attempt_anchor_segment_load_echo_keeps_armed_without_junk_row():
    """BitDW pipe entry (attempt_anchor segment): level entry at 2000 arms;
    practice_reset at frame 2000 (load echo) must not close it.
    A subsequent warp_entered at 2100 must succeed with rta 100."""
    e = SegmentEngine([PIPE])
    e.feed(jev(20, "level_changed", 2000, {"from": 6, "to": 17}),
           ctx(level=17, prev_level=6))
    # Load echo — same frame
    closed, _ = e.feed(jev(21, "practice_reset", 2000,
                            {"igt_frames_before": 64}), ctx(level=17))
    assert closed == []
    assert e.armed_ids() == {5}
    # Real end trigger
    closed, _ = e.feed(jev(22, "warp_entered", 2100,
                            {"level": 17, "area": 1, "action": 0x1300}),
                        ctx(level=17))
    assert len(closed) == 1
    assert closed[0].outcome == "success" and closed[0].rta_frames == 100


def test_real_reset_frames_later_still_closes():
    """Guard the guard: a practice_reset that lands at a DIFFERENT frame
    from the arm frame is a real player reset and must close the segment."""
    e = SegmentEngine([LBLJ])
    e.feed(jev(10, "level_changed", 1000, {"from": 16, "to": 6}),
           ctx(level=6, prev_level=16))
    closed, _ = e.feed(jev(11, "practice_reset", 1179,
                            {"igt_frames_before": 30}), ctx(level=6))
    assert len(closed) == 1
    assert closed[0].outcome == "reset" and closed[0].rta_frames == 179


# ---------------------------------------------------------------------------
# Area-door echo guard (live report 2026-06-12)
# An area_changed (castle door) mid-segment triggers a synthetic
# practice_reset at the SAME frame.  The segment must stay armed.
# ---------------------------------------------------------------------------

def test_area_door_echo_mid_segment_does_not_close():
    """LBLJ armed in lobby (level 16→6 @1000); area_changed 1→3 @1200 (door);
    practice_reset @1200 (door echo) → no closure, segment stays armed.
    Subsequent level_changed 6→17 @1300 → success rta 300."""
    e = SegmentEngine([LBLJ])
    # arm via castle entry
    e.feed(jev(10, "level_changed", 1000, {"from": 16, "to": 6}),
           ctx(level=6, prev_level=16))
    # load echo at arm frame — already covered, still no closure
    e.feed(jev(11, "practice_reset", 1000, {"igt_frames_before": 64}),
           ctx(level=6))
    assert e.armed_ids() == {1}
    # area door crossed mid-segment: area_changed then echo at SAME frame
    e.feed(jev(12, "area_changed", 1200, {"level": 6, "from": 1, "to": 3}),
           ctx(level=6))
    closed, _ = e.feed(jev(13, "practice_reset", 1200, {}), ctx(level=6))
    assert closed == [], "area-door echo must not close the segment"
    assert e.armed_ids() == {1}, "segment must remain armed after door echo"
    # end trigger fires — success timed from original arm at 1000
    closed, _ = e.feed(jev(14, "level_changed", 1300, {"from": 6, "to": 17}),
                       ctx(level=17, prev_level=6))
    assert len(closed) == 1
    assert closed[0].outcome == "success" and closed[0].rta_frames == 300


def test_real_reset_after_door_still_closes():
    """Same prefix through the door @1200; a practice_reset at @1400 (delta
    from last transition = 200 frames) is a real player reset → outcome reset,
    rta 400 (from original arm at 1000)."""
    e = SegmentEngine([LBLJ])
    e.feed(jev(10, "level_changed", 1000, {"from": 16, "to": 6}),
           ctx(level=6, prev_level=16))
    e.feed(jev(11, "practice_reset", 1000, {"igt_frames_before": 64}),
           ctx(level=6))
    e.feed(jev(12, "area_changed", 1200, {"level": 6, "from": 1, "to": 3}),
           ctx(level=6))
    # door echo — ignored
    e.feed(jev(13, "practice_reset", 1200, {}), ctx(level=6))
    assert e.armed_ids() == {1}
    # real reset 200 frames later — must close
    closed, _ = e.feed(jev(14, "practice_reset", 1400,
                            {"igt_frames_before": 30}), ctx(level=6))
    assert len(closed) == 1
    assert closed[0].outcome == "reset" and closed[0].rta_frames == 400


# ---------------------------------------------------------------------------
# Intra-area door echo (live gate 2026-06-12, finding 3)
# Same area on both sides of the door → no area_changed → _last_transition_frame
# guard cannot see it.  Classified instead by action in DOOR_ACTIONS.
# ---------------------------------------------------------------------------

def test_intra_area_door_echo_does_not_close():
    """seq 23-31 replay: LBLJ armed at lobby entry (16→6 @92855, co-frame
    load echo already ignored); player crosses the small lobby door toward
    the basement stairs — SAME area on both sides (no area_changed) — and a
    synthetic practice_reset fires @93025 with action=ACT_WARP_DOOR_SPAWN.
    Must not close the segment.  level_changed 6→17 @93100 → success rta 245."""
    e = SegmentEngine([LBLJ])
    # arm via castle entry @92855
    e.feed(jev(10, "level_changed", 92855, {"from": 16, "to": 6}),
           ctx(level=6, prev_level=16))
    # co-frame load echo at arm tick — already ignored by _last_transition_frame
    e.feed(jev(11, "practice_reset", 92855, {"igt_frames_before": 64}),
           ctx(level=6))
    assert e.armed_ids() == {1}
    # intra-area door echo: NO area_changed fired, but igt reset with door action
    closed, _ = e.feed(
        jev(12, "practice_reset", 93025,
            {"igt_frames_before": 128, "action": 0x00001322}),
        ctx(level=6))
    assert closed == [], "intra-area door echo must not close the segment"
    assert e.armed_ids() == {1}, "segment must remain armed after door echo"
    # end trigger fires — success timed from original arm @92855
    closed, _ = e.feed(jev(13, "level_changed", 93100, {"from": 6, "to": 17}),
                       ctx(level=17, prev_level=6))
    assert len(closed) == 1
    assert closed[0].outcome == "success" and closed[0].rta_frames == 245


def test_real_reset_with_gameplay_action_still_closes():
    """A practice_reset whose action is a regular gameplay action (idle =
    L-press default) is a genuine player reset and must close the segment."""
    e = SegmentEngine([LBLJ])
    e.feed(jev(10, "level_changed", 1000, {"from": 16, "to": 6}),
           ctx(level=6, prev_level=16))
    # real L-reset: action is ACT_IDLE (0x0C400201), not a door action
    closed, _ = e.feed(
        jev(11, "practice_reset", 1200, {"action": 0x0C400201}),
        ctx(level=6))
    assert len(closed) == 1
    assert closed[0].outcome == "reset" and closed[0].rta_frames == 200


def test_historical_anchor_without_action_field_closes():
    """Historical journal events have no 'action' key in the payload.
    .get('action') returns None → None not in DOOR_ACTIONS → conservative
    close behaviour (real reset) is preserved for old events."""
    e = SegmentEngine([LBLJ])
    e.feed(jev(10, "level_changed", 1000, {"from": 16, "to": 6}),
           ctx(level=6, prev_level=16))
    # no "action" key — historical event
    closed, _ = e.feed(
        jev(11, "practice_reset", 1200, {"igt_frames_before": 30}),
        ctx(level=6))
    assert len(closed) == 1
    assert closed[0].outcome == "reset" and closed[0].rta_frames == 200


# ---------------------------------------------------------------------------
# prev_action discriminator (live race fix 2026-06-12)
# A Usamune L-reset respawns Mario at the level entrance in
# ACT_WARP_DOOR_SPAWN (0x1322).  If the anchor poll catches the IGT drop one
# tick late, a REAL reset carries the door action as curr.mario_action and
# was incorrectly eaten as a door echo.
#
# Discriminator: a genuine door crossing is ALWAYS preceded by the door open
# animation — prev_action in DOOR_ACTIONS (inputs locked during door anim).
# An L-reset's prev_action is the gameplay action when the reset was pressed.
# ---------------------------------------------------------------------------

def test_lreset_respawning_at_door_still_closes():
    """THE RACE CASE: LBLJ armed @1000; L-reset fires while the poll catches
    Mario already in ACT_WARP_DOOR_SPAWN (0x1322) — curr action is a door
    action but prev was freefall (0x04000440).  Must close as reset rta 200.
    (Red before the fix — this is the live intermittent-miss bug.)"""
    e = SegmentEngine([LBLJ])
    lblj_arm(e)
    closed, _ = e.feed(
        jev(11, "practice_reset", 1200,
            {"action": 0x00001322, "prev_action": 0x04000440}),
        ctx(level=6))
    assert len(closed) == 1
    assert closed[0].outcome == "reset" and closed[0].rta_frames == 200


def test_door_crossing_prev_action_is_echo():
    """A door crossing where prev_action itself is in DOOR_ACTIONS (inputs
    were already locked on the previous poll tick) → genuine door echo, not
    a player reset.  Segment must stay armed."""
    e = SegmentEngine([LBLJ])
    lblj_arm(e)
    closed, _ = e.feed(
        jev(11, "practice_reset", 1200,
            {"action": 0x00001322, "prev_action": 0x00001321}),
        ctx(level=6))
    assert closed == []
    assert e.armed_ids() == {1}


def test_intra_area_door_echo_with_prev_action_stays_echo():
    """Existing intra-area door test shape updated to carry the realistic
    prev_action=0x1321 (door-open anim on the previous tick).  Still green —
    segment must remain armed and succeed at rta 245."""
    e = SegmentEngine([LBLJ])
    e.feed(jev(10, "level_changed", 92855, {"from": 16, "to": 6}),
           ctx(level=6, prev_level=16))
    e.feed(jev(11, "practice_reset", 92855, {"igt_frames_before": 64}),
           ctx(level=6))
    assert e.armed_ids() == {1}
    # intra-area door echo: prev tick was PULLING_DOOR (0x1321) — door anim
    closed, _ = e.feed(
        jev(12, "practice_reset", 93025,
            {"igt_frames_before": 128,
             "action": 0x00001322, "prev_action": 0x00001321}),
        ctx(level=6))
    assert closed == [], "intra-area door echo must not close the segment"
    assert e.armed_ids() == {1}
    closed, _ = e.feed(jev(13, "level_changed", 93100, {"from": 6, "to": 17}),
                       ctx(level=17, prev_level=6))
    assert len(closed) == 1
    assert closed[0].outcome == "success" and closed[0].rta_frames == 245


# ---------------------------------------------------------------------------
# Non-warp door recency echo (live gate 2026-06-12, journal seq 26)
# NON-WARP doors (ACT_PULLING/PUSHING_DOOR 0x1320/0x1321) end the Usamune
# section AFTER the animation: the IGT reset arrives 1-5 frames later when
# Mario is already idle/landing — neither prev_action nor action carries door
# context at that point.  The frames_since_door recency field bridges the gap.
# ---------------------------------------------------------------------------

def test_nonwarp_door_section_reset_is_echo():
    """THE SEQ-26 REGRESSION: LBLJ armed @1000; non-warp door was crossed
    ~1296 (ACT_PUSHING_DOOR 0x0C400201→0x1321); Usamune resets the section
    IGT 4 frames later @1300 when Mario is already in FREEFALL_LAND — no door
    action in prev or curr.  frames_since_door=4 is the recency discriminator.
    Must NOT close the segment (must stay armed).
    (Red before fix — this is the live-gate seq-26 bug.)"""
    e = SegmentEngine([LBLJ])
    lblj_arm(e, jid=10, frame=1000)
    # Non-warp door reset: Mario in FREEFALL_LAND, prev=IDLE, but frames_since_door=4
    closed, _ = e.feed(
        jev(26, "practice_reset", 1300,
            {"igt_frames_before": 296,
             "action": 0x04000440,        # ACT_FREEFALL
             "prev_action": 0x0C400201,   # ACT_IDLE — not in DOOR_ACTIONS
             "frames_since_door": 4}),
        ctx(level=6))
    assert closed == [], "non-warp door section reset must not close the segment"
    assert e.armed_ids() == {1}, "segment must remain armed"


def test_reset_long_after_door_still_closes():
    """Same door crossing but frames_since_door=200 (well outside the echo
    window) → genuine player L-reset → outcome reset, rta 400."""
    e = SegmentEngine([LBLJ])
    lblj_arm(e, jid=10, frame=1000)
    closed, _ = e.feed(
        jev(27, "practice_reset", 1400,
            {"igt_frames_before": 400,
             "action": 0x04000440,
             "prev_action": 0x0C400201,
             "frames_since_door": 200}),
        ctx(level=6))
    assert len(closed) == 1
    assert closed[0].outcome == "reset" and closed[0].rta_frames == 400


def test_historical_anchor_without_frames_since_door_closes():
    """Historical events (no frames_since_door key) keep conservative close
    behaviour — .get() returns None, out-of-window, treated as real reset."""
    e = SegmentEngine([LBLJ])
    lblj_arm(e, jid=10, frame=1000)
    closed, _ = e.feed(
        jev(28, "practice_reset", 1200,
            {"igt_frames_before": 200,
             "action": 0x04000440,
             "prev_action": 0x0C400201}),  # no frames_since_door key
        ctx(level=6))
    assert len(closed) == 1
    assert closed[0].outcome == "reset" and closed[0].rta_frames == 200


# ---------------------------------------------------------------------------
# Anchor closure re-arm (live-gate amendment 2026-06-12)
# A practice_reset/state_loaded that CLOSES an armed segment must also
# RE-ARM the same segment at the anchor frame — the practice-loop
# continuation.  Usamune L-reset respawns Mario at the level's last entrance,
# which is the segment's start position (lobby door for LBLJ, HMC exit for
# MIPS), so timing from the anchor equals a fresh start-trigger arm.
# ---------------------------------------------------------------------------

def test_second_reset_also_records():
    """Live regression (2026-06-12 report: grounds→lobby, reset, reset again —
    second reset recorded nothing, armed chip dark).
    LBLJ armed via level_changed 16→6 @1000 (+ load echo @1000 ignored);
    real reset @1300 → row 1 (reset rta 300), segment still armed;
    real reset @1600 → row 2 (reset rta 300), segment still armed;
    success end @1800 → row 3 (success rta 200, timed from second reset)."""
    e = SegmentEngine([LBLJ])
    # arm
    e.feed(jev(10, "level_changed", 1000, {"from": 16, "to": 6}),
           ctx(level=6, prev_level=16))
    # load echo — ignored
    e.feed(jev(11, "practice_reset", 1000, {"igt_frames_before": 64}),
           ctx(level=6))
    assert e.armed_ids() == {1}

    # first real reset
    closed1, notices1 = e.feed(
        jev(12, "practice_reset", 1300, {"action": 0x0C400201}),
        ctx(level=6))
    assert len(closed1) == 1
    assert closed1[0].outcome == "reset" and closed1[0].rta_frames == 300
    assert e.armed_ids() == {1}, "segment must stay armed after first reset"
    # no armed/disarmed notices: attempt boundary, not a state change
    assert [n["event"] for n in notices1
            if n["event"] in ("segment_armed", "segment_disarmed")] == []

    # second real reset — the live-regression case (was yielding no row)
    closed2, notices2 = e.feed(
        jev(13, "practice_reset", 1600, {"action": 0x0C400201}),
        ctx(level=6))
    assert len(closed2) == 1, "second reset must record a row (was the bug)"
    assert closed2[0].outcome == "reset" and closed2[0].rta_frames == 300
    assert e.armed_ids() == {1}, "segment must stay armed after second reset"
    assert [n["event"] for n in notices2
            if n["event"] in ("segment_armed", "segment_disarmed")] == []

    # success end — timed from the second reset at 1600
    closed3, _ = e.feed(
        jev(14, "level_changed", 1800, {"from": 6, "to": 17}),
        ctx(level=17, prev_level=6))
    assert len(closed3) == 1
    assert closed3[0].outcome == "success" and closed3[0].rta_frames == 200


def test_anchor_continuation_emits_no_notices():
    """The closing anchor (a real practice_reset) must produce zero
    segment_armed / segment_disarmed notices — it is an attempt boundary,
    not a state change."""
    e = SegmentEngine([LBLJ])
    lblj_arm(e, jid=10, frame=1000)
    _, notices = e.feed(
        jev(11, "practice_reset", 1300, {"action": 0x0C400201}),
        ctx(level=6))
    state_notices = [n["event"] for n in notices
                     if n["event"] in ("segment_armed", "segment_disarmed")]
    assert state_notices == []


def test_afk_anchor_rebases_without_row():
    """AFK discard (paused_frames_before >= 150): no row recorded, but the
    segment is re-armed at the AFK anchor frame.  A subsequent end trigger
    times from the AFK anchor, not the original arm."""
    e = SegmentEngine([LBLJ])
    lblj_arm(e, jid=10, frame=1000)
    # AFK anchor at 1500 (200 paused frames) — no row, still armed
    closed_afk, _ = e.feed(
        jev(11, "practice_reset", 1500,
            {"paused_frames_before": 200, "action": 0x0C400201}),
        ctx(level=6))
    assert closed_afk == [], "AFK anchor must not record a row"
    assert e.armed_ids() == {1}, "segment must stay armed after AFK anchor"
    # success end — timed from the AFK anchor at 1500, not the original arm
    closed, _ = e.feed(
        jev(12, "level_changed", 1700, {"from": 6, "to": 17}),
        ctx(level=17, prev_level=6))
    assert len(closed) == 1
    assert closed[0].outcome == "success" and closed[0].rta_frames == 200


# ---------------------------------------------------------------------------
# Area-scoped attempt_anchor (warp-menu arming, live gate 2026-06-12)
# The Usamune warp menu (06 01 00) deposits Mario at the castle lobby
# entrance — equivalent to the grounds→lobby door — emitting only a
# practice_reset (menu pause → warp → IGT reset; NO level edge), so a
# level_enter-only LBLJ never arms.  The anchor gains an optional "area"
# param; area scoping prevents cross-arming (a basement respawn must not
# arm a lobby-anchored segment).
# ---------------------------------------------------------------------------

LBLJ_V5 = SegmentDef(
    id=1, name="LBLJ", enabled=True,
    start_triggers=[{"type": "level_enter", "to": 6, "from": 16},
                    {"type": "attempt_anchor", "level": 6, "area": 1}],
    end_triggers=[{"type": "level_enter", "to": 17}], guards=[])


def test_area_scoped_anchor_arms_when_tracked_area_matches():
    """Warp-menu deposit: practice_reset with ctx(level=6, area=1) — the
    lobby-scoped anchor must arm LBLJ."""
    e = SegmentEngine([LBLJ_V5])
    closed, notices = e.feed(
        jev(10, "practice_reset", 1000, {"action": 0x0C400201}),
        ctx(level=6, area=1))
    assert closed == []
    assert e.armed_ids() == {1}
    assert [n["event"] for n in notices] == ["segment_armed"]


def test_area_scoped_anchor_does_not_arm_in_other_area():
    """Basement guard: ctx(level=6, area=3) must NOT arm the lobby-anchored
    segment — area scoping prevents cross-arming."""
    e = SegmentEngine([LBLJ_V5])
    e.feed(jev(10, "practice_reset", 1000, {"action": 0x0C400201}),
           ctx(level=6, area=3))
    assert e.armed_ids() == set()


def test_area_scoped_anchor_unknown_area_does_not_arm():
    """Legacy journals (no area events): ctx.area is None — the scoped
    anchor conservatively does not arm."""
    e = SegmentEngine([LBLJ_V5])
    e.feed(jev(10, "practice_reset", 1000, {"action": 0x0C400201}),
           ctx(level=6))
    assert e.armed_ids() == set()


def test_anchor_without_area_param_matches_any_area():
    """Compat: an attempt_anchor WITHOUT the area param (all other seeds)
    keeps matching regardless of ctx.area."""
    e = SegmentEngine([PIPE])
    e.feed(jev(20, "practice_reset", 2000, {"action": 0x0C400201}),
           ctx(level=17, area=2))
    assert e.armed_ids() == {5}


# ---------------------------------------------------------------------------
# Menu-warp pause gate (live-gate amendment 2026-06-12)
# Usamune menu warps (e.g. 06-01-00) cross areas and emit an area_changed
# co-frame with their anchor.  The transition-echo guard would previously
# classify the anchor as a load echo (ev.frame == _last_transition_frame),
# keeping the segment armed with a STALE start_frame — so success rta was
# measured from the original arm minutes earlier.
#
# Discriminator (journal-proven): menu warps pass through the pause menu —
# paused_frames_before 13/18/29/890 observed in live logs.  Walked load
# echoes (level entries, area doors) carry 0-3.  A deliberate menu action
# is never an involuntary load echo.
#
# Fix: the transition-co-frame shape only suppresses if
# paused_frames_before <= _MENU_PAUSE_FRAMES (5).  Above that threshold the
# anchor is REAL → close the stale attempt + re-arm at the warp frame.
# ---------------------------------------------------------------------------

def test_menu_warp_across_areas_rebases_the_attempt():
    """THE REGRESSION: LBLJ armed via level_changed 16→6 @1000 (+co-frame echo
    @1000 paused 3 — stays echo); walked door area_changed 1→3 @1500 + echo
    @1500 paused 2 (still echo, segment stays armed at start_frame 1000);
    then THE MENU WARP: area_changed 3→1 @2000 + practice_reset @2000
    paused_frames_before 18 — co-frame but paused > 5 → REAL anchor → closes
    the stale attempt (reset row, rta 1000) AND re-arms at 2000;
    level_changed 6→17 @2100 → success rta 100 (NOT 1100).

    Red before fix: transition-echo guard eats the warp anchor as a load echo,
    success rta is 1100 (measured from original arm at 1000)."""
    e = SegmentEngine([LBLJ])
    # arm via castle grounds → lobby transition @1000
    e.feed(jev(10, "level_changed", 1000, {"from": 16, "to": 6}),
           ctx(level=6, prev_level=16))
    # co-frame load echo at arm tick (paused 3 — walked entry) — stays echo
    e.feed(jev(11, "practice_reset", 1000, {"paused_frames_before": 3,
                                             "igt_frames_before": 64}),
           ctx(level=6))
    assert e.armed_ids() == {1}
    # walked area door @1500 — echo, segment stays armed at 1000
    e.feed(jev(12, "area_changed", 1500, {"level": 6, "from": 1, "to": 3}),
           ctx(level=6, area=3))
    e.feed(jev(13, "practice_reset", 1500, {"paused_frames_before": 2,
                                             "igt_frames_before": 30}),
           ctx(level=6))
    assert e.armed_ids() == {1}
    # menu warp: area_changed 3→1 @2000 (sets _last_transition_frame=2000)
    e.feed(jev(14, "area_changed", 2000, {"level": 6, "from": 3, "to": 1}),
           ctx(level=6, area=1))
    # anchor @2000 — co-frame, but paused_frames_before 18 > 5 → REAL
    closed, _ = e.feed(jev(15, "practice_reset", 2000, {"paused_frames_before": 18,
                                                          "action": 0x0C400201}),
                       ctx(level=6))
    # must close stale attempt as reset with rta 1000 (2000 - 1000)
    assert len(closed) == 1, f"expected 1 closed attempt, got {len(closed)}"
    assert closed[0].outcome == "reset"
    assert closed[0].rta_frames == 1000
    # segment re-armed at the warp frame 2000
    assert e.armed_ids() == {1}
    assert e._armed[1].start_frame == 2000
    # success times from the warp, not the original arm
    closed2, _ = e.feed(jev(16, "level_changed", 2100, {"from": 6, "to": 17}),
                         ctx(level=17, prev_level=6))
    assert len(closed2) == 1
    assert closed2[0].outcome == "success"
    assert closed2[0].rta_frames == 100


def test_long_menu_warp_rebases_without_row():
    """AFK-length pause during menu warp (paused_frames_before 890 — user
    sat in the menu): no reset row (AFK discard), but segment re-arms at
    the warp frame 2000.  Success times from 2000."""
    e = SegmentEngine([LBLJ])
    lblj_arm(e, jid=10, frame=1000)
    # area_changed sets _last_transition_frame = 2000
    e.feed(jev(11, "area_changed", 2000, {"level": 6, "from": 1, "to": 3}),
           ctx(level=6, area=3))
    # warp anchor co-frame but paused 890 → REAL, AFK → discard (no row)
    closed, _ = e.feed(jev(12, "practice_reset", 2000, {"paused_frames_before": 890,
                                                          "action": 0x0C400201}),
                       ctx(level=6))
    assert closed == [], "AFK-level menu warp must not record a row"
    assert e.armed_ids() == {1}
    assert e._armed[1].start_frame == 2000
    # success times from the warp
    closed2, _ = e.feed(jev(13, "level_changed", 2100, {"from": 6, "to": 17}),
                         ctx(level=17, prev_level=6))
    assert len(closed2) == 1
    assert closed2[0].rta_frames == 100


def test_walked_area_door_with_pause_buffer_stays_echo():
    """Door context (prev_action 0x1321) outranks the pause gate: even with
    paused_frames_before 40 (above _MENU_PAUSE_FRAMES) a door-action anchor
    stays echo.  Segment must remain armed at original start_frame."""
    e = SegmentEngine([LBLJ])
    lblj_arm(e, jid=10, frame=1000)
    # NOT a co-frame reset — different frame, so _last_transition_frame guard
    # is not active.  The intra-area door echo guard (shape c) handles this:
    # prev_action in DOOR_ACTIONS → echo regardless of pause.
    closed, _ = e.feed(
        jev(11, "practice_reset", 1200,
            {"prev_action": 0x1321, "action": 0x00001322,
             "paused_frames_before": 40}),
        ctx(level=6))
    assert closed == [], "door-context anchor must stay echo despite large pause"
    assert e.armed_ids() == {1}
    assert e._armed[1].start_frame == 1000


def test_arm_frame_echo_immune_to_pause():
    """Shape (a) arm-frame echo: co-frame anchor at the same tick as the arm
    must be suppressed UNCONDITIONALLY, even with paused_frames_before 800
    (player was paused on the grounds before entering the lobby).
    No row, stays armed at 3000."""
    e = SegmentEngine([LBLJ])
    e.feed(jev(10, "level_changed", 3000, {"from": 16, "to": 6}),
           ctx(level=6, prev_level=16))
    # co-frame echo at arm tick — large pause, but still a load echo
    closed, _ = e.feed(jev(11, "practice_reset", 3000,
                            {"paused_frames_before": 800,
                             "igt_frames_before": 64}),
                       ctx(level=6))
    assert closed == [], "arm-frame echo must be suppressed regardless of pause"
    assert e.armed_ids() == {1}
    assert e._armed[1].start_frame == 3000
