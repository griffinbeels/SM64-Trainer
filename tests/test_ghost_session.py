"""Ghost preferences and target lifecycle over real storage and native selection."""
import asyncio
from types import SimpleNamespace

import pytest

from sm64_events.ghost import settings
from sm64_events.ghost.native_session import NativeGhost
from sm64_events.ghost.session import GhostSession
from sm64_events.inputs.templates import TemplateStore
from sm64_events.storage.db import Database
from tests.ghost_fixture import FakeReplay, a_document


class FakeTracker:
    def __init__(self, db=None, target=None, strat_tag=None):
        self.db, self.target, self.strat_tag = db, target, strat_tag


class FakeBroadcaster:
    def __init__(self):
        self.events = []

    async def publish(self, event):
        self.events.append(event)
        return len(self.events)


def a_store(tmp_path, name="t.db"):
    db = Database(tmp_path / name)
    return TemplateStore(db._conn, db._lock)


def mark(store, *, kind="star", entity_key="24-1", strat="10 coin", attempt_id=7):
    return store.save(kind=kind, entity_key=entity_key, strat_tag=strat,
                      name="the good one", origin=f"attempt:{attempt_id}",
                      document=a_document())


def a_session(tmp_path, *, target=("star", 24, 1), strat="10 coin",
              saved=(7,), db=None, store=None, replay=None):
    store = a_store(tmp_path) if store is None else store
    replay = FakeReplay(saved=saved) if replay is None else replay
    tracker = FakeTracker(db=db, target=target, strat_tag=strat)
    native = NativeGhost(replay)
    session = GhostSession(tracker=tracker, inputs=SimpleNamespace(templates=store),
                           replay=replay, broadcaster=FakeBroadcaster(), native=native)
    return session, native, tracker, store


def drive(session, *, resolve=True):
    return asyncio.run(session.refresh(resolve=resolve))


def test_a_session_with_no_target_says_why(tmp_path):
    session, _, _, _ = a_session(tmp_path, target=None)
    body = drive(session)
    assert body["source"]["reason"] == "no_target"
    assert body["target"] is None and not body["window"]["visible"]


def test_a_recorded_template_resolves_to_native_poses(tmp_path):
    session, _, _, store = a_session(tmp_path)
    marked = mark(store)
    body = drive(session)
    assert body["source"]["available"]
    assert body["source"]["template_id"] == marked.id
    assert body["source"]["attempt_id"] == 7
    assert body["source"]["exactness"] == "native_pose"
    assert not ({"view", "whole_available", "mario_boxes_available"} & set(body))
    assert not ({"view", "whole_available", "mario_boxes_available"} & set(body["source"]))


def test_missing_saved_poses_carry_a_readable_reason(tmp_path):
    session, _, _, store = a_session(tmp_path, saved=())
    mark(store)
    body = drive(session)
    assert not body["source"]["available"] and body["source"]["reason_text"]


def test_preferences_survive_restart_and_nudge_resets_on_target_change(tmp_path):
    db = Database(tmp_path / "preferences.db")
    session, _, tracker, store = a_session(tmp_path, db=db)

    async def check():
        await session.update(enabled=False, opacity=0.25,
                             tint={"enabled": False, "hue_degrees": 210}, nudge_frames=12)
        restored, _, _, _ = a_session(tmp_path, db=db, store=store)
        body = await restored.refresh()
        assert body["enabled"] is False and body["opacity"] == 0.25
        assert body["tint"] == {"enabled": False, "hue_degrees": 210}
        assert body["nudge_frames"] == 0
        tracker.target = ("segment", 31)
        tracker.strat_tag = None
        body = await session.refresh()
        assert body["target"] == {"kind": "segment", "entity_key": "31", "strat": None}
        assert body["nudge_frames"] == 0
        await session.stop()
        await restored.stop()
    asyncio.run(check())


def test_legacy_whole_view_is_ignored_without_rewriting_other_preferences(tmp_path):
    db = Database(tmp_path / "preferences.db")
    db.set_state("ghost_view", "whole")
    db.set_state("ghost_opacity", 0.25)
    session, _, _, _ = a_session(tmp_path, db=db)
    body = drive(session)
    assert "view" not in body and body["opacity"] == 0.25
    assert db.get_state("ghost_view") == "whole"


