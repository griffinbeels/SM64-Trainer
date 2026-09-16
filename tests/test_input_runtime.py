"""Late database attachment restores capture, routes and replay audit together."""
import asyncio
from contextlib import closing
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from sm64_events.memory.layout import US
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.server.inputruntime import InputRuntime
from sm64_events.server.inputs_api import create_inputs_router
from sm64_events.server.poller import Poller
from sm64_events.storage.db import Database
from sm64_events.tracking.service import TrackerService
from test_app import OfflineMemory
from test_inputs_api import FakeAttempt
from test_inputs_sampler import ScriptedMemory
from test_poller import ScriptedReader


def test_dbless_inputs_are_503_then_same_routes_capture_and_read_back(tmp_path):
    tracker = TrackerService(None, Broadcaster())
    memory = ScriptedMemory([(100, 0x8000, 0, 84, 0), (101, 0, 0, 0, 0)])
    activity = []
    replay = SimpleNamespace(recorder=SimpleNamespace(set_player_active=activity.append))
    runtime = InputRuntime(tracker, memory, US, replay)
    poller = Poller(OfflineMemory(), [], tracker)
    runtime.bind(poller)
    app = FastAPI()
    app.include_router(create_inputs_router(runtime.get_service))
    with closing(Database(tmp_path / "late.db")) as db, TestClient(app) as client:
        assert client.get("/api/inputs/templates").status_code == 503
        assert client.get("/api/attempts/7/inputs").status_code == 503
        assert poller.input_sampler is None
        asyncio.run(tracker.attach_db(db))
        runtime.attach(db)
        assert poller.input_sampler is runtime.sampler
        assert poller.interval == 1 / Poller.SAMPLING_HZ
        before = datetime.now(timezone.utc).isoformat()
        runtime.sampler.sample()
        runtime.sampler.sample()
        runtime.close()
        after = datetime.now(timezone.utc).isoformat()
        attempt = FakeAttempt(7, started_utc=before, ended_utc=after)
        # Use the route's bound store and actual encoded chunks; only the
        # independent attempt fixture substitutes for a game star completion.
        runtime.inputs._attempts = lambda: [attempt]
        assert client.get("/api/inputs/templates").status_code == 200
        assert replay.track_pads(attempt) == {100: (84, 0, 32768), 101: (0, 0, 0)}
        chunks = db.inputs.chunks_between(before, after)
        assert {chunk.session_id for chunk in chunks} == {tracker.session_id}
        original = runtime.sampler
        runtime.attach(db)
        assert runtime.sampler is original  # no source churn on a repeated callback


def test_database_retry_survives_transient_open_failure_and_wires_once(tmp_path, monkeypatch):
    import sm64_events.server.app as app_module
    monkeypatch.setattr(app_module, "_DB_RETRY_INTERVAL_S", 0.001)
    tracker = TrackerService(None, Broadcaster())
    calls, attached = [], []
    db = Database(tmp_path / "retry.db")

    def retry():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("temporary file access")
        return db

    try:
        asyncio.run(app_module._db_reattach_loop(tracker, retry, attached.append))
        assert len(calls) == 2
        assert attached == [db]
        assert tracker.session_id is not None
    finally:
        db.close()


def test_late_attach_waits_for_inflight_pre_session_event(tmp_path, monkeypatch):
    from sm64_events.detectors.level import LevelChangeDetector
    import sm64_events.server.app as app_module
    from test_star_grab import snap
    monkeypatch.setattr(app_module, "_DB_RETRY_INTERVAL_S", 0)

    async def exercise(db):
        entered, release, opened = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class GatedBroadcaster(Broadcaster):
            async def publish(self, event):
                if event.type == "level_changed":
                    entered.set()
                    await release.wait()
                return await super().publish(event)

        tracker = TrackerService(None, GatedBroadcaster())
        poller = Poller(OfflineMemory(), [LevelChangeDetector()], tracker,
                        detector_factory=lambda: [LevelChangeDetector()],
                        reader=ScriptedReader([snap(global_timer=100), snap(global_timer=101)]))
        await poller.tick()
        publishing = asyncio.create_task(poller.tick())
        await entered.wait()

        def retry():
            opened.set()
            return db

        attaching = asyncio.create_task(app_module._db_reattach_loop(tracker, retry, poller=poller))
        await opened.wait()
        assert tracker.session_id is None  # the publishing tick still owns its old context
        release.set()
        await publishing
        await attaching
        assert not [row for row in db.events() if row.type == "level_changed"]
        assert poller.latest is None
        assert tracker.session_id is not None

    with closing(Database(tmp_path / "race.db")) as db:
        asyncio.run(exercise(db))


def test_failed_attach_after_session_allocation_is_retried(tmp_path, monkeypatch):
    import sm64_events.server.app as app_module
    monkeypatch.setattr(app_module, "_DB_RETRY_INTERVAL_S", 0.001)
    with closing(Database(tmp_path / "partial.db")) as db:
        tracker = TrackerService(None, Broadcaster())
        original = tracker.start
        calls = []

        async def start():
            calls.append(1)
            if len(calls) == 1:
                tracker.session_id = db.insert_session(datetime.now(timezone.utc).isoformat())
                raise OSError("start failed after allocating session")
            await original()

        tracker.start = start
        asyncio.run(app_module._db_reattach_loop(tracker, lambda: db))
        assert len(calls) == 2
        assert tracker.session_id is not None
