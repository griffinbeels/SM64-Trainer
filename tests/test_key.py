# tests/test_key.py
from datetime import datetime, timezone

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.key import KeyGrabDetector
from sm64_events.memory.addresses import ACT_STAR_DANCE_EXIT, BOWSER_1_ARENA

ACT_IDLE = 0x0C400201


def snap(**overrides) -> GameSnapshot:
    defaults = dict(
        wall_time_utc=datetime(2026, 6, 11, tzinfo=timezone.utc),
        global_timer=3000, mario_action=ACT_IDLE, mario_action_timer=0,
        num_stars=8, last_completed_course=1, last_completed_star=1,
        curr_level=BOWSER_1_ARENA, curr_area=1)
    defaults.update(overrides)
    return GameSnapshot(**defaults)


def test_grab_action_in_bowser_arena_is_a_key():
    events = KeyGrabDetector().process(
        snap(), snap(mario_action=ACT_STAR_DANCE_EXIT))
    assert len(events) == 1
    assert events[0].type == "key_grabbed"
    assert events[0].payload == {"level": BOWSER_1_ARENA, "which": "bitdw"}


def test_grab_action_outside_arena_is_not_a_key():
    events = KeyGrabDetector().process(
        snap(curr_level=24), snap(curr_level=24,
                                  mario_action=ACT_STAR_DANCE_EXIT))
    assert events == []
