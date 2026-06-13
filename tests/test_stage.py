from datetime import datetime, timezone

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.stage import StageChangeDetector


def snap(**overrides) -> GameSnapshot:
    defaults = dict(
        wall_time_utc=datetime(2026, 6, 12, tzinfo=timezone.utc),
        global_timer=1000, mario_action=0, mario_action_timer=0,
        num_stars=5, last_completed_course=1, last_completed_star=3,
        curr_level=6, curr_area=1)          # 6 = Castle Inside (no course)
    defaults.update(overrides)
    return GameSnapshot(**defaults)


def test_entering_a_main_course_emits_in_stage():
    d = StageChangeDetector()
    d.process(snap(curr_level=6), snap(curr_level=6))            # establish: castle
    events = d.process(snap(curr_level=6), snap(curr_level=8))   # -> SSL (course 8)
    assert len(events) == 1
    assert events[0].type == "stage_changed"
    assert events[0].payload == {"course_id": 8, "level": 8, "in_stage": True}


def test_first_pair_establishes():
    events = StageChangeDetector().process(snap(curr_level=8), snap(curr_level=8))
    assert len(events) == 1
    assert events[0].payload == {"course_id": 8, "level": 8, "in_stage": True}


def test_leaving_to_the_castle_emits_not_in_stage():
    d = StageChangeDetector()
    d.process(snap(curr_level=8), snap(curr_level=8))            # in SSL
    events = d.process(snap(curr_level=8), snap(curr_level=6))   # -> castle
    assert len(events) == 1
    assert events[0].payload == {"course_id": None, "level": 6, "in_stage": False}


def test_bowser_course_is_not_a_main_stage():
    # course_for_level(17) == 16 (a Bowser COURSE) — excluded by the 1..15 gate.
    # Establish in a MAIN course so the move changes the resolved course
    # (8 -> None); a None->None move correctly emits nothing (see the
    # dedicated test below).
    d = StageChangeDetector()
    d.process(snap(curr_level=8), snap(curr_level=8))            # SSL (course 8)
    events = d.process(snap(curr_level=8), snap(curr_level=17))  # -> BitDW
    assert len(events) == 1
    assert events[0].payload == {"course_id": None, "level": 17, "in_stage": False}


def test_secret_star_area_is_not_a_main_stage():
    # course_for_level(27) == 19 (Secret Slide course) — excluded.
    d = StageChangeDetector()
    d.process(snap(curr_level=8), snap(curr_level=8))            # SSL (course 8)
    events = d.process(snap(curr_level=8), snap(curr_level=27))  # -> Secret Slide
    assert len(events) == 1
    assert events[0].payload["in_stage"] is False
    assert events[0].payload["course_id"] is None


def test_no_event_between_two_non_stage_levels():
    # castle -> Bowser arena: neither is a main course, so the banner's hidden
    # state is unchanged -> no stage_changed (keyed on resolved course, not level).
    d = StageChangeDetector()
    d.process(snap(curr_level=6), snap(curr_level=6))            # castle (None)
    assert d.process(snap(curr_level=6), snap(curr_level=17)) == []


def test_no_event_on_in_course_area_switch():
    # Keyed on course, not level/area: an SSL area switch (level stays 8) is
    # silent, unlike area_changed.
    d = StageChangeDetector()
    d.process(snap(curr_level=8, curr_area=1), snap(curr_level=8, curr_area=1))
    assert d.process(snap(curr_level=8, curr_area=1),
                     snap(curr_level=8, curr_area=2)) == []


def test_no_event_while_course_stable():
    d = StageChangeDetector()
    d.process(snap(curr_level=8), snap(curr_level=8))
    assert d.process(snap(curr_level=8), snap(curr_level=8)) == []


def test_reattach_gap_to_a_new_course_is_caught():
    # Keyed on last EMITTED course: a change across a detach gap still emits.
    d = StageChangeDetector()
    d.process(snap(curr_level=6), snap(curr_level=6))           # emitted: None
    events = d.process(snap(curr_level=9), snap(curr_level=9))  # reattached in BoB
    assert len(events) == 1
    assert events[0].payload == {"course_id": 1, "level": 9, "in_stage": True}


def test_frame_matches_curr_global_timer():
    d = StageChangeDetector()
    d.process(snap(curr_level=6), snap(curr_level=6))
    events = d.process(snap(curr_level=6), snap(curr_level=8, global_timer=4321))
    assert events[0].frame == 4321
