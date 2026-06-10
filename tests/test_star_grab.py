# tests/test_star_grab.py
from datetime import datetime, timezone

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.star_grab import StarGrabDetector, format_igt
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
                last_completed_course=1, last_completed_star=3,
                igt_timer=233)
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
        "igt_frames": 231,  # back-computed: 233 - 2
        "igt": "0'07\"70",  # 231 frames at 30 fps
        "igt_reconstructed": False,
    }


def test_igt_format():
    assert format_igt(0) == "0'00\"00"
    assert format_igt(231) == "0'07\"70"     # the screenshot case
    assert format_igt(1800) == "1'00\"00"    # exactly one minute
    assert format_igt(1800 + 65) == "1'02\"16"  # 65 frames = 2s + 5f (16cs)


def test_igt_zero_when_timer_unused():
    curr = snap(mario_action=A.ACT_STAR_DANCE_EXIT, igt_timer=0)
    ev = StarGrabDetector().process(snap(), curr)[0]
    assert ev.payload["igt_frames"] == 0
    assert ev.payload["igt"] == "0'00\"00"


def run_pairs(detector, snaps):
    """Feed consecutive snapshot pairs; return all emitted events."""
    events = []
    for prev, curr in zip(snaps, snaps[1:]):
        events.extend(detector.process(prev, curr))
    return events


def test_igt_reset_racing_grab_reports_prior_attempt_time():
    # Regression from a live trace (2026-06-10): the player's reset landed
    # ~3 game frames BEFORE the star touch, so the raw IGT read 5 at the
    # grab. The event must report the attempt that earned the star:
    # 185 frames at g=429726, touch at g=429731 -> 190 frames (0'06"33).
    snaps = [
        snap(global_timer=429722, igt_timer=181),
        snap(global_timer=429726, igt_timer=185),
        snap(global_timer=429729, igt_timer=3),   # Usamune reset hit here
        snap(global_timer=429730, igt_timer=4),
        snap(global_timer=429731, igt_timer=5,
             mario_action=A.ACT_FALL_AFTER_STAR_GRAB, mario_action_timer=0),
    ]
    events = run_pairs(StarGrabDetector(), snaps)
    assert len(events) == 1
    assert events[0].payload["igt_frames"] == 190
    assert events[0].payload["igt"] == "0'06\"33"
    assert events[0].payload["igt_reconstructed"] is True
    assert events[0].frame == 429731


def test_igt_reset_between_touch_and_sample_uses_exact_prior_value():
    # Touch frame back-computes to BEFORE the reset gap: the prior attempt's
    # clock extrapolates exactly to the touch.
    snaps = [
        snap(global_timer=1000, igt_timer=500),
        snap(global_timer=1003, igt_timer=1,
             mario_action=A.ACT_STAR_DANCE_EXIT, mario_action_timer=3),
    ]
    events = run_pairs(StarGrabDetector(), snaps)
    assert events[0].payload["igt_frames"] == 500  # 500 + (1000 - 1000)
    assert events[0].payload["igt_reconstructed"] is True


def test_grab_well_after_reset_is_a_genuine_new_attempt():
    # A reset in recent history must NOT hijack a grab that happened a full
    # attempt later (post-reset IGT >= RESET_GRACE_FRAMES).
    snaps = [
        snap(global_timer=1000, igt_timer=400),
        snap(global_timer=1010, igt_timer=5),    # reset
        snap(global_timer=1050, igt_timer=45),
        snap(global_timer=1100, igt_timer=95,
             mario_action=A.ACT_STAR_DANCE_EXIT, mario_action_timer=0),
    ]
    events = run_pairs(StarGrabDetector(), snaps)
    assert events[0].payload["igt_frames"] == 95
    assert events[0].payload["igt_reconstructed"] is False


def test_history_cleared_when_time_jumps_backward():
    # A savestate load rewinds global_timer; pre-jump IGT samples must not
    # be used for reconstruction afterwards.
    d = StarGrabDetector()
    snaps_before = [
        snap(global_timer=5000, igt_timer=900),
        snap(global_timer=5001, igt_timer=901),
    ]
    run_pairs(d, snaps_before)
    snaps_after = [
        snap(global_timer=5001, igt_timer=901),
        snap(global_timer=100, igt_timer=50),  # backward jump (savestate)
        snap(global_timer=110, igt_timer=60,
             mario_action=A.ACT_STAR_DANCE_EXIT, mario_action_timer=0),
    ]
    events = run_pairs(d, snaps_after)
    assert len(events) == 1
    assert events[0].payload["igt_frames"] == 60
    assert events[0].payload["igt_reconstructed"] is False


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
