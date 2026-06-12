from sm64_events.storage.db import EventRow
from sm64_events.tracking.projection import Projector, cleared_ids, project, replay

W = "2026-06-10T12:00:00Z"


def jev(id, type, frame, payload=None, session_id=1):
    return EventRow(id=id, session_id=session_id, seq=id, type=type,
                    frame=frame, wall_time_utc=W, payload=payload or {})


def star(id, frame, course=2, star_id=2, igt=343):
    return jev(id, "star_collected", frame,
               {"course_id": course, "star_id": star_id, "igt_frames": igt})


def test_anchor_then_grab_is_a_success_attempt_with_both_clocks():
    attempts = project([
        jev(1, "practice_reset", 1000, {"igt_frames_before": 0}),
        star(2, 1350, igt=343),
    ])
    assert len(attempts) == 1
    a = attempts[0]
    assert a.id == 1 and a.outcome == "success"
    assert a.anchor_type == "practice_reset" and a.anchor_frame == 1000
    assert a.course_id == 2 and a.star_id == 2
    assert a.igt_frames == 343 and a.rta_frames == 350
    assert a.cleared is False


def test_data_wiped_suppresses_prior_matches_only():
    attempts = project([
        jev(1, "practice_reset", 1000, {"igt_frames_before": 0}),
        star(2, 1350),                                   # success on (2,2)
        jev(3, "practice_reset", 2000, {"igt_frames_before": 0, "mario_acted": True}),
        star(4, 2400, course=8, star_id=1, igt=500),     # success on (8,1)
        jev(5, "data_wiped", 0, {"kind": "star", "course_id": 2, "star_id": 2,
                                 "segment_id": None, "session_id": None}),
        jev(6, "practice_reset", 3000, {"igt_frames_before": 0}),
        star(7, 3400),                                   # fresh (2,2) AFTER the wipe
    ])
    keys = [(a.course_id, a.star_id) for a in attempts]
    assert (8, 1) in keys                  # other star untouched
    assert keys.count((2, 2)) == 1         # pre-wipe row gone, post-wipe row stays
    assert attempts[-1].id == 6


def test_data_wiped_session_scope_spares_other_sessions():
    attempts = project([
        jev(1, "star_collected", 900,
            {"course_id": 2, "star_id": 2, "igt_frames": 343}, session_id=1),
        jev(2, "star_collected", 1900,
            {"course_id": 2, "star_id": 2, "igt_frames": 350}, session_id=2),
        jev(3, "data_wiped", 0, {"kind": "star", "course_id": 2, "star_id": 2,
                                 "segment_id": None, "session_id": 2}),
    ])
    assert [a.session_id for a in attempts] == [1]


def test_data_wiped_all_kind_wipes_everything_in_scope():
    attempts = project([
        jev(1, "practice_reset", 1000, {"igt_frames_before": 0}, session_id=1),
        jev(2, "practice_reset", 1500,
            {"igt_frames_before": 480, "mario_acted": True}, session_id=1),  # unassigned reset
        jev(3, "star_collected", 2000,
            {"course_id": 2, "star_id": 2, "igt_frames": 343}, session_id=1),
        jev(4, "star_collected", 2900,
            {"course_id": 2, "star_id": 2, "igt_frames": 350}, session_id=2),
        jev(5, "data_wiped", 0, {"kind": "all", "course_id": None,
                                 "star_id": None, "segment_id": None,
                                 "session_id": 1}),
    ])
    assert [a.session_id for a in attempts] == [2]   # unassigned + star of s1 gone


def test_grab_without_anchor_is_a_grab_only_attempt():
    attempts = project([star(5, 2000)])
    a = attempts[0]
    assert a.id == 5 and a.anchor_type == "none"
    assert a.anchor_frame is None and a.rta_frames is None
    assert a.igt_frames == 343 and a.outcome == "success"


def test_new_anchor_closes_open_attempt_as_reset_failure():
    attempts = project([
        star(1, 900),                                          # sets target
        jev(2, "practice_reset", 1000, {"igt_frames_before": 0, "mario_acted": True}),
        jev(3, "practice_reset", 1400, {"igt_frames_before": 380, "mario_acted": True}),
    ])
    assert len(attempts) == 2
    fail = attempts[1]
    assert fail.id == 2 and fail.outcome == "reset"
    assert fail.course_id == 2 and fail.star_id == 2     # attributed to target
    assert fail.igt_frames == 380                        # duration before reset
    assert fail.rta_frames == 400


def test_state_loaded_anchor_gives_rta_clock():
    attempts = project([
        jev(1, "state_loaded", 3000, {"igt_frames_restored": 120}),
        star(2, 3360),
    ])
    assert attempts[0].anchor_type == "state_loaded"
    assert attempts[0].rta_frames == 360


def test_failure_without_any_target_has_null_identity():
    attempts = project([
        jev(1, "practice_reset", 1000, {"igt_frames_before": 0}),
        jev(2, "practice_reset", 1500, {"igt_frames_before": 480}),
    ])
    assert attempts[0].course_id is None and attempts[0].star_id is None


def test_game_reset_closes_as_hard_reset():
    attempts = project([
        jev(1, "practice_reset", 1000, {"igt_frames_before": 0}),
        jev(2, "game_reset", 50),
    ])
    assert attempts[0].outcome == "hard_reset"
    assert attempts[0].igt_frames is None
    assert attempts[0].rta_frames is None   # frame went backward: no delta


