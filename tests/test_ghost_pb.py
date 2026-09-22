"""A validated PB is the ghost he races next, the moment he saves it."""
import asyncio
import time
from types import SimpleNamespace

from sm64_events.ghost.native_session import NativeGhost
from sm64_events.ghost.pb import current_pb_ghost
from sm64_events.ghost.session import GhostSession
from sm64_events.storage.db import Database
from tests.test_ghost_native_session import Capture
from tests.test_ghost_session import FakeBroadcaster, FakeTracker, a_store, mark

ORIGIN = {"kind": "star", "session_id": 1, "arm_id": 9, "frame": 100,
          "utc": "2026-09-21T08:00:00+00:00"}


def save_pb(db, row, mode="igt", strat=None):
    return db.insert_pb(course_id=row.course_id, star_id=row.star_id, strat_tag=strat,
                        timer_mode=mode, frames=300, attempt_id=row.id,
                        saved_utc="2026-09-22T08:00:00Z")


def pb_world(tmp_path):
    """Real pose archive, replay service, pbs table, template store and native
    selection; only the renderer's shared memory is a stand-in."""
    from test_ghost_recording import native_service

    replay, row, archive = native_service(tmp_path)
    capture = Capture()
    replay.recorder.ghost_capture = capture
    db = Database(tmp_path / "tracker.db")
    store = a_store(tmp_path)
    tracker = FakeTracker(db=db, target=("star", row.course_id, row.star_id), strat_tag=row.strat_tag)
    session = GhostSession(tracker=tracker, inputs=SimpleNamespace(templates=store), replay=replay,
                           broadcaster=FakeBroadcaster(), controller_factory=lambda **_: None,
                           native=NativeGhost(replay, origin_of=lambda *_: dict(ORIGIN)))
    return session, replay, row, archive, db, store, capture


def test_latest_visible_save_is_the_pb_ghost_across_clocks(tmp_path):
    db = Database(tmp_path / "pbs.db")
    row = SimpleNamespace(id=5, course_id=24, star_id=1)
    assert current_pb_ghost(db, "star", "24-1", None) is None
    save_pb(db, row, "igt")
    newer = save_pb(db, SimpleNamespace(id=6, course_id=24, star_id=1), "rta")
    assert current_pb_ghost(db, "star", "24-1", None).attempt_id == 6
    db.delete_pb(newer)
    pb = current_pb_ghost(db, "star", "24-1", None)
    assert (pb.attempt_id, pb.timer_mode, pb.id, pb.origin) == (5, "igt", -5, "attempt:5")
    # Another strategy's PB is not this strategy's ghost.
    save_pb(db, SimpleNamespace(id=7, course_id=24, star_id=1), strat="TJ Owlless")
    assert current_pb_ghost(db, "star", "24-1", "TJ Owlless").attempt_id == 7
    assert current_pb_ghost(db, "star", "24-1", "Standard") is None
    db.insert_pb(course_id=None, star_id=None, strat_tag=None, timer_mode="rta", frames=90,
                 attempt_id=8, saved_utc="2026-09-22T08:00:00Z", segment_id=3)
    assert current_pb_ghost(db, "segment", "3", None).attempt_id == 8


def test_saving_a_pb_makes_it_the_playing_ghost_immediately(tmp_path):
    session, _replay, row, archive, db, store, capture = pb_world(tmp_path)

    async def check():
        await session.update({"enabled": True, "view": "mario"})
        assert not session.payload()["source"]["available"]
        assert not any(command.get("enabled") for command in capture.commands)
        save_pb(db, row)
        started = time.perf_counter()
        await session.pb_changed(row, True)
        session._native.note()
        elapsed = time.perf_counter() - started
        body = session.payload()
        assert body["source"]["available"] and body["source"]["attempt_id"] == row.id
        assert body["source"]["exactness"] == "native_pose"
        assert body["selection"]["pb"]["attempt_id"] == row.id
        assert body["selection"]["pb"]["native_available"]
        command = capture.commands[-1]
        assert command["enabled"] and command["clip_path"].endswith(f"attempt_{row.id}.ghost")
        # Everything between his click and the renderer's command, measured.
        assert elapsed < 1.0, elapsed
        await session.stop()

    try:
        asyncio.run(check())
    finally:
        archive.close()


def test_undoing_the_pb_removes_it_as_the_ghost(tmp_path):
    session, _replay, row, archive, db, store, capture = pb_world(tmp_path)

    async def check():
        await session.update({"enabled": True, "view": "mario"})
        pb = save_pb(db, row)
        await session.pb_changed(row, True)
        assert session.payload()["source"]["available"]
        db.delete_pb(pb)
        await session.pb_changed(row, False)
        session._native.note()
        body = session.payload()
        assert not body["source"]["available"] and body["selection"]["pb"] is None
        assert not capture.commands[-1]["enabled"]
        await session.stop()

    try:
        asyncio.run(check())
    finally:
        archive.close()


def test_a_new_pb_replaces_an_explicit_pick_but_a_later_pick_still_wins(tmp_path):
    session, _replay, row, archive, db, store, _capture = pb_world(tmp_path)
    target_key = f"{row.course_id}-{row.star_id}"
    picked = mark(store, entity_key=target_key, strat=row.strat_tag, attempt_id=999)

    async def check():
        target = session.payload()["target"] or (await session.refresh())["target"]
        await session.update({"template_id": picked.id, "target": target})
        save_pb(db, row)
        await session.pb_changed(row, True)
        body = session.payload()
        assert body["selection"]["template_id"] is None
        assert body["source"]["attempt_id"] == row.id
        assert db.get_state("ghost_templates", {}) == {}
        body = await session.update({"template_id": picked.id, "target": target})
        assert body["source"]["attempt_id"] == 999
        await session.stop()

    try:
        asyncio.run(check())
    finally:
        archive.close()


def test_without_a_pb_the_input_template_still_plays(tmp_path):
    session, _replay, row, archive, db, store, _capture = pb_world(tmp_path)
    mark(store, entity_key=f"{row.course_id}-{row.star_id}", strat=row.strat_tag, attempt_id=row.id)
    try:
        body = asyncio.run(session.refresh())
        assert body["selection"]["pb"] is None and body["source"]["attempt_id"] == row.id
        asyncio.run(session.stop())
    finally:
        archive.close()
