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

def test_entering_a_main_course_emits_stars_mode():
    d = StageChangeDetector()
    d.process(snap(curr_level=6), snap(curr_level=6))            # establish: castle
    events = d.process(snap(curr_level=6), snap(curr_level=8))   # -> SSL (course 8)
    assert len(events) == 1
    assert events[0].type == "stage_changed"
    assert events[0].payload == {"course_id": 8, "level": 8, "area": 1,
                                 "mode": "stars"}


def test_first_pair_establishes():
    events = StageChangeDetector().process(snap(curr_level=8), snap(curr_level=8))
    assert len(events) == 1
    assert events[0].payload == {"course_id": 8, "level": 8, "area": 1,
                                 "mode": "stars"}


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
                                 "mode": "stars"}


def test_frame_matches_curr_global_timer():
    d = StageChangeDetector()
    d.process(snap(curr_level=6), snap(curr_level=6))
    events = d.process(snap(curr_level=6), snap(curr_level=8, global_timer=4321))
    assert events[0].frame == 4321


# --- Bowser COURSE context (BitDW / BitFS / BitS) --------------------------

def test_entering_a_bowser_course_emits_bowser_course_mode():
    # BitDW (level 17) -> course 16; the banner offers reds (8-coin star) AND
    # the pipe-entry segment, so course_id is carried for the star side.
    d = StageChangeDetector()
    d.process(snap(curr_level=8), snap(curr_level=8))            # SSL (course 8)
    events = d.process(snap(curr_level=8), snap(curr_level=17))  # -> BitDW
    assert len(events) == 1
    assert events[0].payload == {"course_id": 16, "level": 17, "area": 1,
                                 "mode": "bowser_course"}


def test_moving_between_two_bowser_courses_re_emits():
    # BitDW -> BitFS: both are bowser_course but a DIFFERENT level, so the
    # offered targets change and the banner must re-emit (unlike two cap levels).
    d = StageChangeDetector()
    d.process(snap(curr_level=17), snap(curr_level=17))            # BitDW
    events = d.process(snap(curr_level=17), snap(curr_level=19))   # -> BitFS
    assert len(events) == 1
    assert events[0].payload == {"course_id": 17, "level": 19, "area": 1,
                                 "mode": "bowser_course"}


# --- Bowser ARENA context (Bowser 1 / 2 / 3 fights) -----------------------

def test_entering_a_bowser_arena_emits_arena_mode():
    # Bowser 1 arena (level 30) has no course of its own (course_for_level -> None);
    # the banner offers only the fight segment (auto-selected client-side).
    d = StageChangeDetector()
    d.process(snap(curr_level=17), snap(curr_level=17))           # BitDW
    events = d.process(snap(curr_level=17), snap(curr_level=30))  # -> Bowser 1 arena
    assert len(events) == 1
    assert events[0].payload == {"course_id": None, "level": 30, "area": 1,
                                 "mode": "arena"}


def test_moving_between_two_arenas_re_emits():
    d = StageChangeDetector()
    d.process(snap(curr_level=30), snap(curr_level=30))           # Bowser 1 arena
    events = d.process(snap(curr_level=30), snap(curr_level=33))  # -> Bowser 2 arena
    assert len(events) == 1
    assert events[0].payload["mode"] == "arena"
    assert events[0].payload["level"] == 33


# --- castle segment context (Castle Inside subareas) ----------------------

def test_entering_castle_inside_emits_castle_mode():
    # Leaving SSL into the castle is the SEGMENT context — the banner shows that
    # subarea's segments. No main course, so course_id is null.
    d = StageChangeDetector()
    d.process(snap(curr_level=8), snap(curr_level=8))                       # SSL
    events = d.process(snap(curr_level=8), snap(curr_level=6, curr_area=1))  # -> lobby
    assert len(events) == 1
    assert events[0].payload == {"course_id": None, "level": 6, "area": 1,
                                 "mode": "castle"}


def test_castle_subarea_switch_emits():
    # lobby -> upstairs swaps the offered segments, so it re-emits (unlike an
    # in-course area switch).
    d = StageChangeDetector()
    d.process(snap(curr_level=6, curr_area=1), snap(curr_level=6, curr_area=1))
    events = d.process(snap(curr_level=6, curr_area=1),
                       snap(curr_level=6, curr_area=2))
    assert len(events) == 1
    assert events[0].payload == {"course_id": None, "level": 6, "area": 2,
                                 "mode": "castle"}


def test_no_event_while_castle_subarea_stable():
    d = StageChangeDetector()
    d.process(snap(curr_level=6, curr_area=2), snap(curr_level=6, curr_area=2))
    assert d.process(snap(curr_level=6, curr_area=2),
                     snap(curr_level=6, curr_area=2)) == []


# --- no context (hubs, caps, secret-star areas) ---------------------------

def test_castle_grounds_is_no_context():
    # Castle GROUNDS is level 16 (not Castle Inside / level 6) — no named
    # subareas, no banner; leaving the lobby for the grounds hides the banner.
    d = StageChangeDetector()
    d.process(snap(curr_level=6, curr_area=1), snap(curr_level=6, curr_area=1))  # lobby
    events = d.process(snap(curr_level=6, curr_area=1),
                       snap(curr_level=16, curr_area=1))                          # -> grounds
    assert len(events) == 1
    assert events[0].payload == {"course_id": None, "level": 16, "area": 1,
                                 "mode": None}


def test_no_event_between_two_cap_levels():
    # Two levels that are neither a main course, Bowser geography, nor Castle
    # Inside (Vanish Cap 18 -> Metal Cap 20) — the banner stays hidden, so no
    # stage_changed.
    d = StageChangeDetector()
    d.process(snap(curr_level=18), snap(curr_level=18))           # Vanish Cap (None)
    assert d.process(snap(curr_level=18), snap(curr_level=20)) == []  # -> Metal Cap
