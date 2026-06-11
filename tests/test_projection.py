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
        jev(2, "practice_reset", 1000, {"igt_frames_before": 0}),
        jev(3, "practice_reset", 1400, {"igt_frames_before": 380}),
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
        jev(2, "practice_reset", 1000, {"igt_frames_before": 0}),
        jev(3, "practice_reset", 1400, {"igt_frames_before": 380}),
    ])
    a = attempts[0]
    assert (a.course_id, a.star_id, a.strat_tag) == (8, 2, "carpetless")


def test_valid_grab_moves_target_and_strat_persists():
    attempts = project([
        jev(1, "target_set", 0, {"course_id": 8, "star_id": 2, "strat_tag": "x"}),
        star(2, 900, course=2, star_id=2),                     # grab WF — target moves
        jev(3, "practice_reset", 1000, {"igt_frames_before": 0}),
        jev(4, "practice_reset", 1400, {"igt_frames_before": 380}),
    ])
    fail = attempts[1]
    assert (fail.course_id, fail.star_id) == (2, 2)
    assert fail.strat_tag == "x"   # strat is sticky until changed


def test_cleared_grab_does_not_move_target_retroactively():
    # going for SSL (8,2); accidentally grab WF (2,2); failures follow;
    # then the WF grab is marked a mistake -> failures re-attribute to SSL.
    events = [
        jev(1, "target_set", 0, {"course_id": 8, "star_id": 2}),
        star(2, 900, course=2, star_id=2),                     # accidental
        jev(3, "practice_reset", 1000, {"igt_frames_before": 0}),
        jev(4, "practice_reset", 1400, {"igt_frames_before": 380}),
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
        jev(3, "level_changed", 0, {"from": 1, "to": 2}),
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
        jev(2, "practice_reset", 1000, {"igt_frames_before": 0}),
    ])
    assert len(attempts) == 1            # the grab closed; the reset is open
    assert isinstance(proj, Projector)
    assert proj.target == (2, 2)
    more = proj.feed(star(3, 1300))
    assert len(more) == 1 and more[0].id == 2 and more[0].outcome == "success"
