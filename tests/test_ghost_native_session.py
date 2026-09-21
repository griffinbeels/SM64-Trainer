"""Reach the native renderer through the same ghost switch as video playback."""
import asyncio
import threading
from types import SimpleNamespace

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
        assert command["stop_at_star"] and command["rom_path"] == ""
        assert command["origin"]["global_timer"] == 100
        assert command["origin"]["configuration"] == 2 and command["origin"]["epoch"] == 3
        count = len(source.commands)
        await session.refresh(resolve=False)
        assert len(source.commands) == count
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
        assert source.commands[-1] == {"enabled": False}
        await session.stop()
        session._native.note()
        assert source.commands[-1] == {"enabled": False}

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
