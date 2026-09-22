"""Reach the native renderer through the same ghost switch as video playback."""
import asyncio
import threading
from types import SimpleNamespace

import pytest

from sm64_events.ghost.native_session import NativeGhost
from tests.test_ghost_session import a_session, mark


class Capture:
    def __init__(self):
        self.commands = []
        self.displayed = False

    def set_playback(self, *, blocking=True, **command):
        self.commands.append(command)
        return True

    def snapshot_status(self, *, blocking=True):
        return {"available": True, "state": 4, "playback_reason": 2,
                "producer": SimpleNamespace(configuration=2, epoch=3),
                "displayed": self.displayed}


def native_session(tmp_path, *, asset=True):
    session, windows, tracker, store = a_session(tmp_path)
    source = Capture()
    manifest = {"clip_id": "ab" * 16, "sha256": "cd" * 32}
    replay = SimpleNamespace(recorder=SimpleNamespace(ghost_capture=source),
                             native_identity=lambda _: {"native_clock_frame": 100},
                             find_native=lambda _: (tmp_path / "run.ghost", manifest) if asset else None)
    session._native = NativeGhost(replay, origin_of=lambda *_: {
        "kind": "star", "session_id": 1, "arm_id": 9,
        "frame": 100, "utc": "2026-09-21T08:00:00+00:00"})
    mark(store)
    return session, source, windows, tracker


def test_native_switch_uses_pose_without_video_window_and_preserves_controls(tmp_path):
    session, source, windows, _ = native_session(tmp_path)

    async def check():
        await session.update({"enabled": True, "view": "mario", "opacity": 1,
                           "tint": {"enabled": True, "hue_degrees": 120}, "nudge_frames": 9})
        session._native.note()
        assert not windows
        assert session.payload()["source"]["exactness"] == "native_pose"
        command = source.commands[-1]
        assert command["enabled"] and command["opacity"] == 1 and command["nudge"] == 9
        assert command["tint_enabled"] and command["hue_degrees"] == 120
        assert command["proximity_enabled"] and command["proximity_distance"] == 0
        assert command["stop_at_star"] and command["rom_path"] == ""
        assert command["origin"]["global_timer"] == 100
        assert command["origin"]["configuration"] == 2 and command["origin"]["epoch"] == 3
        count = len(source.commands)
        await session.refresh(resolve=False)
        assert len(source.commands) == count
        await session.update({"proximity": {"extra_distance": 120}})
        assert source.commands[-1]["proximity_distance"] == 120
        await session.update({"proximity": {"enabled": False}})
        assert not source.commands[-1]["proximity_enabled"]
        assert source.commands[-1]["proximity_distance"] == 120
        await session.update({"view": "whole"})
        assert not source.commands[-1]["enabled"] and windows[-1].visible
        await session.stop()

    asyncio.run(check())


def test_missing_pose_never_opens_whole_picture_as_mario(tmp_path):
    session, source, windows, _ = native_session(tmp_path, asset=False)

    async def check():
        await session.update({"enabled": True, "view": "mario"})
        assert not session.payload()["source"]["available"]
        assert session.payload()["source"]["reason"] == "no_native_poses"
        assert not windows and not source.commands[-1]["enabled"]
        await session.stop()

    asyncio.run(check())


def test_pose_ready_is_not_display_evidence(tmp_path):
    session, source, _, _ = native_session(tmp_path)

    async def check():
        await session.update({"enabled": True, "view": "mario"})
        session._native.note()
        assert session._native.state()["running"]
        assert not session._native.state()["visible"]
        source.displayed = True
        assert session._native.state()["visible"]
        source.displayed = False
        assert not session._native.state()["visible"]
        await session.stop()

    asyncio.run(check())


