"""One recording per strategy subdivision, the ghost ceiling, and reclassification.

His words, 2026-10-13: a ghost reclassified from Standard to TJ Strat "should no
longer appear for the old strategy"; "Did we save the BEST ghost per
subdivision and remove the old, worse ghost from the system?"; and the capless
threshold "plus 10%. Anything above that DOES NOT CREATE A GHOST."
"""
import asyncio
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import logging
from types import SimpleNamespace

import pytest

from sm64_events.ghost.adaptive import PracticeIdentity
from sm64_events.ghost.community_catalog import model_cutoffs, slowest_cutoff_frames, within_ghost_ceiling
from sm64_events.ghost.population import LocalPopulation
from sm64_events.ghost.rival_recording import MAX_BACKFILL, PopulationRecorder
from sm64_events.storage.db import Database
from tests.test_ghost_adaptive import Standards, attempt
from tests.test_ghost_population import recorded

# Reference ladders (centiseconds) as the bundled standards plus sheet-library
# adoptions resolve them for the US ROM, 2026-10-13. 10-2 TJ Strat is his run.
TJ_10_2 = {"Mario": 570, "Grandmaster": 586, "Master": 603, "Diamond": 620, "Platinum": 636,
           "Gold": 653, "Silver": 670, "Bronze": 686}
STANDARD_10_2 = {"Mario": 556, "Grandmaster": 573, "Master": 590, "Diamond": 606, "Platinum": 623,
                 "Gold": 640, "Silver": 656, "Bronze": 1656}
STANDARD_2_2 = {"Mario": 990, "Grandmaster": 1006, "Master": 1023, "Diamond": 1040, "Platinum": 1063,
                "Gold": 1096, "Silver": 1120, "Bronze": 2193}
PLAYER = "known-player"


class Ladders:
    """The rank store boundary only; grading and the binary search are real."""
    calibration_revision = "reference"

    def __init__(self, ladders):
        self.ladders = ladders

    @contextmanager
    def read_context(self):
        yield

    def ladder_cs(self, _key, strategy, _version=None):
        return self.ladders.get(strategy, {})

    def clock_for(self, _key):
        return "igt"


class Community:
    def __init__(self, *, fail=False):
        self.offers, self.retractions, self.fail = [], [], fail

    def contribution_context(self):
        return {"owner": "community-owner", "consent_epoch": 1}

    def remember_source(self, source, **contribution):
        self.offers.append((source, contribution))
        return True

    def retract_other_strategies(self, **values):
        self.retractions.append(values)
        if self.fail:
            raise OSError("community unavailable")


class Replays:
    """One fixture replay per recorded run, addressed by attempt number."""
    def __init__(self, *runs, root=None):
        self.runs = {row.id: replay for row, replay, _path in runs}
        self.cfg = SimpleNamespace(save_root=root)

    def attempt(self, number):
        return self.runs[number].attempt(number)

    def preserve_native(self, number):
        return self.runs[number].preserve_native(number)

    def find_native(self, number):
        return self.runs[number].find_native(number)


def identity_of(run):
    return PracticeIdentity("star", "2-1", "igt", "us", run.strat_tag) if run.strat_tag else None


def recorder_for(tmp_path, replay, *, ranks=None, community=None):
    database = Database(tmp_path / "practice.db")
    database.set_state("ghost_player_id", PLAYER)
    population = LocalPopulation(tmp_path / "population")
    tracker = type("Tracker", (), {"db": database, "ranks": ranks or Standards()})()
    return PopulationRecorder(population, replay, tracker, identity_of, community=community), population


def rows(population):
    return sorted((row["player_id"], row["strategy"], row["frames"])
                  for row in population.snapshot_all())