@pytest.mark.parametrize("changes", [
    {"opacity": 4}, {"enabled": "on"}, {"view": "whole"},
    {"tint": {"hue_degrees": 400}}, {"nudge_frames": 10_000},
])
def test_invalid_updates_are_atomic(tmp_path, changes):
    session, _, _, _ = a_session(tmp_path)
    before = drive(session)
    with pytest.raises(ValueError):
        asyncio.run(session.update({"opacity": 0.25, **changes}))
    assert session.payload() == before


def test_only_changed_state_is_broadcast_and_never_journaled(tmp_path):
    session, _, _, _ = a_session(tmp_path)
    drive(session)
    first = len(session._broadcaster.events)
    drive(session)
    assert len(session._broadcaster.events) == first
    asyncio.run(session.update(opacity=0.25))
    event = session._broadcaster.events[-1]
    assert event.type == "ghost_changed" and event.frame == 0
    assert event.payload["opacity"] == 0.25


def test_watch_and_native_command_stop_with_session(tmp_path):
    session, native, _, _ = a_session(tmp_path)
    stops = []
    native.stop = lambda: stops.append(True)

    async def check():
        await session.start()
        assert session._task is not None
        await session.stop()
        assert session._task is None and stops == [True]
    asyncio.run(check())


def test_server_lifespan_starts_and_stops_its_ghost_session():
    from fastapi.testclient import TestClient

    from sm64_events.detectors.star_grab import StarGrabDetector
    from sm64_events.server.app import create_app
    from sm64_events.server.broadcaster import Broadcaster
    from sm64_events.server.poller import Poller

    class Recording:
        def __init__(self):
            self.calls = []

        async def start(self):
            self.calls.append("start")

        async def stop(self):
            self.calls.append("stop")

    class Offline:
        attached = False

        def attach(self):
            return False

        def detach(self):
            pass

    session = Recording()
    broadcaster = Broadcaster()
    poller = Poller(Offline(), [StarGrabDetector()], broadcaster)
    with TestClient(create_app(poller, broadcaster, ghost=session)):
        assert session.calls == ["start"]
    assert session.calls == ["start", "stop"]


def test_watch_recovers_after_a_failing_cycle_and_follows_the_target(tmp_path):
    session, _, tracker, store = a_session(tmp_path)
    session._period_s = 0.01
    mark(store)

    async def check():
        failed = asyncio.Event()

        class Exploding:
            db = None
            strat_tag = None

            @property
            def target(self):
                failed.set()
                raise RuntimeError("the projector is mid-recovery")

        await session.start()
        try:
            session._tracker = Exploding()
            await asyncio.wait_for(failed.wait(), 1)
            assert not session._task.done()
            tracker.target, tracker.strat_tag = ("segment", 31), None
            session._tracker = tracker

            async def wait_for_target():
                while session.payload()["target"]["kind"] != "segment":
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(wait_for_target(), 1)
            assert session.payload()["target"]["entity_key"] == "31"
        finally:
            session._tracker = tracker
            await session.stop()
        assert asyncio.all_tasks() == {asyncio.current_task()}

    asyncio.run(check())


def test_strategy_change_alone_resets_the_nudge(tmp_path):
    session, _, tracker, store = a_session(tmp_path)
    mark(store)
    asyncio.run(session.update(nudge_frames=9))
    tracker.strat_tag = "backflip"
    body = drive(session)
    assert body["target"]["strat"] == "backflip" and body["nudge_frames"] == 0


def test_malformed_stored_preferences_never_block_native_selection(tmp_path):
    db = Database(tmp_path / "malformed.db")
    for key, value in {
        "ghost_enabled": "sure", "ghost_opacity": 12,
        "ghost_tint_enabled": "sure", "ghost_tint_hue": "blue",
        "ghost_path_tuning": {"width": "wide"}, "ghost_path_source": 7,
    }.items():
        db.set_state(key, value)
    session, _, _, store = a_session(tmp_path, db=db)
    mark(store)
    body = drive(session)
    assert body["enabled"] is True and 0 <= body["opacity"] <= 1
    assert body["tint"] == settings.defaults()["tint"]
    assert body["path_tuning"] == settings.defaults()["path_tuning"]
    assert body["path_source"] == settings.defaults()["path_source"]
    assert body["source"]["available"]
