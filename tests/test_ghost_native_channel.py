"""Native pose transport ownership, byte layout, and epoch association."""
import ctypes as C
from dataclasses import replace
import struct
import threading

import pytest

from sm64_events.ghost import capture, native_channel as channel, native_protocol as P
from sm64_events.memory.ghost_layout import GhostLayout
from sm64_events.memory.layout import US


def status():
    value = P.Status()
    value.magic, value.version, value.bytes = P.MAGIC, P.VERSION, P.PAGE_BYTES
    value.producer_pid, value.producer_created_lo, value.producer_created_hi = 123, 456, 0
    value.generation, value.rom_generation, value.rom_open, value.state = 1, 2, 1, 1
    value.record_bytes, value.qpc_frequency = P.RECORD_BYTES, 10_000_000
    value.qpc_anchor, value.utc_anchor = 500, 1000.0
    return value


class Kernel:
    def __init__(self):
        self.buffer = C.create_string_buffer(P.PAGE_BYTES)
        self.address = C.addressof(self.buffer)
        C.memmove(self.address, bytes(status()), 256)
        self.closed, self.wakes = [], 0

    def OpenFileMappingW(self, *_): return 1
    def MapViewOfFile(self, *_): return self.address
    def CreateMutexW(self, *_): return 2
    def CreateEventW(self, *_): return 3
    def WaitForSingleObject(self, *_): return 0
    def ReleaseMutex(self, *_): return 1
    def UnmapViewOfFile(self, *_): return 1
    def CloseHandle(self, handle): self.closed.append(handle); return 1
    def SetEvent(self, *_): self.wakes += 1; return 1

    @property
    def request(self):
        return P.Request.from_buffer_copy(C.string_at(self.address + 256, C.sizeof(P.Request)))


@pytest.fixture
def kernel(monkeypatch):
    fake = Kernel()
    monkeypatch.setattr(channel, "_kernel", lambda: fake)
    monkeypatch.setattr(channel, "_process_creation", lambda _, pid: (456, 0) if pid == 123 else (789, 0))
    return fake


def test_channel_acquires_renews_copies_and_releases(kernel):
    connection = channel.NativeChannel(123, GhostLayout.from_layout(US).to_bytes())
    try:
        request = kernel.request
        assert request.token and request.capture_enabled and request.generation == 1
        assert request.star_action_count == len(channel.STAR_GRAB_ACTIONS)
        assert set(request.star_actions[:request.star_action_count]) == channel.STAR_GRAB_ACTIONS
        payload = bytes(range(192))
        C.memmove(kernel.address + P.RING_OFFSET, payload, len(payload))
        P.U64.from_address(kernel.address + P.Status.produced.offset).value = 1
        before = kernel.request.heartbeat
        current, frames = connection.poll({"enabled": False, "opacity": 1, "nudge": -10})
        assert frames == (payload,) and current.produced == 1
        assert kernel.request.heartbeat != before
        assert kernel.request.nudge == -10 and kernel.request.opacity == 1
        assert connection.poll()[1] == ()
    finally:
        connection.close()
    assert not kernel.request.capture_enabled and not kernel.request.play_enabled
    assert set(kernel.closed) == {1, 2, 3}


def test_foreign_live_owner_is_never_replaced(kernel):
    request = P.Request()
    request.owner_pid, request.owner_created_lo = 555, 789
    request.generation, request.token, request.capture_enabled = 1, 42, 1
    C.memmove(kernel.address + 256, bytes(request), C.sizeof(request))
    with pytest.raises(RuntimeError, match="already has an owner"):
        channel.NativeChannel(123, GhostLayout.from_layout(US).to_bytes())
    assert kernel.request.token == 42 and kernel.request.capture_enabled


def test_invalid_control_does_not_partly_publish(kernel):
    connection = channel.NativeChannel(123, GhostLayout.from_layout(US).to_bytes())
    try:
        original = bytes(kernel.request)
        with pytest.raises(ValueError):
            connection.poll({"enabled": True, "opacity": float("nan")})
        assert bytes(kernel.request) == original
    finally:
        connection.close()


def test_new_command_cannot_borrow_old_display_acknowledgement(kernel):
    connection = channel.NativeChannel(123, GhostLayout.from_layout(US).to_bytes())
    try:
        reply = status()
        reply.ack_token, reply.ack_revision = kernel.request.token, kernel.request.revision
        reply.state, reply.playback_reason, reply.displayed = 4, 2, 1
        C.memmove(kernel.address, bytes(reply), 256)
        assert connection.poll()[0].displayed == 1
        current, _ = connection.poll({"enabled": False})
        assert current.displayed == 0 and current.playback_reason == 1
        # Mask the local telemetry copy, never overwrite producer-owned status.
        assert P.Status.from_address(kernel.address).displayed == 1
    finally:
        connection.close()


def test_status_rejects_overrun_and_wrong_producer():
    value = status()
    assert P.checked_status(bytes(value), 123).generation == 1
    value.produced = P.SLOTS + 1
    with pytest.raises(ValueError): P.checked_status(bytes(value), 123)
    with pytest.raises(ValueError): P.checked_status(bytes(status()), 999)


def frame(configuration, epoch):
    value = bytearray(192)
    struct.pack_into("<I", value, 8, configuration)
    struct.pack_into("<I", value, 24, epoch)
    return bytes(value)


def test_queued_epoch_transition_uses_record_identity():
    producer = P.Producer.from_status(status())
    frames = (frame(2, 7), frame(2, 7), frame(2, 8), frame(4, 9))
    batches = list(capture.grouped(producer, frames))
    assert batches == [(replace(producer, configuration=2, epoch=7), frames[:2]),
                       (replace(producer, configuration=2, epoch=8), frames[2:3]),
                       (replace(producer, configuration=4, epoch=9), frames[3:])]


def test_stop_retains_owner_until_inflight_callback_releases(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    closed = []

    class Source:
        def __init__(self, *_): pass
        def poll(self, *_): return status(), (frame(2, 7),)
        def close(self): closed.append(threading.current_thread().name)

    def accept(*_):
        entered.set()
        assert release.wait(5)

    monkeypatch.setattr(capture, "NativeChannel", Source)
    source = capture.NativeCapture(US, accept, producer_pid=123)
    source.start()
    try:
        assert entered.wait(3)
        assert source.stop(0.01) is False
        assert not closed
    finally:
        release.set()
        assert source.stop(3)
    assert closed == ["ghost-pose-capture"]


def test_partial_constructor_owner_blocks_reacquisition_and_shutdown(monkeypatch):
    from sm64_events.replay.ownedclose import CleanupPending
    entered, release = threading.Event(), threading.Event()
    constructions, releases = [], []

    class Partial:
        def close(self):
            entered.set()
            if not release.is_set():
                raise OSError("release still pending")
            releases.append(threading.current_thread().name)

    def fail(*_):
        constructions.append(1)
        raise CleanupPending(Partial(), OSError("failed construction"))

    monkeypatch.setattr(capture, "NativeChannel", fail)
    source = capture.NativeCapture(US, lambda *_: None, producer_pid=123)
    source.start()
    try:
        assert entered.wait(3)
        assert source.stop(0.01) is False
        assert constructions == [1] and not releases
    finally:
        release.set()
        assert source.stop(3)
    assert constructions == [1] and releases == ["ghost-pose-capture"]
