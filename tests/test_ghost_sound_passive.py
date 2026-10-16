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
        assert self.kernel.priority == 0, "output-device initialization is NORMAL"
        self.is_open = True
        return True

    def write(self, data):
        assert self.kernel.priority == 2, "PCM submission is HIGHEST"
        self.queued_frames += len(data) // 4
        return True

    def queued(self):
        if self.queued_frames:
            assert self.kernel.priority == 2, "queued PCM is serviced at HIGHEST"
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
    assert S.main(["--pj64", "10", "--parent", "20"]) == 0, output.getvalue()
    assert kernel.changes == [0, 2, 0]
    assert kernel.closed == [10, 20]
    stages = [json.loads(line[len(P.DIAGNOSTIC_PREFIX):])["stage"] for line in output.getvalue().splitlines()]
    assert stages == ["started", "ring_attached", "endpoint_ready", "first_device_open", "first_played", "exit"]


@pytest.mark.parametrize("opens, kept", [
    ([True, True], [0, 1, 2, 3]),
    ([False, True, True], [1, 2, 3]),
    ([True, False, True], [0, 1, 3]),
], ids=["cold-and-reopen", "cold-failure-and-retry", "reopen-failure-and-retry"])
def test_device_open_priority_preserves_pcm_through_idle_and_retry(monkeypatch, opens, kept):
    kernel, output, now = Kernel(), io.StringIO(), [0.0]
    writes = [(8199, 2176), (10375, 13), (10388, 1088), (11476, 17)]
    actions = iter([("write", 2176), ("write", 13), ("drain", 1), ("idle", 3.01),
                    ("write", 1088), ("write", 17), ("drain", 1), ("idle", 3.01)])
    outcomes, opened, pcm, waits = iter(opens), [], bytearray(), []
    active = [True]

    def samples(first, count):
        return b"".join(frame.to_bytes(4, "little") for frame in range(first, first + count))

    class Device(PriorityDevice):
        def __init__(self, **kwargs):
            super().__init__(kernel)
            self.route = kwargs["device_id"]

        def open(self):
            assert kernel.priority == 0, "every cold/reopened/retried device open is NORMAL"
            opened.append(self.route())
            now[0] += 0.007
            self.is_open = next(outcomes)
            return self.is_open

        def write(self, data):
            pcm.extend(data)
            return super().write(data)

    device, ring = [None], [None]

    class Ring:
        written, renderer_open, capacity = 8190, False, 8192
        played = None

        def __init__(self, _pid):
            assert kernel.priority == 0
            ring[0] = self

        def publish(self, played):
            self.played = played

        def beat(self, _pid=None):
            pass

        def wait_written(self, milliseconds):
            waits.append(milliseconds)
            action, value = next(actions)
            if action == "write":
                self.renderer_open = True
                self.written += value
            else:
                now[0] += value
                self.renderer_open = False
                device[0].queued_frames = 0
            if len(waits) == 8:
                active[0] = False

        def read(self, first, count):
            assert first % self.capacity + count <= self.capacity, "ring wrap is preserved"
            return samples(first, count)

        def close(self):
            assert kernel.priority == 0

    def make_device(**kwargs):
        device[0] = Device(**kwargs)
        return device[0]

    def find_endpoint():
        assert kernel.priority == 0
        ring[0].written += 9  # PCM produced during lookup is still skipped on attachment.
        return 7

    monkeypatch.setattr(S, "_kernel", lambda: kernel)
    monkeypatch.setattr(S, "_alive", lambda _handle: active[0])
    monkeypatch.setattr(S, "Ring", Ring)
    monkeypatch.setattr(S, "WaveOutDevice", make_device)
    monkeypatch.setattr(S, "device_for", lambda _pid: find_endpoint)
    monkeypatch.setattr(S, "StageNotes", lambda: P.StageNotes(output))
    # RingPlayer's default clock is bound at import, so supply the inert clock
    # through its real constructor while retaining all actual pacing methods.
    player_type = S.RingPlayer
    monkeypatch.setattr(S, "RingPlayer", lambda *args, **kwargs:
                        player_type(*args, clock=lambda: now[0], **kwargs))
    assert S.main(["--pj64", "10", "--parent", "20"]) == 0, output.getvalue()
    assert opened == [7] * len(opens), "routing and exactly one open per attempt are preserved"
    assert pcm == b"".join(samples(*writes[index]) for index in kept)
    assert not device[0].is_open and kernel.priority == 0
    assert ring[0].played == ring[0].written
    assert kernel.closed == [10, 20] and waits == [100, 10, 10, 10, 100, 10, 10, 10]
    stages = {entry["stage"]: entry for entry in (
        json.loads(line[len(P.DIAGNOSTIC_PREFIX):]) for line in output.getvalue().splitlines())}
    assert stages["ring_attached"]["written"] == 8190
    assert stages["endpoint_ready"]["written"] == 8199
    assert stages["first_device_open"]["attach_written"] == 8199
    assert stages["first_device_open"]["open_ms"] == pytest.approx(7.0)


