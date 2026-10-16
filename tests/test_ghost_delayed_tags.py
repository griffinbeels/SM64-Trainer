"""An unassigned completion survives real pose expiry and a tracker restart."""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from ghost_native_fixture import frame, producer
from sm64_events.core.events import Event
from sm64_events.ghost.archive import ARCHIVE_RETENTION_S, PoseArchive
from sm64_events.ghost.clip import ClipError
from sm64_events.ghost.population import LocalPopulation
from sm64_events.ghost.rival_recording import PopulationRecorder
from sm64_events.ghost.session import GhostSession
from sm64_events.main import _ghost_follows_attempts
from sm64_events.ranks.standards import RankStandards
from sm64_events.replay.service import saved_attempt_ids
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.storage.db import Database
from sm64_events.tracking.service import TrackerService
from test_ghost_population_bins import Community, STANDARD_2_2
from test_ghost_adaptive import Standards
from test_ghost_recording import native_service
from test_replay_service import make_service


def event(kind, frame_number, seconds, **payload):
    return Event(type=kind, frame=frame_number,
                 timestamp_utc=datetime.fromtimestamp(1800000000 + seconds, timezone.utc),
                 payload={"game_version": "us", **payload})


def session_for(tmp_path, tracker, community):
    tracker.ranks = RankStandards(tmp_path / "ranks.json")
    for strategy in ("Standard", "Other"):
        for rank, centiseconds in STANDARD_2_2.items():
            tracker.ranks.set_threshold("star:2:2", strategy, rank, centiseconds / 100)
    replay = make_service(tmp_path, [])
    replay.tracker = tracker
    population = LocalPopulation(tmp_path / "population")
    session = GhostSession(tracker=tracker, inputs=None, replay=replay,
                           broadcaster=Broadcaster(), population=population, community=community,
                           native=SimpleNamespace(), version_of=lambda: "us")

    async def refresh(**_kwargs):
        pass  # Playback rendering is independent; the shipping callbacks still run.
    session.refresh = refresh
    _ghost_follows_attempts(tracker, session)
    return session, replay, population


def expire_capture(tmp_path, archive, replay, row):
    """Prove real retention eviction before detaching the archive."""
    found = replay.find_native(row.id)
    assert found is not None
    original = found[0].read_bytes()
    archive.ingest(producer(), (frame(32, qpc=1000 + (ARCHIVE_RETENTION_S + 2) * 30),))
    with pytest.raises(ClipError):
        archive.export_attempt(row, tmp_path / "expired.ghost", target=replay._native_target(row))
    archive.close()
    replay.recorder.pose_archive = None
    return original


@pytest.mark.parametrize("restart", [False, True])
def test_untagged_completion_then_expired_archive_then_strategy_and_multiple_retags(tmp_path, restart):
    async def check():
        database = Database(tmp_path / "practice.db")
        tracker = TrackerService(database, Broadcaster())
        await tracker.start()
        community = Community()
        session, replay, population = session_for(tmp_path, tracker, community)
        recorder = session._population_recorder
        archive = PoseArchive(tmp_path / "poses.sqlite")
        replay.recorder.pose_archive = archive
        archive.ingest(producer(), tuple(frame(n, elapsed=n - 1) for n in range(1, 32)))
        recorder.start()
        try:
            await tracker.publish(event("practice_reset", 101, 0, area=1, igt_frames_before=0,
                                        loads_journaled=True))
            await tracker.publish(event("star_collected", 131, 1, course_id=2, star_id=2,
                                        igt_frames=30, igt_timed_at="xcam"))
            await recorder.queue.join()
            row = database.attempts()[0]
            assert row.valid is True and row.strat_tag is None and not row.cleared
            assert population.snapshot_all() == () and community.offers == []
            original = expire_capture(tmp_path, archive, replay, row)

            if restart:
                await recorder.stop()
                population.close()
                database.close()
                database = Database(tmp_path / "practice.db")
                tracker = TrackerService(database, Broadcaster())
                tracker.saved_clip_ids = lambda among=None: saved_attempt_ids(replay.cfg.save_root, among=among)
                await tracker.start()  # The real startup prune must spare the unassigned run.
                assert database.attempt(row.id) is not None
                session, replay, population = session_for(tmp_path, tracker, community)
                recorder = session._population_recorder
                recorder.start()

            await tracker.set_strat(2, 2, "Standard", save_adopted=False)
            await asyncio.gather(*session._reclassify_tasks)
            await recorder.queue.join()
            assert [record["strategy"] for record in population.snapshot_all()] == ["Standard"]
            for strategy in ("Other", None, "Standard"):
                await tracker.set_attempt_strat(row.id, strategy)
                await asyncio.gather(*session._reclassify_tasks)
                await recorder.queue.join()
                assert [record["strategy"] for record in population.snapshot_all()] == ([strategy] if strategy else [])
                path, manifest = replay.find_native(row.id)
                assert manifest["target"]["strat"] == strategy and path.read_bytes() == original
            assert [source["strategy"] for source, _ in community.offers] == ["Standard", "Other", "Standard"]
            assert [entry["keep_strategy"] for entry in community.retractions] == ["Standard", "Other", None, "Standard"]
        finally:
            await recorder.stop()
            archive.close()
            population.close()
            database.close()
    asyncio.run(check())