def clips(population):
    return sorted(path.name for path in (population.root / "clips").glob("*.ghost"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def drain(recorder, *steps):
    recorder.start()
    try:
        for step in steps:
            step()
        await recorder.queue.join()
    finally:
        await recorder.stop()


# -- The ceiling ----------------------------------------------------------------

@pytest.mark.parametrize("ladder,slowest", [(TJ_10_2, 210), (STANDARD_10_2, 737), (STANDARD_2_2, 915)])
def test_slowest_cutoff_is_rung_one_of_the_unchanged_catalog_ladder(ladder, slowest):
    cutoffs = model_cutoffs(ladder, "strategy")
    assert cutoffs[0] == {"step": 0, "frames": 100000}
    assert cutoffs[1] == {"step": 1, "frames": slowest}
    assert slowest_cutoff_frames(ladder, "strategy") == slowest
    assert slowest_cutoff_frames({}, "strategy") is None


def test_catalog_cutoffs_for_his_ladder_keep_their_exported_values():
    """The cloud catalog generation hashes these values; the refactor must not move one."""
    assert model_cutoffs(TJ_10_2, "strategy")[:3] == [
        {"step": 0, "frames": 100000}, {"step": 1, "frames": 210}, {"step": 2, "frames": 209}]


def test_ceiling_is_the_slowest_cutoff_plus_ten_percent_in_integers():
    assert within_ghost_ceiling(231, 210) and not within_ghost_ceiling(232, 210)
    assert within_ghost_ceiling(10 ** 6, None)


class Admissions:
    """Records every population call; the ceiling must refuse before any of them."""
    def __init__(self):
        self.calls = []

    def qualified_source(self, run, _replay, **values):
        self.calls.append("qualified_source")
        return {"strategy": run.strat_tag, "frames": run.igt_frames, "raw_sha256": "c" * 64}

    def metadata_snapshot(self, *_args, **_kwargs):
        self.calls.append("metadata_snapshot")
        return ()

    def accept_verified(self, *_args, **_kwargs):
        self.calls.append("accept_verified")
        return {"id": "f" * 64}


def test_a_run_past_the_ceiling_creates_no_ghost_and_no_offer_silently(tmp_path, caplog):
    database = Database(tmp_path / "practice.db")
    database.set_state("ghost_player_id", PLAYER)
    tracker = type("Tracker", (), {"db": database, "ranks": Ladders({"TJ Strat": TJ_10_2})})()
    population, community = Admissions(), Community()
    tagged = lambda run: PracticeIdentity("star", "10-2", "igt", "us", run.strat_tag)
    recorder = PopulationRecorder(population, object(), tracker, tagged, community=community)
    caplog.set_level(logging.DEBUG, logger="sm64.ghost")
    slow = attempt(frames=232, course_id=10, star_id=2, strat_tag="TJ Strat")
    assert recorder._admit(slow, community.contribution_context()) is False
    assert population.calls == [] and community.offers == []
    assert not [record for record in caplog.records if record.levelno > logging.DEBUG]
    edge = attempt(frames=231, course_id=10, star_id=2, strat_tag="TJ Strat")
    assert recorder._admit(edge, community.contribution_context()) is True
    assert population.calls == ["qualified_source", "metadata_snapshot", "accept_verified"]
    assert [source["frames"] for source, _ in community.offers] == [231]


# -- One recording per subdivision ----------------------------------------------

@pytest.mark.parametrize("shared", [False, True])
def test_a_faster_run_replaces_the_slower_row_and_its_unreferenced_clip(tmp_path, shared):
    slow = recorded(tmp_path / "slow", number=77, frames=3, pad=(1, 0, 0))
    fast = recorded(tmp_path / "fast", number=78, frames=2)
    recorder, population = recorder_for(tmp_path, Replays(slow, fast))
    assert recorder._admit(slow[0])
    if shared:  # Another player's row holds the same bytes, so they stay.
        population.accept_verified(slow[0], slow[1], player_id="other-player", player_name="Other",
                                   clock="igt", game_version="us", full_start=True)
    assert recorder._admit(fast[0])
    expected = [(PLAYER, "Standard", 2)] + ([("other-player", "Standard", 3)] if shared else [])
    assert rows(population) == expected
    names = {f"{digest(fast[2])}.ghost"} | ({f"{digest(slow[2])}.ghost"} if shared else set())
    assert set(clips(population)) == names
    assert not list((population.root / "clips").glob("*.removing"))
    assert recorder._admit(slow[0]) is False  # Slower than the kept row: skipped.
    population.close()


def test_start_prune_keeps_each_players_fastest_per_subdivision(tmp_path):
    runs = [recorded(tmp_path / str(number), number=number, frames=frames, strategy=strategy)
            for number, frames, strategy in [(77, 4, "Standard"), (78, 2, "Standard"), (79, 3, "Standard"),
                                             (80, 3, "Other"), (81, 5, "Missing"), (82, 6, "Missing")]]
    recorder, population = recorder_for(tmp_path, Replays(*runs))
    for row, replay, _path in runs:
        population.accept_verified(row, replay, player_id=PLAYER, player_name="You",
                                   clock="igt", game_version="us", full_start=True)
    population.accept_verified(runs[0][0], runs[0][1], player_id="other-player", player_name="Other",
                               clock="igt", game_version="us", full_start=True)
    # The worker prunes before its first job, so draining one unknown run orders it.
    asyncio.run(drain(recorder, lambda: recorder.reclassify(replace(runs[0][0], id=999))))
    assert recorder.pruned == 2 and recorder.refused == 1
    # "Missing" has no ladder: its target is left alone, both rows kept.
    assert rows(population) == [(PLAYER, "Missing", 5), (PLAYER, "Missing", 6), (PLAYER, "Other", 3),
                                (PLAYER, "Standard", 2), ("other-player", "Standard", 4)]
    assert recorder._prune() == 0
    population.close()


# -- Reclassification -------------------------------------------------------------

def test_reclassifying_standard_to_tj_moves_the_ghost_and_retracts_the_old_offer(tmp_path):
    run = recorded(tmp_path / "run", number=77, frames=2, strategy="Standard")
    replays, community = Replays(run), Community()
    recorder, population = recorder_for(tmp_path, replays, community=community)
    assert recorder._admit(run[0])
    assert rows(population) == [(PLAYER, "Standard", 2)]
    run[1].current = replace(run[0], strat_tag="TJ Strat")
    asyncio.run(drain(recorder, lambda: recorder.reclassify(run[0])))  # The caller's stale row.
    assert recorder.accepted == 1 and recorder.refused == 0
    assert rows(population) == [(PLAYER, "TJ Strat", 2)]
    assert community.retractions == [{"raw_sha256": digest(run[2]), "keep_strategy": "TJ Strat"}]
    assert [source["strategy"] for source, _ in community.offers] == ["Standard", "TJ Strat"]
    assert clips(population) == [f"{digest(run[2])}.ghost"]
    population.close()


def test_reclassifying_to_no_strategy_removes_every_row_and_admits_nothing(tmp_path):
    run = recorded(tmp_path / "run", number=77, frames=2, strategy="Standard")
    community = Community()
    recorder, population = recorder_for(tmp_path, Replays(run), community=community)
    assert recorder._admit(run[0])
    run[1].current = replace(run[0], strat_tag=None)
    assert recorder._reclassify(run[0]) is False
    assert rows(population) == [] and clips(population) == []
    assert community.retractions == [{"raw_sha256": digest(run[2]), "keep_strategy": None}]
    population.close()


def test_an_untagged_run_retained_at_completion_is_admitted_once_it_has_a_strategy(tmp_path):
    """His runs 18711 and 18930 finished untagged; picking a strategy seconds later admits them."""
    run = recorded(tmp_path / "run", number=77, frames=2, strategy=None)
    recorder, population = recorder_for(tmp_path, Replays(run), community=Community(fail=True))

    def tag():
        run[1].current = replace(run[0], strat_tag="TJ Strat")
        recorder.reclassify(run[0])
    asyncio.run(drain(recorder, lambda: recorder.enqueue(run[0]), tag))
    assert recorder.refused == 0 and recorder.accepted == 1
    assert rows(population) == [(PLAYER, "TJ Strat", 2)]
    population.close()


def test_reclassify_only_touches_this_runs_lineage(tmp_path):
    """A fresh profile reuses attempt numbers; another run's row is never removed."""
    run = recorded(tmp_path / "run", number=77, frames=2, strategy="Standard")
    recorder, population = recorder_for(tmp_path, Replays(run))
    assert recorder._admit(run[0])
    run[1].current = replace(run[0], session_id=10, strat_tag="TJ Strat")
    with pytest.raises(ValueError):  # Its clip belongs to the other session's run.
        recorder._reclassify(run[0])
    assert rows(population) == [(PLAYER, "Standard", 2)]
    run[1].current = run[0]
    assert recorder._reclassify(run[0]) is False  # Unchanged strategy: kept, never duplicated.
    assert rows(population) == [(PLAYER, "Standard", 2)]
    population.close()


# -- Start backfill of saved PB runs ------------------------------------------------

def save_pb(database, run):
    return database.insert_pb(course_id=run.course_id, star_id=run.star_id, strat_tag=run.strat_tag,
                              timer_mode="igt", frames=run.igt_frames, attempt_id=run.id,
                              saved_utc="2026-10-13T00:00:00Z")


def test_start_backfill_admits_a_saved_pb_whose_clip_never_became_a_row(tmp_path):
    """His runs 18711 and 18930 were tagged and saved after completion; no later change re-admits them."""
    root = tmp_path / "runs"
    kept, lost, clipless = (recorded(root, number=number, frames=2, strategy=strategy)
                            for number, strategy in [(77, "Standard"), (78, "TJ Strat"), (79, "Other")])
    clipless[2].with_suffix(".json").unlink()
    community = Community()
    recorder, population = recorder_for(tmp_path, Replays(kept, lost, clipless, root=root / "replays"),
                                        community=community)
    assert recorder._admit(kept[0], community.contribution_context())
    for run in (kept, lost, clipless):
        save_pb(recorder.tracker.db, run[0])
    # The worker backfills before its first job, so draining one unknown run orders it.
    asyncio.run(drain(recorder, lambda: recorder.reclassify(replace(kept[0], id=999))))
    assert recorder.backfilled == 1 and recorder.accepted == 1
    assert rows(population) == [(PLAYER, "Standard", 2), (PLAYER, "TJ Strat", 2)]
    # The run that already had a row is not offered again.
    assert [source["strategy"] for source, _ in community.offers] == ["Standard", "TJ Strat"]
    population.close()


def test_start_backfill_takes_only_the_newest_pb_runs(tmp_path):
    native = tmp_path / "replays" / "native"
    native.mkdir(parents=True)
    recorder, population = recorder_for(tmp_path, Replays(root=tmp_path / "replays"))
    for number in range(1, MAX_BACKFILL + 7):
        (native / f"attempt_{number}.json").write_text("{}", encoding="utf-8")
        recorder.tracker.db.insert_pb(course_id=2, star_id=1, strat_tag="Standard", timer_mode="igt",
                                      frames=2, attempt_id=number, saved_utc="2026-10-13T00:00:00Z")
    assert recorder._backfill_candidates() == tuple(range(MAX_BACKFILL + 6, 6, -1))
    population.close()


def test_start_backfill_never_reoffers_a_pb_run_its_subdivision_already_beats(tmp_path):
    """An older PB in the same subdivision as a faster kept row stays a candidate on
    every start; offering it would spend a daily cloud operation on "not faster"."""
    root = tmp_path / "runs"
    fast, slow = (recorded(root, number=number, frames=frames, strategy="Standard")
                  for number, frames in [(77, 2), (78, 3)])
    community = Community()
    recorder, population = recorder_for(tmp_path, Replays(fast, slow, root=root / "replays"),
                                        community=community)
    assert recorder._admit(fast[0], community.contribution_context())
    for run in (fast, slow):
        save_pb(recorder.tracker.db, run[0])
    assert recorder._backfill_candidates() == (78,)
    asyncio.run(drain(recorder, lambda: recorder.reclassify(replace(fast[0], id=999))))
    assert rows(population) == [(PLAYER, "Standard", 2)]
    assert [source["frames"] for source, _ in community.offers] == [2]  # nothing re-offered
    population.close()


def test_start_prune_removes_rows_stored_before_the_ceiling_existed(tmp_path):
    """10-2 TJ Strat: Capless 4 at 210 frames, so 232 frames is past the ceiling even
    alone in its subdivision; 205 frames stays."""
    runs = [recorded(tmp_path / str(number), number=number, frames=frames, strategy="TJ Strat")
            for number, frames in [(77, 232), (78, 205)]]
    recorder, population = recorder_for(tmp_path, Replays(*runs), ranks=Ladders({"TJ Strat": TJ_10_2}))
    for row, replay, _path in runs:
        population.accept_verified(row, replay, player_id=PLAYER, player_name="You",
                                   clock="igt", game_version="us", full_start=True)
    assert recorder._prune() == 1
    assert rows(population) == [(PLAYER, "TJ Strat", 205)]
    population.close()


def test_start_backfill_matches_rows_by_lineage_not_attempt_number(tmp_path):
    """A fresh profile reuses attempt numbers: another run's row never hides this PB."""
    root = tmp_path / "runs"
    run = recorded(root, number=77, frames=2, strategy="Standard")
    recorder, population = recorder_for(tmp_path, Replays(run, root=root / "replays"))
    save_pb(recorder.tracker.db, run[0])
    assert recorder._admit(run[0])
    assert recorder._backfill_candidates() == ()
    run[1].current = replace(run[0], session_id=10)  # This profile's attempt 77 is another run.
    assert recorder._backfill_candidates() == (77,)
    population.close()


def test_start_prune_removes_rows_stored_past_the_ceiling(tmp_path):
    """Rows admitted before the ceiling existed follow the same rule as the cloud."""
    over = recorded(tmp_path / "over", number=77, frames=232, strategy="TJ Strat")
    edge = recorded(tmp_path / "edge", number=78, frames=231, strategy="TJ Strat")
    recorder, population = recorder_for(tmp_path, Replays(over, edge), ranks=Ladders({"TJ Strat": TJ_10_2}))
    for (row, replay, _path), player in ((over, PLAYER), (edge, "other-player")):
        population.accept_verified(row, replay, player_id=player, player_name="Name", clock="igt",
                                   game_version="us", full_start=True)
    assert recorder._prune() == 1
    assert rows(population) == [("other-player", "TJ Strat", 231)]
    assert clips(population) == [f"{digest(edge[2])}.ghost"]
    population.close()
