import pytest

from sm64_events.tracking.projection import Attempt
from sm64_events.tracking.runs import RunTracker, pb_run, gold_splits


class Ev:
    """Minimal event stand-in (RunTracker reads .type/.id/.wall_time_utc/.payload)."""
    def __init__(self, type, id=0, wall="2026-06-14T00:00:00Z", payload=None):
        self.type = type; self.id = id; self.wall_time_utc = wall
        self.payload = payload or {}


def att(outcome="success", course=None, star=None, segment_id=None):
    return Attempt(id=1, session_id=1, course_id=course, star_id=star,
                   strat_tag=None, anchor_type="none", anchor_frame=None,
                   outcome=outcome, outcome_detail=None, igt_frames=None,
                   rta_frames=None, started_utc="t", ended_utc="t",
                   cleared=False, cleared_reason=None, segment_id=segment_id)


STAR = {"type": "star", "course": 2, "star": 0}
SEG = {"type": "segment", "segment_id": 5}


def started(steps, offset=1360, rid=1):
    return Ev("run_started", payload={"route_id": rid, "route_name": "R",
              "route_steps": steps, "mode": "forgiving", "start_offset_ms": offset})


def test_arm_then_game_reset_starts_run():
    rt = RunTracker()
    steps = [{"need": 1, "candidates": [STAR]}]
    assert rt.feed(started(steps), []) == []          # arming produces nothing
    assert rt.active_run_view() is None               # not started until F1
    rt.feed(Ev("game_reset", id=100, wall="2026-06-14T00:00:01Z"), [])
    v = rt.active_run_view()
    assert v is not None and v["id"] == 100 and v["current_step"] == 0


def test_completing_only_step_finishes_run():
    rt = RunTracker()
    steps = [{"need": 1, "candidates": [STAR]}]
    rt.feed(started(steps), [])
    rt.feed(Ev("game_reset", id=100, wall="2026-06-14T00:00:00Z"), [])
    done = rt.feed(Ev("star_collected", id=101, wall="2026-06-14T00:02:00Z"),
                   [att(course=2, star=0)])
    assert len(done) == 1
    r = done[0]
    assert r.status == "finished" and r.reached_step == 1
    assert r.total_ms == 120000 and r.start_offset_ms == 1360
    assert r.splits[0]["elapsed_ms"] == 120000
    assert r.is_pb is True                             # first finished run
    assert rt.active_run_view() is None                # run over


def test_segment_step_completes_on_segment_success():
    rt = RunTracker()
    rt.feed(started([{"need": 1, "candidates": [SEG]}]), [])
    rt.feed(Ev("game_reset", id=100), [])
    done = rt.feed(Ev("attempt_completed", id=101, wall="2026-06-14T00:00:30Z"),
                   [att(segment_id=5)])
    assert done and done[0].status == "finished"


def test_completion_before_start_is_ignored():
    rt = RunTracker()
    rt.feed(started([{"need": 1, "candidates": [STAR]}]), [])
    # armed but no game_reset yet -> a grab does nothing
    assert rt.feed(Ev("star_collected", id=99), [att(course=2, star=0)]) == []
    assert rt.active_run_view() is None


def test_group_needs_k_distinct_no_duplicates():
    rt = RunTracker()
    A = {"type": "star", "course": 2, "star": 0}
    B = {"type": "star", "course": 2, "star": 1}
    C = {"type": "star", "course": 2, "star": 2}
    rt.feed(started([{"need": 2, "candidates": [A, B, C]}]), [])
    rt.feed(Ev("game_reset", id=100), [])
    # grab A, then A again (dup — no credit), then B -> 2 distinct -> finish
    assert rt.feed(Ev("star_collected", id=101), [att(course=2, star=0)]) == []
    assert rt.feed(Ev("star_collected", id=102), [att(course=2, star=0)]) == []  # dup
    done = rt.feed(Ev("star_collected", id=103, wall="2026-06-14T00:00:40Z"),
                   [att(course=2, star=1)])
    assert done and done[0].status == "finished" and done[0].reached_step == 1


