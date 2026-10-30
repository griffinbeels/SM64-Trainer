"""Bowser course ghosts on the Overall lineup, graded by the Sheet's own curves.

His words, round 8: "We should definitely be showing ghosts OVERALL for reds /
no reds. If the user's practicing star grab only (no pipe for whatever reason),
we should let them. If the user's practicing including the pipe, we should let
them. If we don't have overall ranks for this, we should add them (derived from
the ultimate star sheet)."

The Overall ranks already exist once the Sheet calibration runs
(`library/calibration.py::prepare`): `placements.scoring_rows` files every
reds-to-pipe Sheet row under the reds->pipe segment and keeps only the
"Red coin star Xcam" rows on the star, so the star's curve never mixes the two
endpoints. Earlier ghost tests built ranks without that calibration, where the
segment has no curve; the live app never runs that way. These tests build the
real calibration from the bundled Ultimate Sheet's Bowser targets and drive
the ghost and Practice through the one resolver both use (`resolve_curve`).
"""
from types import SimpleNamespace

import pytest

from sm64_events.core.paths import bundled_rank_standards, bundled_sheet_ladders, bundled_sheet_library
from sm64_events.ghost.pb import saved_pb_basis
from sm64_events.ghost.rivals import Rivals
from sm64_events.library.audit import row_key
from sm64_events.library.placements import _bowser_reds_endpoint
from sm64_events.library.store import LibraryStore, read_snapshot, write_snapshot
from sm64_events.ranks.calibration import resolve_curve
from sm64_events.ranks.standards import RankStandards
from sm64_events.server.app import _create_library_adoptions
from sm64_events.storage.db import Database
from sm64_events.tracking.views import entity_rank
from tests.test_ghost_adaptive import recording, save
from tests.test_ghost_pb_reds_pipe import segment_run
from tests.test_ghost_rivals import CONTROLS, Native, Population

# (course abbreviation, the level it starts in, the paired star, a Pipe strategy)
COURSES = [("bitdw", 17, "star:16:0", "Shig (Pipe)"),
           ("bitfs", 19, "star:17:0", "Lavaboost (Pipe)"),
           ("bits", 21, "star:18:0", "Ultimate Cycle (Pipe)")]


@pytest.fixture(scope="module")
def bowser(tmp_path_factory):
    """His layout: the reds->pipe and No Reds segments by seed key, and the
    Sheet calibration fitted from the bundled library's Bowser targets."""
    root = tmp_path_factory.mktemp("bowser")
    database = Database(root / "practice.db")
    seeded = {row["seed_key"]: row["id"] for row in database.segment_defs()}
    segments = {}
    for abbrev, level, _star, _strategy in COURSES:
        segments[abbrev] = {
            "reds": database.insert_segment_def(f"{abbrev} 8 Red Coins → Pipe",
                                                [{"type": "level_enter", "to": level}], [], [],
                                                "2026-10-30T00:00:00Z", seed_key=f"seg:reds->pipe:{abbrev}"),
            "no_reds": seeded[f"seg:{abbrev}-pipe"]}
    payload = read_snapshot(bundled_sheet_library())
    payload["targets"] = [target for target in payload["targets"] if target.get("group") == "Bowser Courses"]
    write_snapshot(root / "library.json.gz", payload)
    ranks = RankStandards(root / "ranks.json", seed_path=bundled_rank_standards(),
                          sheet_path=bundled_sheet_ladders())
    ranks.load()
    ranks.grading_version = "us"
    library = LibraryStore(root / "library.json.gz")
    library.load()
    _create_library_adoptions(library, SimpleNamespace(db=database, ranks=ranks), root / "adoptions.json")
    assert ranks.calibrations.read is not None, "the Sheet calibration must be live, as in the app"
    yield SimpleNamespace(db=database, ranks=ranks, segments=segments, payload=payload)
    database.close()


def rows_by_endpoint(payload, star):
    target, = [target for target in payload["targets"] if target["entity_key"] == star]
    rows = {"star": set(), "pipe": set()}
    for item in target["approaches"]:
        rows[_bowser_reds_endpoint(target, item, "approach")].add(row_key(target, item["name"], item["ids"]))
    return rows


