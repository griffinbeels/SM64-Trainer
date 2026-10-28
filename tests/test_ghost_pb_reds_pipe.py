"""A Bowser reds->pipe segment's PB races, graded on its paired star's Pipe ladder.

His report, 2026-10-28: "it looks like my personal best ghosts aren't loading
for BITDW / Bow1?" He practiced "BitDW — 8 Red Coins → Pipe" on "Shig (Pipe)".
That segment has no ladder of its own: its Pipe-family ladders live on the
paired star (`tracking/activestrat.py::reds_pipe_segments`), which Practice
borrows. The ghost graded against the segment's own (empty) ladder, so his
saved PB had no rank, no basis and never raced. Every ghost grading path now
borrows the same star ladder through `PracticeIdentity.ladder_key`; the clock,
PB slot and region scope stay on the segment, as Practice keeps them.
"""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from sm64_events.core.paths import bundled_rank_standards, bundled_sheet_ladders
from sm64_events.ghost.community_catalog import match_windows, slowest_cutoff_frames, strategy_ghost_cutoff
from sm64_events.ghost.pb import saved_pb_basis
from sm64_events.ghost.rival_recording import PopulationRecorder
from sm64_events.ghost.rivals import Rivals
from sm64_events.ranks.standards import RankStandards
from sm64_events.storage.db import Database
from sm64_events.tracking.activestrat import PIPE_FAMILY_SUFFIX
from sm64_events.tracking.views import _strat_rank, grading_basis, pb_slot
from tests.test_ghost_adaptive import attempt, save
from tests.test_ghost_rivals import CONTROLS, Native, Population

# Each Bowser course's reds->pipe definition: (seed key, the level it starts
# in, the paired star whose ladders grade it).
PAIRINGS = [("seg:reds->pipe:bitdw", 17, "star:16:0"),
            ("seg:reds->pipe:bitfs", 19, "star:17:0"),
            ("seg:reds->pipe:bits", 21, "star:18:0")]


@pytest.fixture
def ranks(tmp_path):
    store = RankStandards(tmp_path / "ranks.json", seed_path=bundled_rank_standards(),
                          sheet_path=bundled_sheet_ladders())
    store.load()
    store.grading_version = "us"
    return store


def pipe_segment(database, seed_key, level):
    return database.insert_segment_def("8 Red Coins → Pipe", [{"type": "level_enter", "to": level}],
                                       [], [], "2026-10-28T00:00:00Z", seed_key=seed_key)


def segment_run(number, segment_id, strategy, frames):
    return replace(attempt(number, frames, strat_tag=strategy, timed_at=None, native_clock_kind="segment"),
                   course_id=None, star_id=None, segment_id=segment_id, igt_frames=None)


def graded_frames(ranks, star, strategy):
    """A PB time inside the paired star's ladder: its slowest named cutoff."""
    return max(ranks.ladder_cs(star, strategy, "us").values()) * 30 // 100


@pytest.mark.parametrize("seed_key,level,star", PAIRINGS)
def test_every_pipe_strategy_pb_grades_on_the_paired_star_like_practice(tmp_path, ranks, seed_key, level, star):
    database = Database(tmp_path / "practice.db")
    try:
        segment_id = pipe_segment(database, seed_key, level)
        segment = f"segment:{segment_id}"
        rivals = Rivals(SimpleNamespace(db=database, ranks=ranks), Population(), Native(), lambda: "us")
        strategies = [name for name in ranks.strategies(star) if name.endswith(PIPE_FAMILY_SUFFIX)]
        assert strategies and not ranks.strategies(segment)
        for number, strategy in enumerate(strategies, start=1):
            frames = graded_frames(ranks, star, strategy)
            run = segment_run(number, segment_id, strategy, frames)
            database.upsert_attempt(run)
            save(database, run)
            identity = rivals.identity_for({"kind": "segment", "entity_key": str(segment_id), "strat": strategy})
            # Practice's clock, PB slot and region scope stay on the segment.
            assert identity.clock == "rta" == ranks.clock_for(segment)
            assert identity.region_scope == pb_slot(ranks, segment, strategy)["region_scope"]
            basis = saved_pb_basis(database, ranks, identity)
            assert basis is not None, f"{strategy}: the saved PB found no ladder"
            pb = database.current_pb(None, None, "rta", segment_id=segment_id, strat_tag=strategy,
                                     **pb_slot(ranks, segment, strategy))
            practice = _strat_rank(ranks, star, strategy, grading_basis("pb", pb, [], strategy, "rta"))
            assert (basis["tier"], basis["division"]) == (practice["rank"], practice["division"])
            assert basis["ghost"].attempt_id == run.id and basis["ghost"].pb
    finally:
        database.close()