def test_session_started_closes_open_attempt_as_abandoned():
    attempts = project([
        jev(1, "practice_reset", 1000, {"igt_frames_before": 0}),
        jev(2, "session_started", 0, {"session_id": 2}),
    ])
    assert attempts[0].outcome == "abandoned"


def test_target_set_command_overrides_attribution():
    attempts = project([
        jev(1, "target_set", 0, {"course_id": 8, "star_id": 2, "strat_tag": "carpetless"}),
        jev(2, "practice_reset", 1000, {"igt_frames_before": 0, "mario_acted": True}),
        jev(3, "practice_reset", 1400, {"igt_frames_before": 380, "mario_acted": True}),
    ])
    a = attempts[0]
    assert (a.course_id, a.star_id, a.strat_tag) == (8, 2, "carpetless")


def test_strat_memory_is_per_star_not_global():
    attempts = project([
        jev(1, "target_set", 0, {"course_id": 8, "star_id": 2, "strat_tag": "carpetless"}),
        star(2, 900, course=2, star_id=2),                     # grab WF: target moves
        jev(3, "practice_reset", 1000, {"igt_frames_before": 0, "mario_acted": True}),
        jev(4, "practice_reset", 1400, {"igt_frames_before": 380, "mario_acted": True}),
    ])
    grab = attempts[0]
    fail = attempts[1]
    assert grab.strat_tag is None        # WF has no remembered strat
    assert fail.strat_tag is None        # failures follow WF's memory, not SSL's


def test_cleared_grab_does_not_move_target_retroactively():
    # going for SSL (8,2); accidentally grab WF (2,2); failures follow;
    # then the WF grab is marked a mistake -> failures re-attribute to SSL.
    events = [
        jev(1, "target_set", 0, {"course_id": 8, "star_id": 2}),
        star(2, 900, course=2, star_id=2),                     # accidental
        jev(3, "practice_reset", 1000, {"igt_frames_before": 0, "mario_acted": True}),
        jev(4, "practice_reset", 1400, {"igt_frames_before": 380, "mario_acted": True}),
        jev(5, "attempt_cleared", 0, {"attempt_id": 2, "reason": "accidental"}),
    ]
    attempts = project(events)
    grab = next(a for a in attempts if a.id == 2)
    fail = next(a for a in attempts if a.id == 3)
    assert grab.cleared is True and grab.cleared_reason == "accidental"
    assert grab.course_id == 2                  # the grab itself keeps its star
    assert (fail.course_id, fail.star_id) == (8, 2)   # re-attributed
    # restore flips it back
    attempts2 = project(events + [jev(6, "attempt_restored", 0, {"attempt_id": 2})])
    fail2 = next(a for a in attempts2 if a.id == 3)
    assert next(a for a in attempts2 if a.id == 2).cleared is False
    assert (fail2.course_id, fail2.star_id) == (2, 2)


def test_unknown_and_derived_event_types_are_ignored():
    attempts = project([
        jev(1, "emulator_connected", 0),
        jev(2, "attempt_completed", 0, {"attempt_id": 99}),
        jev(3, "level_changed", 0, {"from": 1, "to": 2}),  # _open is None -> no-op
    ])
    assert attempts == []


def test_cleared_ids_last_action_wins():
    events = [
        jev(1, "attempt_cleared", 0, {"attempt_id": 7, "reason": "oops"}),
        jev(2, "attempt_restored", 0, {"attempt_id": 7}),
        jev(3, "attempt_cleared", 0, {"attempt_id": 9, "reason": "accidental"}),
    ]
    assert cleared_ids(events) == {9: "accidental"}


def test_replay_returns_end_state_projector():
    attempts, proj = replay([
        star(1, 900),
        jev(2, "practice_reset", 1000, {"igt_frames_before": 0, "mario_acted": True}),
    ])
    assert len(attempts) == 1            # the grab closed; the reset is open
    assert isinstance(proj, Projector)
    assert proj.target == ("star", 2, 2)
    more = proj.feed(star(3, 1300))
    assert len(more) == 1 and more[0].id == 2 and more[0].outcome == "success"


def test_reset_spam_then_grab_uses_last_anchor():
    attempts = project([
        jev(1, "practice_reset", 1000, {"igt_frames_before": 0, "mario_acted": True}),
        jev(2, "practice_reset", 1400, {"igt_frames_before": 380, "mario_acted": True}),
        star(3, 1500, igt=95),
    ])
    assert [a.outcome for a in attempts] == ["reset", "success"]
    win = attempts[1]
    assert win.id == 2 and win.anchor_frame == 1400
    assert win.rta_frames == 100 and win.igt_frames == 95


def test_grab_during_open_attempt_records_grabbed_star_not_target():
    attempts = project([
        jev(1, "target_set", 0, {"course_id": 8, "star_id": 2}),
        jev(2, "practice_reset", 1000, {"igt_frames_before": 0, "mario_acted": True}),
        star(3, 1350, course=2, star_id=2),
    ])
    [a] = attempts
    assert a.id == 2 and (a.course_id, a.star_id) == (2, 2)


