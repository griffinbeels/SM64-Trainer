import ctypes as C

import pytest

from ghost_native_fixture import frame

from sm64_events.ghost import live_channel as module
from sm64_events.multiplayer import native_protocol as P
from sm64_events.multiplayer.native_inbox import NativeInbox
from sm64_events.multiplayer.protocol import encode_pose


class Kernel:
    def __init__(self):
        self.buffer = C.create_string_buffer(P.PAGE_BYTES)
        self.address = C.addressof(self.buffer)
        self.header = P.Header.from_buffer(self.buffer)
        h = self.header
        h.magic, h.version, h.bytes, h.request_bytes = P.MAGIC, P.VERSION, P.PAGE_BYTES, P.UPDATE_BYTES
        h.producer_pid, h.producer_created_lo, h.producer_created_hi, h.generation = 123, 456, 0, 2
        self.closed, self.wakes, self.busy = [], 0, False

    def OpenFileMappingW(self, *_): return 1
    def MapViewOfFile(self, *_): return self.address
    def CreateMutexW(self, *_): return 2
    def CreateEventW(self, *_): return 3
    def WaitForSingleObject(self, *_): return 258 if self.busy else 0
    def ReleaseMutex(self, *_): return True
    def UnmapViewOfFile(self, handle): self.closed.append(handle); return True
    def CloseHandle(self, handle): self.closed.append(handle); return True
    def SetEvent(self, *_): self.wakes += 1; return True


@pytest.fixture
def kernel(monkeypatch):
    value = Kernel()
    monkeypatch.setattr(module, "_kernel", lambda: value)
    monkeypatch.setattr(module, "_process_creation", lambda *_: (456, 0))
    return value


def endpoint():
    return module.LiveEndpoint(123, 456, 0, 2, 888, 999, 0, 42, 3, 4, 10_000_000)


def request(kernel):
    return P.Update.from_buffer_copy(C.string_at(kernel.address + 256, P.UPDATE_BYTES))


def test_receiver_mapping_uses_actual_owner_and_withdraws_before_closing(kernel):
    inbox = NativeInbox(b"r" * 16)
    channel = module.LiveChannel(endpoint())
    channel.write(inbox.snapshot(now_ns=1))
    value = request(kernel)
    assert value.enabled and value.owner.pid == 888 and value.owner.token == 42
    assert value.owner.model_id == 0  # local native worker alone chooses ROM/model
    revision = value.revision
    channel.close()
    assert request(kernel).enabled and request(kernel).revision == revision
    assert not any(peer.present for peer in request(kernel).peers)
    assert len(kernel.closed) == 4 and kernel.wakes == 2


def test_recoverable_mapping_close_does_not_consume_a_new_inbox_control_revision(kernel):
    inbox = NativeInbox(b"r" * 16)
    inbox.admit("friend", b"s" * 16, name="Friend", hue=130)
    packet = encode_pose(frame(), participant_incarnation=b"s" * 16, source_generation=1,
                         elapsed_ns=1, continuity=1, rom_layout=1, rom_generation=3)
    inbox.ingest("friend", packet, received_ns=1)
    view = inbox.snapshot(now_ns=1)
    channel = module.LiveChannel(endpoint())
    channel.write(view)
    assert request(kernel).peers[0].present
    channel.close()
    suspended = request(kernel)
    assert suspended.enabled and suspended.revision == view.revision
    assert suspended.peers[0].member and bytes(suspended.peers[0].incarnation) == b"s" * 16
    assert not suspended.peers[0].present and not suspended.peers[0].received_ns and not any(suspended.peers[0].packet)
    resumed = module.LiveChannel(endpoint())
    resumed.write(inbox.snapshot(now_ns=2))
    assert request(kernel).revision == suspended.revision
    inbox.revoke()
    departed = inbox.snapshot(now_ns=3)
    resumed.write(departed)
    resumed.close()
    assert not request(kernel).enabled and request(kernel).revision == departed.revision > view.revision


