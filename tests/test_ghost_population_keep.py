"""No run owed a ghost is lost silently: queue, retry, close, restart, outbox.

His words, 2026-11-02: "whenever someone gets a new time that could genuinely
contribute to the community's catalogue of ghosts (e.g., any new PB for any
subdivision ...) I want to make sure we keep all of them and upload each one to
the server."
"""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import logging
import threading
from types import SimpleNamespace

import pytest

from sm64_events.ghost import rival_recording
from sm64_events.ghost.clip import NO_POSE_ARCHIVE
from sm64_events.ghost.population import LocalPopulation
from sm64_events.ghost.rival_recording import PopulationRecorder, failure_reason
from tests.test_ghost_population import recorded
from tests.test_ghost_population_bins import (PLAYER, Community, Ladders, Replays, TJ_10_2, recorder_for, rows)

PRIVATE_PATH = r"C:\Users\griff\AppData\Local\SM64Trainer\data\replays\native\attempt_77.ghost"


class Flaky(Replays):
    """Fails each named attempt's first preservations, the way a full disk would."""
    def __init__(self, *runs, root=None, failures=1, error=None):
        super().__init__(*runs, root=root)
        self.failures, self.error, self.calls = failures, error, []

    def preserve_native(self, number, *, wait_tail=True):
        self.calls.append((number, wait_tail))
        if self.failures:
            self.failures -= 1
            if self.error is not None:
                raise self.error
            return {"path": None, "native_ghost": None, "native_ghost_reason": "The disk is full."}
        return {**super().preserve_native(number, wait_tail=wait_tail), "native_ghost": {"records": 3}}


def live(recorder, run):
    """The rolling archive still holds this run's poses."""
    recorder._now = lambda: datetime.fromisoformat(run.ended_utc.replace("Z", "+00:00")) + timedelta(seconds=1)
    recorder.retry_delays = (0,) * 10


def expired(recorder, run):
    recorder._now = lambda: datetime.fromisoformat(run.ended_utc.replace("Z", "+00:00")) + timedelta(days=1)


async def settled(predicate):
    for _ in range(500):
        if predicate():
            return
        await asyncio.sleep(.01)
    assert predicate()


# -- Hole 1: the queue ----------------------------------------------------------

def test_a_burst_of_finished_runs_never_overflows_the_queue(tmp_path):
    run = recorded(tmp_path / "run", number=77)[0]
    recorder, population = recorder_for(tmp_path, Replays())
    seen = []
    recorder._admit = lambda attempt, _contribution: seen.append(attempt.id) or True

    async def scenario():
        recorder.start()
        for number in range(1, 201):
            recorder.enqueue(replace(run, id=number))
        await recorder.queue.join()
        await recorder.stop()
    asyncio.run(scenario())
    assert seen == list(range(1, 201)) and recorder.dropped == 0
    population.close()


def test_a_run_finished_before_the_worker_started_still_counts(tmp_path):
    run = recorded(tmp_path / "run", number=77)
    recorder, population = recorder_for(tmp_path, Replays(run))
    recorder.enqueue(run[0])

    async def scenario():
        recorder.start()
        await recorder.queue.join()
        await recorder.stop()
    asyncio.run(scenario())
    assert rows(population) == [(PLAYER, "Standard", 2)]
    population.close()


def test_a_run_finished_after_stop_is_counted_and_said_out_loud(tmp_path, caplog):
    run = recorded(tmp_path / "run", number=77)
    recorder, population = recorder_for(tmp_path, Replays(run))
    asyncio.run(recorder.stop())
    with caplog.at_level(logging.WARNING, logger="sm64.ghost"):
        recorder.enqueue(run[0])
    assert recorder.dropped == 1 and "attempt 77" in caplog.text
    population.close()


# -- Hole 2: a failed admission -------------------------------------------------

