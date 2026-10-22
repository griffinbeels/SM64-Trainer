"""A concurrent PB read cannot cache a refusal after its real poses commit."""
import asyncio
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from sm64_events.ghost.rivals import Rivals
from tests.test_ghost_adaptive import Standards
from tests.test_ghost_pb import pb_world


class EmptyPopulation:
    generation = 1

    def metadata_snapshot(self, *_args, **_kwargs):
        return ()

    def get(self, identity):
        raise LookupError(identity)


def test_saved_slower_ghost_plays_without_replacing_pb_missing_poses(tmp_path):
    session, replay, original, archive, database, store, capture = pb_world(tmp_path)
    tracker = session._tracker
    tracker.ranks, tracker.strat_tag = Standards(), "Standard"
    slower = replace(original, igt_frames=533, strat_tag="Standard", game_version="us")
    faster = replace(slower, id=slower.id + 1, igt_frames=515)
    replay.tracker.db._attempts = [slower, faster]
    for row in (slower, faster):
        database.upsert_attempt(row)
    database.insert_pb(course_id=faster.course_id, star_id=faster.star_id,
        strat_tag="Standard", timer_mode="igt", frames=515,
        attempt_id=faster.id, saved_utc=faster.ended_utc, game_version="us")
    session._version_of = lambda: "us"
    session._rivals = Rivals(tracker, EmptyPopulation(), session._native, lambda: "us")

    async def scenario():
        try:
            before = await session.update({"enabled": True})
            assert before["rivals"]["pb"]["frames"] == 515
            assert before["rivals"]["pb"]["available"] is False
            assert replay.find_native(faster.id) is None
            answer = await session.save_ghost(slower.id)
            assert answer["saved"] is True and answer["poses_reason"] is None
            after = answer["session"]
            assert after["rivals"]["pb"]["frames"] == 515
            assert after["rivals"]["pb"]["available"] is False
            chosen = [row for row in after["rivals"]["rows"] if row.get("pinned")]
            assert len(chosen) == 1 and chosen[0]["frames"] == 533 and chosen[0]["own"] is True
            assert after["source"]["available"] is True
            ghosts = after["source"]["ghosts"]
            assert any(row["attempt_id"] == slower.id and row["available"] for row in ghosts)
            assert any(row["attempt_id"] == faster.id and not row["available"] for row in ghosts)
            assert replay.find_native(slower.id) is not None
            prepared = [item["clip_path"] for item in capture.commands[-1].get("clips", [])]
            assert len(prepared) == 1 and prepared[0].endswith(f"attempt_{slower.id}.ghost")
            assert len(database.pbs()) == 1 and database.pbs()[0]["frames"] == 515
            assert store.list_for("star", f"{slower.course_id}-{slower.star_id}") == []
        finally:
            await session.stop()

    try:
        asyncio.run(scenario())
    finally:
        archive.close()
        database.close()
        store._conn.close()


async def assert_ready_playback(session, row):
    ready = session.payload()
    assert ready["source"]["available"] is True
    assert ready["source"]["attempt_id"] == row.id
    assert ready["rivals"]["pb"]["available"] is True
    assert ready["rivals"]["pb"]["reason_text"] is None
    generation = ready["rivals"]["generation"]
    again = await session.refresh(resolve=True)
    assert again["rivals"]["pb"]["available"] is True
    assert again["rivals"]["generation"] == generation


@pytest.mark.parametrize("outcome", ["ready", "superseded", "missing"])
def test_pb_read_during_preservation_recovers_and_retains_playback(tmp_path, monkeypatch, outcome):
    session, replay, row, archive, _db, _store, _capture = pb_world(tmp_path)
    tracker = session._tracker
    tracker.ranks = Standards()
    tracker.strat_tag = "Standard"
    row = replace(row, strat_tag="Standard")
    replay.tracker.db._attempts = [row]
    tracker.db.upsert_attempt(row)
    population = SimpleNamespace(generation=1, metadata_snapshot=lambda *_args, **_kwargs: ())
    session._rivals = Rivals(tracker, population, session._native, lambda: "us")
    entered, release = threading.Event(), threading.Event()
    original = replay.preserve_native

    def preserve(number, **_kwargs):
        entered.set()
        assert release.wait(5), "test did not release preservation"
        return original(number) if outcome != "missing" else {"native_ghost": None}

    monkeypatch.setattr(replay, "preserve_native", preserve)

    async def scenario():
        await session.update({"enabled": True})
        pb_id = tracker.db.insert_pb(course_id=row.course_id, star_id=row.star_id,
            strat_tag="Standard", timer_mode="igt", frames=321, attempt_id=row.id,
            saved_utc=row.ended_utc, game_version="us")
        task = asyncio.create_task(session.pb_changed(row, True))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            early = await session.refresh(resolve=True)
            assert early["rivals"]["pb"]["available"] is False
            assert early["source"]["reason"] == "no_native_poses"
            if outcome == "superseded":
                tracker.db.delete_pb(pb_id)
            release.set()
            await task
            ready = session.payload()
            if outcome != "ready":
                assert ready["source"]["available"] is False
                if outcome == "superseded":
                    assert replay.find_native(row.id) is not None
                    assert ready["rivals"]["pb"] is None
                else:
                    assert replay.find_native(row.id) is None
                    assert ready["rivals"]["pb"]["available"] is False
                    assert ready["rivals"]["generation"] == early["rivals"]["generation"]
                return
            assert replay.find_native(row.id) is not None
            await assert_ready_playback(session, row)
        finally:
            release.set()
            await task
            await session.stop()

    try:
        asyncio.run(scenario())
    finally:
        archive.close()
