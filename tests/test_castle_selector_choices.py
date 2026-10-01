"""Complete castle offers precede arming; a deliberate pick lasts one session."""
from dataclasses import replace

import pytest

from sm64_events.storage.db import EventRow
from sm64_events.tracking.projection import Projector, replay
from sm64_events.tracking.segments import SegmentDef, selector_start_areas, start_origin

LOBBY_DOOR = "6:1:bhvDoorWarp:-1100,0,2202"
BASEMENT_DOOR = "6:3:bhvDoorWarp:717,-1177,-869"
LBLJ_START = {"type": "moment_reached", "kind": "door_arrived", "level": 6,
              "landmark": LOBBY_DOOR}
BOB_START = {"type": "level_enter", "to": 6, "from": 16}


def definitions():
    return [
        SegmentDef(id=1, name="LBLJ", enabled=True, start_triggers=[LBLJ_START],
            waypoints=[[{"type": "moment_reached", "kind": "door_open", "level": 6,
                         "landmark": "6:1:bhvDoor:766,0,-3247"}]],
            end_triggers=[{"type": "entrance_touched", "to": 17}],
            match_mode="strict", clock_start="move", guards=[]),
        SegmentDef(id=11, name="Castle Entrance → BoB", enabled=True, start_triggers=[BOB_START],
            end_triggers=[{"type": "entrance_touched", "to": 9}], guards=[]),
        SegmentDef(id=12, name="Basement probe", enabled=True,
            start_triggers=[{**LBLJ_START, "landmark": BASEMENT_DOOR}],
            end_triggers=[{"type": "entrance_touched", "to": 7}], guards=[]),
    ]


class Play:
    def __init__(self, defs=None):
        self.defs = defs or definitions()
        self.projector = Projector(segments=self.defs)
        self.events = []

    def send(self, kind, payload=None):
        n = len(self.events) + 1
        event = EventRow(id=n, session_id=1, seq=n, type=kind, frame=n * 100,
            wall_time_utc="2026-09-30T12:00:00Z", payload=payload or {})
        self.events.append(event)
        self.projector.feed(event)

    def place(self, level, area=1):
        self.send("level_changed", {"from": self.projector._level, "to": level})
        self.send("area_changed", {"level": level, "from": None, "to": area})

    def pick(self, sid, auto=False):
        self.send("target_set", {"kind": "segment", "segment_id": sid, "auto": auto})


def test_exact_saved_starts_are_both_offered_before_the_door_fires():
    assert selector_start_areas([LBLJ_START]) == [[6, 1]]
    assert selector_start_areas([BOB_START]) == [[6, 1]]
    play = Play()
    play.place(16)
    play.place(6)
    assert play.projector.target is None
    assert play.projector.armed_segment_ids() == {11}
    assert play.projector._castle_selector_ids() == {1, 11}
    play.send("moment_reached", {"kind": "door_arrived", "level": 6, "area": 1,
        "landmark": {"key": LOBBY_DOOR}, "igt_frames": 2})
    assert play.projector.target is None


def test_landmark_origin_preserves_explicit_area_and_all_or_sides():
    basement = {**LBLJ_START, "landmark": BASEMENT_DOOR}
    assert start_origin([basement]) == "6:3"
    assert selector_start_areas([{k: v for k, v in basement.items() if k != "level"}]) == [[6, 3]]
    assert selector_start_areas([LBLJ_START, basement]) == [[6, 1], [6, 3]]
    assert selector_start_areas([{**basement, "area": 2}]) == [[6, 2]]
    names = {LOBBY_DOOR: "Shared door", BASEMENT_DOOR: "Shared door"}
    assert selector_start_areas([LBLJ_START], names) == [[6, 1], [6, 3]]
    assert selector_start_areas([{**LBLJ_START, "area": 1}], names) == [[6, 1]]


def test_exit_origin_does_not_offer_a_course_movement_as_a_lobby_start():
    assert selector_start_areas([
        {"type": "level_exit", "from": 7, "to": 6, "to_subarea": 1}]) == []
    assert selector_start_areas([{"type": "entrance_touched", "to": 7}]) == [[6, 3]]
    assert selector_start_areas([{"type": "reset_game"}]) == []


def test_manual_lblj_choice_restores_after_foreign_play_and_replays_identically():
    play = Play()
    play.place(6)
    play.pick(1)
    play.place(17)
    play.send("target_set", {"course_id": 16, "star_id": 0})
    play.place(6)
    assert play.projector.target == ("segment", 1)
    assert play.projector._picked_target == ("segment", 1)
    assert not play.projector._target_hooked
    assert len([e for e in play.events if e.type == "target_set"]) == 2
    _, rebuilt = replay(play.events, segments=play.defs)
    assert rebuilt.target == play.projector.target
    assert rebuilt._session_castle_choices == play.projector._session_castle_choices


def test_an_automatic_choice_teaches_no_session_preference():
    play = Play()
    play.place(6)
    play.pick(1, auto=True)
    play.place(17)
    play.send("target_set", {"course_id": 16, "star_id": 0})
    play.place(6)
    assert play.projector._session_castle_choices == {}
    assert play.projector.target == ("star", 16, 0)


def test_a_later_click_replaces_the_session_choice_and_new_session_forgets_it():
    play = Play()
    play.place(6)
    play.pick(1)
    play.pick(11)
    play.place(9)
    play.place(6)
    assert play.projector.target == ("segment", 11)
    play.send("session_started")
    assert play.projector.target is None
    play.place(16)
    play.place(6)
    assert play.projector.target is None
    assert play.projector._session_castle_choices == {}


def test_departing_area_cannot_restore_a_basement_choice_on_lobby_entry():
    play = Play()
    play.place(6, 1)
    play.pick(1)
    play.place(6, 3)
    play.pick(12)
    play.place(7, 3)
    play.send("level_changed", {"from": 7, "to": 6})
    assert play.projector.target != ("segment", 12)
    play.send("area_changed", {"level": 6, "from": None, "to": 1})
    assert play.projector.target == ("segment", 1)


@pytest.mark.parametrize("change", ["disabled", "deleted", "piece"])
def test_remembered_choice_must_still_be_visible(change):
    play = Play()
    play.place(6)
    play.pick(1)
    play.place(17)
    play.send("target_set", {"course_id": 16, "star_id": 0})
    defs = play.defs
    if change == "disabled":
        defs = [replace(defs[0], enabled=False), *defs[1:]]
    elif change == "deleted":
        defs = defs[1:]
    else:
        defs = [replace(defs[0], parents=["segment:11"]), *defs[1:]]
    _, rebuilt = replay(play.events, segments=defs)
    play.projector = rebuilt
    play.place(6)
    assert play.projector.target != ("segment", 1)


def test_route_fallback_and_armed_extras_match_the_castle_offer():
    play = Play()
    play.place(6)
    play.projector._route_segments = frozenset({900})
    assert play.projector._castle_selector_ids() == {1, 11}
    play.projector._route_segments = frozenset({11})
    assert play.projector._castle_selector_ids() == {11}
    play.send("moment_reached", {"kind": "door_arrived", "level": 6, "area": 1,
        "landmark": {"key": LOBBY_DOOR}, "igt_frames": 2})
    assert play.projector._castle_selector_ids() == {1, 11}
