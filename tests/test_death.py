# tests/test_death.py
import pytest
from datetime import datetime, timezone

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.death import DeathDetector
from sm64_events.memory.addresses import DEATH_ACTIONS

ACT_IDLE = 0x0C400201
ACT_STANDING_DEATH = 0x00021311
ACT_DROWNING = 0x300032C4
ACT_WATER_DEATH = 0x300032C7
WARP_OP_WARP_FLOOR = 0x13
WARP_OP_DEATH = 0x12


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


def test_edge_into_standing_death_emits_event():
    events = DeathDetector().process(snap(), snap(mario_action=ACT_STANDING_DEATH))
    assert len(events) == 1
    ev = events[0]
    assert ev.type == "death"
    assert ev.frame == 1000
    assert ev.payload["cause"] == "standing"
    assert ev.payload["igt_frames"] == 300
    assert ev.payload["level"] == 8


def test_death_payload_carries_igt_and_level():
    events = DeathDetector().process(
        snap(),
        snap(mario_action=ACT_STANDING_DEATH, igt_overall=450, curr_level=24, global_timer=2000),
    )
    assert events[0].payload == {"cause": "standing", "igt_frames": 450, "level": 24}
    assert events[0].frame == 2000


def test_drowning_action_emits_death_with_correct_cause():
    events = DeathDetector().process(snap(), snap(mario_action=ACT_DROWNING))
    assert len(events) == 1
    assert events[0].payload["cause"] == "drowning"


def test_water_death_action_emits_death_with_correct_cause():
    events = DeathDetector().process(snap(), snap(mario_action=ACT_WATER_DEATH))
    assert len(events) == 1
    assert events[0].payload["cause"] == "water"


def test_no_event_while_death_action_continues():
    # still in the same death action — must not double-fire
    prev = snap(mario_action=ACT_STANDING_DEATH)
    curr = snap(mario_action=ACT_STANDING_DEATH, mario_action_timer=10)
    assert DeathDetector().process(prev, curr) == []


def test_no_event_on_adjacent_death_action_transition():
    # drowning -> water_death must not fire a second event
    prev = snap(mario_action=ACT_DROWNING)
    curr = snap(mario_action=ACT_WATER_DEATH)
    assert DeathDetector().process(prev, curr) == []


def test_no_event_for_normal_action():
    assert DeathDetector().process(snap(), snap(mario_action=ACT_IDLE)) == []


def test_no_event_for_non_death_action():
    ACT_WALKING = 0x04000440
    assert DeathDetector().process(snap(), snap(mario_action=ACT_WALKING)) == []


@pytest.mark.parametrize("action_id,cause", list(DEATH_ACTIONS.items()))
def test_all_death_action_ids_fire(action_id, cause):
    events = DeathDetector().process(snap(), snap(mario_action=action_id))
    assert len(events) == 1, f"Expected death event for {hex(action_id)} ({cause})"
    assert events[0].payload["cause"] == cause


# -- void-outs (death barriers): no death ACTION ever runs in-level — the game
# -- pends WARP_OP_WARP_FLOOR ~20 frames before the level unloads (death.py).

def test_void_warp_floor_edge_emits_fall_death():
    events = DeathDetector().process(
        snap(),
        snap(pending_warp_op=WARP_OP_WARP_FLOOR, igt_overall=512,
             curr_level=7, global_timer=2000))
    assert len(events) == 1
    assert events[0].type == "death"
    assert events[0].frame == 2000
    assert events[0].payload == {"cause": "fall", "igt_frames": 512, "level": 7}


def test_void_warp_pending_does_not_refire():
    # the op stays set for ~20 game frames — one event per pulse
    prev = snap(pending_warp_op=WARP_OP_WARP_FLOOR)
    curr = snap(pending_warp_op=WARP_OP_WARP_FLOOR, global_timer=1002)
    assert DeathDetector().process(prev, curr) == []


def test_death_warp_op_does_not_fire():
    # normal deaths pend WARP_OP_DEATH after their death ACTION already fired
    # the event — reacting to that op would double-count every normal death
    assert DeathDetector().process(snap(), snap(pending_warp_op=WARP_OP_DEATH)) == []


@pytest.mark.parametrize("op", [0x03, 0x11, 0x14])  # warp door, star exit, game over
def test_other_warp_ops_do_not_fire(op):
    assert DeathDetector().process(snap(), snap(pending_warp_op=op)) == []


def test_action_death_with_simultaneous_warp_op_emits_one_event():
    curr = snap(mario_action=ACT_STANDING_DEATH,
                pending_warp_op=WARP_OP_WARP_FLOOR)
    events = DeathDetector().process(snap(), curr)
    assert len(events) == 1
    assert events[0].payload["cause"] == "standing"


def test_ongoing_action_death_suppresses_fall_event():
    # already dying when a warp-floor pulse appears: still one death total
    prev = snap(mario_action=ACT_STANDING_DEATH)
    curr = snap(mario_action=ACT_STANDING_DEATH,
                pending_warp_op=WARP_OP_WARP_FLOOR)
    assert DeathDetector().process(prev, curr) == []
