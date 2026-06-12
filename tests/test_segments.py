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


def ctx(level=None, prev_level=None, num_stars=None):
    return MatchContext(level=level, prev_level=prev_level,
                        num_stars=num_stars)


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
