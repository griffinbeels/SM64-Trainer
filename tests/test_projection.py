from sm64_events.storage.db import EventRow
from sm64_events.tracking.projection import (
    Projector, cleared_ids, project, replay, strat_overrides)

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
    # The grabbed star and the level must name the SAME course now that
    # re-entry checks it (projection.py): a WF star (course 2) is practiced via
    # level 24 (WF). Exit to the castle (hub: no course, target survives) then
    # RE-ENTER the same course — re-entering the star's own course never
    # retires the target, so the in-level L-reset still attributes to it.
    attempts = project([
        star(1, 900),
        lvl(2, 1000, 24, 6),
        jev(3, "practice_reset", 1000, {"igt_frames_before": 900, "mario_acted": True}),
        lvl(4, 1250, 6, 24),
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


def test_segment_success_into_a_star_stage_is_projected_and_clears_target():
    # LBLJ (seg_defs) ENDS by entering level 17 = BITDW, a course-bearing star
    # stage — so the success is recorded but the target clears to None (we're
    # in a fresh stage with no star picked). The auto-follow-onto-segment path
    # is covered by the star-grab and into-hub tests below.
    p = Projector(segments=seg_defs())
    p.feed(jev(1, "level_changed", 900, {"from": 16, "to": 16}))
    p.feed(jev(2, "level_changed", 1000, {"from": 16, "to": 6}))
    closed = p.feed(jev(3, "level_changed", 1085, {"from": 6, "to": 17}))
    segs = [a for a in closed if a.segment_id == 1]
    assert len(segs) == 1 and segs[0].outcome == "success"
    assert p.target is None


def test_segment_completing_into_the_hub_still_follows_onto_the_segment():
    # Boundary of caveat 12: only entering a STAR STAGE clears. A segment that
    # ends by entering the castle hub (level 6, no course) still auto-follows
    # onto the segment — it becomes the active segment.
    from sm64_events.tracking.segments import SegmentDef
    seg = SegmentDef(id=8, name="to-hub", enabled=True,
                     start_triggers=[{"type": "level_enter", "to": 8}],
                     end_triggers=[{"type": "level_enter", "to": 6}], guards=[])
    p = Projector(segments=[seg])
    p.feed(jev(1, "level_changed", 500, {"from": 6, "to": 8}))          # arm in SSL
    closed = p.feed(jev(2, "level_changed", 2000, {"from": 8, "to": 6}))  # end into hub
    assert any(a.segment_id == 8 and a.outcome == "success" for a in closed)
    assert p.target == ("segment", 8)


def test_his_pick_keeps_the_slot_when_one_event_finishes_two_segments():
    """Live report 2026-08-05: *"It seems like we somehow deselect MIPS and
    then trigger a different split?? All i know is that MIPs should remain
    selected because that's what I'm practicing!"*

    The DDD portal touch closes MIPS Clip and HMC -> DDD on the SAME event, and
    the auto-follow ran once per closure, so whichever finished LAST took the
    slot. Replaying his own journal moved the target `MIPS Clip` ->
    `HMC -> DDD` on that frame. A convenience default may fill an empty hand;
    it may not take something out of one."""
    from sm64_events.tracking.segments import SegmentDef
    common = dict(start_triggers=[{"type": "level_enter", "to": 8}],
                  end_triggers=[{"type": "level_enter", "to": 6}], guards=[])
    picked = SegmentDef(id=41, name="his pick", enabled=True, **common)
    other = SegmentDef(id=42, name="the other one", enabled=True, **common)
    p = Projector(segments=[picked, other])
    p.feed(jev(1, "level_changed", 500, {"from": 6, "to": 8}))     # both arm
    p.feed(jev(2, "target_set", 0, {"kind": "segment", "segment_id": 41}))
    assert p.target == ("segment", 41)
    # Ends into the HUB, so caveat 12's course-change retirement stays out of
    # it -- the question here is only which of the two closures wins the slot.
    closed = p.feed(jev(3, "level_changed", 2000, {"from": 8, "to": 6}))
    assert {a.segment_id for a in closed if a.outcome == "success"} == {41, 42}
    assert p.target == ("segment", 41), "his pick, not whichever closed last"


def test_mips_segment_records_attempt_but_leaves_nothing_active():
    # The user's exact report: MIPS is run in the basement and COMPLETES by
    # entering DDD (level 23 = course 9). The segment attempt is recorded with
    # its time, but because we're now in DDD with no star picked the target is
    # cleared — no active segment AND no active star.
    from sm64_events.tracking.segments import SegmentDef
    mips = SegmentDef(id=7, name="MIPS", enabled=True,
                      start_triggers=[{"type": "level_enter", "to": 6}],
                      end_triggers=[{"type": "level_enter", "to": 23}], guards=[])
    p = Projector(segments=[mips])
    p.feed(jev(1, "level_changed", 500, {"from": 16, "to": 6}))           # arm in the castle
    closed = p.feed(jev(2, "level_changed", 5000, {"from": 6, "to": 23}))  # enter DDD -> completes MIPS
    win = next(a for a in closed if a.segment_id == 7)
    assert win.outcome == "success" and win.rta_frames == 4500   # attempt + time recorded
    assert p.target is None                                      # nothing active


# -- active-star resume on re-entry (caveat 13, UI requirement 2026-06-12) ------

def _mips():
    from sm64_events.tracking.segments import SegmentDef
    return SegmentDef(id=7, name="MIPS", enabled=True,
                      start_triggers=[{"type": "level_enter", "to": 6}],
                      end_triggers=[{"type": "level_enter", "to": 23}], guards=[])


def test_reentering_the_course_just_left_resumes_the_active_star():
    # User's scenario: grab an HMC star (active); exit HMC -> MIPS arms and the
    # star deactivates (caveat 12). RE-ENTER HMC instead of doing MIPS -> the
    # HMC star is reinstated (caveat 13). HMC = course 6, level 7.
    p = Projector(segments=[_mips()])
    p.feed(jev(1, "level_changed", 100, {"from": 6, "to": 7}))   # enter HMC
    p.feed(jev(2, "star_collected", 900,
               {"course_id": 6, "star_id": 2, "igt_frames": 300}))
    assert p.target == ("star", 6, 2)
    p.feed(jev(3, "level_changed", 1500, {"from": 7, "to": 6}))  # exit: MIPS arms, star suspended
    assert p.target is None
    p.feed(jev(4, "level_changed", 1700, {"from": 6, "to": 7}))  # re-enter HMC (MIPS disarms)
    assert p.target == ("star", 6, 2)                            # resumed


def test_doing_the_segment_does_not_later_resume_the_suspended_star():
    # Contrast: if you DO the segment (complete MIPS into DDD) instead of going
    # back, the suspended HMC star is dropped — returning to HMC later does NOT
    # resurrect it (caveat 13: a committed completion clears the stash).
    p = Projector(segments=[_mips()])
    p.feed(jev(1, "level_changed", 100, {"from": 6, "to": 7}))   # HMC
    p.feed(jev(2, "star_collected", 900,
               {"course_id": 6, "star_id": 2, "igt_frames": 300}))
    p.feed(jev(3, "level_changed", 1500, {"from": 7, "to": 6}))  # exit: suspend HMC star
    p.feed(jev(4, "level_changed", 5000, {"from": 6, "to": 23}))  # complete MIPS into DDD
    assert p.target is None                                      # nothing active (caveat 12)
    p.feed(jev(5, "level_changed", 6000, {"from": 23, "to": 7}))  # later, back to HMC
    assert p.target is None                                      # NOT resumed


def test_resume_after_direct_warp_to_another_course_and_back():
    # Suspend via the different-course path (no segment): an SSL star is active,
    # warp straight to DDD (suspends it), warp back to SSL -> resumed.
    p = Projector()
    p.feed(jev(1, "target_set", 0, {"course_id": 8, "star_id": 1}))  # SSL
    p.feed(jev(2, "level_changed", 1000, {"from": 6, "to": 8}))       # enter SSL (same course)
    assert p.target == ("star", 8, 1)
    p.feed(jev(3, "level_changed", 2000, {"from": 8, "to": 23}))      # warp to DDD: suspend
    assert p.target is None
    p.feed(jev(4, "level_changed", 3000, {"from": 23, "to": 8}))      # warp back to SSL
    assert p.target == ("star", 8, 1)                                # resumed


def test_a_new_grab_drops_the_suspended_star():
    # Committing a new focus elsewhere clears the stash: grabbing a BoB star
    # after suspending the SSL star means returning to SSL does not resume it.
    p = Projector()
    p.feed(jev(1, "target_set", 0, {"course_id": 8, "star_id": 1}))  # SSL
    p.feed(jev(2, "level_changed", 1000, {"from": 8, "to": 9}))       # enter BoB (course 1): suspend SSL star
    assert p.target is None
    p.feed(jev(3, "star_collected", 2000,
               {"course_id": 1, "star_id": 0, "igt_frames": 200}))   # grab BoB star -> drops stash
    assert p.target == ("star", 1, 0)
    p.feed(jev(4, "level_changed", 3000, {"from": 9, "to": 8}))       # back to SSL
    assert p.target is None                                          # SSL star NOT resumed


def test_star_target_is_tagged_now():
    p = Projector()
    p.feed(jev(1, "target_set", 0, {"course_id": 2, "star_id": 2}))
    assert p.target == ("star", 2, 2)


def test_segment_target_set_event_round_trips():
    p = Projector()
    p.feed(jev(1, "target_set", 0, {"kind": "segment", "segment_id": 4}))
    assert p.target == ("segment", 4)


def test_cleared_segment_attempt_does_not_move_target():
    # Arming a segment retires the active-star target (active-star/segment
    # exclusivity, 2026-06-12): the star target -> None as LBLJ arms on the
    # 16->6 entry, and the CLEARED segment success then does NOT move the
    # target onto the segment either — so it stays None throughout.
    p = Projector(cleared={2 + 10**10 * 1: "mistake"}, segments=seg_defs())
    p.feed(jev(1, "target_set", 0, {"course_id": 2, "star_id": 2}))
    p.feed(jev(2, "level_changed", 1000, {"from": 16, "to": 6}))  # arms LBLJ -> clears star target
    assert p.target is None
    closed = p.feed(jev(3, "level_changed", 1100, {"from": 6, "to": 17}))
    assert closed[-1].cleared is True
    assert p.target is None       # cleared success does not set a segment target


def test_replay_signature_accepts_segments():
    from sm64_events.tracking.projection import replay
    attempts, projector = replay([
        jev(1, "level_changed", 1000, {"from": 16, "to": 6}),
        jev(2, "level_changed", 1100, {"from": 6, "to": 17}),
    ], segments=seg_defs())
    assert any(a.segment_id == 1 for a in attempts)


# -- active-star/segment mutual exclusivity (UI requirement 2026-06-12) ---------

def test_segment_arming_retires_active_star_target():
    # Req 1: starting a segment run means "doing a segment, not the star" — a
    # star target is retired the instant a segment arms (LBLJ arms on 16->6).
    p = Projector(segments=seg_defs())
    p.feed(jev(1, "target_set", 0, {"course_id": 2, "star_id": 2}))
    assert p.target == ("star", 2, 2)
    p.feed(jev(2, "level_changed", 1000, {"from": 16, "to": 6}))
    assert p.target is None


def test_entering_a_different_course_retires_active_star():
    # Req 3: an active star in one course (SSL, course 8) can't be active once
    # Mario warps to another stage (DDD, level 23 = course 9). Re-entering the
    # SAME course (level 8) on the way in does NOT retire it.
    p = Projector()
    p.feed(jev(1, "target_set", 0, {"course_id": 8, "star_id": 1}))
    p.feed(jev(2, "level_changed", 1000, {"from": 6, "to": 8}))   # enter SSL: same course
    assert p.target == ("star", 8, 1)
    p.feed(jev(3, "level_changed", 2000, {"from": 8, "to": 23}))  # warp to DDD: different course
    assert p.target is None


def test_exit_to_hub_keeps_active_star_until_another_stage():
    # Req 3 hub rule: the castle hub (level 6) has no course of its own, so
    # bouncing out to it and back into the same course leaves the active star
    # untouched — only entering a genuinely different stage retires it.
    p = Projector()
    p.feed(jev(1, "target_set", 0, {"course_id": 8, "star_id": 1}))   # SSL
    p.feed(jev(2, "level_changed", 1000, {"from": 8, "to": 6}))        # exit to hub
    assert p.target == ("star", 8, 1)
    p.feed(jev(3, "level_changed", 1500, {"from": 6, "to": 8}))        # re-enter SSL
    assert p.target == ("star", 8, 1)
    p.feed(jev(4, "level_changed", 2000, {"from": 8, "to": 9}))        # enter BoB (level 9 = course 1)
    assert p.target is None


def test_different_course_entry_keeps_segment_target():
    # A segment target whose def the projector doesn't know (deleted def,
    # history-only) has no start-level evidence, so it conservatively
    # survives level changes — level-bound retirement needs the def.
    p = Projector()
    p.feed(jev(1, "target_set", 0, {"kind": "segment", "segment_id": 4}))
    p.feed(jev(2, "level_changed", 1000, {"from": 8, "to": 23}))
    assert p.target == ("segment", 4)


# -- segment-target retirement on leaving the start levels (2026-07-23) ---------

def _bowser1_fight():
    from sm64_events.tracking.segments import SegmentDef
    return SegmentDef(id=9, name="Bowser 1", enabled=True,
                      start_triggers=[{"type": "level_enter", "to": 30},
                                      {"type": "attempt_anchor", "level": 30}],
                      end_triggers=[{"type": "key_grabbed", "level": 30}],
                      guards=[])


def test_leaving_the_start_levels_retires_a_segment_target():
    # User report 2026-07-23: after completing the Bowser 1 fight (the target
    # auto-follows the success) a Usamune warp to WF left "ACTIVE SEGMENT:
    # Bowser 1" pinned. Every start trigger of the fight def is level-bound to
    # the arena (level 30), so the segment cannot possibly START from WF —
    # a level_changed to a level outside the start set retires the target.
    p = Projector(segments=[_bowser1_fight()])
    p.feed(jev(1, "level_changed", 500, {"from": 17, "to": 30}))   # enter arena: arms
    closed = p.feed(jev(2, "key_grabbed", 900, {"level": 30, "igt_frames": 830}))
    assert any(a.segment_id == 9 and a.outcome == "success" for a in closed)
    assert p.target == ("segment", 9)        # auto-follow: still in the arena
    p.feed(jev(3, "level_changed", 1200, {"from": 30, "to": 24}))  # warp to WF
    assert p.target is None                  # can't start Bowser 1 from WF


def test_level_events_within_the_start_set_keep_the_segment_target():
    # Establishing/corrective level_changed (from == to) and re-entries into a
    # start level are not "leaving" — the target stays put.
    p = Projector(segments=[_bowser1_fight()])
    p.feed(jev(1, "level_changed", 500, {"from": 17, "to": 30}))
    p.feed(jev(2, "target_set", 600, {"kind": "segment", "segment_id": 9}))
    p.feed(jev(3, "level_changed", 700, {"from": 30, "to": 30}))   # establishing
    assert p.target == ("segment", 9)


def test_segment_with_a_location_free_start_trigger_keeps_its_target():
    # A def with ANY non-level-bound start trigger (here star_grabbed) can
    # start anywhere, so no level change can prove it inactive — the target
    # conservatively survives.
    from sm64_events.tracking.segments import SegmentDef
    d = SegmentDef(id=11, name="post-star", enabled=True,
                   start_triggers=[{"type": "star_grabbed"}],
                   end_triggers=[{"type": "level_enter", "to": 6}], guards=[])
    p = Projector(segments=[d])
    p.feed(jev(1, "target_set", 0, {"kind": "segment", "segment_id": 11}))
    p.feed(jev(2, "level_changed", 1000, {"from": 8, "to": 23}))
    assert p.target == ("segment", 11)


def _ddd_bitfs_loose_reentry():
    """A LOOSE re-entry movement practiced FROM DDD (level 23, course 9):
    start_triggers' `level_exit from=23` resolves its origin to node "23"
    (segments.start_origin), same as the corpus's real DDD -> BitFS
    movements."""
    from sm64_events.tracking.segments import SegmentDef
    return SegmentDef(id=30, name="DDD -> BitFS (loose, re-entry)",
                      enabled=True,
                      start_triggers=[{"type": "level_exit", "from": 23}],
                      end_triggers=[{"type": "level_enter", "to": 19}],
                      guards=[], match_mode="loose")


def test_an_armed_loose_segment_survives_entering_a_foreign_course():
    # Task 5 (spec 2026-07-28-multi-step-segments), task 0017's second
    # example: "when we have started a multi-step segment and enter a
    # course where that multi-step segment is still valid, we should be
    # able to see that it's still active". The origin-retirement rule just
    # above is right for an IDLE pin (see the paired test below) and wrong
    # for an ARM -- a re-entry movement enters a course on purpose, and this
    # rule was hiding the card exactly while the segment was running.
    d = _ddd_bitfs_loose_reentry()
    p = Projector(segments=[d])
    p.feed(jev(1, "target_set", 0, {"kind": "segment", "segment_id": d.id}))
    p.feed(jev(2, "level_changed", 500, {"from": 23, "to": 6}))  # exits DDD via the hub: arms
    assert p.armed_segment_ids() == {d.id}
    p.feed(jev(3, "level_changed", 600, {"from": 6, "to": 24}))  # into WF -- not DDD's origin
    assert p.target == ("segment", d.id)


def test_an_idle_loose_segment_target_still_retires_on_a_foreign_course():
    # The 2026-07-27 rule is unchanged for anything not armed: a target that
    # never ran is exactly the "doing something else now" case it exists for.
    d = _ddd_bitfs_loose_reentry()
    p = Projector(segments=[d])
    p.feed(jev(1, "target_set", 0, {"kind": "segment", "segment_id": d.id}))
    p.feed(jev(2, "level_changed", 500, {"from": 6, "to": 24}))  # into WF, never armed
    assert p.armed_segment_ids() == set()
    assert p.target is None


def _sl_hmc_waypoint_segment():
    from sm64_events.tracking.segments import SegmentDef
    return SegmentDef(id=20, name="SL->HMC", enabled=True,
                      start_triggers=[{"type": "level_exit", "from": 10, "to": 16}],
                      end_triggers=[{"type": "level_enter", "to": 24}],
                      guards=[],
                      waypoints=[[{"type": "level_enter", "to": 10}],
                                 [{"type": "level_exit", "from": 10, "to": 16}]])


def test_waypoint_level_keeps_a_multi_level_segment_target():
    # Fix (whole-branch review 2026-07-24): start_level_set only considered
    # a def's START triggers, so a multi-level waypoint segment's re-entry
    # level (SL->HMC starts on level_exit 10->16, then waypoints re-enter
    # SL at level 10) fell OUTSIDE that set and the practice target was
    # wrongly retired mid-sequence. This test FAILS before the segments.py
    # fix (target goes None on the waypoint level_changed) and passes after.
    p = Projector(segments=[_sl_hmc_waypoint_segment()])
    p.feed(jev(1, "level_changed", 500, {"from": 10, "to": 16}))   # arms via start trigger
    p.feed(jev(2, "target_set", 600, {"kind": "segment", "segment_id": 20}))
    p.feed(jev(3, "level_changed", 700, {"from": 16, "to": 10}))   # waypoint 0: re-enter SL
    assert p.target == ("segment", 20)                            # RED today: wrongly None
    p.feed(jev(4, "level_changed", 900, {"from": 10, "to": 16}))   # waypoint 1: exit again
    assert p.target == ("segment", 20)
    p.feed(jev(5, "level_changed", 1100, {"from": 16, "to": 8}))   # unrelated level: retires
    assert p.target is None


def _bowser1_wf_strict_reentry():
    """The shipped `Bowser 1 -> WF` as of 2026-08-03: exit the arena, re-enter
    BitDW, pause-exit back to the Lobby, then WF. Its origin is the arena, so
    every one of its own steps is in a foreign course or the castle."""
    from sm64_events.tracking.segments import SegmentDef
    return SegmentDef(
        id=32, name="Bowser 1 -> WF", enabled=True,
        start_triggers=[{"type": "level_exit", "from": 30}],
        end_triggers=[{"type": "level_enter", "to": 24}],
        waypoints=[[{"type": "level_enter", "to": 17, "from": 6}],
                   [{"type": "level_enter", "to": 6, "to_subarea": 1,
                     "from": 17}]],
        guards=[], match_mode="strict")


def test_a_strict_segment_keeps_its_target_walking_its_own_declared_route():
    """Live report 2026-08-03: "I finished bowser 1, selected Bowser 1 -> WF,
    entered BitDW, and now Bowser 1 -> WF is not selected anymore!"

    His rule, and the whole of this fix: *"If I select a segment, it should be
    selected until it's no longer possible for it to be armed / it gets
    invalidated by deviating from the path."* BitDW is step 1 of this
    movement's own route, so entering it is the opposite of deviating.

    The exemption to the origin-retirement rule used to require `match_mode ==
    "loose"`, because the rule ran BEFORE the matcher and a stale "was armed"
    reading is only safe for a mode that stays armed through everything. This
    branch flipped all 56 movements to strict and took the exemption away from
    every one of them. Deferring the verdict until after the matcher is what
    lets it cover strict too -- the matcher's disarm IS the invalidation.

    Mutation proof: restore `and not self._armed_loosely(self.target[1])` on
    the `_dispatch` condition and this goes red while the two tests below stay
    green."""
    d = _bowser1_wf_strict_reentry()
    p = Projector(segments=[d])
    p.feed(jev(1, "level_changed", 500, {"from": 30, "to": 6}))    # arena exit: arms
    p.feed(jev(2, "area_changed", 500,
               {"level": 6, "from": 1, "to": 1, "from_transient": True}))
    p.feed(jev(3, "target_set", 600, {"kind": "segment", "segment_id": d.id}))
    assert p.armed_segment_ids() == {d.id}
    p.feed(jev(4, "level_changed", 800, {"from": 6, "to": 17}))    # step 1: BitDW
    assert p.armed_segment_ids() == {d.id}, "the matcher kept it; it is on route"
    assert p.target == ("segment", d.id), "and so must the pick"


def test_a_strict_segment_loses_its_target_the_moment_it_deviates():
    """The other half, and the reason the old guard existed: a move OFF the
    declared route cancels the definition on that very event, and the target
    must go with it rather than pointing at something the matcher just
    disarmed. Same test the deferred check has to keep passing."""
    d = _bowser1_wf_strict_reentry()
    p = Projector(segments=[d])
    p.feed(jev(1, "level_changed", 500, {"from": 30, "to": 6}))
    p.feed(jev(2, "area_changed", 500,
               {"level": 6, "from": 1, "to": 1, "from_transient": True}))
    p.feed(jev(3, "target_set", 600, {"kind": "segment", "segment_id": d.id}))
    p.feed(jev(4, "level_changed", 800, {"from": 6, "to": 8}))     # SSL: not step 1
    assert p.armed_segment_ids() == set()
    assert p.target is None


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


# -- armed_arms (Task 4, spec 2026-07-28-multi-step-segments) -----------------

def test_armed_arms_reports_progress_total_and_frames_for_a_waypoint_def():
    from sm64_events.tracking.segments import SegmentDef
    d = SegmentDef(id=21, name="WF -> SSL", enabled=True,
                   start_triggers=[{"type": "level_exit", "from": 24}],
                   waypoints=[[{"type": "area_enter", "level": 6, "area": 3}]],
                   end_triggers=[{"type": "level_enter", "to": 8}], guards=[])
    p = Projector(segments=[d])
    p.feed(jev(1, "level_changed", 1000, {"from": 24, "to": 6}))
    arm = p.armed_arms()[21]
    assert arm["progress"] == 0 and arm["total"] == 1
    assert arm["start_frame"] == 1000
    assert arm["deadline_frame"] is None      # strict def: no staleness budget
    p.feed(jev(2, "area_changed", 1010, {"level": 6, "from": 1, "to": 3}))
    assert p.armed_arms()[21]["progress"] == 1   # waypoint consumed


def test_armed_arms_carries_a_deadline_for_a_loose_def():
    from sm64_events.tracking import segments as segments_module
    from sm64_events.tracking.segments import SegmentDef
    loose = SegmentDef(id=20, name="DDD -> BitFS (loose)", enabled=True,
                       start_triggers=[{"type": "level_exit", "from": 23}],
                       end_triggers=[{"type": "level_enter", "to": 19}],
                       guards=[], match_mode="loose")
    p = Projector(segments=[loose])
    p.feed(jev(1, "level_changed", 1000, {"from": 23, "to": 6}))
    arm = p.armed_arms()[20]
    assert arm["total"] == 0   # no waypoints
    # No history yet, so the budget is the floor (never the literal constant
    # — Task 9 re-measures it; see progress.md and CLAUDE.md's shipped-
    # default rule).
    assert arm["deadline_frame"] == 1000 + segments_module.budget_frames(None)


def test_armed_arms_is_empty_with_nothing_armed():
    from sm64_events.tracking.segments import SegmentDef
    d = SegmentDef(id=1, name="LBLJ", enabled=True, guards=[],
                   start_triggers=[{"type": "level_enter", "to": 6, "from": 16}],
                   end_triggers=[{"type": "level_enter", "to": 17}])
    p = Projector(segments=[d])
    assert p.armed_arms() == {}


def test_armed_arms_drops_an_id_once_it_closes():
    from sm64_events.tracking.segments import SegmentDef
    d = SegmentDef(id=1, name="LBLJ", enabled=True, guards=[],
                   start_triggers=[{"type": "level_enter", "to": 6, "from": 16}],
                   end_triggers=[{"type": "level_enter", "to": 17}])
    p = Projector(segments=[d])
    p.feed(jev(1, "level_changed", 1000, {"from": 16, "to": 6}))
    assert 1 in p.armed_arms()
    p.feed(jev(2, "level_changed", 1085, {"from": 6, "to": 17}))
    assert p.armed_arms() == {}


def test_replay_derives_finished_run(tmp_path):
    from sm64_events.tracking.projection import replay
    from sm64_events.storage.db import Database
    db = Database(tmp_path / "t.db")
    sid = db.insert_session("2026-06-14T00:00:00Z")
    from sm64_events.core.events import Event
    from datetime import datetime, timezone
    T = datetime(2026, 6, 14, tzinfo=timezone.utc)
    steps = [{"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]
    db.append_event(sid, 1, Event(type="run_started", frame=0, timestamp_utc=T,
        payload={"route_id": 1, "route_name": "R", "route_steps": steps,
                 "mode": "forgiving", "start_offset_ms": 1360}))
    db.append_event(sid, 2, Event(type="game_reset", frame=0, timestamp_utc=T,
        payload={}))
    db.append_event(sid, 3, Event(type="star_collected", frame=0, timestamp_utc=T,
        payload={"course_id": 2, "star_id": 0, "igt_frames": 100}))
    attempts, proj = replay(db.events())
    runs = proj.finished_runs()
    assert len(runs) == 1 and runs[0].status == "finished"
    assert proj.active_run_view() is None


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


# -- route-scoped arming (spec 2026-07-23-default-routes-foundation) -----------

def test_route_selected_threads_into_matchcontext():
    # Proves the Projector actually threads self._route_segments into the
    # MatchContext it builds for the engine, by showing a route that does NOT
    # name this def keeping it unarmed and one that does arming it.
    #
    # This used to open with "no route_selected has fired yet -> unarmed",
    # which stopped being true on 2026-08-02: an EMPTY scope now means no
    # filter (segments.py::_route_allows carries his ruling). A route that
    # names OTHER segments is the shape that still proves the threading, and
    # it is the one that was never covered.
    from sm64_events.tracking.segments import SegmentDef
    guarded = SegmentDef(id=42, name="CCM->BitDW", enabled=True,
                        start_triggers=[{"type": "level_exit", "from": 5}],
                        end_triggers=[{"type": "level_enter", "to": 17}],
                        guards=[{"type": "in_active_route"}])
    p = Projector(segments=[guarded])
    p.feed(jev(1, "route_selected", 0, {"route_id": 1, "segment_ids": [99]}))
    p.feed(jev(2, "level_changed", 1000, {"from": 5, "to": 16}))
    assert 42 not in p.armed_segment_ids()
    p.feed(jev(3, "route_selected", 0, {"route_id": 2, "segment_ids": [42]}))
    p.feed(jev(4, "level_changed", 2000, {"from": 5, "to": 16}))
    assert 42 in p.armed_segment_ids()


def test_no_active_route_arms_every_guarded_movement():
    # The live report itself (2026-08-02): scope chip on "Overall" -> no
    # route_selected scope -> every castle movement silently unpracticable.
    # His ruling: "I would expect to see EVERY SINGLE OPTION enabled."
    from sm64_events.tracking.segments import SegmentDef
    guarded = SegmentDef(id=42, name="CCM->BitDW", enabled=True,
                        start_triggers=[{"type": "level_exit", "from": 5}],
                        end_triggers=[{"type": "level_enter", "to": 17}],
                        guards=[{"type": "in_active_route"}])
    p = Projector(segments=[guarded])
    p.feed(jev(1, "level_changed", 1000, {"from": 5, "to": 16}))
    assert 42 in p.armed_segment_ids()
    # ...and clearing a route back to Overall restores that, rather than
    # leaving the empty member set behind as a filter matching nobody.
    p.feed(jev(2, "route_selected", 0, {"route_id": 1, "segment_ids": [99]}))
    p.feed(jev(3, "route_selected", 0, {"route_id": None, "segment_ids": []}))
    p.feed(jev(4, "level_changed", 2000, {"from": 5, "to": 16}))
    assert 42 in p.armed_segment_ids()


def test_segment_target_satisfies_in_active_route_without_a_route_selected():
    # Practicing a guarded segment directly (target_set) also satisfies
    # in_active_route with no route_selected ever fired: the Projector
    # derives target_segment from the live target, not from
    # self._route_segments. Proves that half of the ctx-build wiring too.
    from sm64_events.tracking.segments import SegmentDef
    guarded = SegmentDef(id=42, name="g", enabled=True,
                        start_triggers=[{"type": "level_exit", "from": 5}],
                        end_triggers=[{"type": "level_enter", "to": 17}],
                        guards=[{"type": "in_active_route"}])
    p = Projector(segments=[guarded])
    p.feed(jev(1, "target_set", 0, {"kind": "segment", "segment_id": 42}))
    p.feed(jev(2, "level_changed", 1000, {"from": 5, "to": 16}))
    assert 42 in p.armed_segment_ids()


def test_run_starts_on_configured_level_enter(tmp_path):
    from sm64_events.tracking.projection import replay
    from sm64_events.storage.db import Database
    from sm64_events.core.events import Event
    from datetime import datetime, timezone
    db = Database(tmp_path / "t.db"); sid = db.insert_session("t")
    T = datetime(2026, 6, 15, tzinfo=timezone.utc)
    steps = [{"need": 1, "candidates": [{"type": "star", "course": 9, "star": 0}]}]
    db.append_event(sid, 1, Event(type="run_started", frame=0, timestamp_utc=T, payload={
        "route_id": 1, "route_name": "R", "route_steps": steps, "mode": "forgiving",
        "start_offset_ms": 0, "start_condition": {"type": "level_enter", "to": 9}}))
    # a game_reset must NOT start this run (start condition is level_enter)
    db.append_event(sid, 2, Event(type="game_reset", frame=0, timestamp_utc=T, payload={}))
    attempts, proj = replay(db.events())
    assert proj.active_run_view() is None
    # entering level 9 starts it
    db.append_event(sid, 3, Event(type="level_changed", frame=0, timestamp_utc=T,
        payload={"from": 1, "to": 9}))
    db.append_event(sid, 4, Event(type="star_collected", frame=0, timestamp_utc=T,
        payload={"course_id": 9, "star_id": 0, "igt_frames": 300}))
    attempts, proj = replay(db.events())
    assert len(proj.finished_runs()) == 1


def test_success_below_default_min_is_auto_ignored():
    # igt 10 < DEFAULT_MIN_FRAMES 15: a detection artifact, auto-cleared
    attempts = project([
        jev(1, "practice_reset", 1000, {"igt_frames_before": 0}),
        star(2, 1350, igt=10),
    ])
    a = attempts[0]
    assert a.outcome == "success" and a.cleared is True
    assert a.cleared_reason == "auto: below 0.50s min"


def test_star_min_override_flags_what_default_allows():
    tf = {"2:2": {"min_frames": 180, "max_frames": None}}
    attempts = project([
        jev(1, "practice_reset", 1000, {"igt_frames_before": 0}),
        star(2, 1350, igt=150),
    ], time_filters=tf)
    assert attempts[0].cleared is True
    assert attempts[0].cleared_reason == "auto: below 6.00s min"


def test_star_max_override_flags_slow_success():
    tf = {"2:2": {"min_frames": 15, "max_frames": 300}}
    attempts = project([star(1, 900, igt=343)], time_filters=tf)
    assert attempts[0].cleared is True
    assert attempts[0].cleared_reason == "auto: above 10.00s max"


def test_min_zero_disables_the_floor():
    tf = {"2:2": {"min_frames": 0, "max_frames": None}}
    attempts = project([star(1, 900, igt=1)], time_filters=tf)
    assert attempts[0].cleared is False


def test_exactly_at_min_counts():
    tf = {"2:2": {"min_frames": 150, "max_frames": None}}
    attempts = project([star(1, 900, igt=150)], time_filters=tf)
    assert attempts[0].cleared is False


def test_failures_are_never_auto_flagged():
    # a 2-frame reset is legitimate practice behavior (fail fast)
    attempts = project([
        star(1, 900),                                     # sets target (2,2)
        jev(2, "practice_reset", 1000, {"igt_frames_before": 0}),
        jev(3, "practice_reset", 1002,
            {"igt_frames_before": 2, "mario_acted": True}),
    ])
    reset_row = [a for a in attempts if a.outcome == "reset"][0]
    assert reset_row.cleared is False


def test_rta_fallback_when_igt_missing():
    # star_collected without igt_frames: judge on the wall-frame delta
    attempts = project([
        jev(1, "practice_reset", 1000, {"igt_frames_before": 0}),
        jev(2, "star_collected", 1005, {"course_id": 2, "star_id": 2}),
    ])
    assert attempts[0].cleared is True          # rta 5 < 15
    attempts = project([
        jev(1, "practice_reset", 1000, {"igt_frames_before": 0}),
        jev(2, "star_collected", 1350, {"course_id": 2, "star_id": 2}),
    ])
    assert attempts[0].cleared is False         # rta 350


def test_manual_restore_wins_over_auto_flag():
    # journaled clear/restore history exempts the id from the auto rule
    attempts = project([
        jev(1, "practice_reset", 1000, {"igt_frames_before": 0}),
        star(2, 1350, igt=10),
        jev(3, "attempt_restored", 0, {"attempt_id": 1}),
    ])
    assert attempts[0].cleared is False


def test_auto_ignored_grab_does_not_move_target():
    _, proj = replay([
        star(1, 900),                            # valid grab: target (2,2)
        jev(2, "practice_reset", 1000, {"igt_frames_before": 0}),
        star(3, 1005, course=8, star_id=1, igt=5),   # bogus grab of (8,1)
    ])
    assert proj.target == ("star", 2, 2)


# -- segment-attempt validity bounds (spec 2026-07-23) --------------------

from sm64_events.tracking.segments import SegmentDef


def seg_def(**over):
    base = dict(id=1, name="S", enabled=True,
                start_triggers=[{"type": "spawned"}],
                end_triggers=[{"type": "warp_entered", "level": 16}],
                guards=[])
    base.update(over)
    return SegmentDef(**base)


def test_segment_success_below_default_min_is_auto_ignored():
    attempts = project([
        jev(1, "spawned", 1000, {"level": 16}),
        jev(2, "warp_entered", 1005, {"level": 16}),   # rta 5 < 15
    ], segments=[seg_def()])
    [a] = [a for a in attempts if a.segment_id == 1]
    assert a.outcome == "success" and a.cleared is True
    assert a.cleared_reason == "auto: below 0.50s min"


def test_segment_min_time_guard_overrides_the_default():
    d = seg_def(guards=[{"type": "min_time", "frames": 180}])
    flagged = project([
        jev(1, "spawned", 1000, {"level": 16}),
        jev(2, "warp_entered", 1150, {"level": 16}),   # rta 150 < 180
    ], segments=[d])
    [a] = [a for a in flagged if a.segment_id == 1]
    assert a.cleared is True and a.cleared_reason == "auto: below 6.00s min"
    ok = project([
        jev(1, "spawned", 1000, {"level": 16}),
        jev(2, "warp_entered", 1200, {"level": 16}),   # rta 200 >= 180
    ], segments=[d])
    [a] = [a for a in ok if a.segment_id == 1]
    assert a.cleared is False


def test_segment_max_time_guard_flags_slow_success():
    d = seg_def(guards=[{"type": "max_time", "frames": 300}])
    attempts = project([
        jev(1, "spawned", 1000, {"level": 16}),
        jev(2, "warp_entered", 1400, {"level": 16}),   # rta 400 > 300
    ], segments=[d])
    [a] = [a for a in attempts if a.segment_id == 1]
    assert a.cleared is True and a.cleared_reason == "auto: above 10.00s max"


def test_auto_ignored_segment_success_does_not_follow_target():
    _, proj = replay([
        jev(1, "spawned", 1000, {"level": 16}),
        jev(2, "warp_entered", 1005, {"level": 16}),   # bogus: rta 5
    ], segments=[seg_def()])
    assert proj.target is None


# -- the 100-coin star IS the segment (spec 2026-07-28-multi-step-segments) --
# The seg:100c->exit:* family's currently-seeded shape: arms on course entry
# (level_enter/attempt_anchor), the 100-coin grab is a WAYPOINT, the end is
# any of the course's other six stars, strict (tools/corpus_movements.py).

def _hc_def(course=2, level=24, match_mode="strict", enabled=True):
    return SegmentDef(
        id=100, name=f"course {course} 100 Coins -> Exit", enabled=enabled,
        start_triggers=[{"type": "level_enter", "to": level},
                        {"type": "attempt_anchor", "level": level}],
        end_triggers=[{"type": "star_grabbed", "course": course, "star": s}
                      for s in range(6)],
        waypoints=[[{"type": "star_grabbed", "course": course, "star": 6}]],
        guards=[], match_mode=match_mode)


def test_hundred_coin_grab_alone_creates_no_star_attempt():
    # Enter the course (arms the engine), grab 100 coins (waypoint advance,
    # silent). No exit star yet -- nothing should be recorded at all: the
    # grab used to close its own star-6 attempt, and must not any more.
    attempts = project([
        jev(1, "level_changed", 900, {"from": 16, "to": 24}),
        star(2, 1000, course=2, star_id=6, igt=1000),
    ], segments=[_hc_def()])
    assert attempts == []


def test_hundred_coin_completion_attributes_to_the_star_not_the_segment():
    # Enter, grab 100 coins, grab the exit star -- the ENGINE's own
    # completion is what closes the 100-coin star's attempt (measured
    # shape: STAR course-6 success + the exit star's own success, one run).
    attempts = project([
        jev(1, "level_changed", 900, {"from": 16, "to": 24}),
        star(2, 1000, course=2, star_id=6, igt=1000),
        star(3, 1200, course=2, star_id=3, igt=1200),
    ], segments=[_hc_def()])
    hundred = [a for a in attempts if a.course_id == 2 and a.star_id == 6]
    exit_star = [a for a in attempts if a.course_id == 2 and a.star_id == 3]
    assert len(hundred) == 1
    assert hundred[0].segment_id is None
    assert hundred[0].outcome == "success"
    # Live report: a segment's own igt_frames is always None (RTA-only by
    # design), but the reattributed attempt IS a star now and stars
    # display/grade on IGT -- without this it renders with no time and
    # cannot be graded. The closing star_collected event's OWN igt_frames
    # (the same value the exit star's own attempt gets, since one event
    # closes both) is the authoritative source, never rta_frames.
    assert hundred[0].igt_frames == 1200
    # decision #1: the exit star keeps its OWN attempt too -- a real grab,
    # never suppressed by this change (only star 6 changes).
    assert len(exit_star) == 1 and exit_star[0].segment_id is None
    assert exit_star[0].igt_frames == 1200


def test_hundred_coin_strat_tag_comes_from_the_star_not_the_segment():
    _, proj = replay([
        jev(1, "strat_set", 0, {"course_id": 2, "star_id": 6,
                                "strat_tag": "Coin Route A"}),
        jev(2, "level_changed", 900, {"from": 16, "to": 24}),
        star(3, 1000, course=2, star_id=6, igt=1000),
    ], segments=[_hc_def()])
    closed = proj.feed(star(4, 1200, course=2, star_id=3, igt=1200))
    hundred = next(a for a in closed if a.course_id == 2 and a.star_id == 6)
    assert hundred.strat_tag == "Coin Route A"


def test_hundred_coin_engine_death_also_attributes_to_the_star():
    # decision #3: what happens with no exit star. death is a hard fail
    # WITH a row (_feed_waypoint precedence, unchanged by this change) and
    # must attribute the same way a success does.
    attempts = project([
        jev(1, "level_changed", 900, {"from": 16, "to": 24}),
        star(2, 1000, course=2, star_id=6, igt=1000),
        jev(3, "death", 1100, {"cause": "fell", "igt_frames": 1267}),
    ], segments=[_hc_def()])
    hundred = [a for a in attempts if a.course_id == 2 and a.star_id == 6]
    assert len(hundred) == 1
    assert hundred[0].outcome == "death" and hundred[0].segment_id is None
    # Live report's other example ("42.23 death"): a death closure carries
    # igt_frames exactly like a star death does (_close_by_death reads the
    # same key) -- must be stamped too, not just the success path.
    assert hundred[0].igt_frames == 1267


def test_leaving_without_the_exit_star_records_nothing_confirmed_sane():
    # decision #3: leaving the course before the exit star is a SILENT
    # CANCEL under strict/waypoint dispatch (a real-edge level_changed that
    # isn't the next waypoint) -- no row at all, unchanged by this task and
    # confirmed here rather than assumed.
    attempts = project([
        jev(1, "level_changed", 900, {"from": 16, "to": 24}),
        star(2, 1000, course=2, star_id=6, igt=1000),
        jev(3, "level_changed", 1100, {"from": 24, "to": 19}),  # leave for BitFS
    ], segments=[_hc_def()])
    assert attempts == []


def test_hundred_coin_falls_back_to_a_plain_star_attempt_with_no_engine():
    # No HUNDRED_COIN_EXIT-shaped def at all (deleted, never seeded) -- the
    # grab still records a plain star attempt, same fallback philosophy the
    # retired star->segment TARGET redirect used ("falling back to the
    # plain star keeps the click meaningful").
    attempts = project([star(1, 1000, course=2, star_id=6, igt=1000)])
    assert len(attempts) == 1
    assert attempts[0].course_id == 2 and attempts[0].star_id == 6
    assert attempts[0].segment_id is None


def test_hundred_coin_falls_back_when_the_engine_is_disabled():
    attempts = project([star(1, 1000, course=2, star_id=6, igt=1000)],
                       segments=[_hc_def(enabled=False)])
    assert len(attempts) == 1
    assert attempts[0].course_id == 2 and attempts[0].star_id == 6


def test_hundred_coin_target_survives_entering_its_own_course():
    # The star-target model this change relies on: target = ("star", 2, 6)
    # must not be wiped the instant its OWN engine arms on course entry
    # (feed()'s "a segment armed retires a star target" rule would
    # otherwise fire on every single visit).
    _, proj = replay([
        jev(1, "target_set", 0, {"kind": "star", "course_id": 2, "star_id": 6}),
    ], segments=[_hc_def()])
    assert proj.target == ("star", 2, 6)
    proj.feed(jev(2, "level_changed", 900, {"from": 16, "to": 24}))
    assert proj.target == ("star", 2, 6)


def test_hundred_coin_arm_still_retires_a_different_courses_star_target():
    # A target for a different star in a DIFFERENT course is still retired
    # when some unrelated segment arms (this rule, unchanged for every
    # other case) -- level 8 (SSL, course 8) vs a course-5 target.
    other = SegmentDef(id=200, name="unrelated", enabled=True,
                       start_triggers=[{"type": "level_enter", "to": 8}],
                       end_triggers=[{"type": "level_enter", "to": 6}],
                       guards=[])
    _, proj = replay([
        jev(1, "target_set", 0, {"kind": "star", "course_id": 5, "star_id": 1}),
    ], segments=[other])
    assert proj.target == ("star", 5, 1)
    proj.feed(jev(2, "level_changed", 900, {"from": 6, "to": 8}))  # arms `other`
    assert proj.target is None


def test_ambient_arm_exemption_generalizes_to_every_star_in_the_course():
    # THE regression this generalization fixes: not just star 6 -- ANY star
    # practiced in WF must survive the SAME course's ambient 100-coin engine
    # arming. Before the generalization this wiped the target on every
    # single course entry (confirmed live against the real seeded def).
    _, proj = replay([
        jev(1, "target_set", 0, {"kind": "star", "course_id": 2, "star_id": 3}),
    ], segments=[_hc_def()])
    assert proj.target == ("star", 2, 3)
    proj.feed(jev(2, "level_changed", 900, {"from": 16, "to": 24}))
    assert proj.target == ("star", 2, 3)


def test_ambient_arm_exemption_covers_the_reds_to_pipe_family_too():
    # Bowser's seg:reds->pipe:<abbrev> shares the identical ambient shape
    # (arms on stage entry) and never surfaced this bug only because a
    # Bowser course has exactly one star to collide with -- a target for
    # THAT star must still survive its own pipe engine arming.
    reds_pipe = SegmentDef(
        id=201, name="BitDW — 8 Red Coins → Pipe", enabled=True,
        start_triggers=[{"type": "level_enter", "to": 17},
                        {"type": "attempt_anchor", "level": 17}],
        end_triggers=[{"type": "warp_entered", "level": 17}],
        waypoints=[[{"type": "star_grabbed", "course": 16, "star": 0}]],
        guards=[], match_mode="strict")
    _, proj = replay([
        jev(1, "target_set", 0, {"kind": "star", "course_id": 16, "star_id": 0}),
    ], segments=[reds_pipe])
    assert proj.target == ("star", 16, 0)
    proj.feed(jev(2, "level_changed", 900, {"from": 6, "to": 17}))
    assert proj.target == ("star", 16, 0)


def _reds_pipe_segment_def():
    """The real seg:reds->pipe:<abbrev> shape (matches
    test_ambient_arm_exemption_covers_the_reds_to_pipe_family_too and
    tests/test_views.py::_reds_pipe_def): starts on stage entry, waypoints
    on the course's reds grab, ends on the pipe warp."""
    from sm64_events.tracking.segments import SegmentDef
    return SegmentDef(
        id=201, name="BitDW — 8 Red Coins → Pipe", enabled=True,
        start_triggers=[{"type": "level_enter", "to": 17},
                        {"type": "attempt_anchor", "level": 17}],
        end_triggers=[{"type": "warp_entered", "level": 17}],
        waypoints=[[{"type": "star_grabbed", "course": 16, "star": 0}]],
        guards=[], match_mode="strict")


def test_grabbing_a_waypoint_star_does_not_steal_an_armed_segments_target():
    # THE FLASH bug (live report 2026-07-30, round 2): with the Pipe segment
    # explicitly targeted, grabbing the reds star is merely THIS segment's
    # own waypoint on the way to the pipe, not a deliberate "practice the
    # star alone" pick -- practice.js's pinned card was flashing to the
    # star's own (Star Section) rank standards + practice log for the rest
    # of the run because the server's target flipped to the star the
    # instant the grab closed, even though the run never stopped being a
    # Pipe run. Reproduces the exact live shape: target the segment while
    # armed, then feed the waypoint grab.
    p = Projector(segments=[_reds_pipe_segment_def()])
    p.feed(jev(1, "level_changed", 900, {"from": 6, "to": 17}))   # arms ambiently
    p.feed(jev(2, "target_set", 950, {"kind": "segment", "segment_id": 201}))
    assert p.target == ("segment", 201)
    closed = p.feed(jev(3, "star_collected", 1500,
                        {"course_id": 16, "star_id": 0, "igt_frames": 500}))
    # The grab still records its own star attempt -- the Star family's
    # history must keep existing even while its card is not shown (user's
    # own words: "the practice log should still exist for star mode in the
    # history, just that we don't see it").
    assert [a.segment_id for a in closed] == [None]
    assert (closed[0].course_id, closed[0].star_id) == (16, 0)
    assert closed[0].outcome == "success"
    # ...but the target must stay on the still-running segment, not flash
    # over to the star it merely stepped through.
    assert p.target == ("segment", 201)
    assert 201 in p.armed_segment_ids()          # mid-sequence, not closed
    closed2 = p.feed(jev(4, "warp_entered", 2200, {"level": 17}))
    assert [a.segment_id for a in closed2] == [201]
    assert closed2[0].outcome == "success"
    assert p.target == ("segment", 201)          # unaffected by the fix


def test_grabbing_a_star_still_moves_the_target_with_nothing_pinned_there():
    # Regression guard the other direction: the fix must not blanket-protect
    # every armed segment from every grab -- only one that is BOTH the
    # current explicit target and mid-sequence. With no target set yet (the
    # ambient arm alone, no explicit pick), the ordinary "last valid grab
    # moves the target" rule must still apply.
    p = Projector(segments=[_reds_pipe_segment_def()])
    p.feed(jev(1, "level_changed", 900, {"from": 6, "to": 17}))   # arms ambiently
    assert p.target is None
    p.feed(jev(2, "star_collected", 1500,
                {"course_id": 16, "star_id": 0, "igt_frames": 500}))
    assert p.target == ("star", 16, 0)


def test_a_grab_that_cancels_a_waypoint_segment_still_leaves_it_targeted():
    # A DIFFERENT armed, targeted STRICT waypoint segment that does NOT
    # expect a star grab next is silently CANCELLED by the matcher's own
    # major-action rule on this very event (segments.py's _feed_waypoint
    # precedence) -- the restore only ever fires for a segment the grab
    # genuinely advanced, never one it broke.
    from sm64_events.tracking.segments import SegmentDef
    unrelated = SegmentDef(
        id=55, name="unrelated waypoint movement", enabled=True,
        start_triggers=[{"type": "level_enter", "to": 6}],
        waypoints=[[{"type": "level_enter", "to": 10}]],
        end_triggers=[{"type": "level_enter", "to": 17}],
        guards=[], match_mode="strict")
    p = Projector(segments=[unrelated])
    p.feed(jev(1, "level_changed", 900, {"from": 16, "to": 6}))
    p.feed(jev(2, "target_set", 950, {"kind": "segment", "segment_id": 55}))
    assert p.target == ("segment", 55)
    assert 55 in p.armed_segment_ids()
    p.feed(jev(3, "star_collected", 1500,
                {"course_id": 9, "star_id": 2, "igt_frames": 500}))
    assert 55 not in p.armed_segment_ids()   # cancelled: major-action mismatch
    # REVERSED 2026-08-01 by his ruling that nothing may overwrite a segment
    # he picked. The def really is cancelled -- that half was right and is
    # unchanged -- but the pick survives it: he is still pointed at the thing
    # he chose, and re-running it is one retry rather than one re-pick.
    assert p.target == ("segment", 55)


def test_a_grab_does_not_move_the_target_off_a_plain_armed_segment():
    # This test asserted the OPPOSITE until 2026-08-01, and the reasoning it
    # carried is worth keeping because it was right about the mechanism and
    # wrong about the remedy: a plain armed def has no notion of "this grab
    # was my own next step", so "still armed after" could not tell a relevant
    # grab from an irrelevant one, and caveat 18's restore was scoped to
    # waypoint defs to avoid guessing. What it called "a far bigger behavior
    # change than the reported bug asks for" is precisely what the NEXT
    # report asked for: a course-exit movement lost its target to the star he
    # grabbed on the way out, and with it its arm and its whole card. The
    # question was never whether the grab was relevant to the def -- it is
    # that a click outranks a thing that merely happened.
    from sm64_events.tracking.segments import SegmentDef
    plain = SegmentDef(id=77, name="plain movement", enabled=True,
                       start_triggers=[{"type": "level_enter", "to": 6}],
                       end_triggers=[{"type": "level_enter", "to": 17}],
                       guards=[])
    p = Projector(segments=[plain])
    p.feed(jev(1, "level_changed", 900, {"from": 16, "to": 6}))
    p.feed(jev(2, "target_set", 950, {"kind": "segment", "segment_id": 77}))
    p.feed(jev(3, "star_collected", 1500,
                {"course_id": 9, "star_id": 2, "igt_frames": 500}))
    assert 77 in p.armed_segment_ids()      # unaffected: still armed either way
    assert p.target == ("segment", 77)      # and the pick he made stands


def test_star_success_with_no_clock_at_all_is_not_flagged():
    # grab-only attempt (no anchor -> rta None) with no igt_frames in the
    # payload: _auto_ignored's "no clock -> no flag" branch — nothing to
    # judge, so the success stands.
    attempts = project([
        jev(1, "star_collected", 1000, {"course_id": 2, "star_id": 2}),
    ])
    [a] = attempts
    assert a.outcome == "success" and a.igt_frames is None and a.rta_frames is None
    assert a.cleared is False


# -- last-star tracking feeds MatchContext (spec 2026-07-23) --------------

def test_last_star_grabbed_guard_gates_arming():
    d = seg_def(guards=[{"type": "last_star_grabbed", "course": 2}])
    # no grab yet: unknown history conservatively fails -> no arm
    _, proj = replay([jev(1, "spawned", 1000, {"level": 16})], segments=[d])
    assert proj.armed_segment_ids() == set()
    # after grabbing (2,2) the same spawn arms
    _, proj = replay([
        star(1, 900),
        jev(2, "spawned", 1000, {"level": 16}),
    ], segments=[d])
    assert proj.armed_segment_ids() == {1}


def test_last_star_attempted_counts_failures_grabbed_does_not():
    dg = seg_def(id=1, guards=[{"type": "last_star_grabbed", "course": 8}])
    da = seg_def(id=2, guards=[{"type": "last_star_attempted", "course": 8}])
    _, proj = replay([
        star(1, 900),                                  # grab (2,2)
        jev(2, "target_set", 950, {"course_id": 8, "star_id": 1}),
        jev(3, "practice_reset", 1000, {"igt_frames_before": 0}),
        jev(4, "practice_reset", 1400,
            {"igt_frames_before": 380, "mario_acted": True}),  # reset on (8,1)
        jev(5, "spawned", 1500, {"level": 16}),
    ], segments=[dg, da])
    # last ATTEMPT is (8,1); last GRAB is still (2,2)
    assert proj.armed_segment_ids() == {2}


def test_game_reset_clears_last_star_memory():
    d = seg_def(guards=[{"type": "last_star_grabbed", "course": 2}])
    _, proj = replay([
        star(1, 900),
        jev(2, "game_reset", 50, {}),
        jev(3, "spawned", 1000, {"level": 16}),
    ], segments=[d])
    assert proj.armed_segment_ids() == set()


def test_last_star_guard_star_param_narrows():
    d = seg_def(guards=[{"type": "last_star_grabbed", "course": 2, "star": 3}])
    _, proj = replay([
        star(1, 900, star_id=2),
        jev(2, "spawned", 1000, {"level": 16}),
    ], segments=[d])
    assert proj.armed_segment_ids() == set()
    _, proj = replay([
        star(1, 900, star_id=3),
        jev(2, "spawned", 1000, {"level": 16}),
    ], segments=[d])
    assert proj.armed_segment_ids() == {1}


# -- attempt strat reclassification (spec 2026-07-23) --------------------------

def test_attempt_strat_set_reclassifies_a_star_attempt():
    events = [
        jev(1, "target_set", 0, {"course_id": 2, "star_id": 2,
                                 "strat_tag": "Cannonless"}),
        jev(2, "practice_reset", 1000, {"igt_frames_before": 0}),
        star(3, 1350),
    ]
    [before] = project(events)
    # the attempt is keyed by its FIRST event — the anchor, not the grab
    assert before.id == 2 and before.strat_tag == "Cannonless"
    [after] = project(events + [
        jev(4, "attempt_strat_set", 0, {"attempt_id": 2,
                                        "strat_tag": "Slide Kick"})])
    assert after.strat_tag == "Slide Kick"
    assert after.outcome == "success"      # nothing else moved


def test_attempt_strat_set_null_unlabels_an_attempt():
    [a] = project([
        jev(1, "target_set", 0, {"course_id": 2, "star_id": 2,
                                 "strat_tag": "Cannonless"}),
        jev(2, "practice_reset", 1000, {"igt_frames_before": 0}),
        star(3, 1350),
        jev(4, "attempt_strat_set", 0, {"attempt_id": 2, "strat_tag": None}),
    ])
    assert a.strat_tag is None


def test_strat_overrides_last_write_wins():
    assert strat_overrides([
        jev(1, "attempt_strat_set", 0, {"attempt_id": 7, "strat_tag": "A"}),
        jev(2, "attempt_strat_set", 0, {"attempt_id": 7, "strat_tag": "B"}),
        jev(3, "attempt_strat_set", 0, {"attempt_id": 9, "strat_tag": None}),
    ]) == {7: "B", 9: None}


def test_attempt_strat_set_reclassifies_a_segment_attempt():
    events = [
        jev(1, "strat_set", 0, {"kind": "segment", "segment_id": 1,
                                "strat_tag": "old route"}),
        jev(2, "level_changed", 900, {"from": 16, "to": 16}),
        jev(3, "level_changed", 1000, {"from": 16, "to": 6}),    # arms LBLJ
        jev(4, "level_changed", 1085, {"from": 6, "to": 17}),    # ends it
    ]
    [before] = project(events, segments=seg_defs())
    assert before.segment_id == 1 and before.strat_tag == "old route"
    [after] = project(events + [
        jev(5, "attempt_strat_set", 0, {"attempt_id": before.id,
                                        "strat_tag": "new route"})],
        segments=seg_defs())
    assert after.strat_tag == "new route"


def test_attempt_strat_set_is_not_an_attempt_boundary():
    """It must not open, close, or discard anything — it is a pure
    annotation folded in by the pre-pass."""
    attempts = project([
        jev(1, "practice_reset", 1000, {"igt_frames_before": 0}),
        jev(2, "attempt_strat_set", 0, {"attempt_id": 1, "strat_tag": "X"}),
        star(3, 1350),
    ])
    assert len(attempts) == 1
    assert attempts[0].id == 1 and attempts[0].rta_frames == 350
    assert attempts[0].strat_tag == "X"


# -- a definition's default strategy (spec 2026-07-24-segment-default-strat) ---

def _defaulted_move(default="Standard"):
    from sm64_events.tracking.segments import SegmentDef
    return SegmentDef(id=42, name="MIPS", enabled=True,
                      start_triggers=[{"type": "level_enter", "to": 6}],
                      end_triggers=[{"type": "level_enter", "to": 23}],
                      guards=[], default_strat=default)


def _run_move(p):
    """Arm in the castle, complete by entering DDD; returns the attempt."""
    p.feed(jev(1, "level_changed", 500, {"from": 16, "to": 6}))
    closed = p.feed(jev(9, "level_changed", 5000, {"from": 6, "to": 23}))
    return next(a for a in closed if a.segment_id == 42)


def test_segment_attempt_is_tagged_with_the_definitions_default_strat():
    """Nothing is journaled on the user's behalf — the default is pre-seeded
    into strat_by_segment, the one dict every consumer already reads."""
    p = Projector(segments=[_defaulted_move()])
    assert p.strat_by_segment == {42: "Standard"}
    assert _run_move(p).strat_tag == "Standard"


def test_a_definition_without_a_default_is_unchanged():
    p = Projector(segments=[_defaulted_move(default=None)])
    assert p.strat_by_segment == {}
    assert _run_move(p).strat_tag is None


def test_an_explicit_pick_overrides_the_default():
    p = Projector(segments=[_defaulted_move()])
    p.feed(jev(0, "strat_set", 0, {"kind": "segment", "segment_id": 42,
                                   "strat_tag": "Blindfolded"}))
    assert _run_move(p).strat_tag == "Blindfolded"


def test_a_null_pick_falls_back_to_the_default_instead_of_unsetting():
    """"No strategy" is not a legitimate choice for a defaulted segment, and
    that rule lives in the data — not only in the dropdown, which merely hides
    the blank option. Old journals can still carry a null pick."""
    p = Projector(segments=[_defaulted_move()])
    p.feed(jev(0, "strat_set", 0, {"kind": "segment", "segment_id": 42,
                                   "strat_tag": None}))
    assert p.strat_by_segment[42] == "Standard"
    assert _run_move(p).strat_tag == "Standard"
    # a def with no default still clears to None
    q = Projector(segments=[_defaulted_move(default=None)])
    q.feed(jev(0, "strat_set", 0, {"kind": "segment", "segment_id": 42,
                                   "strat_tag": None}))
    assert q.strat_by_segment[42] is None


# -- a session boundary clears the live focus (live report 2026-08-01) ---------

def test_a_target_does_not_survive_into_the_next_session():
    """His report, verbatim: "I was working on a hundred coins. So when I
    opened this session, a hundred coins was selected as the active target…
    it reads as a bug if something is selected for a new session."

    The target is replay-derived, so before this it simply outlived every
    boundary — the journal's last target_set won no matter how many launches
    ago it was written."""
    p = Projector()
    p.feed(jev(1, "target_set", 0, {"course_id": 2, "star_id": 6}))
    assert p.target == ("star", 2, 6)
    p.feed(jev(2, "session_started", 0, {"session_id": 9}))
    assert p.target is None


def test_a_suspended_star_does_not_resume_across_a_session_boundary():
    """The half that would have made the fix look done and fail live. A star
    the player left its course with is STASHED, not forgotten (caveat 13), and
    re-entering that course restores it — so clearing only the target would
    have put 100 Coins back on screen the moment he walked into the course."""
    p = Projector(segments=[_mips()])
    p.feed(jev(1, "target_set", 0, {"course_id": 9, "star_id": 6}))
    p.feed(jev(2, "level_changed", 500, {"from": 16, "to": 6}))     # arm, suspends
    assert p.target is None
    p.feed(jev(3, "session_started", 0, {"session_id": 9}))
    p.feed(jev(4, "level_changed", 5000, {"from": 6, "to": 23}))    # back into DDD
    assert p.target is None


def test_the_abandoned_attempt_still_names_the_star_it_was_run_on():
    """Close BEFORE clearing, the same discipline the course-change branch
    keeps: the run that the boundary ends belongs to what he was practicing,
    not to nothing."""
    attempts = project([
        jev(1, "target_set", 0, {"course_id": 2, "star_id": 6}),
        jev(2, "practice_reset", 1000, {"igt_frames_before": 0,
                                        "mario_acted": True}),
        jev(3, "session_started", 0, {"session_id": 9}),
    ])
    [a] = attempts
    assert a.outcome == "abandoned" and (a.course_id, a.star_id) == (2, 6)


def test_a_strategy_choice_is_a_preference_and_DOES_survive():
    """Deliberately not cleared. The target is where he is pointed right now;
    which strategy he practices a star with is a standing preference, and
    re-picking it every launch would be the annoyance, not the fix."""
    p = Projector()
    p.feed(jev(1, "target_set", 0, {"course_id": 2, "star_id": 6,
                                    "strat_tag": "Cannonless"}))
    p.feed(jev(2, "session_started", 0, {"session_id": 9}))
    assert p.strat_by_star[(2, 6)] == "Cannonless"


# -- a deliberate segment pick is not overwritten by an inferred one -----------

def test_a_star_grab_does_not_steal_a_segment_target():
    """Live report 2026-08-01. He picks a course-exit movement, plays the
    course, grabs the star he is leaving with — the ordinary thing to do
    immediately before running that movement — and the grab moves the target
    onto the star. The movement then cannot arm (every castle movement is
    guarded to the target or the active route), has no section, and is simply
    gone from the page.

    The star's own ATTEMPT is still recorded: his earlier ruling on the same
    rule, "the practice log should still exist for star mode in the history,
    just that we don't see it". Only the target move is wrong."""
    p = Projector()
    p.feed(jev(1, "target_set", 0, {"kind": "segment", "segment_id": 21}))
    closed = p.feed(star(2, 900, course=2, star_id=0))
    assert p.target == ("segment", 21)
    assert [(a.course_id, a.star_id, a.outcome) for a in closed] \
        == [(2, 0, "success")]


def test_a_star_grab_still_moves_a_STAR_target():
    """The rule that is not being changed: practising star after star follows
    you around, and that is the whole reason it exists."""
    p = Projector()
    p.feed(jev(1, "target_set", 0, {"course_id": 2, "star_id": 3}))
    p.feed(star(2, 900, course=2, star_id=0))
    assert p.target == ("star", 2, 0)


# ---------------------------------------------------------------------------
# AN ATTEMPT'S DURATION SPANS EVERY INVOLUNTARY RESTART (live report
# 2026-08-03). Usamune restarts its overall counter at an in-level teleporter
# and at a subarea load; neither is a retry, so the attempt runs straight
# through. The closing event only ever reports the LAST leg, and the legs
# before it are sitting in the involuntary anchors the attempt already passed.
# "This is incorrect... We need to be able to time from the actual start time
# of the course."
# ---------------------------------------------------------------------------

def involuntary(id, frame, igt_before, kind="teleport"):
    return jev(id, "practice_reset", frame,
               {"igt_frames_before": igt_before, "mario_acted": True,
                "acted_tracking": True, kind: True})


def test_a_reset_after_in_level_warps_is_timed_from_the_course_start():
    # His CCM run verbatim, journal ids 23273-23287: entry at f363060, three
    # bridge warps taking 194 / 130 / 160 frames, then a reset 268 frames
    # later. The row read 0'08"93 (268f) for a run that took 0'25"06 (752f).
    [row] = project([
        jev(1, "level_changed", 363013, {"from": 6, "to": 5}),
        jev(2, "practice_reset", 363060, {"igt_frames_before": 18,
                                          "mario_acted": True}),
        jev(3, "mario_acted", 363100, {}),
        involuntary(4, 363256, 194),
        involuntary(5, 363388, 130),
        involuntary(6, 363550, 160),
        jev(7, "practice_reset", 363818, {"igt_frames_before": 268,
                                          "mario_acted": True}),
    ])
    assert row.outcome == "reset"
    assert row.igt_frames == 194 + 130 + 160 + 268
    assert row.rta_frames == 363818 - 363060


def test_a_reset_inside_a_subarea_is_timed_from_the_course_start():
    # The same defect the same way round, and it predates the teleporter: walk
    # into the SSL pyramid 484 frames in (journal id 23253) and reset there,
    # and the row used to report only the time since the pyramid door.
    [row] = project([
        jev(1, "level_changed", 255797, {"from": 24, "to": 8}),
        jev(2, "practice_reset", 255845, {"igt_frames_before": 2665,
                                          "mario_acted": True}),
        jev(3, "mario_acted", 255900, {}),
        involuntary(4, 256331, 484, kind="area_load"),
        jev(5, "practice_reset", 256700, {"igt_frames_before": 369,
                                          "mario_acted": True}),
    ])
    assert row.igt_frames == 484 + 369


def test_a_death_after_an_in_level_warp_is_timed_the_same_way():
    [row] = project([
        jev(1, "level_changed", 363013, {"from": 6, "to": 5}),
        jev(2, "practice_reset", 363060, {"igt_frames_before": 18,
                                          "mario_acted": True}),
        jev(3, "mario_acted", 363100, {}),
        involuntary(4, 363256, 194),
        jev(5, "death", 363500, {"igt_frames": 244, "cause": "fall"}),
    ])
    assert row.outcome == "death" and row.igt_frames == 194 + 244


def test_a_star_time_is_never_inflated_by_the_carry():
    """Usamune's result store already answers for the WHOLE star, so a grab
    must not be summed with the legs — that would double-count them."""
    [row] = project([
        jev(1, "level_changed", 363013, {"from": 6, "to": 5}),
        jev(2, "practice_reset", 363060, {"igt_frames_before": 18,
                                          "mario_acted": True}),
        jev(3, "mario_acted", 363100, {}),
        involuntary(4, 363256, 194),
        star(5, 363500, course=5, star_id=0, igt=440),
    ])
    assert row.outcome == "success" and row.igt_frames == 440


def test_an_involuntary_anchor_that_opens_the_attempt_carries_nothing():
    """Walk into a course and straight through a warp: the attempt begins
    THERE, so there is no earlier leg to add."""
    [row] = project([
        jev(1, "level_changed", 363013, {"from": 6, "to": 5}),
        involuntary(2, 363256, 194),
        jev(3, "mario_acted", 363300, {}),
        jev(4, "practice_reset", 363818, {"igt_frames_before": 268,
                                          "mario_acted": True}),
    ])
    assert row.igt_frames == 268


def test_the_carry_does_not_survive_the_attempt_that_earned_it():
    [first, second] = project([
        jev(1, "level_changed", 363013, {"from": 6, "to": 5}),
        jev(2, "practice_reset", 363060, {"igt_frames_before": 18,
                                          "mario_acted": True}),
        jev(3, "mario_acted", 363100, {}),
        involuntary(4, 363256, 194),
        jev(5, "practice_reset", 363818, {"igt_frames_before": 268,
                                          "mario_acted": True}),
        jev(6, "mario_acted", 363900, {}),
        jev(7, "practice_reset", 364200, {"igt_frames_before": 382,
                                          "mario_acted": True}),
    ])
    assert first.igt_frames == 194 + 268
    assert second.igt_frames == 382


# --- the entrance touch's missing destination (task 0081) -------------------

def test_a_historical_touch_recovers_its_destination_from_the_level_edge():
    """Every warp_entered written before 2026-08-04 carries no `to`, because
    the detector could not know one (decomp: sWarpDest is filled 77 frames
    after the touch). The journal still knows: the level edge that follows
    names it, which is exactly what the live detector now waits for."""
    from sm64_events.tracking.projection import warp_destinations
    events = [
        jev(1, "warp_entered", 1000, {"level": 6, "area": 3}),
        jev(2, "level_changed", 1077, {"from": 6, "to": 23}),
    ]
    assert warp_destinations(events) == {1: 23}


def test_a_touch_that_already_names_its_destination_is_left_alone():
    """Forward rows are authoritative; the pre-pass must never second-guess
    one, or a teleporter's honest `to: None` would be overwritten by the next
    level change that happens along."""
    from sm64_events.tracking.projection import warp_destinations
    events = [
        jev(1, "warp_entered", 1000, {"level": 6, "area": 3, "to": None}),
        jev(2, "level_changed", 1077, {"from": 6, "to": 23}),
    ]
    assert warp_destinations(events) == {}


def test_a_historical_touch_with_no_level_edge_recovers_nothing():
    """An in-level teleporter or an aborted fade. Bounded by the same
    HOLD_CAP_FRAMES the live detector holds a touch for, so a touch and a
    level change minutes apart are never paired."""
    from sm64_events.detectors.warp import WarpDetector
    from sm64_events.tracking.projection import warp_destinations
    far = 1000 + WarpDetector.HOLD_CAP_FRAMES + 1
    assert warp_destinations([
        jev(1, "warp_entered", 1000, {"level": 5, "area": 1}),
        jev(2, "level_changed", far, {"from": 5, "to": 23}),
    ]) == {}


def test_an_establishing_level_row_is_not_a_crossing():
    """detectors/level.py journals establishing/corrective rows with from ==
    to. Pairing a touch with one would stamp the level Mario was ALREADY in."""
    from sm64_events.tracking.projection import warp_destinations
    assert warp_destinations([
        jev(1, "warp_entered", 1000, {"level": 6, "area": 3}),
        jev(2, "level_changed", 1010, {"from": 6, "to": 6}),
        jev(3, "level_changed", 1077, {"from": 6, "to": 23}),
    ]) == {1: 23}


def test_the_recovered_destination_reaches_the_matcher():
    """End to end: a pinned touch clause closes a segment on a HISTORICAL row.
    Without the pre-pass this records nothing -- measured over the real
    journal, 54 of 106 segment successes vanish."""
    from sm64_events.tracking.segments import SegmentDef
    ddd = SegmentDef(
        id=9, name="MIPS Clip", enabled=True,
        start_triggers=[{"type": "level_exit", "from": 7, "to": 6}],
        end_triggers=[{"type": "warp_entered", "level": 6, "to": 23}],
        waypoints=[], guards=[], match_mode="strict")
    events = [
        jev(1, "level_changed", 1000, {"from": 7, "to": 6, "from_area": 1}),
        jev(2, "area_changed", 1000, {"level": 6, "from": 1, "to": 3,
                                      "from_transient": True}),
        jev(3, "warp_entered", 1400, {"level": 6, "area": 3}),
        jev(4, "level_changed", 1477, {"from": 6, "to": 23}),
    ]
    attempts, _ = replay(events, segments=[ddd])
    wins = [a for a in attempts if a.segment_id == 9 and a.outcome == "success"]
    assert len(wins) == 1
    assert wins[0].rta_frames == 400, "timed to the TOUCH, not the load"
