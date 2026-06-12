from datetime import datetime, timezone

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.warp import WarpDetector
from sm64_events.memory.addresses import ACT_DISAPPEARED

ACT_IDLE = 0x0C400201


def snap(**overrides) -> GameSnapshot:
    defaults = dict(
        wall_time_utc=datetime(2026, 6, 11, tzinfo=timezone.utc),
        global_timer=2000, mario_action=ACT_IDLE, mario_action_timer=0,
        num_stars=8, last_completed_course=1, last_completed_star=1,
        curr_level=17, curr_area=1)
    defaults.update(overrides)
    return GameSnapshot(**defaults)


def test_edge_into_warp_action_emits_warp_entered():
    events = WarpDetector().process(snap(), snap(mario_action=ACT_DISAPPEARED))
    assert len(events) == 1
    assert events[0].type == "warp_entered"
    assert events[0].payload == {"level": 17, "area": 1,
                                 "action": ACT_DISAPPEARED}


def test_no_event_while_warp_action_persists():
    d = WarpDetector()
    d.process(snap(), snap(mario_action=ACT_DISAPPEARED))
    assert d.process(snap(mario_action=ACT_DISAPPEARED),
                     snap(mario_action=ACT_DISAPPEARED)) == []