def test_poll_markers_rebase_and_disarm_without_frame_pacing_or_file_reads(tmp_path):
    session, source, _, _ = native_session(tmp_path)
    origin = {"kind": "segment", "session_id": 1, "arm_id": 99,
              "frame": 120, "utc": "2026-09-21T08:00:00+00:00"}
    session._native._origin_of = lambda *_: origin

    async def check():
        nonlocal origin
        await session.update({"enabled": True, "view": "mario"})
        assert not source.commands[-1]["enabled"]
        assert session._native.state()["reason"] == "Waiting for the next attempt."
        session._native.note()
        first = source.commands[-1]
        assert first["origin"]["kind"] == 2 and first["origin"]["global_timer"] == 120
        count = len(source.commands)
        for _ in range(90):
            session._native.note()
        assert len(source.commands) == count
        origin = {**origin, "frame": 145, "utc": "2026-09-21T08:00:00.8+00:00"}
        session._native.note()
        assert source.commands[-1]["origin"]["global_timer"] == 145
        assert source.commands[-1]["origin"]["arm_id"] == 99
        origin = None
        session._native.note()
        assert source.commands[-1]["enabled"] and source.commands[-1]["owner_suspended"]
        assert source.commands[-1]["origin"]["arm_id"] == 99
        assert not session._native.state()["visible"]
        origin = {"kind": "segment", "session_id": 1, "arm_id": 100,
                  "frame": 130, "utc": "2026-09-21T08:00:02+00:00"}
        session._native.note()
        assert source.commands[-1]["enabled"] and not source.commands[-1]["owner_suspended"]
        assert source.commands[-1]["origin"]["arm_id"] == 100
        await session.stop()
        session._native.note()
        assert source.commands[-1] == {"enabled": False}

    asyncio.run(check())


def test_rom_reopen_keeps_binding_until_new_producer_identity_arrives(tmp_path):
    session, source, _, _ = native_session(tmp_path)
    original_status = source.snapshot_status

    async def check():
        await session.update({"enabled": True, "view": "mario"})
        session._native.note()
        before = len(source.commands)
        source.snapshot_status = lambda **_: {"available": False}
        session._native.note()
        assert len(source.commands) == before and source.commands[-1]["enabled"]
        source.snapshot_status = lambda **_: {**original_status(),
            "producer": SimpleNamespace(configuration=5, epoch=8)}
        session._native.note()
        assert source.commands[-1]["origin"]["configuration"] == 5
        assert source.commands[-1]["origin"]["epoch"] == 8
        source.snapshot_status = lambda **_: {"available": False}
        await session.update({"enabled": False})
        assert source.commands[-1] == {"enabled": False}
        await session.stop()

    asyncio.run(check())


def test_target_change_revokes_old_pose_and_resets_nudge(tmp_path):
    session, source, windows, tracker = native_session(tmp_path)

    async def check():
        await session.update({"enabled": True, "view": "mario", "nudge_frames": 8})
        tracker.target = ("segment", 55)
        await session.refresh()
        assert not source.commands[-1]["enabled"]
        assert session.payload()["nudge_frames"] == 0 and not windows
        await session.stop()

    asyncio.run(check())


def test_stop_joins_uncancelled_selection_before_final_revoke(tmp_path):
    session, source, _, _ = native_session(tmp_path)
    entered, release = threading.Event(), threading.Event()
    resolve = session._native.resolve

    def blocked(*args):
        answer = resolve(*args)
        entered.set()
        assert release.wait(5)
        return answer

    session._native.resolve = blocked

    async def check():
        selection = asyncio.create_task(session.update({"enabled": True, "view": "mario"}))
        assert await asyncio.to_thread(entered.wait, 3)
        stopping = asyncio.create_task(session.stop())
        try:
            await asyncio.sleep(0.03)
            assert not stopping.done()
        finally:
            release.set()
        await selection
        await stopping
        assert not source.commands[-1]["enabled"]
        count = len(source.commands)
        await session.refresh()
        assert len(source.commands) == count

    asyncio.run(check())


def test_unchanged_clip_is_validated_once_until_asset_changes(tmp_path):
    session, _, _, _ = native_session(tmp_path)
    path = tmp_path / "run.ghost"
    path.write_bytes(b"asset")
    path.with_suffix(".json").write_text("{}")
    calls = []
    find = session._native._replay.find_native
    session._native._replay.find_native = lambda number: calls.append(number) or find(number)

    async def check():
        await session.refresh()
        await session.refresh()
        assert calls == [7]
        path.write_bytes(b"changed asset")
        await session.refresh()
        assert calls == [7, 7]
        await session.stop()

    asyncio.run(check())


