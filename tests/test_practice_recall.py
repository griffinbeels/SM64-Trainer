"""Recall follows the latest valid played success, never per-area stale history."""
from dataclasses import replace

import pytest

from test_castle_selector_choices import LOBBY_DOOR, Play, definitions

from sm64_events.tracking.projection import replay
from sm64_events.tracking.prune import PRUNE_EVENT


def door(play, key=LOBBY_DOOR):
    play.send("moment_reached", {"kind": "door_arrived", "level": 6, "area": 1,
              "landmark": {"key": key}, "igt_frames": 2})


def complete_lblj(play, *, pick=False, arrive=True):
    if arrive:
        play.place(16)
        play.place(6)
    if pick:
        play.pick(1)
    door(play)
    play.send("mario_acted", {"igt_frames": 2})
    play.send("moment_reached", {"kind": "door_open", "level": 6, "area": 1,
              "landmark": {"key": "6:1:bhvDoor:766,0,-3247"}, "igt_frames": 80})
    rows = play.send("warp_entered", {"level": 6, "area": 1, "to": 17,
                                    "igt_frames": 600, "igt_source": "counter"})
    row = next(a for a in rows if a.segment_id == 1)
    assert row.outcome == "success" and not row.cleared
    return row


def complete_star(play):
    play.place(9)
    play.send("practice_reset", {"igt_frames_before": 0})
    rows = play.send("star_collected", {"course_id": 1, "star_id": 0,
                    "igt_frames": 400, "igt_timed_at": "xcam"})
    row = next(a for a in rows if a.course_id == 1 and a.star_id == 0)
    assert row.outcome == "success" and not row.cleared
    return row


def return_to_start(play):
    play.place(16)
    play.place(6)
    door(play)
    assert play.projector._castle_selector_ids() >= {1, 11}


def assert_replay_agrees(play):
    _, rebuilt = replay(play.events, segments=play.defs)
    assert rebuilt.target == play.projector.target
    assert rebuilt._session_successes == play.projector._session_successes


def test_latest_lblj_success_selects_only_on_its_specific_start():
    play = Play()
    complete_lblj(play)
    play.place(9)  # An accidental visit retires the current focus.
    play.place(16)
    play.place(6)
    assert play.projector.target is None
    door(play, "6:1:bhvDoorWarp:999,0,999")
    assert play.projector.target is None
    door(play)
    assert play.projector.target == ("segment", 1)
    assert play.projector._picked_target is None  # Recall is not a new click.
    assert not any(e.type == "target_set" for e in play.events)
    assert_replay_agrees(play)


@pytest.mark.parametrize("pick", [False, True])
def test_a_newer_successful_star_blocks_lblj_inference_and_dormant_click(pick):
    play = Play()
    complete_lblj(play, pick=pick)
    complete_star(play)
    return_to_start(play)
    assert play.projector.target != ("segment", 1)
    assert_replay_agrees(play)


@pytest.mark.parametrize("old_click", [False, True])
def test_a_newer_star_click_blocks_old_castle_recall_without_a_new_success(old_click):
    play = Play()
    complete_lblj(play, pick=old_click)
    successes = dict(play.projector._session_successes)
    play.place(9)
    play.send("target_set", {"course_id": 1, "star_id": 0})
    assert play.projector.target == ("star", 1, 0)
    return_to_start(play)
    assert play.projector._session_successes == successes
    assert play.projector.target is None
    assert_replay_agrees(play)


def test_a_newer_other_segment_replaces_the_old_success():
    play = Play()
    complete_lblj(play, pick=True)
    play.place(16)
    play.place(6)
    play.send("mario_acted")
    rows = play.send("warp_entered", {"level": 6, "area": 1, "to": 9})
    assert any(a.segment_id == 11 and a.outcome == "success" for a in rows)
    play.place(9)
    play.place(16)
    play.place(6)
    assert play.projector.target == ("segment", 11)
    door(play)
    assert play.projector.target == ("segment", 11)
    assert_replay_agrees(play)


@pytest.mark.parametrize("failure", ["practice_reset", "death"])
def test_foreign_failed_attempt_does_not_replace_last_success(failure):
    play = Play()
    complete_lblj(play)
    play.place(9)
    play.send("practice_reset", {"igt_frames_before": 0})
    play.send("mario_acted")
    rows = play.send(failure, {"igt_frames_before": 80, "igt_frames": 80,
                              "mario_acted": True})
    outcome = "reset" if failure == "practice_reset" else "death"
    assert any(a.outcome == outcome for a in rows)
    return_to_start(play)
    assert play.projector.target == ("segment", 1)
    assert_replay_agrees(play)


def test_a_newer_actual_click_wins_and_remains_a_session_choice():
    play = Play()
    complete_lblj(play)
    play.place(9)
    play.place(16)
    play.place(6)
    play.pick(11)
    door(play)
    assert play.projector.target == ("segment", 11)
    play.place(9)
    return_to_start(play)
    assert play.projector.target == ("segment", 11)
    assert_replay_agrees(play)