def test_a_failed_preservation_retries_and_the_ghost_arrives(tmp_path):
    run = recorded(tmp_path / "run", number=77)
    replays = Flaky(run, failures=2)
    recorder, population = recorder_for(tmp_path, replays)
    live(recorder, run[0])

    async def scenario():
        recorder.start()
        recorder.enqueue(run[0])
        await settled(lambda: rows(population))
        await recorder.stop()
    asyncio.run(scenario())
    assert rows(population) == [(PLAYER, "Standard", 2)] and recorder.refused == 0
    assert [call[0] for call in replays.calls] == [77, 77, 77]
    assert population.owed_runs(10) == ()
    population.close()


def test_an_ineligible_run_refuses_at_once_with_no_retry(tmp_path, caplog):
    run = recorded(tmp_path / "run", number=77)
    recorder, population = recorder_for(tmp_path, Flaky(run, failures=0))
    live(recorder, run[0])
    # A segment-less non-xcam star has no native clock certificate: a plain ValueError.
    broken = replace(run[0], native_clock_kind="segment", timed_at="grab")
    run[1].current = broken

    async def scenario():
        recorder.start()
        recorder.enqueue(broken)
        await recorder.queue.join()
        assert recorder._retries == []
        await recorder.stop()
    with caplog.at_level(logging.DEBUG, logger="sm64.ghost"):
        asyncio.run(scenario())
    assert recorder.refused == 1 and rows(population) == [] and population.owed_runs(10) == ()
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]
    population.close()


def test_a_run_with_no_pose_recorder_never_retries(tmp_path, caplog):
    run = recorded(tmp_path / "run", number=77)
    replays = Flaky(run, failures=9)
    replays.preserve_native = lambda number, **_: {"path": None, "native_ghost_reason": NO_POSE_ARCHIVE}
    recorder, population = recorder_for(tmp_path, replays)
    live(recorder, run[0])

    async def scenario():
        recorder.start()
        recorder.enqueue(run[0])
        await recorder.queue.join()
        assert recorder._retries == []
        await recorder.stop()
    with caplog.at_level(logging.WARNING, logger="sm64.ghost"):
        asyncio.run(scenario())
    assert recorder.refused == 1 and not caplog.records
    population.close()


def test_giving_up_says_so_once_per_reason_and_names_no_path(tmp_path, caplog):
    runs = [recorded(tmp_path / str(number), number=number) for number in (77, 78)]
    recorder, population = recorder_for(tmp_path, Flaky(*runs, failures=999))
    live(recorder, runs[0][0])
    recorder.retry_delays = (0,)

    async def scenario():
        recorder.start()
        recorder.enqueue(runs[0][0])
        recorder.enqueue(runs[1][0])
        await settled(lambda: recorder.refused == 2)
        assert population.owed_runs(10) == ()  # The poses are gone: nothing more is owed.
        await recorder.stop()
    with caplog.at_level(logging.WARNING, logger="sm64.ghost"):
        asyncio.run(scenario())
    errors = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert [record.getMessage() for record in errors] == ["Attempt 77 lost its ghost: The disk is full."]
    assert [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING] == [
        "Attempt 78 lost its ghost: The disk is full."]
    population.close()


