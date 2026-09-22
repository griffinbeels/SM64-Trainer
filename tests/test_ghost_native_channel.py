"""Native pose transport ownership, byte layout, and epoch association."""
import ctypes as C
from dataclasses import replace
import struct
import threading
import time

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
        payload = bytes(i % 256 for i in range(P.RECORD_BYTES))
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
        for changes in ({"proximity_enabled": 1}, {"proximity_distance": float("nan")},
                        {"proximity_distance": 401}, {"proximity_distance": -1}):
            with pytest.raises(ValueError):
                connection.poll(changes)
            assert bytes(kernel.request) == original
        connection.poll({"proximity_enabled": True, "proximity_distance": 120})
        assert kernel.request.proximity_enabled == 1 and kernel.request.proximity_distance == 120
        connection.poll({"proximity_enabled": False, "proximity_distance": 120})
        assert kernel.request.proximity_enabled == 0 and kernel.request.proximity_distance == 120
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


@pytest.mark.parametrize("birth", [None, (999, 0)])
def test_retained_ready_page_cannot_keep_dead_or_replaced_producer_alive(kernel, monkeypatch, birth):
    connection = channel.NativeChannel(123, GhostLayout.from_layout(US).to_bytes())
    try:
        reply = status()
        reply.ack_token, reply.ack_revision = kernel.request.token, kernel.request.revision
        reply.state, reply.displayed = 4, 1
        C.memmove(kernel.address, bytes(reply), 256)
        assert connection.poll()[0].displayed == 1
        before = bytes(kernel.request)
        monkeypatch.setattr(channel, "_process_creation", lambda *_: birth)
        with pytest.raises(ProcessLookupError, match="producer ended or was replaced"):
            connection.poll()
        assert bytes(kernel.request) == before
    finally:
        connection.close()
    assert set(kernel.closed) == {1, 2, 3}


def frame(configuration, epoch):
    value = bytearray(P.RECORD_BYTES)
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


def clip(tmp_path, key: bytes, hue=0):
    return {"clip_id": key * 16, "clip_sha256": b"b" * 32,
            "clip_path": tmp_path / f"{key.decode()}.ghost", "hue_degrees": hue}


def test_exact_origin_transport_and_full_width_seek(kernel, tmp_path):
    connection = channel.NativeChannel(123, GhostLayout.from_layout(US).to_bytes())
    controls = {"enabled": True, "model_id": 1, "clips": [clip(tmp_path, b"a")],
                "origin": {"kind": 2, "configuration": 4, "epoch": 5,
                           "global_timer": 1234, "utc": 1000.0, "session_id": 7, "arm_id": 8}}
    try:
        connection.poll(controls)
        assert kernel.request.origin.global_timer == 1234
        assert kernel.request.origin.kind == 2 and kernel.request.origin.arm_id == 8
        before = bytes(kernel.request)
        with pytest.raises(ValueError):
            connection.poll({**controls, "origin": {**controls["origin"], "epoch": 2**32}})
        assert bytes(kernel.request) == before
        with pytest.raises(ValueError):
            connection.poll({**controls, "mode": 1, "seek_igt": 2**32})
        assert bytes(kernel.request) == before
        connection.poll({**controls, "mode": 1, "seek_igt": 100000})
        assert kernel.request.seek_igt == 100000 and not any(bytes(kernel.request.origin))
    finally:
        connection.close()


def test_session_shaped_command_carries_the_shadow_switch(kernel, tmp_path):
    """The exact keys NativeGhostSession._send publishes, plus shadow_enabled."""
    connection = channel.NativeChannel(123, GhostLayout.from_layout(US).to_bytes())
    assert kernel.request.shadow_enabled == 1  # the lease starts with shadows on
    command = {"enabled": True, "model_id": 1, "owner_suspended": False,
               "clips": [clip(tmp_path, b"a", 10), clip(tmp_path, b"b", 130)], "rom_path": "",
               "opacity": 0.5, "nudge": 0, "proximity_enabled": True, "proximity_distance": 40.0,
               "tint_enabled": True, "stop_at_star": True, "mode": 0,
               "origin": {"kind": 1, "configuration": 4, "epoch": 5, "global_timer": 1234,
                          "utc": 1000.0, "session_id": 7, "arm_id": 8}}
    try:
        connection.poll(command)  # a session that predates the key
        assert kernel.request.shadow_enabled == 1 and kernel.request.clip_count == 2
        connection.poll({**command, "shadow_enabled": False})
        assert kernel.request.shadow_enabled == 0 and kernel.request.reserved2 == 0
        assert kernel.request.opacity == 0.5 and kernel.request.origin.global_timer == 1234
        before = bytes(kernel.request)
        for value in (1, "yes", None):
            with pytest.raises(channel.RejectedControl):
                connection.poll({**command, "shadow_enabled": value})
            assert bytes(kernel.request) == before
        connection.poll({**command, "shadow_enabled": True})
        assert kernel.request.shadow_enabled == 1
    finally:
        connection.close()


