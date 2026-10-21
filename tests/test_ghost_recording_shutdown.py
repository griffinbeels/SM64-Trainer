"""Nested retirement witnesses with inert process/channel boundaries only.

No child process, sound device, native mapping, GPU or emulator is opened.
The recorder and its existing capture worker own the real shutdown lifecycle.
"""
import asyncio
import io
import subprocess
import threading

import pytest

from sm64_events.ghost.capture import NativeCapture
from sm64_events.ghost.sound_process import SoundPlayerProcess, _Diagnostics
from sm64_events.memory.layout import US
from test_replay_startup_order import WIN, make
from tests.test_ghost_native_session import Capture, native_session


class Child:
    pid = 909
    returncode = None

    def __init__(self, failure="terminate"):
        self.failure = failure
        self.release = threading.Event()
        self.failed = threading.Event()
        self.alive = True
        self.stderr = None

    def poll(self):
        return None if self.alive else self.returncode

    def terminate(self):
        if not self.release.is_set() and self.failure == "terminate":
            self.failed.set()
            raise OSError("terminate refused 1234")

    def wait(self, timeout):
        if not self.release.is_set():
            self.failed.set()
            raise subprocess.TimeoutExpired("inert sound child", timeout)
        self.alive, self.returncode = False, 0
        return 0

    def kill(self):
        if not self.release.is_set() and self.failure == "kill":
            raise OSError("kill refused 5678")


def player_for(child, spawn=None):
    player = SoundPlayerProcess(spawn=spawn or (lambda *_args, **_kwargs: pytest.fail("second sound child")))
    player._child, player.pj64_pid = child, 42
    return player


@pytest.mark.parametrize("failure", ["terminate", "kill", "wait"])
def test_failed_child_retirement_keeps_owner_and_blocks_replacement(failure):
    child = Child(failure)
    player = player_for(child)
    with pytest.raises((OSError, subprocess.TimeoutExpired)):
        player.stop(timeout_s=0)
    assert child.alive and player._child is child and player.pj64_pid == 42
    with pytest.raises((OSError, subprocess.TimeoutExpired)):
        player.start(43)
    assert player._child is child and player.pj64_pid == 42
    child.release.set()
    player.stop(timeout_s=0)
    assert not child.alive and player._child is None and player.pj64_pid is None


class Reader:
    def __init__(self):
        self.alive = True
    def join(self, timeout):
        pass
    def is_alive(self):
        return self.alive


class Pipe(io.BytesIO):
    fail_close = False
    def close(self):
        if self.fail_close:
            raise OSError("sound pipe close refused 88")
        super().close()


def diagnostics_for(pipe):
    diagnostics = _Diagnostics.__new__(_Diagnostics)
    diagnostics.stream, diagnostics._reader = pipe, Reader()
    diagnostics._tail = [b"retained sound error"]
    return diagnostics


@pytest.mark.parametrize("failure", ["reader", "pipe"])
def test_diagnostics_failure_keeps_exact_owners_until_retry(failure):
    child, pipe = Child(), Pipe()
    child.alive, child.returncode, child.stderr = False, 0, pipe
    diagnostics = diagnostics_for(pipe)
    diagnostics._reader.alive = failure == "reader"
    pipe.fail_close = failure == "pipe"
    player = player_for(child)
    player._diagnostics = diagnostics
    with pytest.raises((RuntimeError, OSError), match="cleanup pending|close refused"):
        player.stop(timeout_s=0)
    assert player._child is child and player._diagnostics is diagnostics
    assert player.pj64_pid == 42 and not pipe.closed
    with pytest.raises((RuntimeError, OSError)):
        player.start(43)
    diagnostics._reader.alive, pipe.fail_close = False, False
    player.stop(timeout_s=0)
    assert player._child is None and player._diagnostics is None and pipe.closed


def test_diagnostics_initialization_failure_still_retires_spawned_pipe(monkeypatch):
    import sm64_events.ghost.sound_process as module
    child, pipe = Child(), Pipe()
    child.stderr = pipe
    player = SoundPlayerProcess(spawn=lambda *_args, **_kwargs: child)
    monkeypatch.setattr(module, "child_command", lambda _pid: ([], {}))
    def failed_diagnostics(*_):
        raise RuntimeError("diagnostics thread could not start")
    monkeypatch.setattr(module, "_Diagnostics", failed_diagnostics)
    with pytest.raises(RuntimeError, match="could not start"):
        player.start(42)
    assert player._child is child and player._diagnostics is None
    child.release.set()
    pipe.fail_close = True
    with pytest.raises(OSError, match="close refused"):
        player.stop(timeout_s=0)
    assert player._child is child and not child.alive and not pipe.closed
    pipe.fail_close = False
    player.stop(timeout_s=0)
    assert pipe.closed and player._child is None


