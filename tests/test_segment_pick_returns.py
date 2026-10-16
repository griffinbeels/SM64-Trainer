"""His picked segment comes back when he does (projection `_suspended_pick`).

MIPS Clip starts at the HMC door and ends by entering Dire, Dire Docks, and a
completion into a star stage empties the hand there (caveat 12). His words,
2026-09-24: "if I select the ghost, it should be ready for me the next time I
start that segment -- that is, I reset (coming out of HMC), and then enter
the HMC door. Once I start that segment, the ghost should start with me."
Replayed over his session 25 (journal ids 4663-4719), the pick was gone from
the DDD entry on, so each next door opened with nothing selected and he
re-picked it a second after the door, too late for the ghost.
"""
import pytest

from sm64_events.storage.db import EventRow
from sm64_events.tracking.projection import ATTEMPT_RULES, Projector, replay
from sm64_events.tracking.segments import SegmentDef

W = "2026-09-24T07:51:00Z"
CASTLE, HMC, DDD = 6, 7, 23
DOOR = "6:3:bhvDoor:1126,-1074,-2661"


def jev(id, type, frame, payload=None):
    return EventRow(id=id, session_id=1, seq=id, type=type, frame=frame,
                    wall_time_utc=W, payload=payload or {})


def mips_clip():
    return SegmentDef(id=2, name="MIPS Clip", enabled=True,
                      start_triggers=[{"type": "moment_reached", "kind": "door_open",
                                       "level": CASTLE, "landmark": DOOR}],
                      end_triggers=[{"type": "level_enter", "to": DDD}],
                      guards=[], clock_start="move", parents=["area:6:3"])


def lblj():
    """The castle's lone option on arrival, which the page offers an empty
    hand (journal id 4712's auto fill)."""
    return SegmentDef(id=1, name="LBLJ", enabled=True,
                      start_triggers=[{"type": "level_enter", "to": CASTLE, "from": 16}],
                      end_triggers=[{"type": "entrance_touched", "to": 17}], guards=[])


def door(id, frame):
    return jev(id, "moment_reached", frame,
               {"kind": "door_open", "ordinal": 1, "level": CASTLE, "area": 3,
                "landmark": {"key": DOOR, "symbol": "bhvDoor"}})


def door_zero(id, frame):
    """Usamune's counter zero as Mario comes through the door (+61 frames):
    the MOVE clock's origin, and an echo of the door rather than a reset."""
    return jev(id, "practice_reset", frame,
               {"igt_frames_before": 270, "mario_acted": True, "acted_tracking": True,
                "frames_since_door": 2, "area": 3, "prev_area": 3, "area_load": False,
                "teleport": False, "loads_journaled": True})


def level(id, frame, frm, to, area=1):
    return [jev(id, "level_changed", frame, {"from": frm, "to": to}),
            jev(id + 1, "area_changed", frame, {"level": to, "from": area, "to": area})]


def pick_mips(id):
    return jev(id, "target_set", 0, {"kind": "segment", "segment_id": 2})


def his_run_into_ddd():
    """A door, his click, the clip, DDD (ids 4663-4697)."""
    return [jev(1, "session_started", 0, {"session_id": 1, "attempt_rules": ATTEMPT_RULES}),
            *level(2, 29082, HMC, CASTLE, area=3),
            door(4, 29303), door_zero(5, 29364), pick_mips(6),
            *level(7, 31672, CASTLE, DDD, area=2)]


def back_to_the_castle(id=20):
    """DDD -> HMC, then a fall out of HMC lands him in the basement (4702-4711)."""
    return [*level(id, 31839, DDD, HMC), jev(id + 2, "death", 32033, {"cause": "fall"}),
            *level(id + 3, 32055, HMC, CASTLE, area=3)]


def test_his_pick_is_selected_again_before_the_next_door():
    events = [*his_run_into_ddd()]
    attempts, proj = replay(events, segments=[mips_clip()])
    assert [a.outcome for a in attempts if a.segment_id == 2] == ["success"]
    assert proj.target is None, "in DDD the hand stays empty, as always"

    events += back_to_the_castle()
    _, proj = replay(events, segments=[mips_clip()])
    assert proj.target == ("segment", 2)

    events += [door(30, 32265), door_zero(31, 32326), *level(32, 33944, CASTLE, DDD, area=2)]
    attempts, proj = replay(events, segments=[mips_clip()])
    assert [(a.outcome, a.rta_frames) for a in attempts if a.segment_id == 2] == [
        ("success", 31672 - 29364), ("success", 33944 - 32326)]


def test_the_lone_option_cannot_take_the_returned_pick():
    """The page offers the castle's lone option to what it saw as an empty
    hand; the pick came back on the same tick, so the offer is refused."""
    events = [*his_run_into_ddd(), *back_to_the_castle(),
              jev(40, "target_set", 0, {"kind": "segment", "segment_id": 1, "auto": True})]
    _, proj = replay(events, segments=[mips_clip(), lblj()])
    assert proj.target == ("segment", 2)