def test_reprojected_attempt_invalidates_unchanged_cached_asset(tmp_path):
    session, _, _, _ = native_session(tmp_path)
    path = tmp_path / "run.ghost"
    path.write_bytes(b"asset")
    path.with_suffix(".json").write_text("{}")
    calls = []
    replay = session._native._replay
    find = replay.find_native
    replay.find_native = lambda number: calls.append(number) or find(number)

    async def check():
        await session.update({"view": "mario"})
        await session.refresh()
        assert session._native._asset is not None
        replay.native_identity = lambda _: {"native_clock_frame": 145}
        replay.find_native = lambda number: calls.append(number)
        await session.refresh()
        assert calls == [7, 7] and session._native._asset is None
        assert not session.payload()["source"]["available"]
        await session.stop()

    asyncio.run(check())


def test_poll_tick_skips_capture_lock_and_retries_unpublished_command(tmp_path):
    from sm64_events.ghost.capture import NativeCapture
    from sm64_events.memory.layout import US

    session, fake, _, _ = native_session(tmp_path)
    source = NativeCapture(US, lambda *_: None)
    source._report(**fake.snapshot_status())
    session._native._replay.recorder.ghost_capture = source

    async def check():
        await session.update({"enabled": True, "view": "mario"})
        completed = threading.Event()
        thread = threading.Thread(target=lambda: (session._native.note(), completed.set()))
        source._lock.acquire()
        try:
            thread.start()
            assert completed.wait(1), "poll tick waited on the capture worker"
            assert source.set_playback(blocking=False, enabled=True) is False
            assert session._native._sent == {"enabled": False}
        finally:
            source._lock.release()
            thread.join(2)
        session._native.note()
        assert source._playback["enabled"] and session._native._sent == source._playback
        await session.stop()

    asyncio.run(check())


def refused_recording(tmp_path):
    from ghost_native_fixture import frame
    from test_ghost_recording import native_service
    from tests.test_ghost_session import a_store

    service, row, archive = native_service(tmp_path, rows=tuple(frame(n, valid=29) for n in (1, 2, 3)))
    store = a_store(tmp_path)
    target = {"kind": "star", "entity_key": f"{row.course_id}-{row.star_id}", "strat": row.strat_tag}
    return service, row, archive, store, target


def test_real_export_refusal_reaches_saved_template_without_retrying_archive(tmp_path, monkeypatch, caplog):
    from dataclasses import replace
    from sm64_events.inputs.frame import InputFrame
    from sm64_events.inputs.service import InputsService
    from sm64_events.storage.db import Database

    service, row, archive, _, target = refused_recording(tmp_path)
    db = Database(tmp_path / "inputs.sqlite")
    row = replace(row, session_id=db.insert_session(row.started_utc))
    service.tracker.db._attempts = [row]
    db.inputs.append(row.session_id, [(n, InputFrame(0, 0, 0, 0)) for n in (101, 102, 103)],
                     row.started_utc, row.ended_utc)
    results = []

    def preserve(attempt_id):
        result = service.preserve_native(attempt_id)
        results.append(result)
        return result

    store = db.input_templates
    inputs = InputsService(db.inputs, store, lambda: [row], preserve_native=preserve)
    try:
        saved = inputs.mark_template(row.id)
        assert len(results) == 1
        result = results[0]
        assert result["native_ghost_reason"] == "native ghost has no supported recorded poses"
        assert result["native_ghost_reason"] in caplog.text
        monkeypatch.setattr(archive, "export_attempt", lambda *a, **k: pytest.fail("resolve retried export"))
        ghost = NativeGhost(service)
        for _ in range(3):
            answer = ghost.resolve(target, store)
            assert not answer["available"] and answer["reason"] == "native_preservation_failed"
            assert answer["template_id"] == saved.id and answer["attempt_id"] == row.id
            assert answer["reason_text"] == (
                "The template was saved, but this recording has no Mario poses supported by the renderer.")
        assert store.active_for(target["kind"], target["entity_key"], row.strat_tag).id == saved.id
        assert not service.extractor.calls
        assert not list(service.cfg.save_root.rglob("*.ghost"))
    finally:
        archive.close()
        db.close()