def test_clearing_a_failure_attempt_only_flags_it():
    attempts = project([
        star(1, 900),
        jev(2, "practice_reset", 1000, {"igt_frames_before": 0, "mario_acted": True}),
        jev(3, "practice_reset", 1400, {"igt_frames_before": 380, "mario_acted": True}),
        jev(4, "attempt_cleared", 0, {"attempt_id": 2, "reason": "warmup"}),
    ])
    fail = next(a for a in attempts if a.id == 2)
    assert fail.cleared is True and fail.outcome == "reset"
    assert (fail.course_id, fail.star_id) == (2, 2)  # attribution unchanged


def test_same_tick_reset_race_row_is_pinned():
    # Documented caveat: rta ~0 while igt carries the prior attempt's
    # reconstructed time. Consumers prefer igt for such rows.
    attempts = project([
        jev(1, "practice_reset", 1400, {"igt_frames_before": 380, "mario_acted": True}),
        star(2, 1405, igt=380),
    ])
    [a] = attempts
    assert a.outcome == "success" and a.rta_frames == 5 and a.igt_frames == 380


def test_strat_memory_per_star_set_clear_and_recall():
    _, proj = replay([
        jev(1, "target_set", 0, {"course_id": 8, "star_id": 2, "strat_tag": "x"}),
        jev(2, "target_set", 0, {"course_id": 8, "star_id": 3}),
    ])
    assert proj.strat_tag is None                      # (8,3) has no memory
    assert proj.strat_by_star[(8, 2)] == "x"           # (8,2) remembers
    _, proj2 = replay([
        jev(1, "target_set", 0, {"course_id": 8, "star_id": 2, "strat_tag": "x"}),
        jev(2, "target_set", 0, {"course_id": 8, "star_id": 3, "strat_tag": "owlless"}),
        jev(3, "target_set", 0, {"course_id": 8, "star_id": 2}),
    ])
    assert proj2.strat_tag == "x"                      # recalled on return
    _, proj3 = replay([
        jev(1, "target_set", 0, {"course_id": 8, "star_id": 2, "strat_tag": "x"}),
        jev(2, "target_set", 0, {"course_id": 8, "star_id": 2, "strat_tag": None}),
    ])
    assert proj3.strat_tag is None                     # explicit null clears


def test_death_closes_attempt_with_cause_and_igt():
    attempts = project([
        jev(1, "practice_reset", 1000, {"igt_frames_before": 0, "mario_acted": True}),
        jev(2, "death", 1300, {"cause": "drowning", "igt_frames": 290, "level": 9}),
    ])
    [a] = attempts
    assert a.outcome == "death" and a.outcome_detail == "drowning"
    assert a.igt_frames == 290 and a.rta_frames == 300
    assert a.id == 1


def test_death_without_anchor_synthesizes_attempt():
    attempts = project([
        star(1, 900),    # sets target (2,2)
        jev(2, "death", 1500, {"cause": "standing", "igt_frames": 80, "level": 24}),
    ])
    death = attempts[1]
    assert death.id == 2 and death.anchor_type == "none"
    assert (death.course_id, death.star_id) == (2, 2)
    assert death.outcome == "death" and death.rta_frames is None


def test_level_change_closes_as_abandoned():
    attempts = project([
        star(1, 900),
        jev(2, "practice_reset", 1000, {"igt_frames_before": 0, "mario_acted": True}),
        jev(3, "level_changed", 1600, {"from": 24, "to": 6}),
    ])
    assert attempts[1].outcome == "abandoned"


def test_void_fall_death_then_level_exit_yields_one_death_attempt():
    # HMC pit fall: the pre-warp pulse fires the death BEFORE the level
    # unloads (death.py), so the spit-out's level_changed closes nothing —
    # one death attempt, no abandoned twin.
    attempts = project([
        jev(1, "target_set", 900, {"course_id": 6, "star_id": 1}),
        jev(2, "practice_reset", 1000, {"igt_frames_before": 0, "mario_acted": True}),
        jev(3, "death", 1450, {"cause": "fall", "igt_frames": 430, "level": 7}),
        jev(4, "level_changed", 1470, {"from": 7, "to": 6}),
    ])
    assert [a.outcome for a in attempts] == ["death"]
    assert attempts[0].outcome_detail == "fall"
    assert attempts[0].rta_frames == 450
    assert (attempts[0].course_id, attempts[0].star_id) == (6, 1)


def test_inactive_reset_closure_is_discarded_entirely():
    attempts = project([
        star(1, 900),
        jev(2, "practice_reset", 1000, {"igt_frames_before": 0, "mario_acted": True}),
        jev(3, "practice_reset", 1100, {"igt_frames_before": 90, "mario_acted": False}),
        star(4, 1400, igt=95),
    ])
    # the attempt opened at 2 vanished (closed by an inactive reset);
    # the anchor at 3 opened the attempt the grab closes.
    assert [a.outcome for a in attempts] == ["success", "success"]
    assert attempts[1].id == 3


def test_old_journal_without_mario_acted_treats_resets_as_acted():
    attempts = project([
        star(1, 900),
        jev(2, "practice_reset", 1000, {"igt_frames_before": 0}),
        jev(3, "practice_reset", 1400, {"igt_frames_before": 380}),
    ])
    assert attempts[1].outcome == "reset"   # rebuild-stable for old data


# -- rollout attachment (Phase 2) --------------------------------------------