@pytest.mark.parametrize("removal", ["clear", "wipe", "prune"])
def test_removing_the_newest_success_falls_back_to_surviving_evidence(removal):
    play = Play()
    complete_lblj(play)
    row = complete_star(play)
    if removal == "clear":
        play.send("attempt_cleared", {"attempt_id": row.id, "reason": "accidental"})
    elif removal == "wipe":
        play.send("data_wiped", {"kind": "star", "course_id": 1, "star_id": 0,
                  "segment_id": None, "session_id": None})
    else:
        play.send(PRUNE_EVENT, {"attempt_ids": [row.id]})
    _, play.projector = replay(play.events, segments=play.defs)
    return_to_start(play)
    assert play.projector.target == ("segment", 1)
    assert_replay_agrees(play)


def test_restoring_a_newer_success_blocks_old_lblj_again():
    play = Play()
    complete_lblj(play)
    row = complete_star(play)
    play.send("attempt_cleared", {"attempt_id": row.id, "reason": "accidental"})
    play.send("attempt_restored", {"attempt_id": row.id})
    _, play.projector = replay(play.events, segments=play.defs)
    return_to_start(play)
    assert play.projector.target != ("segment", 1)
    assert_replay_agrees(play)


def test_time_invalid_success_does_not_erase_the_last_valid_activity():
    play = Play()
    complete_lblj(play)
    complete_star(play)
    _, play.projector = replay(play.events, segments=play.defs,
                              time_filters={"1:0": {"min_frames": 500}})
    return_to_start(play)
    assert play.projector.target == ("segment", 1)


def test_new_session_forgets_practice_recall():
    play = Play()
    complete_lblj(play, pick=True)
    play.send("session_started")
    return_to_start(play)
    assert play.projector.target is None
    assert_replay_agrees(play)


def test_importing_a_time_does_not_replace_current_practice():
    play = Play()
    complete_lblj(play)
    play.send("time_imported", {"course_id": 1, "star_id": 0,
                               "frames": 300, "strat_tag": "Standard"})
    play.place(9)
    return_to_start(play)
    assert play.projector.target == ("segment", 1)
    assert_replay_agrees(play)


def test_simultaneous_different_successes_cannot_choose_by_definition_order():
    defs = definitions()
    play = Play([*defs, replace(defs[0], id=13, name="Other lobby technique")])
    complete_lblj(play)
    play.place(9)
    play.place(16)
    play.place(6)
    door(play)
    assert play.projector.target is None
    assert_replay_agrees(play)


def test_newer_ambiguous_piece_is_a_barrier_to_old_lblj():
    defs = definitions()
    piece = replace(defs[2], id=13, name="Ambiguous piece",
                    start_triggers=[{"type": "level_enter", "to": 9}],
                    end_triggers=[{"type": "moment_reached", "kind": "door_open",
                                   "level": 9, "ordinal": 1}],
                    parents=["star:1:0", "star:1:1"])
    play = Play([*defs, piece])
    complete_lblj(play, pick=True)
    play.place(9)
    rows = play.send("moment_reached", {"kind": "door_open", "level": 9,
                                      "area": 1, "ordinal": 1})
    assert any(a.segment_id == 13 and a.outcome == "success" for a in rows)
    return_to_start(play)
    assert play.projector.target != ("segment", 1)
    assert_replay_agrees(play)


def test_an_older_overlapping_success_cannot_borrow_recency_to_override_a_click():
    defs = definitions()
    long = replace(defs[2], id=13, name="Older overlapping activity", match_mode="loose",
                   start_triggers=[{"type": "moment_reached", "kind": "door_open",
                                    "level": 6, "ordinal": 50}],
                   end_triggers=[{"type": "moment_reached", "kind": "door_open",
                                  "level": 6, "ordinal": 51}])
    play = Play([*defs, long])
    play.place(16)
    play.place(6)
    play.send("moment_reached", {"kind": "door_open", "level": 6, "ordinal": 50})
    complete_lblj(play, arrive=False)
    play.pick(11)
    rows = play.send("moment_reached", {"kind": "door_open", "level": 6, "ordinal": 51})
    assert any(a.segment_id == 13 and a.outcome == "success" for a in rows)
    play.place(9)
    return_to_start(play)
    assert play.projector.target == ("segment", 11)
    assert_replay_agrees(play)


def test_disabled_or_deleted_latest_definition_cannot_resurrect_a_choice():
    play = Play()
    complete_lblj(play)
    play.defs = [replace(definitions()[0], enabled=False), *definitions()[1:]]
    _, play.projector = replay(play.events, segments=play.defs)
    play.place(16)
    play.place(6)
    door(play)
    assert play.projector.target != ("segment", 1)
