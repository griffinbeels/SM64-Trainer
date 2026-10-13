"""A concurrent PB read cannot cache a refusal after its real poses commit."""
import asyncio
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from sm64_events.ghost.rivals import Rivals
from tests.test_ghost_adaptive import Standards
from tests.test_ghost_pb import pb_world


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
