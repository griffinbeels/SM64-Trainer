"""An automatic PB (task 0144) -- his decisions of 2026-09-23, one test per gate.

"If the user has a strategy selected, and the attempt was valid (started from
a true reset), then it should automatically save." It saves through the real
`save_pb`, so `pb_saved`, the ghost's `on_pb_changed` and the replay's
`on_pb_saved` fire exactly as for a click, and Undo still works.
"""
import asyncio
from datetime import datetime, timezone

import pytest

from sm64_events.core.events import Event
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.storage.db import Database
from sm64_events.tracking.service import TrackerService

T0 = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)
WF, COURSE, STAR = 24, 2, 1


def ev(type_, frame, payload=None):
    return Event(type=type_, frame=frame, timestamp_utc=T0, payload=payload or {})


def reset(frame, area=1):
    return ev("practice_reset", frame,
              {"igt_frames_before": 300, "mario_acted": True, "area": area,
               "prev_area": area, "area_load": False, "teleport": False,
               "loads_journaled": True})


def load(frame):
    return ev("state_loaded", frame,
              {"igt_frames_restored": 181, "igt_frames_before": 353,
               "load_signature": "igt_rewound", "mario_acted": True, "area": 1,
               "prev_area": 1, "area_load": False, "teleport": False,
               "loads_journaled": True})


def grab(frame, igt, timed_at="xcam"):
    return ev("star_collected", frame,
              {"course_id": COURSE, "star_id": STAR, "igt_frames": igt,
               "igt_timed_at": timed_at})


def enter():
    return [ev("level_changed", 1000, {"from": 6, "to": WF}),
            ev("area_changed", 1000, {"level": WF, "from": 1, "to": 1}),
            reset(1047)]


class Hooks:
    def __init__(self):
        self.ghost, self.media = [], []

    async def pb_changed(self, attempt, saved):
        self.ghost.append((attempt.id if attempt else None, saved))

    async def pb_saved(self, attempt_id):
        self.media.append(attempt_id)
        return {"status": "saved"}


def run(tmp_path, *steps, strat="Standard"):
    """Drive one live session on ONE event loop (an automatic save runs as a
    task beside the tracking path), then settle every save it started."""
    db = Database(tmp_path / "t.db")
    svc = TrackerService(db, Broadcaster())
    hooks = Hooks()
    svc.on_pb_changed, svc.on_pb_saved = hooks.pb_changed, hooks.pb_saved

    async def scenario():
        await svc.start()
        if strat is not None:
            await svc.set_strat(COURSE, STAR, strat)
        for step in steps:
            if isinstance(step, Event):
                await svc.publish(step)
            else:
                await step(svc, db)
            while svc.auto_pb_tasks:
                await asyncio.gather(*list(svc.auto_pb_tasks))
    asyncio.run(scenario())
    return db, svc, hooks


def pbs(db):
    return [(row["attempt_id"], row["frames"], row["timer_mode"], row["strat_tag"])
            for row in db.pbs()]


def pb_events(db):
    return [e.payload for e in db.events() if e.type == "pb_saved"]


def test_a_valid_run_with_a_strategy_saves_itself(tmp_path):
    db, svc, hooks = run(tmp_path, *enter(), grab(1500, 480))
    run_id = db.attempts()[-1].id
    assert pbs(db) == [(run_id, 480, "igt", "Standard")]
    assert pb_events(db)[-1]["auto"] is True
    # The same doors a click goes through: the ghost follows, the replay keeps.
    assert hooks.ghost == [(run_id, True)] and hooks.media == [run_id]


def test_a_faster_valid_run_replaces_it_and_a_slower_one_does_not(tmp_path):
    db, svc, _ = run(tmp_path, *enter(), grab(1500, 480),
                     reset(1600), grab(2000, 450),
                     reset(2100), grab(2600, 470))
    assert [frames for _, frames, _, _ in pbs(db)] == [480, 450]
    # Each save names the PB it replaced -- the practice log's Time Save.
    assert [e["previous_frames"] for e in pb_events(db)] == [None, 480]


def test_no_strategy_selected_never_saves(tmp_path, caplog):
    """Decided before anything is attempted: a run with no strategy is not a
    failed save to log on every success, it is simply not a candidate."""
    db, _, _ = run(tmp_path, *enter(), grab(1500, 480), strat=None)
    assert pbs(db) == []
    assert not [r for r in caplog.records if "automatic PB" in r.getMessage()]