def test_receiver_rejects_changed_process_or_protocol_without_overwriting(kernel, monkeypatch):
    channel = module.LiveChannel(endpoint())
    before = C.string_at(kernel.address + 256, P.UPDATE_BYTES)
    kernel.header.generation = 3
    with pytest.raises(ProcessLookupError):
        channel.write(NativeInbox(b"r" * 16).snapshot(now_ns=1))
    assert C.string_at(kernel.address + 256, P.UPDATE_BYTES) == before
    channel.close()


def test_busy_mutex_is_bounded_and_does_not_release_an_unacquired_lock(kernel):
    channel = module.LiveChannel(endpoint())
    kernel.busy = True
    with pytest.raises(BlockingIOError):
        channel.write(NativeInbox(b"r" * 16).snapshot(now_ns=1))
    kernel.busy = False
    channel.close()
    assert kernel.wakes == 0


def test_failed_constructor_retires_only_its_owned_handles(kernel):
    kernel.header.version = P.VERSION + 1
    with pytest.raises(ProcessLookupError):
        module.LiveChannel(endpoint())
    assert len(kernel.closed) == 4


def acknowledge(kernel, value, *, state=1, reason=0):
    h = kernel.header
    h.state, h.reason, h.applied_revision = state, reason, value.revision
    h.receipt_room[:] = value.room
    h.receipt_owner = value.owner
    h.presentation_reason = 8
    h.eligible_mask, h.selected_mask, h.displayed_mask = 7, 3, 0
    h.updates, h.refusals, h.drops, h.presentations = 40, 2, 1, 0
    h.gl_major, h.gl_minor, h.ineligible_mask = 4, 3, 2


def test_scoped_receiver_receipt_is_immutable_and_does_not_claim_pixels(kernel):
    from dataclasses import FrozenInstanceError

    channel = module.LiveChannel(endpoint())
    view = NativeInbox(b"r" * 16).snapshot(now_ns=1)
    assert channel.write(view).state == "pending"
    acknowledge(kernel, request(kernel))
    receipt = channel.write(view)
    assert receipt.state == "applied" and receipt.presentation_reason == "ready"
    assert receipt.selected_mask == 3 and receipt.displayed_mask == receipt.presentations == 0
    assert receipt.updates == 40 and receipt.refusals == 2 and receipt.drops == 1
    assert (receipt.gl_major, receipt.gl_minor, receipt.ineligible_mask) == (4, 3, 2)
    with pytest.raises(FrozenInstanceError):
        receipt.state = "displayed"
    acknowledge(kernel, request(kernel), state=2, reason=5)
    refused = channel.write(view)
    assert refused.state == "refused" and refused.native_reason == 5
    acknowledge(kernel, request(kernel), state=0, reason=6)
    assert channel.write(view).state == "pending"
    channel.close()


@pytest.mark.parametrize("field", ["room", "revision", "token", "generation", "rom_generation", "configuration", "pid"])
def test_old_receiver_ack_cannot_cross_room_or_source_capability(kernel, field):
    channel = module.LiveChannel(endpoint())
    view = NativeInbox(b"r" * 16).snapshot(now_ns=1)
    channel.write(view)
    acknowledge(kernel, request(kernel))
    if field == "room":
        kernel.header.receipt_room[0] ^= 1
    elif field == "revision":
        kernel.header.applied_revision += 1
    else:
        setattr(kernel.header.receipt_owner, field, getattr(kernel.header.receipt_owner, field) + 1)
    assert channel.write(view) == module.ReceiverReceipt()
    channel.close()


def test_header_preserves_fixed_mapping_layout():
    assert P.VERSION == 2 and C.sizeof(P.Header) == 256 and P.PAGE_BYTES == 4096
    assert (P.Header.receipt_room.offset, P.Header.receipt_owner.offset,
            P.Header.presentation_reason.offset, P.Header.updates.offset, P.Header.reserved.offset) == (48, 64, 104, 120, 164)
    assert (P.Header.gl_major.offset, P.Header.gl_minor.offset, P.Header.ineligible_mask.offset) == (152, 156, 160)
