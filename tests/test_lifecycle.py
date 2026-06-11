# tests/test_lifecycle.py
from datetime import datetime, timezone

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.lifecycle import GameResetDetector


def snap(timer: int) -> GameSnapshot:
    return GameSnapshot(
        wall_time_utc=datetime(2026, 6, 10, tzinfo=timezone.utc),
        global_timer=timer, mario_action=0, mario_action_timer=0,
        num_stars=0, last_completed_course=0, last_completed_star=0,
    )


def test_backward_jump_into_boot_range_emits_game_reset():
    events = GameResetDetector().process(snap(5000), snap(100))
    assert len(events) == 1
    assert events[0].type == "game_reset"
    assert events[0].frame == 100


def test_backward_jump_to_midgame_value_is_a_state_load_not_a_reset():
    # savestate/section-state loads are AnchorDetector's state_loaded
    assert GameResetDetector().process(snap(5000), snap(3000)) == []


def test_forward_progress_is_silent():
    assert GameResetDetector().process(snap(100), snap(101)) == []


def test_paused_game_is_silent():
    assert GameResetDetector().process(snap(100), snap(100)) == []
