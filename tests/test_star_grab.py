# tests/test_star_grab.py
from datetime import datetime, timezone

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.star_grab import StarGrabDetector
from sm64_events.memory import addresses as A

ACT_IDLE = 0x0C400201  # any non-star-dance action works for tests


def snap(**overrides) -> GameSnapshot:
    defaults = dict(
        wall_time_utc=datetime(2026, 6, 10, tzinfo=timezone.utc),
        global_timer=1000,
        mario_action=ACT_IDLE,
        mario_action_timer=0,
        num_stars=5,
        last_completed_course=1,
        last_completed_star=3,
    )
    defaults.update(overrides)
    return GameSnapshot(**defaults)


def test_edge_into_star_dance_emits_identified_event():
    prev = snap(num_stars=5)
    curr = snap(mario_action=A.ACT_STAR_DANCE_EXIT, mario_action_timer=2,
                global_timer=1002, num_stars=6,
                last_completed_course=1, last_completed_star=3)
    events = StarGrabDetector().process(prev, curr)
    assert len(events) == 1
    ev = events[0]
    assert ev.type == "star_collected"
    assert ev.frame == 1000  # back-computed: 1002 - 2
    assert ev.payload == {
        "course_id": 1,
        "course_name": "Bob-omb Battlefield",
        "star_id": 2,  # game stores 1-based (3); API is 0-based
        "star_name": "Shoot to the Island in the Sky",
        "already_collected": False,
    }


def test_already_collected_star_still_fires_with_flag_true():
    prev = snap(num_stars=6)
    curr = snap(mario_action=A.ACT_STAR_DANCE_NO_EXIT, num_stars=6)
    events = StarGrabDetector().process(prev, curr)
    assert len(events) == 1
    assert events[0].payload["already_collected"] is True


def test_all_grab_action_variants_fire():
    for action in (A.ACT_STAR_DANCE_EXIT, A.ACT_STAR_DANCE_WATER,
                   A.ACT_STAR_DANCE_NO_EXIT, A.ACT_FALL_AFTER_STAR_GRAB):
        events = StarGrabDetector().process(snap(), snap(mario_action=action))
        assert len(events) == 1, hex(action)


def test_no_event_while_dance_continues():
    prev = snap(mario_action=A.ACT_STAR_DANCE_EXIT)
    curr = snap(mario_action=A.ACT_STAR_DANCE_EXIT, mario_action_timer=10)
    assert StarGrabDetector().process(prev, curr) == []


def test_no_event_on_fall_to_dance_transition():
    # midair grab: FALL_AFTER_STAR_GRAB already fired the event; the
    # follow-up dance action must not fire a second one
    prev = snap(mario_action=A.ACT_FALL_AFTER_STAR_GRAB)
    curr = snap(mario_action=A.ACT_STAR_DANCE_NO_EXIT)
    assert StarGrabDetector().process(prev, curr) == []


def test_no_event_without_edge():
    assert StarGrabDetector().process(snap(), snap(global_timer=1001)) == []


def test_same_star_twice_produces_two_events():
    d = StarGrabDetector()
    first = d.process(snap(), snap(mario_action=A.ACT_STAR_DANCE_EXIT))
    between = d.process(snap(mario_action=A.ACT_STAR_DANCE_EXIT), snap())
    second = d.process(snap(), snap(mario_action=A.ACT_STAR_DANCE_EXIT))
    assert len(first) == 1 and between == [] and len(second) == 1


def test_never_collected_sentinel_is_dropped():
    # last_completed_star == 0 means "never set" — cannot identify a star
    curr = snap(mario_action=A.ACT_STAR_DANCE_EXIT,
                last_completed_course=0, last_completed_star=0)
    assert StarGrabDetector().process(snap(), curr) == []


def test_frame_never_negative():
    curr = snap(mario_action=A.ACT_STAR_DANCE_EXIT,
                global_timer=1, mario_action_timer=5)
    events = StarGrabDetector().process(snap(), curr)
    assert events[0].frame == 0
