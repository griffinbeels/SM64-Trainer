"""The lobby entrance is an arrival, independent of how Mario got there.

The 2026-09-30 journal records walked PUSH -> SPAWN transitions and menu
warps at this door. They share its measured home, while an exit pulls the
same door: the landmark alone cannot distinguish entering from leaving.
"""
from datetime import datetime, timezone

import pytest

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.moment import MomentDetector, effective_moment_kind
from sm64_events.memory.addresses import (ACT_PULLING_DOOR, ACT_PUSHING_DOOR,
                                          ACT_WARP_DOOR_SPAWN, DOOR_ACTIONS)
from sm64_events.storage.db import EventRow
from sm64_events.tracking.eventlabel import label_event
from sm64_events.tracking.segments import TRIGGERS
from sm64_events.tracking.synthesize import clause_for

W = "2026-10-01T00:15:00Z"
FRONT = "6:1:bhvDoorWarp:-1100,0,2202"
NAMES = {FRONT: "Castle Lobby Entrance Door"}
IDLE = 0x0C400201


def snap(action, frame, level=6, home=(-1100, 0, 2202), igt=0):
    return GameSnapshot(
        wall_time_utc=datetime(2026, 10, 1, tzinfo=timezone.utc),
        global_timer=frame, mario_action=action, curr_level=level, curr_area=1,
        mario_action_timer=0, num_stars=0, last_completed_course=0,
        last_completed_star=0,
        igt_overall=igt, landmark_symbol="bhvDoorWarp", landmark_home=home,
        landmark_pos=home)


def row(payload, kind="moment_reached"):
    return EventRow(id=1, session_id=1, seq=1, type=kind, frame=100,
                    wall_time_utc=W, payload=payload)


def arrival_payload(kind="door_arrived", action=ACT_WARP_DOOR_SPAWN, key=FRONT):
    return {"kind": kind, "action": action, "level": 6, "area": 1,
            "landmark": {"key": key, "nameable": True}, "ordinal": 1}


def test_a_walked_push_to_spawn_names_the_destination_after_the_settle():
    det = MomentDetector()
    outside = snap(ACT_PUSHING_DOOR, 15430, level=16,
                   home=(-76, 803, -3155), igt=51)
    # A torn read can still have the source object on the action edge.
    edge = snap(ACT_WARP_DOOR_SPAWN, 15431, home=(-76, 803, -3155))
    assert det.process(outside, edge) == []
    settled = snap(ACT_WARP_DOOR_SPAWN, 15432)
    events = det.process(edge, settled)
    assert len(events) == 1
    assert events[0].frame == 15431
    assert events[0].payload["kind"] == "door_arrived"
    assert events[0].payload["landmark"]["key"] == FRONT
    assert det.process(settled, snap(ACT_WARP_DOOR_SPAWN, 15433)) == []


def test_a_menu_arrival_keeps_the_same_door_without_retargeting_or_retiming():
    det = MomentDetector()
    before, edge, settled = (snap(IDLE, 100), snap(ACT_WARP_DOOR_SPAWN, 101),
                             snap(ACT_WARP_DOOR_SPAWN, 102))
    det.process(before, edge)
    event = det.process(edge, settled)[0]
    assert event.payload["kind"] == "door_arrived"
    assert event.payload["landmark"]["key"] == FRONT
    # This refinement inherits the existing measured door clock; it does not
    # recalibrate the old menu row's counter+2 reading (00"06).
    assert event.payload["igt_frames"] == 2
    assert ACT_WARP_DOOR_SPAWN in DOOR_ACTIONS


def test_legacy_opening_ordinals_still_count_a_menu_arrival_then_a_pull():
    det = MomentDetector()
    snapshots = [snap(IDLE, 100), snap(ACT_WARP_DOOR_SPAWN, 101),
                 snap(IDLE, 102), snap(ACT_PULLING_DOOR, 103),
                 snap(ACT_PULLING_DOOR, 104)]
    events = []
    for prev, curr in zip(snapshots, snapshots[1:], strict=False):
        events.extend(det.process(prev, curr))
    assert [(e.payload["kind"], e.payload["ordinal"]) for e in events] == [
        ("door_arrived", 1), ("door_open", 2)]


@pytest.mark.parametrize("action", [ACT_PULLING_DOOR, ACT_PUSHING_DOOR, None])
def test_an_old_opening_or_an_unproven_row_is_not_an_arrival(action):
    payload = arrival_payload("door_open", action)
    assert effective_moment_kind(payload) == "door_open"
    clause = {"type": "moment_reached", "kind": "door_arrived",
              "level": 6, "landmark": FRONT}
    assert not TRIGGERS["moment_reached"].match(clause, row(payload), None)


def test_a_missing_kind_is_not_invented_from_an_action():
    assert effective_moment_kind({"action": ACT_PULLING_DOOR}) is None
    assert effective_moment_kind({"action": ACT_WARP_DOOR_SPAWN}) is None


def test_old_menu_samples_label_and_synthesize_the_specific_arrival():
    sample = row(arrival_payload("door_open"))
    original = dict(sample.payload)
    assert label_event(sample, NAMES) == (
        "Arrive through the Castle Lobby Entrance Door in Castle Inside")
    clause = clause_for(sample, "start")
    assert clause == {"type": "moment_reached", "kind": "door_arrived",
                      "level": 6, "landmark": FRONT}
    assert sample.payload == original, "a derived label must not rewrite history"
    assert TRIGGERS["moment_reached"].match(clause, sample, None)
    assert TRIGGERS["moment_reached"].match(clause, row(arrival_payload()), None)
    assert not TRIGGERS["moment_reached"].match(
        clause, row(arrival_payload(key="6:1:bhvDoorWarp:-1023,-101,-5170")), None)
    assert not TRIGGERS["moment_reached"].match(
        clause, row({"from": 16, "to": 6}, "level_changed"), None)
    assert not TRIGGERS["moment_reached"].match(
        clause, row({"action": ACT_WARP_DOOR_SPAWN}, "practice_reset"), None)


def test_existing_open_door_definitions_continue_matching_new_arrival_rows():
    clause = {"type": "moment_reached", "kind": "door_open", "level": 6,
              "landmark": FRONT}
    assert TRIGGERS["moment_reached"].match(clause, row(arrival_payload()), None)
    assert TRIGGERS["moment_reached"].match(
        clause, row(arrival_payload("door_open", ACT_PULLING_DOOR)), None)