def test_a_full_population_says_so_instead_of_refusing_silently(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr("sm64_events.ghost.population.MAX_POPULATION_BYTES", 0)
    run = recorded(tmp_path / "run", number=77)
    recorder, population = recorder_for(tmp_path, Replays(run))

    async def scenario():
        recorder.start()
        recorder.enqueue(run[0])
        await recorder.queue.join()
        await recorder.stop()
    with caplog.at_level(logging.WARNING, logger="sm64.ghost"):
        asyncio.run(scenario())
    assert [record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR] == [
        "Attempt 77 lost its ghost: The local ghost population has reached its storage limit."]
    assert rows(population) == [] and population.owed_runs(10) == ()
    population.close()


def test_an_old_run_past_its_poses_fails_quietly(tmp_path, caplog):
    """A reclassified run from last month is not a loss: its poses left long ago."""
    run = recorded(tmp_path / "run", number=77)
    recorder, population = recorder_for(tmp_path, Flaky(run, failures=999))
    expired(recorder, run[0])

    async def scenario():
        recorder.start()
        recorder.reclassify(run[0])
        await recorder.queue.join()
        assert recorder._retries == []
        await recorder.stop()
    with caplog.at_level(logging.WARNING, logger="sm64.ghost"):
        asyncio.run(scenario())
    assert recorder.refused == 1 and not caplog.records
    population.close()


@pytest.mark.parametrize("error", [OSError(28, "No space left on device", PRIVATE_PATH),
                                   PermissionError(13, "Access is denied", PRIVATE_PATH)])
def test_no_reason_ever_carries_a_path(error):
    reason = failure_reason(error)
    assert "griff" not in reason and "Users" not in reason and reason
    assert "griff" not in failure_reason(ValueError(f"could not read {PRIVATE_PATH}"))


def test_a_disk_error_with_its_path_stays_out_of_the_log(tmp_path, caplog):
    run = recorded(tmp_path / "run", number=77)
    error = OSError(28, "No space left on device", PRIVATE_PATH)
    recorder, population = recorder_for(tmp_path, Flaky(run, failures=999, error=error))
    live(recorder, run[0])
    recorder.retry_delays = ()

    async def scenario():
        recorder.start()
        recorder.enqueue(run[0])
        await recorder.queue.join()
        await recorder.stop()
    with caplog.at_level(logging.WARNING, logger="sm64.ghost"):
        asyncio.run(scenario())
    assert [record.getMessage() for record in caplog.records] == [
        "Attempt 77 lost its ghost: No space left on device"]
    assert population.owed_runs(10)  # Disk errors may clear: the next start tries again.
    population.close()


def test_preserve_ghost_reasons_never_name_the_file(tmp_path):
    from sm64_events.replay.service import ReplayService
    class Archive:
        def export_attempt(self, *_args, **_kwargs):
            raise OSError(28, "No space left on device", PRIVATE_PATH)
    service = ReplayService.__new__(ReplayService)
    service.recorder = SimpleNamespace(pose_archive=Archive())
    service._native_target = lambda _attempt: {}
    assert service._preserve_ghost(SimpleNamespace(id=1), tmp_path / "x")[2] == "No space left on device"


# -- Hole 3: stop ---------------------------------------------------------------

def test_stop_finishes_the_queue_before_the_poses_go(tmp_path):
    runs = [recorded(tmp_path / str(number), number=number, frames=frames, strategy=strategy)
            for number, frames, strategy in [(77, 3, "A"), (78, 4, "B"), (79, 5, "C")]]
    replays = Flaky(*runs, failures=0)
    recorder, population = recorder_for(tmp_path, replays)

    async def scenario():
        recorder.start()
        await asyncio.sleep(.05)  # The worker is idle on an empty queue.
        for run in runs:
            recorder.enqueue(run[0])
        await recorder.stop()
    asyncio.run(scenario())
    assert rows(population) == [(PLAYER, "A", 3), (PLAYER, "B", 4), (PLAYER, "C", 5)]
    # Committing never waits for a star dance while the app closes.
    assert replays.calls and all(wait_tail is False for _number, wait_tail in replays.calls)
    population.close()


def test_stop_is_bounded_and_says_what_it_could_not_finish(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(rival_recording, "DRAIN_S", .05)
    run = recorded(tmp_path / "run", number=77)
    recorder, population = recorder_for(tmp_path, Replays(run))
    release = threading.Event()

    def stuck(_attempt, _contribution):
        release.wait(.3)
        return True
    recorder._admit = stuck

    async def scenario():
        recorder.start()
        await asyncio.sleep(.05)
        recorder.enqueue(run[0])
        recorder.enqueue(replace(run[0], id=78))
        recorder.enqueue(replace(run[0], id=79))
        await asyncio.sleep(.02)
        started = asyncio.get_running_loop().time()
        await recorder.stop()
        return asyncio.get_running_loop().time() - started
    with caplog.at_level(logging.ERROR, logger="sm64.ghost"):
        elapsed = asyncio.run(scenario())
    assert elapsed < 2
    assert [record.getMessage() for record in caplog.records] == [
        "2 finished runs were not admitted before the app closed; "
        "the next start admits those whose poses were committed"]
    population.close()


def test_a_pending_retry_gets_one_last_try_at_stop(tmp_path):
    run = recorded(tmp_path / "run", number=77)
    recorder, population = recorder_for(tmp_path, Flaky(run, failures=1))
    live(recorder, run[0])
    recorder.retry_delays = (3600,)

    async def scenario():
        recorder.start()
        recorder.enqueue(run[0])
        await recorder.queue.join()
        assert len(recorder._retries) == 1
        await recorder.stop()
    asyncio.run(scenario())
    assert rows(population) == [(PLAYER, "Standard", 2)]
    population.close()


# -- Hole 4: a saved PB ---------------------------------------------------------

def test_a_saved_pb_past_the_ceiling_keeps_trying_for_its_clip_but_gets_no_row(tmp_path):
    """PB ghosts are kept forever, whatever admission thinks of the run."""
    run = recorded(tmp_path / "run", number=77, frames=232, strategy="TJ Strat")
    replays = Flaky(run, failures=2)
    recorder, population = recorder_for(tmp_path, replays, ranks=Ladders({"TJ Strat": TJ_10_2}))
    live(recorder, run[0])
    kept = []

    async def on_kept(number):
        kept.append(number)

    async def scenario():
        recorder.start()
        recorder.keep_pb(run[0], on_kept)
        await settled(lambda: kept)
        await recorder.stop()
    asyncio.run(scenario())
    assert kept == [77] and [call[0] for call in replays.calls] == [77, 77, 77]
    assert rows(population) == []
    population.close()


def test_a_pb_failure_the_session_already_reported_reaches_sentry_once(tmp_path, caplog):
    run = recorded(tmp_path / "run", number=77)
    recorder, population = recorder_for(tmp_path, Flaky(run, failures=999))
    live(recorder, run[0])
    recorder.retry_delays = ()

    async def scenario():
        recorder.start()
        recorder.keep_pb(run[0], None, "The disk is full.")
        await recorder.queue.join()
        await recorder.stop()
    with caplog.at_level(logging.WARNING, logger="sm64.ghost"):
        asyncio.run(scenario())
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert "Saved PB 77 lost its ghost" in caplog.text
    population.close()


def test_a_pb_save_that_fails_once_still_becomes_the_playing_ghost(tmp_path, caplog):
    from tests.test_ghost_pb import pb_world, save_pb
    session, replay, row, archive, db, _store, _capture = pb_world(tmp_path)
    real = replay.preserve_native
    calls = []

    def flaky(attempt_id, *, wait_tail=True):
        calls.append(wait_tail)
        if len(calls) == 1:
            raise OSError(28, "No space left on device", PRIVATE_PATH)
        return real(attempt_id, wait_tail=wait_tail)
    replay.preserve_native = flaky
    population = LocalPopulation(tmp_path / "population")
    recorder = PopulationRecorder(population, replay, SimpleNamespace(db=db, ranks=None), lambda _run: None)
    recorder.retry_delays = (0,)
    recorder._now = lambda: datetime.fromisoformat(row.ended_utc.replace("Z", "+00:00")) + timedelta(seconds=1)
    session._population_recorder = recorder

    async def check():
        await session.update({"enabled": True})
        recorder.start()
        save_pb(db, row)
        await session.pb_changed(row, True)
        await settled(lambda: session.payload()["source"]["available"])
        assert session.payload()["source"]["attempt_id"] == row.id
        await session.stop()
    try:
        with caplog.at_level(logging.WARNING, logger="sm64.ghost"):
            asyncio.run(check())
    finally:
        archive.close()
        population.close()
    assert len(calls) == 2 and "griff" not in caplog.text


# -- Hole 5: an unexpected exit -------------------------------------------------

def crash_between(tmp_path, runs, *, owner=None, community=None, root=None):
    """A first session took these runs and died before finishing them."""
    replays = Replays(*runs, root=root)
    recorder, population = recorder_for(tmp_path, replays, community=community)
    for run in runs:
        recorder._note_owed(SimpleNamespace(attempt=run[0], contribution={"owner": owner} if owner else None))
    return replays, recorder, population


async def boot(recorder):
    recorder.start()
    await recorder.queue.join()
    await settled(lambda: recorder.task.done() or not recorder.job)


def test_the_worker_notes_a_run_as_owed_before_it_starts_on_it(tmp_path):
    run = recorded(tmp_path / "run", number=77)
    community = Community()
    recorder, population = recorder_for(tmp_path, Replays(run), community=community)
    during = []
    real = recorder._admit

    def watched(attempt, contribution):
        during.extend(population.owed_runs(10))
        return real(attempt, contribution)
    recorder._admit = watched

    async def scenario():
        recorder.start()
        recorder.enqueue(run[0])
        await recorder.queue.join()
        await recorder.stop()
    asyncio.run(scenario())
    assert [(row["attempt_id"], row["owner"]) for row in during] == [(77, "community-owner")]
    assert population.owed_runs(10) == ()
    population.close()


def test_a_committed_run_owed_a_row_is_admitted_at_the_next_start(tmp_path):
    root = tmp_path / "runs"
    run = recorded(root, number=77)
    _replays, recorder, population = crash_between(tmp_path, [run], root=root / "replays")
    assert len(population.owed_runs(10)) == 1

    async def scenario():
        await boot(recorder)
        await settled(lambda: rows(population))
        await recorder.stop()
    asyncio.run(scenario())
    assert rows(population) == [(PLAYER, "Standard", 2)] and population.owed_runs(10) == ()
    population.close()


def test_a_run_whose_clip_never_committed_is_dropped_and_said_out_loud(tmp_path, caplog):
    root = tmp_path / "runs"
    run = recorded(root, number=77)
    run[2].with_suffix(".json").unlink()
    _replays, recorder, population = crash_between(tmp_path, [run], root=root / "replays")

    async def scenario():
        await boot(recorder)
        await settled(lambda: population.owed_runs(10) == ())
        await recorder.stop()
    with caplog.at_level(logging.WARNING, logger="sm64.ghost"):
        asyncio.run(scenario())
    assert rows(population) == [] and "Attempt 77 ended with the app" in caplog.text
    population.close()


def test_a_reused_attempt_number_settles_the_owed_row_without_a_ghost(tmp_path):
    root = tmp_path / "runs"
    run = recorded(root, number=77)
    _replays, recorder, population = crash_between(tmp_path, [run], root=root / "replays")
    run[1].current = replace(run[0], session_id=10)
    admitted = []
    recorder._admit = lambda attempt, _contribution: admitted.append(attempt.id) or True

    async def scenario():
        await boot(recorder)
        await settled(lambda: population.owed_runs(10) == ())
        await recorder.stop()
    asyncio.run(scenario())
    assert admitted == []
    population.close()


class Waiting(Community):
    """The account is not back yet at boot, as during a normal start."""
    def __init__(self, owner=None):
        super().__init__()
        self.owner = owner

    def contribution_context(self):
        return {"owner": self.owner, "consent_epoch": 1} if self.owner else None


def test_an_owed_upload_waits_for_its_own_account_to_return(tmp_path):
    root = tmp_path / "runs"
    run = recorded(root, number=77)
    community = Waiting()
    _replays, recorder, population = crash_between(tmp_path, [run], owner="owner-a", community=community,
                                                   root=root / "replays")
    recorder.retry_delays = (.05,) * 100

    async def scenario():
        await boot(recorder)
        await settled(lambda: rows(population))
        assert population.owed_runs(10) and community.offers == []  # Row now; offer waits.
        community.owner = "owner-a"
        await settled(lambda: community.offers)
        await settled(lambda: population.owed_runs(10) == ())
        await recorder.stop()
    asyncio.run(scenario())
    assert [(source["frames"], extra["owner"]) for source, extra in community.offers] == [(2, "owner-a")]
    assert rows(population) == [(PLAYER, "Standard", 2)]
    population.close()


def test_an_owed_upload_never_goes_to_another_account(tmp_path):
    root = tmp_path / "runs"
    run = recorded(root, number=77)
    community = Waiting("owner-b")
    _replays, recorder, population = crash_between(tmp_path, [run], owner="owner-a", community=community,
                                                   root=root / "replays")

    async def scenario():
        await boot(recorder)
        await settled(lambda: population.owed_runs(10) == ())
        await recorder.stop()
    asyncio.run(scenario())
    assert community.offers == [] and rows(population) == [(PLAYER, "Standard", 2)]
    population.close()


def test_an_owed_upload_stops_waiting_after_its_window(tmp_path):
    root = tmp_path / "runs"
    run = recorded(root, number=77)
    community = Waiting()
    _replays, recorder, population = crash_between(tmp_path, [run], owner="owner-a", community=community,
                                                   root=root / "replays")
    recorder._now = lambda: datetime.now(timezone.utc) + timedelta(days=rival_recording.OWED_OFFER_DAYS + 1)

    async def scenario():
        await boot(recorder)
        await settled(lambda: population.owed_runs(10) == ())
        await recorder.stop()
    asyncio.run(scenario())
    assert community.offers == [] and rows(population) == [(PLAYER, "Standard", 2)]
    population.close()


def test_a_start_reads_only_what_is_owed(tmp_path):
    """Committed clips that lost their subdivision are not re-graded on each start."""
    root = tmp_path / "runs"
    run = recorded(root, number=77)
    _other = recorded(root, number=78)  # Committed, never owed, not a saved PB.
    replays = Replays(run, _other, root=root / "replays")
    recorder, population = recorder_for(tmp_path, replays)
    admitted = []
    recorder._admit = lambda attempt, _contribution: admitted.append(attempt.id) or True
    recorder._note_owed(SimpleNamespace(attempt=run[0], contribution=None))

    async def scenario():
        await boot(recorder)
        await settled(lambda: population.owed_runs(10) == ())
        await recorder.stop()
    asyncio.run(scenario())
    assert admitted == [77]
    population.close()


def test_a_finished_admission_leaves_nothing_owed_and_a_table_survives_reopen(tmp_path):
    run = recorded(tmp_path / "run", number=77)
    recorder, population = recorder_for(tmp_path, Replays(run))

    async def scenario():
        recorder.start()
        recorder.enqueue(run[0])
        await recorder.queue.join()
        await recorder.stop()
    asyncio.run(scenario())
    assert population.owed_runs(10) == ()
    recorder._note_owed(SimpleNamespace(attempt=run[0], contribution=None))
    population.close()
    reopened = LocalPopulation(tmp_path / "population")
    assert [row["attempt_id"] for row in reopened.owed_runs(10)] == [77]
    reopened.close()


# -- Hole 6: what an offer costs ------------------------------------------------

def test_a_run_its_kept_row_beats_never_reaches_the_outbox(tmp_path):
    fast, slow = (recorded(tmp_path / name, number=number, frames=frames)
                  for name, number, frames in [("fast", 77, 2), ("slow", 78, 3)])
    community = Community()
    recorder, population = recorder_for(tmp_path, Replays(fast, slow), community=community)
    context = community.contribution_context()
    assert recorder._admit(fast[0], context)
    assert recorder._admit(slow[0], context) is False
    assert [source["frames"] for source, _ in community.offers] == [2]
    population.close()


def test_a_new_best_in_any_subdivision_offers_even_when_it_is_not_the_players_pb(tmp_path):
    """His rule: "any new PB for any subdivision [even if it's not their actual PB]"."""
    fast, slower = (recorded(tmp_path / name, number=number, frames=frames, strategy="TJ Strat")
                    for name, number, frames in [("fast", 77, 150), ("slow", 78, 205)])
    community = Community()
    recorder, population = recorder_for(tmp_path, Replays(fast, slower), community=community,
                                        ranks=Ladders({"TJ Strat": TJ_10_2}))
    context = community.contribution_context()
    assert recorder._admit(fast[0], context)
    assert recorder._admit(slower[0], context)
    assert [row[2] for row in rows(population)] == [150, 205]
    assert [source["frames"] for source, _ in community.offers] == [150, 205]
    assert all(type(source["bin"]) is int for source, _ in community.offers)
    assert community.offers[0][0]["bin"] != community.offers[1][0]["bin"]
    population.close()


def test_the_cloud_catalog_decides_the_bin_when_it_is_held(tmp_path):
    """Local bands can differ: here locally one bin, on the cloud two, so the slow one counts."""
    fast, slow = (recorded(tmp_path / name, number=number, frames=frames)
                  for name, number, frames in [("fast", 77, 2), ("slow", 78, 3)])

    class Held(Community):
        def catalog_step(self, _target, _clock, _version, _strategy, frames):
            return frames
    community = Held()
    recorder, population = recorder_for(tmp_path, Replays(fast, slow), community=community)
    context = community.contribution_context()
    assert recorder._admit(fast[0], context)
    assert recorder._admit(slow[0], context) is False  # Locally beaten: still no second row.
    assert [(source["frames"], source["bin"]) for source, _ in community.offers] == [(2, 2), (3, 3)]
    population.close()


def test_a_run_with_no_grade_is_still_offered_and_the_cloud_answers(tmp_path):
    run = recorded(tmp_path / "run", number=77)
    community = Community()
    recorder, population = recorder_for(tmp_path, Replays(run), community=community,
                                        ranks=Ladders({}))
    run_row = run[0]
    recorder.tracker.ranks.clock_for = lambda _key: "rta"  # This ladder cannot grade the IGT clock.
    assert recorder._admit(run_row, community.contribution_context()) is False
    assert rows(population) == []
    assert ["bin" in source for source, _ in community.offers] == [False]
    population.close()


def test_a_kept_row_of_this_very_run_does_not_cancel_its_offer(tmp_path):
    """The row landed and the offer never did: an owed retry still has to offer it."""
    run = recorded(tmp_path / "run", number=77)
    community = Community()
    recorder, population = recorder_for(tmp_path, Replays(run), community=community)
    assert recorder._admit(run[0], None)
    assert recorder._admit(run[0], community.contribution_context()) is False
    assert len(community.offers) == 1
    population.close()


def test_a_real_kept_row_becomes_an_offer_that_passes_the_outbox_check(tmp_path):
    """Earlier ghosts read rows the real store wrote, not a hand-made shape."""
    from sm64_events.community.outbox import checked_source
    root = tmp_path / "runs"
    run = recorded(root, number=77)
    _replays, recorder, population = crash_between(tmp_path, [run], root=root / "replays")

    async def scenario():
        await boot(recorder)
        await settled(lambda: rows(population))
        await recorder.stop()
    asyncio.run(scenario())
    offers = recorder.earlier_sources()
    assert [offer["frames"] for offer in offers] == [2]
    assert checked_source({**offers[0], "owner": "11111111-1111-4111-8111-111111111111"})
    population.close()