def rollout(id, frame, dustless):
    return jev(id, "rollout", frame,
               {"dustless": dustless, "frames_late": 0 if dustless else 2,
                "level": 24})


def test_rollouts_attach_to_the_attempt_open_when_they_happen():
    attempts = project([
        jev(1, "practice_reset", 1000, {"igt_frames_before": 0}),
        rollout(2, 1100, True),
        rollout(3, 1200, False),
        rollout(4, 1250, True),
        star(5, 1350),
    ])
    a = attempts[0]
    assert a.rollouts_total == 3 and a.rollouts_dustless == 2


def test_rollout_counts_reset_between_attempts():
    attempts = project([
        jev(1, "practice_reset", 1000, {"igt_frames_before": 0}),
        rollout(2, 1100, True),
        jev(3, "practice_reset", 1400, {"igt_frames_before": 380}),
        star(4, 1700),
    ])
    first, second = attempts
    assert first.rollouts_total == 1 and first.rollouts_dustless == 1
    assert second.rollouts_total == 0


def test_rollouts_attach_to_grab_only_attempt():
    attempts = project([rollout(1, 800, False), star(2, 900)])
    assert attempts[0].rollouts_total == 1
    assert attempts[0].rollouts_dustless == 0


def test_rollouts_attach_to_death_closed_attempt():
    attempts = project([
        jev(1, "practice_reset", 1000, {"igt_frames_before": 0}),
        rollout(2, 1100, False),
        jev(3, "death", 1300, {"cause": "standing", "igt_frames": 290}),
    ])
    assert attempts[0].outcome == "death"
    assert attempts[0].rollouts_total == 1
    assert attempts[0].rollouts_dustless == 0


def test_context_breaks_drop_ambient_rollouts():
    # rollout in the idle gap, then a level change: must not leak into the
    # next attempt
    attempts = project([
        rollout(1, 700, True),
        jev(2, "level_changed", 750, {"from": 24, "to": 8}),
        jev(3, "practice_reset", 1000, {"igt_frames_before": 0}),
        star(4, 1350),
    ])
    assert attempts[0].rollouts_total == 0


def test_discarded_noop_reset_drops_its_rollouts():
    attempts = project([
        jev(1, "practice_reset", 1000, {"igt_frames_before": 0}),
        rollout(2, 1100, True),
        jev(3, "practice_reset", 1400,
            {"igt_frames_before": 380, "mario_acted": False}),
        star(4, 1700),
    ])
    # the no-op-closed attempt vanished; its rollout must not attach to the grab
    assert len(attempts) == 1
    assert attempts[0].rollouts_total == 0


# -- jump counts + corrected rollout semantics (Phase 2 fix round) ------------

def jump(id, frame, dustless, kind="double"):
    fl = 0 if dustless else 1
    return jev(id, "jump", frame,
               {"dustless": dustless, "frames_late": fl,
                "landing_frames": fl + 1, "kind": kind, "level": 24})


def new_rollout(id, frame, dustless):
    fl = 0 if dustless else 1
    return jev(id, "rollout", frame,
               {"dustless": dustless, "frames_late": fl,
                "landing_frames": fl + 1, "level": 24})


def test_jumps_attach_to_the_open_attempt():
    attempts = project([
        jev(1, "practice_reset", 1000, {"igt_frames_before": 0}),
        jump(2, 1100, True, kind="double"),
        jump(3, 1150, False, kind="triple"),
        star(4, 1350),
    ])
    a = attempts[0]
    assert a.jumps_total == 2 and a.jumps_dustless == 1
    assert a.rollouts_total == 0


def test_jump_counts_reset_between_attempts():
    attempts = project([
        jev(1, "practice_reset", 1000, {"igt_frames_before": 0}),
        jump(2, 1100, True),
        jev(3, "practice_reset", 1400, {"igt_frames_before": 380}),
        star(4, 1700),
    ])
    first, second = attempts
    assert first.jumps_total == 1 and second.jumps_total == 0


def test_old_journal_rollout_one_frame_late_reprojects_as_dustless():
    # pre-landing_frames journals counted visible slide frames as
    # frames_late: 1 visible frame IS frame perfect (the live 50-trial
    # session that exposed the bug). Replay must fix the classification.
    attempts = project([
        jev(1, "practice_reset", 1000, {"igt_frames_before": 0}),
        jev(2, "rollout", 1100,
            {"dustless": False, "frames_late": 1, "level": 24}),  # old style
        jev(3, "rollout", 1200,
            {"dustless": False, "frames_late": 2, "level": 24}),  # truly late
        star(4, 1350),
    ])
    a = attempts[0]
    assert a.rollouts_total == 2
    assert a.rollouts_dustless == 1


def test_new_journal_rollout_one_late_stays_dusty():
    # new-style payloads carry landing_frames and are trusted verbatim
    attempts = project([
        jev(1, "practice_reset", 1000, {"igt_frames_before": 0}),
        new_rollout(2, 1100, False),   # frames_late=1, landing_frames=2
        new_rollout(3, 1200, True),
        star(4, 1350),
    ])
    a = attempts[0]
    assert a.rollouts_total == 2
    assert a.rollouts_dustless == 1


# -- AFK pause discard (spec §1) ----------------------------------------------

