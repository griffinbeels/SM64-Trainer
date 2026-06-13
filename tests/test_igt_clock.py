# tests/test_igt_clock.py
"""The shared Usamune IGT clock. Its source-precedence behaviour is exercised
end-to-end through test_star_grab.py and test_key.py; these lock the public
interface (empty / observe / igt_at) so a consumer refactor can't silently
break the contract."""
from datetime import datetime, timezone

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.igt_clock import IgtClock


def snap(global_timer, igt_overall=0, igt_result=0) -> GameSnapshot:
    return GameSnapshot(
        wall_time_utc=datetime(2026, 6, 12, tzinfo=timezone.utc),
        global_timer=global_timer, mario_action=0, mario_action_timer=0,
        num_stars=0, last_completed_course=0, last_completed_star=0,
        igt_overall=igt_overall, igt_result=igt_result)


def test_empty_until_first_observe():
    c = IgtClock()
    assert c.empty()
    c.observe(snap(100))
    assert not c.empty()


def test_fresh_result_is_authoritative():
    c = IgtClock()
    c.observe(snap(1386, igt_overall=1386, igt_result=0))
    curr = snap(1389, igt_overall=1388, igt_result=1388)
    c.observe(curr)
    assert c.igt_at(1387, curr) == (1388, "result")


def test_counter_path_adds_the_display_tick():
    # result store untouched (0) -> overall counter back-computed + 1 tick
    c = IgtClock()
    c.observe(snap(1386, igt_overall=1386, igt_result=0))
    curr = snap(1387, igt_overall=1387, igt_result=0)
    c.observe(curr)
    assert c.igt_at(1387, curr) == (1388, "counter")
