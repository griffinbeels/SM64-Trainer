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
    assert proj.target == (2, 2)
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