def test_pause_then_reset_discards_closed_attempt():
    attempts = project([
        star(1, 900),
        jev(2, "practice_reset", 1000, {"igt_frames_before": 0, "mario_acted": True}),
        jev(3, "practice_reset", 1600,
            {"igt_frames_before": 380, "mario_acted": True,
             "paused_frames_before": 150}),
        star(4, 1900, igt=95),
    ])
    # the attempt opened at 2 vanished (closed after a >=5 s pause);
    # the anchor at 3 still opened the attempt the grab closes.
    assert [a.outcome for a in attempts] == ["success", "success"]
    assert attempts[1].id == 3


def test_pause_below_threshold_keeps_reset():
    attempts = project([
        star(1, 900),
        jev(2, "practice_reset", 1000, {"igt_frames_before": 0, "mario_acted": True}),
        jev(3, "practice_reset", 1600,
            {"igt_frames_before": 380, "mario_acted": True,
             "paused_frames_before": 149}),
    ])
    assert attempts[1].outcome == "reset"


def test_pause_discard_applies_to_state_loaded_closures():
    attempts = project([
        star(1, 900),
        jev(2, "practice_reset", 1000, {"igt_frames_before": 0, "mario_acted": True}),
        jev(3, "state_loaded", 800,
            {"igt_frames_restored": 120, "mario_acted": True,
             "paused_frames_before": 300}),
        star(4, 1100, igt=95),
    ])
    assert [a.outcome for a in attempts] == ["success", "success"]
    assert attempts[1].id == 3


# -- activity rule for all closure types (spec §2) ------------------------------

def tracking_anchor(id, frame, igt_before=0):
    """Anchor as the NEW detector emits it (acted_tracking marker)."""
    return jev(id, "practice_reset", frame,
               {"igt_frames_before": igt_before, "mario_acted": False,
                "acted_tracking": True, "paused_frames_before": 0})


def test_unacted_death_is_discarded_for_tracking_anchors():
    attempts = project([
        star(1, 900),
        tracking_anchor(2, 1000),
        jev(3, "death", 1300, {"cause": "quicksand", "igt_frames": 290}),
    ])
    assert [a.outcome for a in attempts] == ["success"]


def test_acted_event_keeps_death():
    attempts = project([
        star(1, 900),
        tracking_anchor(2, 1000),
        jev(3, "mario_acted", 1100),
        jev(4, "death", 1300, {"cause": "quicksand", "igt_frames": 290}),
    ])
    assert attempts[1].outcome == "death"
    assert attempts[1].id == 2


def test_unacted_abandon_is_discarded_for_tracking_anchors():
    attempts = project([
        star(1, 900),
        tracking_anchor(2, 1000),
        jev(3, "level_changed", 1600, {"from": 24, "to": 6}),
    ])
    assert [a.outcome for a in attempts] == ["success"]


def test_unacted_hard_reset_is_discarded_for_tracking_anchors():
    attempts = project([
        star(1, 900),
        tracking_anchor(2, 1000),
        jev(3, "game_reset", 50),
    ])
    assert [a.outcome for a in attempts] == ["success"]


def test_unacted_reset_closure_uses_event_not_closer_payload():
    # closer claims mario_acted True, but the OPENING anchor tracks events
    # and none arrived -> still dropped (event-based rule wins).
    attempts = project([
        star(1, 900),
        tracking_anchor(2, 1000),
        jev(3, "practice_reset", 1400,
            {"igt_frames_before": 380, "mario_acted": True,
             "acted_tracking": True, "paused_frames_before": 0}),
    ])
    assert [a.outcome for a in attempts] == ["success"]


def test_success_is_never_discarded():
    attempts = project([
        tracking_anchor(1, 1000),
        star(2, 1350),                       # no mario_acted event, still counts
    ])
    assert attempts[0].outcome == "success"


def test_acted_state_resets_per_attempt():
    attempts = project([
        star(1, 900),
        tracking_anchor(2, 1000),
        jev(3, "mario_acted", 1100),
        jev(4, "death", 1300, {"cause": "standing", "igt_frames": 250}),  # kept
        tracking_anchor(5, 1400),
        jev(6, "death", 1700, {"cause": "standing", "igt_frames": 250}),  # dropped
    ])
    assert [a.outcome for a in attempts] == ["success", "death"]


def test_legacy_anchor_death_closure_is_kept():
    # old journals have no acted_tracking marker and no mario_acted events:
    # death/abandon closures keep today's semantics (always counted).
    attempts = project([
        star(1, 900),
        jev(2, "practice_reset", 1000, {"igt_frames_before": 0}),
        jev(3, "death", 1300, {"cause": "standing", "igt_frames": 290}),
    ])
    assert attempts[1].outcome == "death"


def test_afk_discarded_attempt_drops_its_rollouts():
    # twin of test_discarded_noop_reset_drops_its_rollouts for the AFK path:
    # a rollout inside an AFK-discarded attempt must not leak into the grab.
    attempts = project([
        jev(1, "practice_reset", 1000, {"igt_frames_before": 0}),
        jev(2, "rollout", 1100, {"dustless": True, "frames_late": 0, "level": 24}),
        jev(3, "practice_reset", 1600,
            {"igt_frames_before": 380, "mario_acted": True,
             "paused_frames_before": 200}),
        star(4, 1900, igt=95),
    ])
    assert len(attempts) == 1
    assert attempts[0].rollouts_total == 0