@pytest.mark.parametrize("changes,clock,unknown_pad", [
    ({"valid": False}, "igt", False),
    ({"valid": None}, "igt", False),
    ({"game_version": None}, "igt", False),
    ({"native_clock_frame": None}, "igt", False),
    ({"native_clock_utc": "not-a-time"}, "igt", False),
    ({"native_clock_kind": None}, "igt", False),
    ({"timed_at": "grab"}, "igt", False),
    ({"timed_by": "imported"}, "igt", False),
    ({"timed_by": "delta"}, "igt", False),
    ({"state_loads": 1}, "igt", False),
    ({"cleared": True}, "igt", False),
    ({"outcome": "death"}, "igt", False),
    ({}, "unknown", False),
    ({}, "igt", True),
])
def test_unassigned_retention_refuses_incomplete_or_uncertified_recordings(tmp_path, changes, clock, unknown_pad):
    replay, row, archive = native_service(tmp_path, rows=tuple(
        frame(n, pad=None if unknown_pad else (0, 0, 0)) for n in (1, 2, 3)))
    row = replace(row, **{**dict(valid=True, game_version="us", timed_at="xcam", igt_frames=2), **changes})
    replay.tracker.db._attempts = [row]
    database = Database(tmp_path / "practice.db")
    community = Community()
    population = LocalPopulation(tmp_path / "population")
    recorder = PopulationRecorder(population, replay, SimpleNamespace(db=database, ranks=Standards(clock)),
                                  lambda _attempt: None, community=community)
    try:
        with pytest.raises(ValueError):
            recorder._admit(row)
        assert population.snapshot_all() == () and community.offers == []
    finally:
        archive.close()
        population.close()
        database.close()


def test_retained_unassigned_capture_still_requires_the_final_strategy_ceiling(tmp_path):
    from sm64_events.ghost.adaptive import PracticeIdentity

    replay, row, archive = native_service(tmp_path)
    row = replace(row, valid=True, game_version="us", timed_at="xcam", igt_frames=5000)
    replay.tracker.db._attempts = [row]
    database = Database(tmp_path / "practice.db")
    community = Community()
    population = LocalPopulation(tmp_path / "population")
    recorder = PopulationRecorder(population, replay, SimpleNamespace(db=database, ranks=Standards()),
        lambda run: PracticeIdentity("star", "2-2", "igt", "us", run.strat_tag) if run.strat_tag else None,
        community=community)
    try:
        assert recorder._admit(row) is False
        assert replay.find_native(row.id) is not None
        archive.close()
        replay.recorder.pose_archive = None
        replay.tracker.db._attempts = [replace(row, strat_tag="Standard")]
        replay.retarget_native(row.id)
        assert recorder._reclassify(row) is False
        assert population.snapshot_all() == () and community.offers == []
    finally:
        archive.close()
        population.close()
        database.close()


def test_unassigned_segment_retains_its_real_rta_clock_without_a_strategy(tmp_path):
    from sm64_events.ghost.adaptive import PracticeIdentity

    replay, row, archive = native_service(tmp_path)
    row = replace(row, valid=True, game_version="jp", segment_id=7, course_id=None, star_id=None,
                  native_clock_kind="segment", timed_by="delta")
    replay.tracker.db._attempts = [row]
    database = Database(tmp_path / "practice.db")
    community = Community()
    population = LocalPopulation(tmp_path / "population")
    recorder = PopulationRecorder(population, replay, SimpleNamespace(db=database, ranks=Standards("rta")),
                                  lambda _attempt: None, community=community)
    try:
        assert recorder._admit(row) is False
        path, manifest = replay.find_native(row.id)
        assert manifest["native_clock_kind"] == "segment" and manifest["target"]["strat"] is None
        assert path.is_file() and population.snapshot_all() == () and community.offers == []
        from sm64_events.ghost.pb import eligible_attempt
        assert eligible_attempt(row, PracticeIdentity("segment", "7", "rta", "jp", None)) is False
    finally:
        archive.close()
        population.close()
        database.close()