def test_reported_bitdw_shig_pipe_pb_races_with_rivals_on_its_ladder(tmp_path, ranks):
    database = Database(tmp_path / "practice.db")
    try:
        segment_id = pipe_segment(database, "seg:reds->pipe:bitdw", 17)
        strategy, star = "Shig (Pipe)", "star:16:0"
        frames = graded_frames(ranks, star, strategy)
        run = segment_run(670000000648, segment_id, strategy, frames)
        database.upsert_attempt(run)
        save(database, run)
        population, native = Population(), Native()
        rivals = Rivals(SimpleNamespace(db=database, ranks=ranks), population, native, lambda: "us")
        target = {"kind": "segment", "entity_key": str(segment_id), "strat": strategy}
        identity = rivals.identity_for(target)
        # A rival on the same segment and strategy, a little faster than his PB.
        population.rows = [{
            "id": "f" * 64, "player_id": "player:rival", "player_name": "Rival", "kind": "segment",
            "entity_key": str(segment_id), "signature": identity.signature, "clock": "rta",
            "game_version": "us", "strategy": strategy, "frames": frames - 30, "certified": True,
            "valid": True, "full_start": True, "ghost": SimpleNamespace(origin="ghost:rival")}]
        rivals.options.update(mode="fixed", min_step=0, max_step=44)

        answer = rivals.refresh(target, CONTROLS, enabled=True)

        assert answer["available"] and rivals.pb["frames"] == frames and type(rivals.pb["step"]) is int
        raced = native.preparations[-1][1]
        assert raced[0].pb and raced[0].attempt_id == run.id
        assert [row["player_name"] for row in rivals.rows] == ["Rival"] and type(rivals.rows[0]["step"]) is int
        assert rivals.payload()["pb"]["frames"] == frames
        # The community list and the ghost ceiling read the same borrowed ladder.
        assert match_windows(ranks, identity)
        assert strategy_ghost_cutoff(ranks, identity) == slowest_cutoff_frames(
            ranks.ladder_cs(star, strategy, "us"), "strategy")
    finally:
        database.close()


class Store:
    """The local population store boundary: rows in, removed ids out."""
    def __init__(self, rows):
        self.rows, self.removed = rows, []

    def rows_for_source(self, _source):
        return []

    def snapshot_all(self):
        return self.rows

    def remove(self, identifiers):
        self.removed.extend(identifiers)
        return len(identifiers)


def test_start_prune_keeps_one_pipe_recording_per_subdivision(tmp_path, ranks):
    database = Database(tmp_path / "practice.db")
    try:
        segment_id = pipe_segment(database, "seg:reds->pipe:bitdw", 17)
        strategy = "Shig (Pipe)"
        frames = graded_frames(ranks, "star:16:0", strategy)

        def row(identifier, player, time):
            return {"id": identifier * 64, "player_id": player, "player_name": player, "kind": "segment",
                    "entity_key": str(segment_id), "signature": "ab" * 32, "clock": "rta",
                    "game_version": "us", "strategy": strategy, "frames": time, "certified": True,
                    "valid": True, "full_start": True, "ghost": SimpleNamespace(origin=identifier)}

        store = Store([row("a", "one", frames - 2), row("b", "one", frames - 1),
                       row("c", "two", frames * 2)])
        recorder = PopulationRecorder(store, None, SimpleNamespace(db=database, ranks=ranks), lambda _: None)
        # His slower run in the same subdivision, and a run past the ghost ceiling.
        assert recorder._prune() == 2 and sorted(store.removed) == ["b" * 64, "c" * 64]
    finally:
        database.close()
