"""A validated PB is the ghost he races next, the moment he saves it."""
import asyncio
import time
from types import SimpleNamespace

from sm64_events.ghost.native_session import NativeGhost
from sm64_events.ghost.pb import current_pb_ghost
from sm64_events.ghost.session import GhostSession
from sm64_events.storage.db import Database
from tests.test_ghost_native_session import Capture
from tests.test_ghost_native_channel import kernel  # noqa: F401 - the real encoder's fixture
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
    assert (pb.attempt_id, pb.frames, pb.id, pb.origin, pb.pb) == (5, 300, -5, "attempt:5", True)
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
        assert command["enabled"] and [clip["clip_path"][-len(f"attempt_{row.id}.ghost"):] for clip in command["clips"]] == [f"attempt_{row.id}.ghost"]
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


def test_pb_plays_beside_ticked_recordings_and_unticking_it_ignores_future_pbs(tmp_path):
    from dataclasses import replace

    session, replay, row, archive, db, store, capture = pb_world(tmp_path)
    other = replace(row, id=row.id + 1)
    replay.tracker.db._attempts.append(other)
    replay.preserve_native(other.id)
    mark(store, entity_key=f"{row.course_id}-{row.star_id}", strat=row.strat_tag,
                  attempt_id=other.id)

    def playing():
        session._native.note()
        return [clip["clip_path"].rsplit("attempt_", 1)[1] for clip in capture.commands[-1].get("clips", [])]

    async def check():
        target = (await session.update({"enabled": True, "view": "mario"}))["target"]
        await session.update({"ghosts": {"pb": True, "attempts": [other.id]}, "target": target})
        assert playing() == [f"{other.id}.ghost"]
        save_pb(db, row)
        await session.pb_changed(row, True)
        assert playing() == [f"{row.id}.ghost", f"{other.id}.ghost"]  # PB first, pick kept
        hues = [clip["hue_degrees"] for clip in capture.commands[-1]["clips"]]
        assert len(set(hues)) == 2
        body = await session.update({"ghosts": {"pb": False, "attempts": [other.id]}, "target": target})
        assert body["selection"]["ghosts"] == {"pb": False, "attempts": [other.id]}
        assert playing() == [f"{other.id}.ghost"]
        newer = replace(row, id=row.id + 2)
        replay.tracker.db._attempts.append(newer)
        save_pb(db, newer)
        await session.pb_changed(newer, True)
        assert playing() == [f"{other.id}.ghost"]  # unticked: a new PB does not appear
        await session.update({"ghosts": {"pb": False, "attempts": []}, "target": target})
        assert not capture.commands[-1]["enabled"]
        body = await session.update({"ghosts": {"pb": True, "attempts": []}, "target": target})
        assert body["selection"]["pb"]["attempt_id"] == newer.id
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


def test_the_sessions_real_command_encodes_on_the_real_channel(tmp_path, kernel):
    """The live 2026-09-22 failure: every session test used a stand-in channel
    that accepted any command, so a shape the real channel rejected shipped.
    Here the command the session actually built goes through the real encoder."""
    from dataclasses import replace

    from sm64_events.ghost import native_channel as channel
    from sm64_events.memory.ghost_layout import GhostLayout
    from sm64_events.memory.layout import US

    session, replay, row, archive, db, store, capture = pb_world(tmp_path)
    other = replace(row, id=row.id + 1)
    replay.tracker.db._attempts.append(other)
    replay.preserve_native(other.id)
    mark(store, entity_key=f"{row.course_id}-{row.star_id}", strat=row.strat_tag,
                  attempt_id=other.id)

    async def check():
        target = (await session.update({"enabled": True, "view": "mario"}))["target"]
        save_pb(db, row)
        await session.pb_changed(row, True)
        await session.update({"ghosts": {"pb": True, "attempts": [other.id]}, "target": target})
        session._native.note()
        await session.stop()
        return capture.commands[-2]  # the last enabled command, before stop()

    try:
        command = asyncio.run(check())
    finally:
        archive.close()
    assert command["enabled"] and len(command["clips"]) == 2
    connection = channel.NativeChannel(123, GhostLayout.from_layout(US).to_bytes())
    try:
        connection.poll(command)
        encoded = kernel.request
        assert encoded.play_enabled and encoded.clip_count == 2
        assert [bytes(encoded.clips[i].clip_id) for i in range(2)] == [
            clip["clip_id"] for clip in command["clips"]]
        assert encoded.clips[0].hue_degrees != encoded.clips[1].hue_degrees
    finally:
        connection.close()


