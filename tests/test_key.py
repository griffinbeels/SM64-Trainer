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


def test_frame_is_touch_frame_not_detection_frame():
    events = KeyGrabDetector().process(
        snap(), snap(mario_action=ACT_STAR_DANCE_EXIT,
                     global_timer=3010, mario_action_timer=4))
    assert events[0].frame == 3006


def test_grab_in_b2_arena_is_the_bitfs_key():
    from sm64_events.memory.addresses import BOWSER_2_ARENA
    events = KeyGrabDetector().process(
        snap(curr_level=BOWSER_2_ARENA),
        snap(curr_level=BOWSER_2_ARENA, mario_action=ACT_STAR_DANCE_EXIT))
    assert events[0].payload == {"level": BOWSER_2_ARENA, "which": "bitfs"}


def test_persisting_dance_action_is_silent():
    d = KeyGrabDetector()
    d.process(snap(), snap(mario_action=ACT_STAR_DANCE_EXIT))
    assert d.process(snap(mario_action=ACT_STAR_DANCE_EXIT),
                     snap(mario_action=ACT_STAR_DANCE_EXIT)) == []