def test_stray_acted_between_attempts_does_not_leak_into_next():
    # mario_acted with nothing open (castle movement after an abandon) must
    # not pre-mark the NEXT attempt as acted — the anchor re-arms the flag.
    attempts = project([
        tracking_anchor(1, 1000),
        jev(2, "mario_acted", 1100),
        jev(3, "level_changed", 1200, {"from": 24, "to": 6}),  # kept (acted)
        jev(4, "mario_acted", 1250),       # castle movement, nothing open
        tracking_anchor(5, 1400),
        jev(6, "death", 1700, {"cause": "standing", "igt_frames": 250}),
    ])
    assert [a.outcome for a in attempts] == ["abandoned"]  # death discarded


def test_state_loaded_tracking_anchor_is_judged_too():
    # the activity rule is anchor-type agnostic: savestate-load spam with
    # zero input is discarded the same as reset spam.
    attempts = project([
        star(1, 900),
        jev(2, "state_loaded", 3000,
            {"igt_frames_restored": 120, "mario_acted": False,
             "acted_tracking": True, "paused_frames_before": 0}),
        jev(3, "state_loaded", 2800,
            {"igt_frames_restored": 120, "mario_acted": False,
             "acted_tracking": True, "paused_frames_before": 0}),
    ])
    assert [a.outcome for a in attempts] == ["success"]


def test_mario_acted_is_not_a_rollout_boundary():
    # mario_acted must never zero the rollout accumulator — pin it, because
    # in live streams the latched event precedes the period's rollouts and
    # an accidental BOUNDARY_EVENT_TYPES addition would be near-invisible.
    attempts = project([
        jev(1, "practice_reset", 1000, {"igt_frames_before": 0}),
        jev(2, "rollout", 1100, {"dustless": True, "frames_late": 0, "level": 24}),
        jev(3, "mario_acted", 1150),
        star(4, 1350),
    ])
    assert attempts[0].rollouts_total == 1


# -- castle-opened attempts are never star attempts (addendum Task 3.5) ---------

def lvl(id, frame, from_, to):
    return jev(id, "level_changed", frame, {"from": from_, "to": to})


# the user's exact report: grab -> exit to castle -> enter next painting
def test_castle_period_after_stage_exit_is_not_a_reset_for_the_star():
    attempts = project([
        star(1, 900),
        lvl(2, 1000, 22, 6),                 # stage exit (same tick as anchor)
        jev(3, "practice_reset", 1000, {"igt_frames_before": 900, "mario_acted": True}),
        jev(4, "practice_reset", 1150, {"igt_frames_before": 148, "mario_acted": True}),  # painting entry
    ])
    assert [a.outcome for a in attempts] == ["success"]


def test_star_select_period_is_not_an_abandon_for_the_star():
    attempts = project([
        star(1, 900),
        lvl(2, 1000, 22, 6),
        jev(3, "practice_reset", 1000, {"igt_frames_before": 900, "mario_acted": True}),
        jev(4, "practice_reset", 1150, {"igt_frames_before": 148, "mario_acted": True}),
        lvl(5, 1250, 6, 22),                 # star select ends, course loads
    ])
    assert [a.outcome for a in attempts] == ["success"]


def test_attribution_resumes_for_in_level_anchors():
    attempts = project([
        star(1, 900),
        lvl(2, 1000, 22, 6),
        jev(3, "practice_reset", 1000, {"igt_frames_before": 900, "mario_acted": True}),
        lvl(4, 1250, 6, 22),
        jev(5, "practice_reset", 1300, {"igt_frames_before": 0, "mario_acted": True}),   # course load
        jev(6, "practice_reset", 1700, {"igt_frames_before": 380, "mario_acted": True}), # L-reset
    ])
    assert [a.outcome for a in attempts] == ["success", "reset"]
    assert attempts[1].id == 5 and attempts[1].course_id == 2


def test_exit_mid_attempt_is_still_abandoned_for_the_star():
    # opened in-level, closed by the exit's level_changed: judged by OPEN level
    attempts = project([
        star(1, 900),
        jev(2, "practice_reset", 1000, {"igt_frames_before": 0, "mario_acted": True}),
        lvl(3, 1600, 22, 6),
    ])
    assert attempts[1].outcome == "abandoned" and attempts[1].course_id == 2


def test_success_from_castle_anchor_still_counts():
    attempts = project([
        lvl(1, 900, 22, 6),
        jev(2, "practice_reset", 1000, {"igt_frames_before": 0, "mario_acted": True}),
        star(3, 1500),                       # Toad/MIPS-style grab
    ])
    assert attempts[0].outcome == "success"


def test_no_level_events_keeps_legacy_attribution():
    # pre-level-detector journals: _level unknown -> today's semantics
    attempts = project([
        star(1, 900),
        jev(2, "practice_reset", 1000, {"igt_frames_before": 0, "mario_acted": True}),
        jev(3, "practice_reset", 1400, {"igt_frames_before": 380, "mario_acted": True}),
    ])
    assert attempts[1].outcome == "reset" and attempts[1].course_id == 2


def test_castle_opened_death_is_discarded():
    attempts = project([
        star(1, 900),
        lvl(2, 1000, 22, 6),
        jev(3, "practice_reset", 1000, {"igt_frames_before": 900, "mario_acted": True}),
        jev(4, "death", 1300, {"cause": "standing", "igt_frames": 100}),
    ])
    assert [a.outcome for a in attempts] == ["success"]


