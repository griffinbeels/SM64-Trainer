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

    def set_playback(self, **command):
        self.commands.append(command)

    def snapshot_status(self):
        return {"available": True, "state": 4, "playback_reason": 2,
                "displayed": self.displayed}


def native_session(tmp_path, *, asset=True):
    session, windows, tracker, store = a_session(tmp_path)
    source = Capture()
    manifest = {"clip_id": "ab" * 16, "sha256": "cd" * 32}
    replay = SimpleNamespace(recorder=SimpleNamespace(ghost_capture=source),
                             find_native=lambda _: (tmp_path / "run.ghost", manifest) if asset else None)
    session._native = NativeGhost(replay)
    mark(store)
    return session, source, windows, tracker


def test_native_switch_uses_pose_without_video_window_and_preserves_controls(tmp_path):
    session, source, windows, _ = native_session(tmp_path)

    async def check():
        await session.update({"enabled": True, "view": "mario", "opacity": 1,
                           "tint": {"enabled": True, "hue_degrees": 120}, "nudge_frames": 9})
        assert not windows
        assert session.payload()["source"]["exactness"] == "native_pose"
        command = source.commands[-1]
        assert command["enabled"] and command["opacity"] == 1 and command["nudge"] == 9
        assert command["tint_enabled"] and command["hue_degrees"] == 120
        assert command["stop_at_star"] and command["rom_path"] == ""
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
        assert session._native.state()["running"]
        assert not session._native.state()["visible"]
        source.displayed = True
        assert session._native.state()["visible"]
        source.displayed = False
        assert not session._native.state()["visible"]
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