def test_clip_set_is_encoded_in_order_with_per_ghost_hue(kernel, tmp_path):
    connection = channel.NativeChannel(123, GhostLayout.from_layout(US).to_bytes())
    controls = {"enabled": True, "model_id": 1, "mode": 1,
                "clips": [clip(tmp_path, b"a", 10), clip(tmp_path, b"c", 200), clip(tmp_path, b"d", 359)]}
    try:
        connection.poll(controls)
        request = kernel.request
        assert request.clip_count == 3
        assert [bytes(entry.clip_id) for entry in request.clips[:3]] == [b"a" * 16, b"c" * 16, b"d" * 16]
        assert [entry.hue_degrees for entry in request.clips[:3]] == [10, 200, 359]
        text = bytes(request.clips[1].clip_path).decode("utf-16-le").rstrip("\0")
        assert text == str((tmp_path / "c.ghost").resolve())
        assert not any(any(bytes(entry)) for entry in request.clips[3:])
        connection.poll({**controls, "clips": controls["clips"][1:2]})
        assert kernel.request.clip_count == 1 and bytes(kernel.request.clips[0].clip_id) == b"c" * 16
        assert not any(bytes(kernel.request.clips[1]))
    finally:
        connection.close()


@pytest.mark.parametrize("change", [
    {"clips": []},
    {"clips": None},
    {"clips": "one"},
    {"clips": ["a"] * 17},
    {"duplicate": True},
    {"zero_id": True},
    {"short_digest": True},
    {"hue": 360},
    {"hue": True},
    {"extra_key": True},
    {"clip_id": b"a" * 16},
    {"hue_degrees": 5},
])
def test_invalid_clip_set_never_mutates_the_live_command(kernel, tmp_path, change):
    connection = channel.NativeChannel(123, GhostLayout.from_layout(US).to_bytes())
    good = {"enabled": True, "model_id": 1, "mode": 1, "clips": [clip(tmp_path, b"a"), clip(tmp_path, b"c")]}
    try:
        connection.poll(good)
        before = bytes(kernel.request)
        controls = {**good, "clips": [dict(entry) for entry in good["clips"]]}
        entry = controls["clips"][1]
        if change.get("duplicate"):
            entry["clip_id"] = b"a" * 16
        elif change.get("zero_id"):
            entry["clip_id"] = bytes(16)
        elif change.get("short_digest"):
            entry["clip_sha256"] = b"b" * 31
        elif "hue" in change:
            entry["hue_degrees"] = change["hue"]
        elif change.get("extra_key"):
            entry["attempt_id"] = 4
        else:
            controls.update(change)
        with pytest.raises(ValueError):
            connection.poll(controls)
        assert bytes(kernel.request) == before
    finally:
        connection.close()


def test_disabled_playback_needs_no_clips(kernel, tmp_path):
    connection = channel.NativeChannel(123, GhostLayout.from_layout(US).to_bytes())
    try:
        connection.poll({"enabled": True, "model_id": 1, "mode": 1, "clips": [clip(tmp_path, b"a")]})
        connection.poll({"enabled": False})
        assert not kernel.request.play_enabled and kernel.request.clip_count == 1
        connection.poll({"enabled": False, "clips": []})
        assert kernel.request.clip_count == 0
    finally:
        connection.close()


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


def test_an_unencodable_command_is_a_typed_rejection_that_changes_nothing(kernel):
    connection = channel.NativeChannel(123, GhostLayout.from_layout(US).to_bytes())
    try:
        original = bytes(kernel.request)
        # The live 2026-09-22 shape: a newer session's command, older channel.
        with pytest.raises(channel.RejectedControl):
            connection.poll({"enabled": True, "model_id": 1, "clips": [{}]})
        assert bytes(kernel.request) == original
        connection.poll(None)  # the lease still renews
    finally:
        connection.close()


def test_a_rejected_command_never_stops_pose_recording(monkeypatch):
    batches, polls, closed = [], [], []

    class Source:
        def __init__(self, *_): pass

        def poll(self, control=None):
            polls.append(control)
            if control is not None and "clips" in control:
                raise channel.RejectedControl("native ghost command rejected: KeyError('clip_id')")
            return status(), (frame(len(polls), 7),)

        def close(self): closed.append(1)

    monkeypatch.setattr(capture, "NativeChannel", Source)
    source = capture.NativeCapture(US, lambda *args: batches.append(args), producer_pid=123)
    source.start()
    try:
        source.set_playback(enabled=True, clips=[{}])
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not (
                any(p and "clips" in p for p in polls) and len(batches) >= 5):
            time.sleep(0.01)
        rejected = next(i for i, p in enumerate(polls) if p and "clips" in p)
        assert len(polls) > rejected + 3 and all(p is None for p in polls[rejected + 1:])
        assert source._thread.is_alive() and not closed
    finally:
        assert source.stop(3)