@pytest.mark.parametrize("error_kind", [OSError, ValueError])
def test_stop_escapes_reconnect_retry_and_retires_sound_while_channel_is_retained(error_kind):
    closed, polled = threading.Event(), threading.Event()
    class Channel:
        def poll(self, _controls):
            polled.set()
            raise OSError("inert channel unavailable")
        def close(self):
            if not closed.is_set():
                raise error_kind("channel close refused 99")
    class Sound:
        stopped = threading.Event()
        def stop(self):
            self.stopped.set()
    sound = Sound()
    source = NativeCapture(US, lambda *_: None, sound_player=sound)
    channel = source._channel = Channel()
    if error_kind is ValueError:
        # An unexpected exception at the final cleanup boundary must also
        # retain ownership; do not raise it in the reconnect pump instead.
        source._pump = lambda: (polled.set(), source._stop.wait(2))
    source.start()
    try:
        assert polled.wait(1)
        assert not source.stop(timeout_s=0.01)
        assert sound.stopped.wait(1), "channel reconnect retry stranded the sound owner"
        assert source._channel is channel and source._thread.is_alive()
        assert "channel close refused 99" in source.cleanup_error
        assert source.snapshot_status()["reason"] == "cleanup_pending"
    finally:
        closed.set()
        assert source.stop(timeout_s=2), "owned inert capture worker survived"
    assert source.cleanup_error is None


def test_dead_capture_worker_without_cleanup_proof_is_not_closed():
    class DeadWorker:
        def join(self, _timeout):
            pass
        def is_alive(self):
            return False
    source = NativeCapture(US, lambda *_: None)
    source._thread = DeadWorker()
    source._cleanup_complete.clear()
    assert not source.stop(timeout_s=0), "thread exit alone is not nested-owner retirement"


def test_recording_off_retains_pose_and_machine_lease_until_sound_retires(tmp_path):
    rec, *_rest = make(tmp_path)
    lease = _rest[3]
    child = Child()
    player = player_for(child)
    sources = []
    class FastStopCapture(NativeCapture):
        def stop(self, timeout_s=3):
            return super().stop(timeout_s=0.01)
    def source_factory(**kwargs):
        source = FastStopCapture(US, sound_player=player, **kwargs)
        source._pump = lambda: source._stop.wait(2)
        sources.append(source)
        return source
    rec._ghost_capture_factory = source_factory
    rec._begin_capture(WIN)
    source, poses = sources[0], rec._pose_owner
    try:
        rec.set_recording_enabled(False)
        rec._teardown_capture()
        assert child.failed.wait(1)
        assert rec.status()["capture_state"] == "stopping"
        assert rec._capture_retained["poses"] is poses and poses.source is source
        assert child.alive and player._child is child and not lease.closed
        # A retry obtains the worker's cached, original exception text.
        rec._teardown_capture()
        assert "terminate refused 1234" in rec.status()["recovery"]["error"]
        child.release.set()
        assert source._cleanup_complete.wait(2)
        rec._teardown_capture()
        assert not child.alive and player._child is None and lease.closed
        assert rec.status()["capture_state"] == "off"
    finally:
        child.release.set()
        assert source._cleanup_complete.wait(2)
        rec.stop(cleanup=False)
        assert not source._thread.is_alive()


def test_native_card_keeps_ghosts_with_footage_off_and_recovers_source_independently(tmp_path):
    session, source, _ = native_session(tmp_path)
    recorder = session._native._replay.recorder
    recorder.recording_enabled = True
    source.displayed = True
    async def check():
        await session.update({"enabled": True})
        session._native.note()
        before = dict(session._values)
        assert session.payload()["window"]["visible"]
        recorder.recording_enabled = False
        # Disabling footage leaves this native race and its display evidence.
        card = session.payload()["window"]
        assert card["running"] and card["visible"]
        assert card["reason"] is None
        session._native.note()
        assert source.commands[-1]["enabled"]
        recorder.ghost_capture = None
        await session.refresh(resolve=False)
        card = session.payload()["window"]
        assert not card["running"] and not card["visible"]
        assert "Start recording" not in card["reason"]
        assert session._values == before
        # Recovery does not require turning footage back on.
        fresh = recorder.ghost_capture = Capture()
        fresh.displayed = True
        await session.refresh(resolve=False)
        session._native.note()
        assert fresh.commands[-1]["enabled"] and session.payload()["window"]["visible"]
        assert session._values == before
        assert recorder.recording_enabled is False
        await session.stop()
    asyncio.run(check())
