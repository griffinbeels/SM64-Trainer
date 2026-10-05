"""CPU-only checks for sound initialization, idle priority and bounded diagnostics."""
import builtins
import io
import json
import logging

import pytest

from sm64_events.ghost import sound_player as S
from sm64_events.ghost import sound_process as P
from sm64_events.replay import audioendpoint


class Kernel:
    def __init__(self):
        self.priority, self.changes, self.closed = None, [], []

    def OpenProcess(self, _access, _inherit, pid):
        return pid

    def GetCurrentThread(self):
        return 99

    def SetThreadPriority(self, _thread, value):
        self.priority = value
        self.changes.append(value)
        return 1

    def CloseHandle(self, handle):
        self.closed.append(handle)


class PriorityDevice:
    is_open = False
    queued_frames = 0

    def __init__(self, kernel, **_):
        self.kernel = kernel
        assert kernel.priority == 0, "device construction is initialization"

    def open(self):
        assert self.kernel.priority == 2, "only actual audio service is elevated"
        self.is_open = True
        return True

    def write(self, data):
        self.queued_frames += len(data) // 4
        return True

    def queued(self):
        return self.queued_frames

    def close(self):
        assert self.kernel.priority == 0
        self.is_open = False


def test_imports_and_idle_do_not_inherit_audio_service_priority():
    kernel, output = Kernel(), io.StringIO()
    priority = S.ServicePriority(kernel, P.StageNotes(output))
    assert kernel.priority == 0
    for active in (False, True, True, False, False):
        priority.set(active)
    assert kernel.changes == [0, 2, 0], "only transitions call the OS; idle is NORMAL"


def test_child_initializes_normally_then_only_prioritizes_queued_sound(monkeypatch):
    kernel, output, waits = Kernel(), io.StringIO(), []

    device = [None]

    class Ring:
        written, renderer_open, capacity = 1000, False, 8192

        def __init__(self, _pid):
            assert kernel.priority == 0

        def publish(self, _played):
            pass

        def beat(self, _pid=None):
            pass

        def wait_written(self, milliseconds):
            waits.append(milliseconds)
            if len(waits) == 1:
                self.renderer_open, self.written = True, 1004
            else:
                self.renderer_open = False
                device[0].queued_frames = 0

        def read(self, _frame, count):
            return bytes(count * 4)

        def close(self):
            pass

    def make_device(**kwargs):
        device[0] = PriorityDevice(kernel, **kwargs)
        return device[0]

    def choose(_pid):
        def find():
            assert kernel.priority == 0, "endpoint enumeration is normal priority"
            return 7
        return find

    monkeypatch.setattr(S, "_kernel", lambda: kernel)
    monkeypatch.setattr(S, "_alive", lambda _handle: len(waits) < 2)
    monkeypatch.setattr(S, "Ring", Ring)
    monkeypatch.setattr(S, "WaveOutDevice", make_device)
    monkeypatch.setattr(S, "device_for", choose)
    monkeypatch.setattr(S, "StageNotes", lambda: P.StageNotes(output))
    assert S.main(["--pj64", "10", "--parent", "20"]) == 0
    assert kernel.changes == [0, 2, 0]
    assert kernel.closed == [10, 20]
    stages = [json.loads(line[len(P.DIAGNOSTIC_PREFIX):])["stage"] for line in output.getvalue().splitlines()]
    assert stages == ["started", "ring_attached", "endpoint_ready", "first_device_open", "first_played", "exit"]


def test_child_failure_keeps_the_exception_and_releases_process_handles(monkeypatch):
    kernel, output = Kernel(), io.StringIO()
    monkeypatch.setattr(S, "_kernel", lambda: kernel)
    monkeypatch.setattr(S, "_alive", lambda _handle: True)
    monkeypatch.setattr(S, "StageNotes", lambda: P.StageNotes(output))

    def broken_ring(_pid):
        raise ValueError("wrong sound ring version 19")

    monkeypatch.setattr(S, "Ring", broken_ring)
    assert S.main(["--pj64", "10", "--parent", "20"]) == 1
    assert kernel.changes == [0] and kernel.closed == [10, 20]
    assert "ValueError: wrong sound ring version 19" in output.getvalue()


def test_sound_endpoint_lookup_never_imports_capture_or_numpy(monkeypatch):
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name == "sm64_events.replay.audio" or name.startswith("numpy"):
            pytest.fail("endpoint lookup imported recording/capture dependencies")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(audioendpoint, "device_name_hosting_pid", lambda _pid: None)
    monkeypatch.setattr(builtins, "__import__", guarded)
    assert S.device_for(10)() == S.WAVE_MAPPER


def test_diagnostics_bound_stage_messages_and_keep_failure_tail(caplog):
    output = io.StringIO()
    notes = P.StageNotes(output)
    for _ in range(100):
        notes.report("error", error="a" * 10000)
    for index in range(30):
        notes.report(f"stage_{index}")
    lines = output.getvalue().splitlines()
    assert len(lines) == 16
    assert len(json.loads(lines[0][len(P.DIAGNOSTIC_PREFIX):])["error"]) == 2048
    raw = (output.getvalue() + "ignored\n" * 10000 + "RuntimeError: device unavailable 77\n").encode()
    with caplog.at_level(logging.INFO, logger="sm64.ghost.sound"):
        diagnostics = P._Diagnostics(io.BytesIO(raw), 4321)
        tail = diagnostics.finish()
    assert len(tail.encode()) <= P.MAX_DIAGNOSTIC_BYTES
    assert "RuntimeError: device unavailable 77" in tail
    assert len([r for r in caplog.records if "ghost sound child 4321:" in r.message]) == 16
    assert not diagnostics._reader.is_alive()


def test_spawn_failure_obeys_retry_bound_without_a_child(caplog):
    now, attempts = [0.0], []

    def failed_spawn(*_args, **_kwargs):
        attempts.append(now[0])
        raise OSError("CreateProcess refused: 1234")

    player = P.SoundPlayerProcess(spawn=failed_spawn, clock=lambda: now[0])
    with pytest.raises(OSError):
        player.start(10)
    for now[0] in (0.1, 1.0, 4.9):
        player.start(10)
    assert attempts == [0.0] and player.pid is None
    now[0] = 5.0
    with pytest.raises(OSError):
        player.start(10)
    assert attempts == [0.0, 5.0]
    assert "OSError: CreateProcess refused: 1234" in caplog.text
