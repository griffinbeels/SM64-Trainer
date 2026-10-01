"""The lobby entrance is an arrival, independent of how Mario got there.

The 2026-09-30 journal records walked PUSH -> SPAWN transitions and menu
warps at this door. They share its measured home, while an exit pulls the
same door: the landmark alone cannot distinguish entering from leaving.
"""
from datetime import datetime, timezone
from dataclasses import fields
from contextlib import closing

import pytest

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.moment import MomentDetector, effective_moment_kind
from sm64_events.memory.addresses import (ACT_PULLING_DOOR, ACT_PUSHING_DOOR,
                                          ACT_WARP_DOOR_SPAWN, DOOR_ACTIONS)
from sm64_events.storage.db import Database, EventRow
from sm64_events.tracking.eventlabel import label_event
from sm64_events.tracking.segments import TRIGGERS, MatchContext, SegmentDef, SegmentEngine
from sm64_events.tracking.synthesize import clause_for
from sm64_events.tracking.defaults import reconcile_defaults

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


def shipped_lblj():
    import json
    from sm64_events.core.paths import bundled_defaults_seed
    seed = json.loads(bundled_defaults_seed().read_text(encoding="utf-8"))
    return next(item for item in seed["segments"] if item["seed_key"] == "seg:lblj"), seed


def lblj_engine(definition):
    accepted = {field.name for field in fields(SegmentDef)}
    return SegmentEngine([SegmentDef(id=1, **{key: value for key, value in definition.items()
                                             if key in accepted})])


@pytest.mark.parametrize("via", ["walk", "warp", "historical_warp"])
def test_shipped_lblj_arms_on_both_arrivals_and_finishes_at_bowser_entrance(via):
    definition, _seed = shipped_lblj()
    engine = lblj_engine(definition)
    if via == "historical_warp":
        arrival = row(arrival_payload("door_open"))
    else:
        detector = MomentDetector()
        before = snap(ACT_PUSHING_DOOR, 99, level=16, home=(-76, 803, -3155)) \
            if via == "walk" else snap(IDLE, 99)
        edge, settled = snap(ACT_WARP_DOOR_SPAWN, 100), snap(ACT_WARP_DOOR_SPAWN, 101)
        detector.process(before, edge)
        event = detector.process(edge, settled)[0]
        arrival = row(event.payload)
    context = MatchContext(level=6, prev_level=16, num_stars=0, area=1)
    closed, _notices = engine.feed(arrival, context)
    assert not closed
    assert engine.armed_ids() == {1}
    finish = EventRow(id=2, session_id=1, seq=2, type="warp_entered", frame=600,
                     wall_time_utc=W, payload={"level": 6, "area": 1, "to": 17,
                                              "igt_frames": 500, "igt_source": "counter"})
    closed, _notices = engine.feed(finish, context)
    assert len(closed) == 1 and closed[0].outcome == "success"
    assert engine.armed_ids() == set()


@pytest.mark.parametrize("sample", [
    row(arrival_payload(key="6:1:bhvDoorWarp:-1023,-101,-5170")),
    row(arrival_payload("door_open", ACT_PULLING_DOOR)),
    row({"from": 16, "to": 6}, "level_changed"),
    row({"action": ACT_WARP_DOOR_SPAWN, "igt_frames_before": 51}, "practice_reset")])
def test_shipped_lblj_does_not_arm_on_a_different_door_departure_or_reset(sample):
    definition, _seed = shipped_lblj()
    engine = lblj_engine(definition)
    engine.feed(sample, MatchContext(level=6, prev_level=16, num_stars=0, area=1))
    assert engine.armed_ids() == set()


@pytest.mark.parametrize("customized", [False, True])
def test_lblj_refinement_refreshes_clean_users_and_preserves_personal_copies(tmp_path, customized):
    definition, seed = shipped_lblj()
    with closing(Database(tmp_path / "upgrade.db")) as db:
        previous = next(item for item in db.segment_defs() if item["seed_key"] == "seg:lblj")
        if customized:
            db.update_segment_def(previous["id"], name="My LBLJ",
                                  start_triggers=previous["start_triggers"], seed_dirty=1)
        assert reconcile_defaults(db, seed) == []
        current = next(item for item in db.segment_defs() if item["seed_key"] == "seg:lblj")
        assert current["id"] == previous["id"]
        assert current["start_triggers"] == (previous["start_triggers"] if customized
                                               else definition["start_triggers"])
        assert current["name"] == ("My LBLJ" if customized else "LBLJ")
        assert current["seed_dirty"] == int(customized)
