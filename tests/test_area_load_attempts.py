"""An in-course load must not replace the reset that started a 100-coin run."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from sm64_events.core.paths import bundled_defaults_seed
from sm64_events.storage.db import EventRow
from sm64_events.tracking.projection import project
from sm64_events.tracking.segments import SegmentDef, hundred_coin_entity


START = datetime(2026, 10, 5, 18, tzinfo=timezone.utc)


def event(jid, kind, frame, **payload):
    return EventRow(id=jid, session_id=1, seq=jid, type=kind, frame=frame,
                    wall_time_utc=(START + timedelta(seconds=frame / 30)).isoformat(),
                    payload=payload)


def definition(mode):
    seed = json.loads(bundled_defaults_seed().read_text(encoding="utf-8"))
    row = next(row for row in seed["segments"]
               if hundred_coin_entity(row["start_triggers"], row.get("waypoints", [])) == (4, 6))
    return SegmentDef(id=100, name=row["name"], enabled=True,
                      start_triggers=row["start_triggers"],
                      end_triggers=row["end_triggers"], guards=row["guards"],
                      waypoints=row["waypoints"], match_mode=mode)


def run_events(area_load=True, anchor_kind="practice_reset", delay=1):
    return [
        event(1, "level_changed", 80, **{"from": 6, "to": 5}),
        event(2, "area_changed", 80, level=5, **{"from": 1, "to": 1}),
        event(3, "practice_reset", 100, level=5, area=1,
              paused_frames_before=12, mario_acted=True),
        event(4, "area_changed", 1200, level=5, **{"from": 1, "to": 2}),
        event(5, anchor_kind, 1200 + delay, level=5, area=2,
              area_load=area_load, mario_acted=True, paused_frames_before=0),
        event(6, "star_collected", 2800, course_id=4, star_id=6,
              coins=100, igt_frames=2700),
        event(7, "star_collected", 3076, course_id=4, star_id=0,
              coins=100, igt_frames=2976),
    ]


@pytest.mark.parametrize("mode", ["strict", "loose"])
@pytest.mark.parametrize("anchor_kind", ["practice_reset", "state_loaded"])
@pytest.mark.parametrize("delay", [1, 5])
def test_ccm_area_load_keeps_original_reset_and_replay_start(mode, anchor_kind, delay):
    events = run_events(anchor_kind=anchor_kind, delay=delay)
    attempts = project(events, segments=[definition(mode)])
    assert len(attempts) == 1, "an area load must not manufacture a reset attempt"
    attempt = attempts[0]
    assert (attempt.course_id, attempt.star_id, attempt.outcome) == (4, 6, "success")
    assert attempt.anchor_type == "practice_reset"
    assert attempt.anchor_frame == 100
    assert attempt.started_utc == events[2].wall_time_utc
    assert attempt.igt_frames == 2976, "keep the closing Usamune time: 1'39\"20"


@pytest.mark.parametrize("mode", ["strict", "loose"])
def test_genuine_reset_at_start_position_still_restarts_the_run(mode):
    events = run_events(area_load=False)
    events[3:5] = [event(5, "practice_reset", 1201, level=5, area=1,
                         area_load=False, mario_acted=True, paused_frames_before=12)]
    attempts = project(events, segments=[definition(mode)])
    successes = [attempt for attempt in attempts if attempt.outcome == "success"]
    assert len(successes) == 1
    assert successes[0].anchor_type == "practice_reset"
    assert successes[0].anchor_frame == 1201
    assert successes[0].igt_frames == 2976


@pytest.mark.parametrize("mode", ["strict", "loose"])
def test_returning_from_slide_keeps_reset_and_collected_100_coin_waypoint(mode):
    events = run_events()
    events[6:6] = [
        event(7, "area_changed", 2900, level=5, **{"from": 2, "to": 1}),
        event(8, "practice_reset", 2901, level=5, area=1,
              area_load=True, mario_acted=True, paused_frames_before=0),
    ]
    events[-1].id = 9
    events[-1].seq = 9
    attempts = project(events, segments=[definition(mode)])
    assert len(attempts) == 1
    attempt = attempts[0]
    assert (attempt.star_id, attempt.outcome) == (6, "success")
    assert attempt.anchor_type == "practice_reset"
    assert attempt.anchor_frame == 100
    assert attempt.started_utc == events[2].wall_time_utc
    assert attempt.igt_frames == 2976