def test_device_open_exception_releases_partial_device_at_normal_priority(monkeypatch):
    kernel, output = Kernel(), io.StringIO()

    class Ring:
        written, renderer_open, capacity = 0, False, 8192

        def __init__(self, _pid):
            pass

        def publish(self, _played):
            pass

        def beat(self, _pid=None):
            pass

        def wait_written(self, _milliseconds):
            self.renderer_open, self.written = True, 4

        def close(self):
            assert kernel.priority == 0

    class Device(PriorityDevice):
        def open(self):
            assert kernel.priority == 0
            self.is_open = True  # initialization acquired a device before failing
            raise RuntimeError("fixture output open failed 23")

    device = [None]

    def make_device(**_):
        device[0] = Device(kernel)
        return device[0]

    monkeypatch.setattr(S, "_kernel", lambda: kernel)
    monkeypatch.setattr(S, "_alive", lambda _handle: True)
    monkeypatch.setattr(S, "Ring", Ring)
    monkeypatch.setattr(S, "WaveOutDevice", make_device)
    monkeypatch.setattr(S, "device_for", lambda _pid: lambda: 7)
    monkeypatch.setattr(S, "StageNotes", lambda: P.StageNotes(output))
    assert S.main(["--pj64", "10", "--parent", "20"]) == 1
    assert "RuntimeError: fixture output open failed 23" in output.getvalue()
    assert not device[0].is_open and kernel.priority == 0 and kernel.closed == [10, 20]


def test_normal_priority_failure_defers_open_and_pcm_until_os_accepts():
    output, opens, pcm = io.StringIO(), [], bytearray()

    class FailingKernel(Kernel):
        reject_normal = True

        def __init__(self):
            super().__init__()
            self.priority, self.attempts = 2, []

        def SetThreadPriority(self, thread, value):
            self.attempts.append(value)
            if value == 0 and self.reject_normal:
                return 0  # actual priority remains HIGHEST
            return super().SetThreadPriority(thread, value)

    kernel = FailingKernel()
    priority = S.ServicePriority(kernel, P.StageNotes(output))

    class Ring:
        written, renderer_open, capacity = 10, True, 8192

        def publish(self, played):
            self.played = played

        def beat(self, _pid=None):
            pass

        def read(self, first, count):
            return b"".join(frame.to_bytes(4, "little") for frame in range(first, first + count))

    class Device:
        is_open = False

        def open(self):
            assert kernel.priority == 0, "OS-refused demotion must prevent driver initialization"
            opens.append(1)
            self.is_open = True
            return True

        def write(self, data):
            assert kernel.priority == 2
            pcm.extend(data)
            return True

        def queued(self):
            assert kernel.priority == 2
            return len(pcm) // 4

        def close(self):
            assert kernel.priority == 0
            self.is_open = False

    ring, device = Ring(), Device()
    player = S.RingPlayer(ring, device, 77, priority=priority.set)
    ring.written = 14
    for _ in range(2):
        player.step()
        assert not opens and not pcm and player.sent == 10 and ring.played == 10
    assert kernel.priority == 2 and kernel.attempts == [0, 0, 0], "every refusal is retried"
    kernel.reject_normal = False
    player.step()
    assert opens == [1] and pcm == b"".join(frame.to_bytes(4, "little") for frame in range(10, 14))
    assert player.sent == 14 and ring.played == 10
    assert priority.set(False) is True
    player.close()
    assert not device.is_open
    errors = [json.loads(line[len(P.DIAGNOSTIC_PREFIX):]) for line in output.getvalue().splitlines()]
    assert len(errors) == 1 and errors[0]["stage"] == "priority_error" and errors[0]["active"] is False


def test_failed_high_priority_transition_is_retried_without_caching_failure():
    output = io.StringIO()

    class FailingKernel(Kernel):
        reject_high = True

        def __init__(self):
            super().__init__()
            self.attempts = []

        def SetThreadPriority(self, thread, value):
            self.attempts.append(value)
            if value == 2 and self.reject_high:
                return 0
            return super().SetThreadPriority(thread, value)

    kernel = FailingKernel()
    priority = S.ServicePriority(kernel, P.StageNotes(output))
    refused = [priority.set(True), priority.set(True)]
    assert kernel.attempts == [0, 2, 2], "a refused HIGH request must reach the OS again"
    assert refused == [False, False] and kernel.priority == 0
    kernel.reject_high = False
    assert priority.set(True) is True and kernel.priority == 2
    attempts = list(kernel.attempts)
    assert priority.set(True) is True and kernel.attempts == attempts
    assert priority.set(False) is True and kernel.priority == 0


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
