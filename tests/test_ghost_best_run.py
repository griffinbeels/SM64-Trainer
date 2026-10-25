"""Your best recorded run races in the PB's seat while the saved PB has no poses.

His decision, 2026-10-25: "When your saved PB has no poses, the PB seat races
your fastest run on that star that does have them. The card labels it "Best
recorded run" and keeps the real PB as the rank you're chasing." An AMD/JP
tester's older PBs were all saved before pose recording started, so before
this no ghost of theirs ever raced.

Real pose archive, replay service, population, `Rivals` and native session;
only the renderer's shared memory is a stand-in (`pb_world`).
"""
import asyncio
from dataclasses import replace

import pytest

from sm64_events.ghost.adaptive import rank_frames
from sm64_events.ghost.population import LocalPopulation
from sm64_events.ghost.rivals import Rivals
from tests.test_ghost_adaptive import Standards
from tests.test_ghost_pb import pb_world

PLAYER = "11111111-1111-4111-8111-111111111111"
PB_FRAMES, RECORDED_FRAMES = 515, 533


def world(tmp_path):
    """A saved PB whose run kept no poses and a slower run of the same
    strategy that the population admitted with its poses."""
    session, replay, original, archive, database, store, capture = pb_world(tmp_path)
    tracker = session._tracker
    tracker.ranks, tracker.strat_tag = Standards(), "Standard"
    recorded = replace(original, igt_frames=RECORDED_FRAMES, strat_tag="Standard", game_version="us",
                       valid=True, timed_at="xcam")
    poseless = replace(recorded, id=recorded.id + 1, igt_frames=PB_FRAMES)
    newer = replace(recorded, id=recorded.id + 2, igt_frames=500)
    replay.tracker.db._attempts = [recorded, poseless, newer]
    for row in (recorded, poseless, newer):
        database.upsert_attempt(row)
    database.set_state("ghost_player_id", PLAYER)
    population = LocalPopulation(tmp_path / "population")
    admitted = population.accept_verified(recorded, replay, player_id=PLAYER, player_name="You",
                                          clock="igt", game_version="us", full_start=True)
    session._version_of = lambda: "us"
    session._rivals = Rivals(tracker, population, session._native, lambda: "us")
    runs = dict(recorded=recorded, poseless=poseless, newer=newer)
    return session, replay, runs, admitted, population, (archive, database, store), capture


def save_pb(database, run):
    return database.insert_pb(course_id=run.course_id, star_id=run.star_id, strat_tag="Standard",
                              timer_mode="igt", frames=run.igt_frames, attempt_id=run.id,
                              saved_utc=run.ended_utc, game_version="us")


def close(population, owned):
    archive, database, store = owned
    population.close()
    archive.close()
    database.close()
    store._conn.close()


def clips(capture):
    return [(clip["clip_path"], clip["label"], clip["hue_degrees"]) for clip in capture.commands[-1].get("clips", [])]


def test_a_pb_without_poses_races_your_best_recorded_run_and_stays_the_rank(tmp_path, caplog):
    session, replay, runs, admitted, population, owned, capture = world(tmp_path)
    save_pb(session._tracker.db, runs["poseless"])

    async def scenario():
        try:
            with caplog.at_level("INFO", logger="sm64.ghost.state"):
                await session.update({"enabled": True})
                body = await session.refresh()  # the watch's cycle writes the decision line
            assert replay.find_native(runs["poseless"].id) is None
            pb, best = body["rivals"]["pb"], body["rivals"]["best_run"]
            # The rank you chase is the real saved PB, not the run that races.
            grade = rank_frames(session._tracker.ranks, session._rivals.identity, PB_FRAMES)
            assert pb["frames"] == PB_FRAMES and pb["attempt_id"] == runs["poseless"].id
            assert {key: pb[key] for key in ("tier", "division", "step")} == grade
            assert pb["available"] is False and pb["reason_text"]
            # The PB's seat and colour race the fastest admitted run instead.
            hue = pb["hue_degrees"]
            assert best == {"id": admitted["id"], "frames": RECORDED_FRAMES, "strategy": "Standard",
                            "game_version": "us", "hue_degrees": hue, "available": True, "reason_text": None,
                            **rank_frames(session._tracker.ranks, session._rivals.identity, RECORDED_FRAMES)}
            assert set(best) >= {"tier", "division", "step"} and "player_id" not in best
            assert body["source"]["available"] is True
            seat, = body["source"]["ghosts"]
            assert (seat["import_id"], seat["label"], seat["pb"], seat["available"]) == (
                admitted["id"], "BEST RUN", False, True)
            prepared, = clips(capture)
            assert prepared[0].endswith(f"{admitted['sha256']}.ghost") and prepared[1:] == ("BEST RUN", hue)
            # Your own run is never also an automatic rival.
            assert body["rivals"]["rows"] == []
        finally:
            await session.stop()

    try:
        asyncio.run(scenario())
    finally:
        close(population, owned)
    lines = [record.getMessage() for record in caplog.records if record.name == "sm64.ghost.state"]
    assert any(" pb_seat=best_run " in line for line in lines)


def test_a_new_pb_with_poses_takes_its_seat_back(tmp_path):
    session, replay, runs, admitted, population, owned, capture = world(tmp_path)
    database = session._tracker.db
    save_pb(database, runs["poseless"])

    async def scenario():
        try:
            before = await session.update({"enabled": True})
            assert before["rivals"]["best_run"]["id"] == admitted["id"]
            save_pb(database, runs["newer"])
            await session.pb_changed(runs["newer"], True)
            after = session.payload()
            assert replay.find_native(runs["newer"].id) is not None
            assert after["rivals"]["best_run"] is None
            assert after["rivals"]["pb"]["frames"] == 500 and after["rivals"]["pb"]["available"] is True
            prepared, = clips(capture)
            assert prepared[0].endswith(f"attempt_{runs['newer'].id}.ghost") and prepared[1] == "YOUR PB"
        finally:
            await session.stop()

    try:
        asyncio.run(scenario())
    finally:
        close(population, owned)