@pytest.mark.parametrize("changes", [
    {"session_id": 99}, {"strat_tag": "reprojected"}, {"native_clock_frame": 102},
    {"native_clock_utc": "2027-01-15T08:00:01+00:00"}, {"native_clock_kind": "segment"},
    {"closed_frame": 104}, {"ended_utc": "2027-01-15T08:00:02+00:00"},
])
def test_preservation_refusal_does_not_survive_changed_attempt_identity(tmp_path, changes):
    from dataclasses import replace

    service, row, archive, _, _ = refused_recording(tmp_path)
    try:
        service.preserve_native(row.id)
        assert service.native_preservation_reason(row.id)
        service.tracker.db._attempts = [replace(row, **changes)]
        assert service.native_preservation_reason(row.id) is None
    finally:
        archive.close()


def test_successful_pose_commit_clears_the_prior_refusal(tmp_path):
    from ghost_native_fixture import frame, producer
    from sm64_events.ghost.archive import PoseArchive

    service, row, archive, store, target = refused_recording(tmp_path)
    good = PoseArchive(tmp_path / "supported.sqlite")
    try:
        service.preserve_native(row.id)
        mark(store, entity_key=target["entity_key"], strat=row.strat_tag, attempt_id=row.id)
        ghost = NativeGhost(service)
        assert ghost.resolve(target, store)["reason"] == "native_preservation_failed"
        good.ingest(producer(), tuple(frame(n) for n in (1, 2, 3)))
        service.recorder.pose_archive = good
        assert service.preserve_native(row.id)["native_ghost"] is not None
        assert service.native_preservation_reason(row.id) is None
        assert ghost.resolve(target, store)["available"]
        path, _ = service.find_native(row.id)
        path.with_suffix(".json").unlink()
        assert ghost.resolve(target, store)["reason"] == "no_native_poses"
    finally:
        archive.close()
        good.close()


def test_refusal_lookup_does_not_wait_for_an_in_progress_export(tmp_path, monkeypatch):
    service, row, archive, store, target = refused_recording(tmp_path)
    entered, release = threading.Event(), threading.Event()
    export, results = archive.export_attempt, []

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return export(*args, **kwargs)

    monkeypatch.setattr(archive, "export_attempt", blocked)
    mark(store, entity_key=target["entity_key"], strat=row.strat_tag, attempt_id=row.id)
    thread = threading.Thread(target=lambda: results.append(service.preserve_native(row.id)))
    try:
        thread.start()
        assert entered.wait(2)
        ghost = NativeGhost(service)
        assert ghost.resolve(target, store)["reason"] == "no_native_poses"
    finally:
        release.set()
        thread.join(5)
        archive.close()
    assert not thread.is_alive() and len(results) == 1
    assert results[0]["native_ghost_reason"] == "native ghost has no supported recorded poses"
    assert ghost.resolve(target, store)["reason"] == "native_preservation_failed"


def test_missing_origin_is_explained_and_refusal_history_is_bounded(tmp_path):
    from test_replay_service import attempt, make_service
    from tests.test_ghost_session import a_store

    rows = [attempt(id=number) for number in range(130)]
    service = make_service(tmp_path, rows)
    for row in rows:
        service.preserve_native(row.id)
    assert service.native_preservation_reason(rows[0].id) is None
    assert service.native_preservation_reason(rows[-1].id) == "This recording has no native Mario poses."
    store = a_store(tmp_path)
    mark(store, entity_key="2-2", strat=None, attempt_id=rows[-1].id)
    answer = NativeGhost(service).resolve({"kind": "star", "entity_key": "2-2"}, store)
    assert answer["reason"] == "native_preservation_failed"
    assert "Mario poses were not recorded" in answer["reason_text"]