def test_a_save_state_run_is_never_saved_even_by_hand(tmp_path):
    async def manual(svc, db):
        for clock in ("igt", "rta"):
            with pytest.raises(ValueError, match="save_state"):
                await svc.save_pb(db.attempts()[-1].id, clock)

    db, svc, _ = run(tmp_path, *enter(), load(1300), grab(1500, 480))
    a = db.attempts()[-1]
    assert (a.valid, a.invalid_reason) == (False, "save_state")
    assert pbs(db) == []
    assert svc.pb_action(a, "igt") == (None, {"reason": "save_state", "strat": None})
    db2, _, _ = run(tmp_path / "manual", *enter(), load(1300), grab(1500, 480), manual)
    assert pbs(db2) == [] and pb_events(db2) == []


def test_a_bowser_fight_rearmed_by_a_load_never_saves_a_partial_pb(tmp_path):
    made = {}

    async def define(svc, db):
        made["id"] = await svc.create_segment({
            "name": "Bowser load regression", "match_mode": "strict",
            "start_triggers": [{"type": "level_enter", "to": 33},
                               {"type": "attempt_anchor", "level": 33}],
            "end_triggers": [{"type": "key_grabbed", "level": 33}]})
        await svc.set_strat_segment(made["id"], "Standard")

    db, svc, _ = run(tmp_path, define,
                     ev("level_changed", 19088, {"from": 19, "to": 33}),
                     reset(20187), load(21638),
                     ev("key_grabbed", 22173,
                        {"level": 33, "igt_frames": 1013, "mario_alive": True}))
    a, = [a for a in db.attempts()
          if a.segment_id == made["id"] and a.outcome == "success"]
    assert (a.rta_frames, a.valid, a.invalid_reason) == (535, False, "save_state")
    assert pbs(db) == [] and pb_events(db) == []
    # Even a stale incorrectly valid cache cannot authorize a partial PB:
    # the opening load is independently proven by the journal.
    from dataclasses import replace
    assert svc._auto_pb_mode(replace(a, valid=True, invalid_reason=None, state_loads=0)) is None


def test_a_subarea_reset_is_not_saved(tmp_path):
    db, _, _ = run(tmp_path, *enter(), reset(1400, area=2), grab(1900, 480))
    assert db.attempts()[-1].invalid_reason == "subarea_reset"
    assert pbs(db) == []


def test_a_grab_timed_star_is_not_saved(tmp_path):
    """An existing PB gate the automatic save must not step round."""
    db, _, _ = run(tmp_path, *enter(), grab(1500, 480, timed_at="grab"))
    assert pbs(db) == []


def test_a_run_nobody_can_judge_is_not_saved(tmp_path):
    """An anchor that does not say loads are journaled: validity unknown."""
    old_reset = ev("practice_reset", 1047, {"igt_frames_before": 0})
    db, _, _ = run(tmp_path, ev("level_changed", 1000, {"from": 6, "to": WF}),
                   old_reset, grab(1500, 480))
    assert db.attempts()[-1].valid is None and pbs(db) == []


def test_undo_still_works_after_an_automatic_save(tmp_path):
    async def undo(svc, db):
        await svc.undo_pb(db.attempts()[-1].id, "igt")

    db, _, hooks = run(tmp_path, *enter(), grab(1500, 480), undo)
    assert pbs(db) == []
    assert hooks.ghost[-1][1] is False


def test_a_valid_segment_saves_on_its_own_clock(tmp_path):
    """A segment grades on RTA, so that is the clock its automatic save uses;
    a picked segment's strategy is the one it is practised with."""
    made = {}

    async def define(svc, db):
        made["id"] = await svc.create_segment({
            "name": "pole to star", "match_mode": "strict",
            "start_triggers": [{"type": "moment_reached", "level": WF,
                                "kind": "pole_grab"}],
            "end_triggers": [{"type": "star_grabbed", "course": COURSE,
                              "star": STAR}]})
        await svc.set_strat_segment(made["id"], "Standard")

    pole = ev("moment_reached", 1200, {"kind": "pole_grab", "level": WF,
                                       "area": 1, "ordinal": 1})
    db, _, _ = run(tmp_path, define, *enter(), pole, grab(1500, 480))
    seg_rows = [row for row in db.pbs() if row["segment_id"] == made["id"]]
    assert [(row["timer_mode"], row["frames"]) for row in seg_rows] == [("rta", 300)]
