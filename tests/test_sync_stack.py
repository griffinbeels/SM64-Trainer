"""The real detector chain over a scripted stream, events out as wire dicts."""
from datetime import datetime, timezone

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.memory import addresses as A
from sm64_events.sync.stack import DetectorRun


def _snap(timer, level, area=1):
    return GameSnapshot(wall_time_utc=datetime(2026, 8, 15, tzinfo=timezone.utc),
                        global_timer=timer, mario_action=A.ACT_JUMP,
                        mario_action_timer=0, num_stars=1,
                        last_completed_course=0, last_completed_star=0,
                        curr_level=level, curr_area=area)


def test_a_level_edge_through_the_real_chain_publishes_level_changed():
    run = DetectorRun("us")
    stream = [_snap(100 + i, 6 if i < 5 else 24) for i in range(10)]
    events = run.run(stream)
    kinds = [e["type"] for e in events]
    assert "level_changed" in kinds
    assert not run.errors
    assert all("seq" in e and "payload" in e for e in events)


def test_stream_yields_per_tick_so_a_gate_can_stop_early():
    run = DetectorRun("us")
    ticks = list(run.stream(_snap(100 + i, 6) for i in range(3)))
    assert len(ticks) == 3
    assert ticks[0][1] == []          # the first tick has no prev


def test_a_detector_that_raises_is_reported_not_fatal():
    class Broken:
        def process(self, prev, curr):
            raise RuntimeError("boom")
    run = DetectorRun("us", detectors=[Broken()])
    run.run([_snap(1, 6), _snap(2, 6)])
    assert run.errors and "Broken" in run.errors[0]
