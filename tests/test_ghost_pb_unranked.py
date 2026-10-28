"""A PB nothing on its scope ranks still races, as "Your PB" with no rank.

His rule, 2026-09-22: "Any new PB should become a ghost immediately." A
strategy he just created has no ladder, and a Bowser reds->pipe segment's
Overall tab has no curve (Practice shows no Overall rank there either). The
saved PB used to need a rank to exist at all, so neither raced. It now races
unranked (`adaptive.UNRANKED`) and anchors no rival range: nobody is seated
automatically around it, while rivals he chose himself still play.
"""
from types import SimpleNamespace

from sm64_events.ghost.adaptive import UNRANKED, select_rivals
from sm64_events.ghost.pb import saved_pb_basis
from sm64_events.ghost.rivals import Rivals
from sm64_events.storage.db import Database
from tests.test_ghost_adaptive import attempt, basis, ranked, save
from tests.test_ghost_pb_reds_pipe import graded_frames, pipe_segment, ranks, segment_run  # noqa: F401
from tests.test_ghost_rivals import CONTROLS, Native, Population


def unranked_pb(frames=900):
    return {"frames": frames, **UNRANKED, "current_strategy": "Standard"}


def roster(tmp_path, ranks, run):
    database = Database(tmp_path / "practice.db")
    database.upsert_attempt(run)
    save(database, run)
    native = Native()
    rivals = Rivals(SimpleNamespace(db=database, ranks=ranks), Population(), native, lambda: "us")
    return database, rivals, native


def test_a_new_strategy_with_no_ladder_races_its_pb_unranked(tmp_path, ranks):
    target = {"kind": "star", "entity_key": "2-1", "strat": "My Route"}
    assert not ranks.ladder_cs("star:2:1", "My Route", "us")
    run = attempt(7, 650, strat_tag="My Route")
    database, rivals, native = roster(tmp_path, ranks, run)
    try:
        basis_now = saved_pb_basis(database, ranks, rivals.identity_for(target))
        assert {key: basis_now[key] for key in UNRANKED} == UNRANKED
        assert basis_now["frames"] == 650 and basis_now["ghost"].attempt_id == run.id

        answer = rivals.refresh(target, CONTROLS, enabled=True)

        assert answer["available"] and not rivals.rows
        raced, = native.preparations[-1][1]
        assert raced.pb and raced.attempt_id == run.id
        assert {key: rivals.payload()["pb"][key] for key in ("frames", *UNRANKED)} == {"frames": 650, **UNRANKED}
    finally:
        database.close()


def test_a_reds_pipe_pb_races_on_the_overall_tab_with_no_overall_curve(tmp_path, ranks):
    database = Database(tmp_path / "practice.db")
    try:
        segment_id = pipe_segment(database, "seg:reds->pipe:bitdw", 17)
        strategy = "Shig (Pipe)"
        run = segment_run(670000000692, segment_id, strategy, graded_frames(ranks, "star:16:0", strategy))
        database.upsert_attempt(run)
        save(database, run)
        native = Native()
        rivals = Rivals(SimpleNamespace(db=database, ranks=ranks), Population(), native, lambda: "us")
        target = {"kind": "segment", "entity_key": str(segment_id), "strat": strategy}
        rivals.refresh(target, CONTROLS, enabled=True)
        assert type(rivals.pb["step"]) is int, "the Strategy tab ranks it on the star's Pipe ladder"

        rivals.apply(rivals.checked({"scope": "overall"}, target))
        answer = rivals.refresh(target, CONTROLS, enabled=True)

        assert rivals.identity.scope == "overall" and answer["available"]
        assert rivals.pb["frames"] == run.rta_frames and rivals.pb["step"] is None
        assert native.preparations[-1][1][0].pb and native.preparations[-1][1][0].attempt_id == run.id
    finally:
        database.close()


def test_an_unranked_pb_seats_only_the_rivals_he_chose():
    rows = [ranked(step) for step in range(0, 20, 2)]
    assert select_rivals(rows, basis(), {}, {}), "control: a ranked PB seats rivals"
    for options in ({}, {"mode": "fixed", "min_step": 0, "max_step": 44}):
        assert select_rivals(rows, unranked_pb(), options, {}) == ()
        assert select_rivals(rows, unranked_pb(), options, {}, previous=select_rivals(rows, basis(), {}, {})) == ()
        chosen, = select_rivals(rows, unranked_pb(), options, {"2": rows[3]["id"]})
        assert (chosen["slot"], chosen["id"], chosen["pinned"]) == (2, rows[3]["id"], True)