@pytest.mark.parametrize("abbrev,level,star,strategy", COURSES)
def test_the_star_reds_pipe_and_no_reds_each_have_their_own_sheet_curve(bowser, abbrev, level, star, strategy):
    rows = rows_by_endpoint(bowser.payload, star)
    assert rows["star"] and rows["pipe"]
    star_curve = resolve_curve(bowser.ranks, star, "us")
    reds_curve = resolve_curve(bowser.ranks, f"segment:{bowser.segments[abbrev]['reds']}", "us")
    no_reds_curve = resolve_curve(bowser.ranks, f"segment:{bowser.segments[abbrev]['no_reds']}", "us")
    assert set(star_curve["metadata"]["source_rows"]) <= rows["star"], "the star never grades on a pipe time"
    assert set(reds_curve["metadata"]["source_rows"]) <= rows["pipe"]
    assert reds_curve["metadata"]["population_count"] > 0
    assert no_reds_curve["metadata"]["population_count"] > 0
    assert not set(no_reds_curve["metadata"]["source_rows"]) & (rows["star"] | rows["pipe"])
    assert reds_curve["ladder_cs"]["Mario"] > star_curve["ladder_cs"]["Mario"], "the pipe comes after the grab"


@pytest.mark.parametrize("abbrev,level,star,strategy", COURSES)
def test_a_reds_pipe_pb_races_ranked_on_overall_as_practice_ranks_it(bowser, abbrev, level, star, strategy):
    segment_id = bowser.segments[abbrev]["reds"]
    ek = f"segment:{segment_id}"
    frames = resolve_curve(bowser.ranks, ek, "us")["ladder_cs"]["Bronze"] * 30 // 100
    run = segment_run(segment_id * 10**9 + 1, segment_id, strategy, frames)
    bowser.db.upsert_attempt(run)
    save(bowser.db, run)
    population, native = Population(), Native()
    rivals = Rivals(SimpleNamespace(db=bowser.db, ranks=bowser.ranks), population, native, lambda: "us")
    target = {"kind": "segment", "entity_key": str(segment_id), "strat": strategy}
    signature = rivals.identity_for(target).signature
    population.rows = [recording(f"{abbrev}-{step}", frames=frames + step * 15, kind="segment",
                                 entity_key=str(segment_id), clock="rta", strategy=strategy, signature=signature)
                       for step in range(-6, 7) if step]

    basis = saved_pb_basis(bowser.db, bowser.ranks, rivals.identity_for(target))
    practice = entity_rank(bowser.ranks, ek, frames, "us")
    assert (basis["tier"], basis["division"]) == (practice["rank"], practice["division"])
    assert basis["ghost"].attempt_id == run.id

    answer = rivals.refresh(target, CONTROLS, enabled=True)
    assert rivals.identity.scope == "overall" and answer["available"]
    assert type(rivals.pb["step"]) is int, "your seat carries its Overall rank"
    assert rivals.rows, "rivals are seated around a ranked PB"
    raced = native.preparations[-1][1]
    assert any(getattr(ghost, "pb", False) and ghost.attempt_id == run.id for ghost in raced)


@pytest.mark.parametrize("abbrev,level,star,strategy", COURSES)
def test_the_star_and_no_reds_pbs_rank_on_their_own_overall_curves(bowser, abbrev, level, star, strategy):
    rivals = Rivals(SimpleNamespace(db=bowser.db, ranks=bowser.ranks), Population(), Native(), lambda: "us")
    course = int(star.split(":")[1])
    star_strategy = strategy.replace(" (Pipe)", " (Star)")
    star_frames = resolve_curve(bowser.ranks, star, "us")["ladder_cs"]["Silver"] * 30 // 100
    from tests.test_ghost_adaptive import attempt
    grab = attempt(course * 1000 + 7, star_frames, course_id=course, star_id=0, strat_tag=star_strategy)
    no_reds_id = bowser.segments[abbrev]["no_reds"]
    pipe_frames = resolve_curve(bowser.ranks, f"segment:{no_reds_id}", "us")["ladder_cs"]["Silver"] * 30 // 100
    pipe = segment_run(no_reds_id * 10**9 + 7, no_reds_id, "Standard", pipe_frames)
    for run in (grab, pipe):
        bowser.db.upsert_attempt(run)
        save(bowser.db, run)
    for target, ek, frames in (({"kind": "star", "entity_key": f"{course}-0", "strat": star_strategy}, star, star_frames),
                               ({"kind": "segment", "entity_key": str(no_reds_id), "strat": "Standard"},
                                f"segment:{no_reds_id}", pipe_frames)):
        basis = saved_pb_basis(bowser.db, bowser.ranks, rivals.identity_for(target))
        practice = entity_rank(bowser.ranks, ek, frames, "us")
        assert basis["tier"] == practice["rank"] == "Silver"
        assert basis["division"] == practice["division"]