def test_castle_discarded_attempt_drops_its_rollouts():
    # addendum §4: castle rollouts must not pollute the star's counts
    attempts = project([
        star(1, 900),
        lvl(2, 1000, 22, 6),
        jev(3, "practice_reset", 1000, {"igt_frames_before": 900, "mario_acted": True}),
        jev(4, "rollout", 1100, {"dustless": True, "frames_late": 0, "level": 6}),
        lvl(5, 1250, 6, 22),
        jev(6, "practice_reset", 1300, {"igt_frames_before": 0, "mario_acted": True}),
        star(7, 1700),
    ])
    assert [a.outcome for a in attempts] == ["success", "success"]
    assert attempts[1].rollouts_total == 0


def test_castle_state_loaded_anchor_is_flagged_too():
    attempts = project([
        star(1, 900),
        lvl(2, 1000, 22, 6),
        jev(3, "state_loaded", 3000,
            {"igt_frames_restored": 120, "mario_acted": True}),
        jev(4, "practice_reset", 3200, {"igt_frames_before": 100, "mario_acted": True}),
    ])
    assert [a.outcome for a in attempts] == ["success"]


def test_castle_opened_hard_reset_is_discarded():
    attempts = project([
        star(1, 900),
        lvl(2, 1000, 22, 6),
        jev(3, "practice_reset", 1000, {"igt_frames_before": 900, "mario_acted": True}),
        jev(4, "game_reset", 50),
    ])
    assert [a.outcome for a in attempts] == ["success"]


# -- strat_set event (per-star strategy without moving the target) ---------------

def test_strat_set_updates_memory_without_moving_target():
    _, proj = replay([
        jev(1, "target_set", 0, {"course_id": 8, "star_id": 2, "strat_tag": "x"}),
        jev(2, "strat_set", 0, {"course_id": 2, "star_id": 2, "strat_tag": "owlless"}),
    ])
    assert proj.target == ("star", 8, 2)              # unmoved
    assert proj.strat_by_star[(2, 2)] == "owlless"
    assert proj.strat_tag == "x"                      # target's own strat intact


def test_strat_set_attributes_future_closures():
    attempts = project([
        jev(1, "target_set", 0, {"course_id": 8, "star_id": 2, "strat_tag": "old"}),
        jev(2, "strat_set", 0, {"course_id": 8, "star_id": 2, "strat_tag": "new"}),
        jev(3, "practice_reset", 1000, {"igt_frames_before": 0, "mario_acted": True}),
        jev(4, "practice_reset", 1400, {"igt_frames_before": 380, "mario_acted": True}),
    ])
    assert attempts[0].strat_tag == "new"


def test_strat_set_null_clears_and_is_not_a_boundary():
    attempts = project([
        jev(1, "practice_reset", 1000, {"igt_frames_before": 0}),
        jev(2, "rollout", 1100, {"dustless": True, "frames_late": 0, "level": 24}),
        jev(3, "strat_set", 0, {"course_id": 2, "star_id": 2, "strat_tag": None}),
        star(4, 1350),
    ])
    assert attempts[0].rollouts_total == 1            # not zeroed by strat_set
    assert attempts[0].strat_tag is None


# -- tagged target identity + SegmentEngine wiring (segments plan Task 11) ------

def seg_defs():
    from sm64_events.tracking.segments import SegmentDef
    return [SegmentDef(id=1, name="LBLJ", enabled=True,
                       start_triggers=[{"type": "level_enter", "to": 6,
                                        "from": 16}],
                       end_triggers=[{"type": "level_enter", "to": 17}],
                       guards=[])]


def test_segment_success_is_projected_and_auto_follows_target():
    p = Projector(segments=seg_defs())
    p.feed(jev(1, "level_changed", 900, {"from": 16, "to": 16}))
    p.feed(jev(2, "level_changed", 1000, {"from": 16, "to": 6}))
    closed = p.feed(jev(3, "level_changed", 1085, {"from": 6, "to": 17}))
    segs = [a for a in closed if a.segment_id == 1]
    assert len(segs) == 1 and segs[0].outcome == "success"
    assert p.target == ("segment", 1)


def test_star_target_is_tagged_now():
    p = Projector()
    p.feed(jev(1, "target_set", 0, {"course_id": 2, "star_id": 2}))
    assert p.target == ("star", 2, 2)


def test_segment_target_set_event_round_trips():
    p = Projector()
    p.feed(jev(1, "target_set", 0, {"kind": "segment", "segment_id": 4}))
    assert p.target == ("segment", 4)


def test_cleared_segment_attempt_does_not_move_target():
    p = Projector(cleared={2 + 10**10 * 1: "mistake"}, segments=seg_defs())
    p.feed(jev(1, "target_set", 0, {"course_id": 2, "star_id": 2}))
    p.feed(jev(2, "level_changed", 1000, {"from": 16, "to": 6}))
    closed = p.feed(jev(3, "level_changed", 1100, {"from": 6, "to": 17}))
    assert closed[-1].cleared is True
    assert p.target == ("star", 2, 2)