@pytest.mark.parametrize("sole", [False, True])
def test_an_armed_arrival_fill_still_replaces_a_pick(sole):
    """The arena row's fill (2026-08-05: walking into Bowser 3 holding "No
    Reds") offers what the arrival armed, and still takes the hand."""
    hmc_exit = SegmentDef(id=85, name="HMC result - Enter HMC (Toad)", enabled=True,
                          start_triggers=[{"type": "level_exit", "from": HMC, "to": CASTLE}],
                          end_triggers=[{"type": "entrance_touched", "to": HMC}], guards=[])
    payload = {"kind": "segment", "segment_id": 85, "auto": True}
    if sole:
        payload["sole_selection"] = {"target": ["segment", 2], "stage": [CASTLE, 3]}
    events = [*his_run_into_ddd(), *back_to_the_castle(), jev(40, "target_set", 0, payload),
              jev(41, "mario_acted", 32060)]
    definitions = [mips_clip(), hmc_exit]
    _, proj = replay(events, segments=definitions)
    assert proj.target == ("segment", 85)
    assert 85 in proj.armed_segment_ids() and proj._target_hooked
    live = Projector(segments=definitions)
    for event in events:
        live.feed(event)
    assert live.target == proj.target and live._target_hooked


def test_another_click_while_away_is_what_he_gets_back():
    events = [*his_run_into_ddd(),
              jev(15, "target_set", 0, {"course_id": 9, "star_id": 0}),
              *back_to_the_castle()]
    _, proj = replay(events, segments=[mips_clip()])
    assert proj.target != ("segment", 2)


def test_a_finish_he_did_not_pick_does_not_come_back():
    """The auto-followed segment (nothing picked) is not his choice to restore."""
    events = [jev(1, "session_started", 0, {"session_id": 1, "attempt_rules": ATTEMPT_RULES}),
              *level(2, 29082, HMC, CASTLE, area=3), door(4, 29303), door_zero(5, 29364),
              *level(7, 31672, CASTLE, DDD, area=2), *back_to_the_castle()]
    attempts, proj = replay(events, segments=[mips_clip()])
    assert [a.outcome for a in attempts if a.segment_id == 2] == ["success"]
    assert proj.target is None



def foreign_practice(outcome):
    """A real played DDD attempt after MIPS, without another target click."""
    reset = {"igt_frames_before": 0, "area": 2, "prev_area": 2,
             "loads_journaled": True, "acted_tracking": True}
    if outcome == "success":
        close = jev(12, "star_collected", 31790,
                    {"course_id": 9, "star_id": 0, "igt_frames": 90,
                     "igt_timed_at": "xcam"})
    elif outcome == "reset":
        close = jev(12, "practice_reset", 31790,
                    {**reset, "igt_frames_before": 90, "mario_acted": True})
    else:
        close = jev(12, "death", 31790, {"cause": "fall", "igt_frames": 90})
    return [jev(10, "practice_reset", 31700, reset),
            jev(11, "mario_acted", 31701), close]


def replay_and_live(events):
    attempts, rebuilt = replay(events, segments=[mips_clip()])
    live = Projector(segments=[mips_clip()])
    for event in events:
        live.feed(event)
    assert live.target == rebuilt.target
    assert live._session_successes == rebuilt._session_successes
    return attempts, rebuilt


@pytest.mark.parametrize("outcome", ["reset", "death"])
def test_failed_foreign_practice_keeps_the_suspended_mips_retry(outcome):
    events = [*his_run_into_ddd(), *foreign_practice(outcome)]
    attempts, proj = replay_and_live(events)
    foreign = next(row for row in attempts if row.id == 10)
    assert foreign.outcome == outcome and not foreign.cleared
    assert proj._suspended_pick == ("segment", 2)
    assert proj._latest_session_success()[0] == ("segment", 2)

    _, proj = replay_and_live([*events, *back_to_the_castle()])
    assert proj.target == ("segment", 2)


def test_newer_foreign_success_defeats_the_suspended_mips_pick():
    events = [*his_run_into_ddd(), *foreign_practice("success")]
    attempts, proj = replay_and_live(events)
    foreign = next(row for row in attempts if row.id == 10)
    assert foreign.outcome == "success" and foreign.valid is True
    assert not foreign.cleared
    assert proj._suspended_pick == ("segment", 2)
    assert proj._picked_target == ("segment", 2), "No later click defeated the old pick"
    assert proj._latest_session_success() == (("star", 9, 0), 12)

    _, proj = replay_and_live([*events, *back_to_the_castle()])
    assert proj.target is None, "The newer successful star must defeat dormant MIPS recall"