def test_reset_within_step_counts_fail_run_continues():
    rt = RunTracker()
    rt.feed(started([{"need": 1, "candidates": [STAR]}]), [])
    rt.feed(Ev("game_reset", id=100), [])
    # a reset attempt on the current star: fail, run keeps going (no finalize)
    assert rt.feed(Ev("practice_reset", id=101), [att(outcome="reset", course=2, star=0)]) == []
    assert rt.active_run_view() is not None
    v = rt.active_run_view()
    assert v["steps"][0]["fails"] == 1
    # then a success finishes it
    done = rt.feed(Ev("star_collected", id=102, wall="2026-06-14T00:01:00Z"),
                   [att(course=2, star=0)])
    assert done and done[0].splits[0]["fails"] == 1


def test_game_reset_aborts_in_progress_and_restarts():
    rt = RunTracker()
    rt.feed(started([{"need": 1, "candidates": [STAR]},
                     {"need": 1, "candidates": [SEG]}]), [])
    rt.feed(Ev("game_reset", id=100, wall="2026-06-14T00:00:00Z"), [])
    rt.feed(Ev("star_collected", id=101, wall="2026-06-14T00:00:30Z"),
            [att(course=2, star=0)])           # step 1 done, on step 2 now
    aborted = rt.feed(Ev("game_reset", id=200, wall="2026-06-14T00:01:00Z"), [])
    assert len(aborted) == 1
    assert aborted[0].status == "aborted" and aborted[0].reached_step == 1
    assert aborted[0].id == 100                # the first run's id
    # a fresh run is now active, id=200, back at step 0
    v = rt.active_run_view()
    assert v["id"] == 200 and v["current_step"] == 0


def test_end_run_aborts_active_and_disarms():
    rt = RunTracker()
    rt.feed(started([{"need": 1, "candidates": [STAR]}]), [])
    rt.feed(Ev("game_reset", id=100), [])
    out = rt.feed(Ev("run_ended", id=300, wall="2026-06-14T00:00:10Z"), [])
    assert out and out[0].status == "aborted"
    assert rt.active_run_view() is None
    # disarmed: a later game_reset does NOT start a run
    rt.feed(Ev("game_reset", id=400), [])
    assert rt.active_run_view() is None


def test_pb_run_picks_min_finished_total():
    runs = [{"status": "finished", "total_ms": 130000},
            {"status": "aborted", "total_ms": 50000},
            {"status": "finished", "total_ms": 121000}]
    assert pb_run(runs)["total_ms"] == 121000
    assert pb_run([{"status": "aborted", "total_ms": 1}]) is None


def test_gold_splits_best_per_step_and_sum_of_best():
    steps = [{"need": 1, "candidates": [STAR]}, {"need": 1, "candidates": [SEG]}]
    # run 1: step0 dur 60s, step1 dur 70s (cumulative 60s, 130s)
    r1 = {"status": "finished", "route_steps": steps,
          "splits": [{"step_index": 0, "elapsed_ms": 60000},
                     {"step_index": 1, "elapsed_ms": 130000}]}
    # run 2: step0 dur 55s (gold), step1 dur 80s (cumulative 55s, 135s)
    r2 = {"status": "finished", "route_steps": steps,
          "splits": [{"step_index": 0, "elapsed_ms": 55000},
                     {"step_index": 1, "elapsed_ms": 135000}]}
    g = gold_splits([r1, r2], steps)
    assert g["durations"][0] == 55000 and g["durations"][1] == 70000
    assert g["sum_of_best"] == 125000


def test_is_pb_frozen_only_when_finished_beats_prior():
    rt = RunTracker()
    steps = [{"need": 1, "candidates": [STAR]}]
    def run(reset_id, grab_id, wall_end):
        rt.feed(started(steps), [])
        rt.feed(Ev("game_reset", id=reset_id, wall="2026-06-14T00:00:00Z"), [])
        return rt.feed(Ev("star_collected", id=grab_id, wall=wall_end),
                       [att(course=2, star=0)])[0]
    first = run(100, 101, "2026-06-14T00:02:00Z")    # 120s
    second = run(200, 201, "2026-06-14T00:01:30Z")   # 90s -> PB
    third = run(300, 301, "2026-06-14T00:02:30Z")    # 150s -> not PB
    assert first.is_pb is True and second.is_pb is True and third.is_pb is False