def test_replay_signature_accepts_segments():
    from sm64_events.tracking.projection import replay
    attempts, projector = replay([
        jev(1, "level_changed", 1000, {"from": 16, "to": 6}),
        jev(2, "level_changed", 1100, {"from": 6, "to": 17}),
    ], segments=seg_defs())
    assert any(a.segment_id == 1 for a in attempts)


def test_grab_closing_star_and_segment_orders_star_first_and_target_follows_segment():
    from sm64_events.tracking.segments import SegmentDef
    b3 = SegmentDef(id=10, name="Bowser 3", enabled=True,
                    start_triggers=[{"type": "level_enter", "to": 34},
                                    {"type": "attempt_anchor", "level": 34}],
                    end_triggers=[{"type": "star_grabbed"}], guards=[])
    p = Projector(segments=[b3])
    p.feed(jev(1, "level_changed", 5000, {"from": 6, "to": 34}))
    p.feed(jev(2, "practice_reset", 5100, {"mario_acted": True}))
    closed = p.feed(jev(3, "star_collected", 6000,
                        {"course_id": 25, "star_id": 0, "igt_frames": 880}))
    assert [a.segment_id for a in closed] == [None, 10]   # star first, then segment
    assert closed[0].outcome == closed[1].outcome == "success"
    assert p.target == ("segment", 10)


def lblj_v5_defs():
    """Seeds-shaped LBLJ as of migration v5: level_enter PLUS the
    area-scoped attempt_anchor (warp-menu arming, 2026-06-12)."""
    from sm64_events.tracking.segments import SegmentDef
    return [SegmentDef(id=1, name="LBLJ", enabled=True,
                       start_triggers=[{"type": "level_enter", "to": 6,
                                        "from": 16},
                                       {"type": "attempt_anchor", "level": 6,
                                        "area": 1}],
                       end_triggers=[{"type": "level_enter", "to": 17}],
                       guards=[])]


def test_warp_menu_anchor_arms_lblj_via_tracked_area():
    """THE LIVE SCENARIO (warp-menu arming, 2026-06-12): the Usamune warp
    menu (06 01 00) deposits Mario at the castle lobby entrance — equivalent
    to the grounds→lobby door — emitting only a practice_reset (menu pause →
    warp → IGT reset; NO level edge).  The projector must track area from
    journaled area_changed payloads and pass it to the matcher so the
    area-scoped attempt_anchor arms LBLJ from idle; the next BitDW entry is
    a success timed from the anchor."""
    p = Projector(segments=lblj_v5_defs())
    # establishing events (server attach mid-lobby): level + area known,
    # from == to so nothing arms via level_enter
    p.feed(jev(1, "level_changed", 900, {"from": 6, "to": 6}))
    p.feed(jev(2, "area_changed", 900, {"level": 6, "from": 1, "to": 1}))
    # warp-menu deposit: a practice_reset with gameplay context (no level or
    # area edge on its frame, no door context — a real anchor, not an echo)
    p.feed(jev(3, "practice_reset", 1000,
               {"action": 0x0C400201, "mario_acted": True}))
    assert p.armed_segment_ids() == {1}, \
        "warp-menu practice_reset must arm LBLJ via attempt_anchor(6, area=1)"
    closed = p.feed(jev(4, "level_changed", 1100, {"from": 6, "to": 17}))
    segs = [a for a in closed if a.segment_id == 1]
    assert len(segs) == 1
    assert segs[0].outcome == "success" and segs[0].rta_frames == 100


def test_basement_respawn_does_not_arm_lobby_anchored_lblj():
    """Area guard: same shape but the tracked area is the basement (3) —
    the lobby-scoped anchor must NOT arm (cross-arming prevention)."""
    p = Projector(segments=lblj_v5_defs())
    p.feed(jev(1, "level_changed", 900, {"from": 6, "to": 6}))
    p.feed(jev(2, "area_changed", 900, {"level": 6, "from": 1, "to": 3}))
    p.feed(jev(3, "practice_reset", 1000,
               {"action": 0x0C400201, "mario_acted": True}))
    assert p.armed_segment_ids() == set()
    closed = p.feed(jev(4, "level_changed", 1100, {"from": 6, "to": 17}))
    assert [a for a in closed if a.segment_id == 1] == []


def test_game_reset_resets_star_count_knowledge_for_guards():
    from sm64_events.tracking.segments import SegmentDef
    guarded = SegmentDef(id=2, name="g", enabled=True,
                         start_triggers=[{"type": "level_enter", "to": 6}],
                         end_triggers=[{"type": "level_enter", "to": 17}],
                         guards=[{"type": "star_count_min", "n": 3}])
    p = Projector(segments=[guarded])
    closed = []
    closed += p.feed(jev(1, "star_collected", 900,
                         {"course_id": 2, "star_id": 1,
                          "igt_frames": 100, "num_stars": 5}))
    closed += p.feed(jev(2, "game_reset", 50, {}))
    closed += p.feed(jev(3, "level_changed", 1000, {"from": 16, "to": 6}))
    closed += p.feed(jev(4, "level_changed", 1100, {"from": 6, "to": 17}))
    closed += p.feed(jev(5, "level_changed", 1200, {"from": 17, "to": 6}))
    # num_stars unknown after hard reset -> guard conservatively fails ->
    # the def never armed -> no segment attempt anywhere
    assert all(a.segment_id != 2 for a in closed)
