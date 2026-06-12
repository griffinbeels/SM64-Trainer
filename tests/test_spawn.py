# tests/test_spawn.py
from datetime import datetime, timezone

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.spawn import SpawnDetector
from sm64_events.memory.addresses import (ACT_INTRO_CUTSCENE,
                                          ACT_SPAWN_SPIN_AIRBORNE,
                                          LEVEL_CASTLE_GROUNDS)

ACT_IDLE = 0x0C400201


def snap(**overrides) -> GameSnapshot:
    defaults = dict(
        wall_time_utc=datetime(2026, 6, 11, tzinfo=timezone.utc),
        global_timer=500, mario_action=ACT_IDLE, mario_action_timer=0,
        num_stars=0, last_completed_course=0, last_completed_star=0,
        curr_level=LEVEL_CASTLE_GROUNDS, curr_area=1)
    defaults.update(overrides)
    return GameSnapshot(**defaults)


def test_leaving_intro_cutscene_emits_spawned_intro():
    events = SpawnDetector().process(
        snap(mario_action=ACT_INTRO_CUTSCENE), snap())
    assert len(events) == 1
    assert events[0].type == "spawned"
    assert events[0].payload == {"level": LEVEL_CASTLE_GROUNDS,
                                 "kind": "intro"}


def test_edge_into_spawn_action_emits_spawned_spawn():
    events = SpawnDetector().process(
        snap(), snap(mario_action=ACT_SPAWN_SPIN_AIRBORNE))
    assert events[0].payload["kind"] == "spawn"
    assert events[0].payload["level"] == LEVEL_CASTLE_GROUNDS


def test_idle_to_idle_is_silent():
    assert SpawnDetector().process(snap(), snap()) == []
