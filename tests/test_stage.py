from datetime import datetime, timezone

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.stage import StageChangeDetector


def snap(**overrides) -> GameSnapshot:
    defaults = dict(
        wall_time_utc=datetime(2026, 6, 12, tzinfo=timezone.utc),
        global_timer=1000, mario_action=0, mario_action_timer=0,
        num_stars=5, last_completed_course=1, last_completed_star=3,
        curr_level=6, curr_area=1)          # 6 = Castle Inside, area 1 = lobby
    defaults.update(overrides)
    return GameSnapshot(**defaults)


# --- star context (main courses) ------------------------------------------

def test_entering_a_main_course_emits_in_stage():
    d = StageChangeDetector()
    d.process(snap(curr_level=6), snap(curr_level=6))            # establish: castle
    events = d.process(snap(curr_level=6), snap(curr_level=8))   # -> SSL (course 8)
    assert len(events) == 1
    assert events[0].type == "stage_changed"
    assert events[0].payload == {"course_id": 8, "level": 8, "area": 1,
                                 "in_stage": True}


def test_first_pair_establishes():
    events = StageChangeDetector().process(snap(curr_level=8), snap(curr_level=8))
    assert len(events) == 1
    assert events[0].payload == {"course_id": 8, "level": 8, "area": 1,
                                 "in_stage": True}


def test_bowser_course_is_not_a_main_stage():
    # course_for_level(17) == 16 (a Bowser COURSE) — excluded by the 1..15 gate,
    # and level 17 is not Castle Inside, so it's no banner context at all.
    d = StageChangeDetector()
    d.process(snap(curr_level=8), snap(curr_level=8))            # SSL (course 8)
    events = d.process(snap(curr_level=8), snap(curr_level=17))  # -> BitDW
    assert len(events) == 1
    assert events[0].payload == {"course_id": None, "level": 17, "area": 1,
                                 "in_stage": False}


def test_no_event_on_in_course_area_switch():
    # Keyed on context, not raw area: an SSL area switch (level stays 8) is
    # silent, unlike a castle subarea switch.
    d = StageChangeDetector()
    d.process(snap(curr_level=8, curr_area=1), snap(curr_level=8, curr_area=1))
    assert d.process(snap(curr_level=8, curr_area=1),
                     snap(curr_level=8, curr_area=2)) == []


def test_no_event_while_course_stable():
    d = StageChangeDetector()
    d.process(snap(curr_level=8), snap(curr_level=8))
    assert d.process(snap(curr_level=8), snap(curr_level=8)) == []


def test_reattach_gap_to_a_new_course_is_caught():
    # Keyed on last EMITTED context: a change across a detach gap still emits.
    d = StageChangeDetector()
    d.process(snap(curr_level=6), snap(curr_level=6))           # emitted: castle lobby
    events = d.process(snap(curr_level=9), snap(curr_level=9))  # reattached in BoB
    assert len(events) == 1
    assert events[0].payload == {"course_id": 1, "level": 9, "area": 1,
                                 "in_stage": True}


def test_frame_matches_curr_global_timer():
    d = StageChangeDetector()
    d.process(snap(curr_level=6), snap(curr_level=6))
    events = d.process(snap(curr_level=6), snap(curr_level=8, global_timer=4321))
    assert events[0].frame == 4321


# --- castle segment context (Castle Inside subareas) ----------------------

def test_entering_castle_inside_emits_segment_context():
    # Leaving SSL into the castle is in_stage=False (no main course) but IS the
    # segment context — the banner shows that subarea's segments.
    d = StageChangeDetector()
    d.process(snap(curr_level=8), snap(curr_level=8))                       # SSL
    events = d.process(snap(curr_level=8), snap(curr_level=6, curr_area=1))  # -> lobby
    assert len(events) == 1
    assert events[0].payload == {"course_id": None, "level": 6, "area": 1,
                                 "in_stage": False}


def test_castle_subarea_switch_emits():
    # lobby -> upstairs swaps the offered segments, so it re-emits (unlike an
    # in-course area switch).
    d = StageChangeDetector()
    d.process(snap(curr_level=6, curr_area=1), snap(curr_level=6, curr_area=1))
    events = d.process(snap(curr_level=6, curr_area=1),
                       snap(curr_level=6, curr_area=2))
    assert len(events) == 1
    assert events[0].payload == {"course_id": None, "level": 6, "area": 2,
                                 "in_stage": False}


def test_no_event_while_castle_subarea_stable():
    d = StageChangeDetector()
    d.process(snap(curr_level=6, curr_area=2), snap(curr_level=6, curr_area=2))
    assert d.process(snap(curr_level=6, curr_area=2),
                     snap(curr_level=6, curr_area=2)) == []


def test_castle_grounds_is_not_a_segment_context():
    # Castle GROUNDS is level 16 (not Castle Inside / level 6) — no named
    # subareas, no segments; leaving the lobby for the grounds hides the banner.
    d = StageChangeDetector()
    d.process(snap(curr_level=6, curr_area=1), snap(curr_level=6, curr_area=1))  # lobby
    events = d.process(snap(curr_level=6, curr_area=1),
                       snap(curr_level=16, curr_area=1))                          # -> grounds
    assert len(events) == 1
    assert events[0].payload == {"course_id": None, "level": 16, "area": 1,
                                 "in_stage": False}


def test_no_event_between_two_non_context_levels():
    # Two levels that are neither a main course nor Castle Inside (BitDW ->
    # BitFS) — the banner stays hidden, so no stage_changed.
    d = StageChangeDetector()
    d.process(snap(curr_level=17), snap(curr_level=17))           # BitDW (None)
    assert d.process(snap(curr_level=17), snap(curr_level=19)) == []  # -> BitFS
