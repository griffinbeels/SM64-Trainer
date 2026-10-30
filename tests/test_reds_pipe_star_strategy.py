"""A Bowser reds->pipe run's reds grab is that run's own star, on the same route.

His words, round 8: "all reds -> pipe ghosts should include, by definition
reds -> star as a subsegment; so, if someone does 'reds -> pipe' then it should
have also contributed to 'reds -> star' ghosts subsegments." One reds->pipe run
already records two attempts (the star at its grab, the segment at the pipe).
The star attempt took the star card's own remembered strategy, so a "Xiah (Pipe)"
run filed its grab under whatever the star last held ("Shig (Star)", or nothing,
which keeps no PB and no ghost). While the reds->pipe segment is the target, the
grab now takes the Pipe strategy's Star twin.
"""
from types import SimpleNamespace

from sm64_events.storage.db import Database, EventRow
from sm64_events.tracking.projection import replay
from sm64_events.tracking.segments import SegmentDef
from sm64_events.tracking.service import TrackerService

W = "2026-10-30T12:00:00Z"
REDS_PIPE = 67


def jev(id, type, frame, payload=None):
    return EventRow(id=id, session_id=1, seq=id, type=type, frame=frame, wall_time_utc=W,
                    payload=payload or {})


def reds_pipe_def():
    return SegmentDef(id=REDS_PIPE, name="BitDW — 8 Red Coins → Pipe", enabled=True,
                      start_triggers=[{"type": "level_enter", "to": 17}],
                      end_triggers=[{"type": "warp_entered", "level": 17}], guards=[])


def reds_grab(target, *, pipe="Xiah (Pipe)", star=0):
    picks = [jev(1, "strat_set", 0, {"course_id": 16, "star_id": star, "strat_tag": "Shig (Star)"}),
             jev(2, "strat_set", 0, {"kind": "segment", "segment_id": REDS_PIPE, "strat_tag": pipe})]
    return [*picks, jev(3, "target_set", 0, target),
            jev(4, "practice_reset", 1000, {"igt_frames_before": 0}),
            jev(5, "star_collected", 2500, {"course_id": 16, "star_id": star, "igt_frames": 1500})]


def star_attempt(events, pairing):
    attempts, projector = replay(events, segments=[reds_pipe_def()], reds_pipe_by_course=pairing)
    star, = [a for a in attempts if a.course_id == 16]
    return star, projector


def test_the_grab_inside_a_reds_pipe_run_takes_the_pipe_routes_star_twin():
    star, projector = star_attempt(reds_grab({"kind": "segment", "segment_id": REDS_PIPE}), {16: REDS_PIPE})
    assert star.outcome == "success" and star.strat_tag == "Xiah (Star)"
    assert projector.strat_by_star[(16, 0)] == "Xiah (Star)", "the star card follows the run"


def test_the_star_practiced_alone_keeps_its_own_strategy():
    star, _ = star_attempt(reds_grab({"course_id": 16, "star_id": 0}), {16: REDS_PIPE})
    assert star.strat_tag == "Shig (Star)"


def test_only_a_pipe_family_strategy_names_a_twin():
    star, _ = star_attempt(reds_grab({"kind": "segment", "segment_id": REDS_PIPE}, pipe="Standard"),
                           {16: REDS_PIPE})
    assert star.strat_tag == "Shig (Star)"


def test_the_service_hands_the_projector_its_reds_pipe_pairing(tmp_path):
    database = Database(tmp_path / "practice.db")
    try:
        segment_id = database.insert_segment_def(
            "8 Red Coins → Pipe", [{"type": "level_enter", "to": 17}], [], [],
            "2026-10-30T00:00:00Z", seed_key="seg:reds->pipe:bitdw")
        database.insert_segment_def("Pipe", [{"type": "level_enter", "to": 17}], [], [],
                                    "2026-10-30T00:00:00Z", seed_key="seg:bitdw-pipe")
        service = TrackerService.__new__(TrackerService)
        service.db = database
        assert service._reds_pipe_by_course() == {16: segment_id}
        assert TrackerService._reds_pipe_by_course(SimpleNamespace(db=None)) == {}
    finally:
        database.close()
