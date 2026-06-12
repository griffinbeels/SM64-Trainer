from datetime import datetime, timezone

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.area import AreaChangeDetector


def snap(**overrides) -> GameSnapshot:
    defaults = dict(
        wall_time_utc=datetime(2026, 6, 11, tzinfo=timezone.utc),
        global_timer=1000, mario_action=0x0C400201, mario_action_timer=0,
        num_stars=5, last_completed_course=1, last_completed_star=3,
        curr_level=6, curr_area=1)
    defaults.update(overrides)
    return GameSnapshot(**defaults)


def test_area_change_emits_event_with_level_from_to():
    d = AreaChangeDetector()
    d.process(snap(curr_area=1), snap(curr_area=1))   # establish (1 event)
    events = d.process(snap(curr_area=1), snap(curr_area=2))
    assert len(events) == 1
    assert events[0].type == "area_changed"
    assert events[0].payload == {"level": 6, "from": 1, "to": 2}


def test_first_pair_emits_establishing_event_from_may_equal_to():
    events = AreaChangeDetector().process(snap(curr_area=1), snap(curr_area=1))
    assert len(events) == 1
    assert events[0].payload == {"level": 6, "from": 1, "to": 1}


def test_no_event_while_area_stable_after_establishing():
    d = AreaChangeDetector()
    d.process(snap(), snap())
    assert d.process(snap(), snap()) == []


def test_level_change_re_establishes_area_for_new_level():
    d = AreaChangeDetector()
    d.process(snap(), snap())                          # castle area 1
    events = d.process(snap(), snap(curr_level=17, curr_area=1))
    assert len(events) == 1                            # same area NUMBER, new level
    assert events[0].payload["level"] == 17
