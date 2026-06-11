# tests/test_level.py
from datetime import datetime, timezone

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.level import LevelChangeDetector

ACT_IDLE = 0x0C400201


def snap(**overrides) -> GameSnapshot:
    defaults = dict(
        wall_time_utc=datetime(2026, 6, 10, tzinfo=timezone.utc),
        global_timer=1000,
        mario_action=ACT_IDLE,
        mario_action_timer=0,
        num_stars=5,
        last_completed_course=1,
        last_completed_star=3,
        igt_overall=300,
        curr_level=8,
    )
    defaults.update(overrides)
    return GameSnapshot(**defaults)


def test_level_change_emits_event_with_from_and_to():
    events = LevelChangeDetector().process(snap(curr_level=8), snap(curr_level=24))
    assert len(events) == 1
    ev = events[0]
    assert ev.type == "level_changed"
    assert ev.frame == 1000
    assert ev.payload == {"from": 8, "to": 24}


def test_same_level_is_silent():
    assert LevelChangeDetector().process(snap(curr_level=8), snap(curr_level=8)) == []


def test_level_change_frame_matches_curr_global_timer():
    events = LevelChangeDetector().process(
        snap(curr_level=1, global_timer=500),
        snap(curr_level=5, global_timer=502),
    )
    assert events[0].frame == 502


def test_boot_default_level_zero_to_real_level_emits_event():
    # On first attach, curr_level starts at 0 (default) and transitions to
    # whichever level is loaded. This fires once on attach — acceptable and
    # noted here so the projection layer can handle it (e.g. ignore from==0).
    events = LevelChangeDetector().process(snap(curr_level=0), snap(curr_level=8))
    assert len(events) == 1
    assert events[0].payload == {"from": 0, "to": 8}
