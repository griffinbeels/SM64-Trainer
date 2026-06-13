# tests/test_key.py
from datetime import datetime, timezone

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.key import KeyGrabDetector
from sm64_events.memory.addresses import (ACT_JUMBO_STAR_CUTSCENE,
                                          ACT_STAR_DANCE_EXIT,
                                          BOWSER_1_ARENA, BOWSER_3_ARENA)

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
    assert events[0].payload["level"] == BOWSER_1_ARENA
    assert events[0].payload["which"] == "bitdw"
    # every fight-ending grab now carries an IGT (see igt test below)
    assert "igt_frames" in events[0].payload


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
    assert events[0].payload["level"] == BOWSER_2_ARENA
    assert events[0].payload["which"] == "bitfs"


def test_persisting_dance_action_is_silent():
    d = KeyGrabDetector()
    d.process(snap(), snap(mario_action=ACT_STAR_DANCE_EXIT))
    assert d.process(snap(mario_action=ACT_STAR_DANCE_EXIT),
                     snap(mario_action=ACT_STAR_DANCE_EXIT)) == []


def test_grand_star_in_b3_arena_is_the_fight_end():
    # Live-verified 2026-06-12: B3 grand star enters ACT_JUMBO_STAR_CUTSCENE
    # (0x1909), not a star-dance action; numStars unchanged; no star_collected.
    events = KeyGrabDetector().process(
        snap(curr_level=BOWSER_3_ARENA),
        snap(curr_level=BOWSER_3_ARENA, mario_action=ACT_JUMBO_STAR_CUTSCENE))
    assert len(events) == 1
    assert events[0].type == "key_grabbed"
    assert events[0].payload["level"] == BOWSER_3_ARENA
    assert events[0].payload["which"] == "grand"


def test_jumbo_star_cutscene_outside_fight_levels_is_silent():
    # ACT_JUMBO_STAR_CUTSCENE edge outside the three Bowser arenas must not fire.
    events = KeyGrabDetector().process(
        snap(curr_level=24),
        snap(curr_level=24, mario_action=ACT_JUMBO_STAR_CUTSCENE))
    assert events == []


def test_grand_star_carries_usamune_igt_like_a_star():
    # The B3 grand star fires key_grabbed (not star_collected), so its time
    # would otherwise come from a wall-frame delta that is one display-tick
    # short of Usamune (live report 2026-06-12: 0'46"23 vs 0'46"26). It now
    # carries Usamune's IGT from the shared clock, exactly like a star grab.
    # Usamune's result store is freshly written at the grab -> exact value.
    d = KeyGrabDetector()
    prev = snap(curr_level=BOWSER_3_ARENA, global_timer=1387,
                igt_overall=1386, igt_result=0)
    curr = snap(curr_level=BOWSER_3_ARENA, mario_action=ACT_JUMBO_STAR_CUTSCENE,
                global_timer=1389, mario_action_timer=2,
                igt_overall=1388, igt_result=1388)
    [ev] = d.process(prev, curr)
    assert ev.frame == 1387  # touch frame: 1389 - 2
    assert ev.payload["igt_frames"] == 1388  # Usamune's exact displayed value
    assert ev.payload["igt"] == "0'46\"26"
    assert ev.payload["igt_source"] == "result"


def test_grand_star_igt_falls_back_to_overall_counter():
    # If Usamune's result store is NOT freshly written for the jumbo-star
    # cutscene, the overall counter (pause-safe, resets at arena entry) plus
    # the display tick still reproduces Usamune's number.
    d = KeyGrabDetector()
    prev = snap(curr_level=BOWSER_3_ARENA, global_timer=1386,
                igt_overall=1386, igt_result=0)
    curr = snap(curr_level=BOWSER_3_ARENA, mario_action=ACT_JUMBO_STAR_CUTSCENE,
                global_timer=1387, mario_action_timer=0,
                igt_overall=1387, igt_result=0)
    [ev] = d.process(prev, curr)
    assert ev.payload["igt_source"] == "counter"
    assert ev.payload["igt_frames"] == 1388  # 1387 counter + 1 display tick