def test_saving_any_run_as_a_ghost_ticks_it_without_touching_templates(tmp_path):
    from dataclasses import replace

    session, replay, row, archive, db, store, capture = pb_world(tmp_path)
    other = replace(row, id=row.id + 1)
    replay.tracker.db._attempts.append(other)

    async def check():
        await session.update({"enabled": True, "view": "mario"})
        answer = await session.save_ghost(other.id)  # poses kept here, not earlier
        assert answer["saved"] and answer["poses_reason"] is None
        body = answer["session"]
        assert body["selection"]["ghosts"] == {"pb": True, "attempts": [other.id]}
        assert [row["attempt_id"] for row in body["selection"]["candidates"]] == [other.id]
        assert body["selection"]["candidates"][0]["frames"] == other.igt_frames
        assert session.is_saved_ghost(other.id) and store.list_for("star", "2-2") == []
        session._native.note()
        assert [clip["clip_path"].endswith(f"attempt_{other.id}.ghost") for clip in capture.commands[-1]["clips"]] == [True]
        body = (await session.remove_ghost(other.id))["session"]
        assert body["selection"]["ghosts"]["attempts"] == [] and body["selection"]["candidates"] == []
        assert not session.is_saved_ghost(other.id)
        await session.stop()

    try:
        asyncio.run(check())
    finally:
        archive.close()


def test_a_ticked_template_from_the_first_build_becomes_its_run(tmp_path):
    session, _replay, row, archive, db, store, _capture = pb_world(tmp_path)
    template = mark(store, entity_key=f"{row.course_id}-{row.star_id}", strat=row.strat_tag, attempt_id=row.id)
    target = {"kind": "star", "entity_key": f"{row.course_id}-{row.star_id}", "strat": row.strat_tag}
    from sm64_events.ghost.selection import SELECTION_KEY, target_key
    db.set_state(SELECTION_KEY, {target_key(target): {"pb": False, "templates": [template.id]}})
    try:
        body = asyncio.run(session.refresh())
        assert body["selection"]["ghosts"] == {"pb": False, "attempts": [row.id]}
        assert body["source"]["attempt_id"] == row.id
        asyncio.run(session.stop())
    finally:
        archive.close()


def test_the_saved_ghost_doors(tmp_path):
    from dataclasses import replace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from sm64_events.server.ghost_api import create_ghost_router

    session, replay, row, archive, db, store, _capture = pb_world(tmp_path)
    other = replace(row, id=row.id + 1)
    replay.tracker.db._attempts.append(other)
    app = FastAPI()
    app.include_router(create_ghost_router(session._inputs, replay, session))
    try:
        with TestClient(app) as client:
            assert client.get(f"/api/saved-ghosts/{other.id}").json()["saved"] is False
            assert client.post(f"/api/saved-ghosts/{other.id}").json()["saved"] is True
            assert client.get(f"/api/saved-ghosts/{other.id}").json()["saved"] is True
            assert client.post("/api/saved-ghosts/999999").status_code == 404
            assert client.delete(f"/api/saved-ghosts/{other.id}").json()["saved"] is False
        asyncio.run(session.stop())
    finally:
        archive.close()


def test_a_recording_with_missing_positions_is_counted_and_said_out_loud(tmp_path, caplog):
    from ghost_native_fixture import frame
    from test_ghost_recording import native_service

    replay, row, archive = native_service(tmp_path, rows=(frame(1), frame(2, valid=29), frame(3)))
    try:
        with caplog.at_level("WARNING", logger="sm64.replay"):
            manifest = replay.preserve_native(row.id)["native_ghost"]
        assert manifest["invalid_records"] == 1
        assert "Mario position missing on 1 of 3 frames" in caplog.text
        target = {"kind": "star", "entity_key": f"{row.course_id}-{row.star_id}", "strat": row.strat_tag}
        from sm64_events.ghost.pb import AttemptGhost
        answer = NativeGhost(replay).probe(target, AttemptGhost(row.id, "star", target["entity_key"], None))
        assert answer["available"] and answer["pose_gaps"] == 1
    finally:
        archive.close()