def test_a_pb_with_poses_races_itself_unchanged(tmp_path):
    session, replay, runs, admitted, population, owned, capture = world(tmp_path)
    replay.preserve_native(runs["poseless"].id)  # this PB kept its poses after all
    save_pb(session._tracker.db, runs["poseless"])

    async def scenario():
        try:
            body = await session.update({"enabled": True})
            assert body["rivals"]["best_run"] is None
            assert body["rivals"]["pb"]["available"] is True
            seat, = body["source"]["ghosts"]
            assert (seat["pb"], seat["attempt_id"], seat["label"]) == (True, runs["poseless"].id, "YOUR PB")
            prepared, = clips(capture)
            assert prepared[0].endswith(f"attempt_{runs['poseless'].id}.ghost")
        finally:
            await session.stop()

    try:
        asyncio.run(scenario())
    finally:
        close(population, owned)


def test_an_imported_pb_races_your_best_recorded_run(tmp_path):
    session, _replay, _runs, admitted, population, owned, capture = world(tmp_path)
    database = session._tracker.db
    database.insert_pb(2, 2, "Standard", "igt", 450, None, "2026-10-08T00:00:00Z",
                       imported_from="Ultimate Sheet", game_version=None)

    async def scenario():
        try:
            body = await session.update({"enabled": True})
            pb = body["rivals"]["pb"]
            assert pb["frames"] == 450 and pb["attempt_id"] is None and pb["available"] is False
            assert body["rivals"]["best_run"]["id"] == admitted["id"]
            assert body["rivals"]["best_run"]["available"] is True
            prepared, = clips(capture)
            assert prepared[1] == "BEST RUN"
        finally:
            await session.stop()

    try:
        asyncio.run(scenario())
    finally:
        close(population, owned)


@pytest.mark.parametrize("changes,scope,stands_in", [
    ({}, "strategy", True),
    ({}, "overall", True),
    # His rule: "If it's not a perfectly recorded ghost, it doesn't belong in
    # the system." Your cloud copy, your bundled collection and a developer
    # simulation are not this install's certified admission.
    ({"certified": False, "provenance": "community-unverified", "structurally_valid": True}, "strategy", False),
    ({"certified": False, "source_status": "bundled-author-recording", "structurally_valid": True,
      "full_start": None, "recorded_game_version": None}, "strategy", False),
    ({"simulated": True}, "strategy", False),
    # Overall admits other strategies as rivals, never as your PB's stand-in.
    ({"strategy": "Other"}, "overall", False),
    ({"player_id": "someone-else"}, "strategy", False),
])
def test_only_your_own_certified_recording_of_the_pb_strategy_stands_in(tmp_path, monkeypatch, changes, scope, stands_in):
    from types import SimpleNamespace
    from sm64_events.ghost import rivals as module
    from sm64_events.storage.db import Database
    from tests.test_ghost_adaptive import basis, recording
    from tests.test_ghost_rivals import CONTROLS, TARGET, Native, Population

    tracker = SimpleNamespace(db=Database(tmp_path / "practice.db"), ranks=Standards())
    try:
        tracker.db.set_state("ghost_player_id", "me")
        population = Population()
        population.rows = [recording("a" * 64, frames=700, **{"player_id": "me", **changes})]
        # An imported PB: a rank with no recording of its own.
        monkeypatch.setattr(module, "saved_pb_basis", lambda *_args: basis(step=10, frames=600))
        rivals = Rivals(tracker, population, Native(), lambda: "us")
        rivals.refresh(TARGET, CONTROLS, enabled=True)
        rivals.scopes[rivals._entity_key(TARGET)] = scope
        rivals.refresh(TARGET, CONTROLS, enabled=True, force=True)
        best = rivals.payload()["best_run"]
        assert (best is not None) is stands_in, best
        assert rivals.payload()["pb"]["available"] is False
    finally:
        tracker.db.close()


@pytest.mark.parametrize("own", [True, False])
def test_unticking_your_pb_also_unseats_the_best_run(tmp_path, own):
    """Off means off; and with no recording of yours, nothing stands in."""
    session, _replay, runs, _admitted, population, owned, capture = world(tmp_path)
    database = session._tracker.db
    save_pb(database, runs["poseless"])
    if not own:
        database.set_state("ghost_player_id", "22222222-2222-4222-8222-222222222222")

    async def scenario():
        try:
            body = await session.update({"enabled": True})
            if own:
                options = {**body["rivals"]["options"], "include_pb": False}
                body = await session.update({"rivals": {"options": options}, "target": body["target"]})
            assert body["rivals"]["best_run"] is None
            assert body["rivals"]["pb"]["frames"] == PB_FRAMES
            assert all(clip[1] != "BEST RUN" for clip in clips(capture))
            seats = [(row["pb"], row["available"]) for row in body["source"]["ghosts"] if row["pb"]]
            # Unticked: no seat at all. Nothing of yours recorded: the PB keeps
            # its seat and says why it cannot race, as before.
            assert seats == ([] if own else [(True, False)])
        finally:
            await session.stop()

    try:
        asyncio.run(scenario())
    finally:
        close(population, owned)
