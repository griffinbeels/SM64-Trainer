"""Receiver-only local mapping, bound to the existing captured-source lease.

The room worker owns this handle and copies fixed validated inbox values. It
does not consume captured poses, open a model or call the renderer. No remote
value selects this endpoint. Losing the captured-source lease withdraws it.
"""
from __future__ import annotations

import ctypes as C
from contextlib import contextmanager
from dataclasses import dataclass

from sm64_events.multiplayer import native_protocol as P
from sm64_events.multiplayer.native_inbox import NativeView, bind_owner
from sm64_events.replay.capturecontrol import _kernel, _process_creation
from sm64_events.replay.ownedclose import close_after_error, release_many


@dataclass(frozen=True, repr=False)
class LiveEndpoint:
    producer_pid: int
    producer_created_lo: int
    producer_created_hi: int
    generation: int
    owner_pid: int
    owner_created_lo: int
    owner_created_hi: int
    token: int
    rom_generation: int
    configuration: int
    qpc_frequency: int

    def owner(self) -> P.LocalOwner:
        return P.LocalOwner(self.owner_pid, self.owner_created_lo, self.owner_created_hi,
            self.generation, self.rom_generation, self.configuration, self.token, 0)


@dataclass(frozen=True)
class ReceiverReceipt:
    """Bounded native observation; applied means accepted, not visible pixels."""
    state: str = "pending"
    native_reason: int = 0
    presentation_reason: str = "waiting"
    eligible_mask: int = 0
    selected_mask: int = 0
    displayed_mask: int = 0
    updates: int = 0
    refusals: int = 0
    drops: int = 0
    presentations: int = 0
    gl_major: int = 0
    gl_minor: int = 0
    ineligible_mask: int = 0


def _receipt(header: P.Header, value: P.Update) -> ReceiverReceipt:
    if (header.applied_revision != value.revision or bytes(header.receipt_room) != bytes(value.room)
            or bytes(header.receipt_owner) != bytes(value.owner)):
        return ReceiverReceipt()
    if header.state not in (0, 1, 2) or header.presentation_reason >= len(P.PRESENTATION_REASONS):
        return ReceiverReceipt()
    mask = (1 << P.MAX_PEERS) - 1
    return ReceiverReceipt(("pending", "applied", "refused")[header.state], header.reason,
        P.PRESENTATION_REASONS[header.presentation_reason], header.eligible_mask & mask,
        header.selected_mask & mask, header.displayed_mask & mask,
        header.updates, header.refusals, header.drops, header.presentations,
        header.gl_major, header.gl_minor, header.ineligible_mask & 63)


class LiveChannel:
    def __init__(self, endpoint: LiveEndpoint):
        self.endpoint, self._k = endpoint, _kernel()
        self._map = self._view = self._mutex = self._wake = None
        self._last = None
        name = P.PREFIX + str(endpoint.producer_pid)
        try:
            self._map = self._k.OpenFileMappingW(0xF001F, False, name)
            if not self._map:
                raise FileNotFoundError("Live ghost support is unavailable in this renderer.")
            self._view = self._k.MapViewOfFile(self._map, 0xF001F, 0, 0, P.PAGE_BYTES)
            self._mutex = self._k.CreateMutexW(None, False, name + "_mutex")
            self._wake = self._k.CreateEventW(None, False, False, name + "_wake")
            if not self._view or not self._mutex or not self._wake:
                raise C.WinError(C.get_last_error())
            with self._locked():
                self._checked_header()
        except BaseException:
            close_after_error(self)
            raise

    @contextmanager
    def _locked(self):
        if not self._view or not self._mutex:
            raise RuntimeError("Live ghost channel is closed.")
        result = self._k.WaitForSingleObject(self._mutex, 50)
        if result not in (0, 0x80):
            raise BlockingIOError("Live ghost channel is busy.")
        try:
            yield
        finally:
            if not self._k.ReleaseMutex(self._mutex):
                raise C.WinError(C.get_last_error())

    def _checked_header(self):
        e = self.endpoint
        if _process_creation(self._k, e.producer_pid) != (e.producer_created_lo, e.producer_created_hi):
            raise ProcessLookupError("Live ghost renderer was replaced.")
        value = P.Header.from_buffer_copy(C.string_at(self._view, P.REQUEST_OFFSET))
        expected = (P.MAGIC, P.VERSION, P.PAGE_BYTES, P.UPDATE_BYTES,
                    e.producer_pid, e.producer_created_lo, e.producer_created_hi, e.generation)
        actual = (value.magic, value.version, value.bytes, value.request_bytes,
                  value.producer_pid, value.producer_created_lo, value.producer_created_hi, value.generation)
        if actual != expected or any(value.reserved):
            raise ProcessLookupError("Live ghost receiver protocol changed.")
        return value

    def write(self, view: NativeView) -> ReceiverReceipt:
        if type(view) is not NativeView or len(view.wire) != P.UPDATE_BYTES:
            raise ValueError("Live ghost update is invalid.")
        value = bind_owner(view, self.endpoint.owner())
        with self._locked():
            header = self._checked_header()
            C.memmove(self._view + P.REQUEST_OFFSET, bytes(value), P.UPDATE_BYTES)
            if not self._k.SetEvent(self._wake):
                raise C.WinError(C.get_last_error())
            self._last = value
            return _receipt(header, value)

    def close(self):
        """Suspend this local connection; room departure writes its revoked view.

        Closing a mapping is also used for recoverable IO/ROM retargets. Do
        not invent a higher control revision or permanently retire the room.
        Same-revision absence clears native display while retaining its fences.
        """
        if self._view and self._last is not None:
            with self._locked():
                try:
                    self._checked_header()
                except ProcessLookupError:
                    pass
                else:
                    value = P.Update.from_buffer_copy(bytes(self._last))
                    for peer in value.peers:
                        peer.present = 0
                        peer.received_ns = 0
                        peer.packet[:] = bytes(len(peer.packet))
                    C.memmove(self._view + P.REQUEST_OFFSET, bytes(value), P.UPDATE_BYTES)
                    if not self._k.SetEvent(self._wake):
                        raise C.WinError(C.get_last_error())
            self._last = None
        release_many(self, (("_view", "UnmapViewOfFile"), ("_map", "CloseHandle"),
                            ("_mutex", "CloseHandle"), ("_wake", "CloseHandle")))