def test_real_missing_origin_refusal_is_available_before_asset_identity(tmp_path):
    from dataclasses import replace

    service, row, archive, store, target = refused_recording(tmp_path)
    service.tracker.db._attempts = [replace(row, native_clock_frame=None)]
    try:
        result = service.preserve_native(row.id)
        assert result["native_ghost_reason"] == "attempt has no exact native frame endpoints or owner kind"
        mark(store, entity_key=target["entity_key"], strat=row.strat_tag, attempt_id=row.id)
        answer = NativeGhost(service).resolve(target, store)
        assert answer["reason"] == "native_preservation_failed"
        assert "exact starting or ending frame" in answer["reason_text"]
    finally:
        archive.close()


def test_stale_lookup_cannot_erase_new_projection_refusal(tmp_path, monkeypatch):
    from dataclasses import replace

    service, row, archive, _, _ = refused_recording(tmp_path)
    entered, release = threading.Event(), threading.Event()
    key, results = service._native_preservation_key, []

    def delayed(attempt):
        identity = key(attempt)
        if threading.current_thread() is query:
            entered.set()
            assert release.wait(5)
        return identity

    monkeypatch.setattr(service, "_native_preservation_key", delayed)
    query = threading.Thread(target=lambda: results.append(service.native_preservation_reason(row.id)))
    try:
        query.start()
        assert entered.wait(2)
        service.tracker.db._attempts = [replace(row, strat_tag="new projection")]
        refusal = service.preserve_native(row.id)["native_ghost_reason"]
    finally:
        release.set()
        query.join(5)
        archive.close()
    assert not query.is_alive() and results == [None]
    assert service.native_preservation_reason(row.id) == refusal


def test_older_pose_file_is_explained_kept_and_never_replaced(tmp_path):
    import json
    from ghost_native_fixture import frame, producer
    from sm64_events.ghost.archive import PoseArchive
    from sm64_events.ghost.clip import OLDER_POSE_FORMAT_TEXT
    from sm64_events.replay.service import saved_attempt_ids
    from test_ghost_recording import native_service
    from tests.test_ghost_session import a_store

    service, row, archive = native_service(tmp_path)
    fresh = PoseArchive(tmp_path / "fresh.sqlite")
    try:
        service.preserve_native(row.id)
        path, manifest = service.find_native(row.id)
        path.with_suffix(".json").write_text(json.dumps({**manifest, "version": 2}), encoding="utf-8")
        before = {item: item.read_bytes() for item in (path, path.with_suffix(".json"))}
        store = a_store(tmp_path)
        target = {"kind": "star", "entity_key": f"{row.course_id}-{row.star_id}", "strat": row.strat_tag}
        mark(store, entity_key=target["entity_key"], strat=row.strat_tag, attempt_id=row.id)
        answer = NativeGhost(service).resolve(target, store)
        assert not answer["available"]
        assert (answer["reason"], answer["reason_text"]) == ("older_pose_format", OLDER_POSE_FORMAT_TEXT)
        # A newer archive must not silently overwrite his committed older recording.
        fresh.ingest(producer(), tuple(frame(n) for n in (1, 2, 3)))
        service.recorder.pose_archive = fresh
        assert service.preserve_native(row.id) == {
            "path": None, "native_ghost": None, "native_ghost_reason": OLDER_POSE_FORMAT_TEXT}
        assert {item: item.read_bytes() for item in before} == before
        assert saved_attempt_ids(service.cfg.save_root) == {row.id}  # pruning keeps it
    finally:
        archive.close()
        fresh.close()


def test_playable_saved_copy_wins_over_an_older_permanent_file(tmp_path):
    import json
    from pathlib import Path
    from test_ghost_recording import native_service

    service, row, archive = native_service(tmp_path)
    service.compressor = SimpleNamespace(enqueue=lambda *args, **kwargs: None)
    original = service.preserve_native(row.id)
    saved = service.save(row.id)
    archive.close()
    permanent = Path(original["path"]).with_suffix(".json")
    permanent.write_text(json.dumps({**original["native_ghost"], "version": 1}), encoding="utf-8")
    path, _ = service.find_native(row.id)
    assert path == Path(saved["path"]).with_suffix(".ghost")
    assert service.native_preservation_reason(row.id) is None
