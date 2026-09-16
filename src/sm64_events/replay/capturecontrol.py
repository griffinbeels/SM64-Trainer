"""Small, independently discoverable native capture control channel.

Discovery does not create a pixel ring or request recording. Only the recorder
owner may acquire a lease. The stage-one native candidate advertises passive
support and explicitly refuses pixel capture until its boundary is proven.
This module intentionally does not fall back to desktop-frame inference.
"""
from __future__ import annotations

import ctypes as C
import os
import secrets
import struct
import threading
from contextlib import contextmanager
from dataclasses import dataclass

from sm64_events.replay.ownedclose import ProcessHandle, close_after_error, release_many

MAGIC = b"SM64CTL1"
VERSION = 1
PAGE_BYTES = 4096
SUFFIX = "_control_v1"
REQUEST_OFFSET = 128
LEASE_MS = 3000
CLOSED, PASSIVE, PREPARING, UNAVAILABLE, ACTIVE = range(5)
CAP_PASSIVE, CAP_GPU = 1, 2
NO_BACKEND, OWNER_GONE, LEASE_EXPIRED, PROTOCOL_ERROR = range(1, 5)
FRESH_REQUEST = 7  # CONTROL_FRESH_REQUEST in plugin/gfxwrap/control.h.
_U32 = struct.Struct("<I")
_REQUEST = struct.Struct("<8I")
_STATUS = struct.Struct("<8s13I")


def _kernel():
    k = C.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "OpenFileMappingW": (C.c_void_p, [C.c_uint32, C.c_int, C.c_wchar_p]),
        "MapViewOfFile": (C.c_void_p, [C.c_void_p, C.c_uint32, C.c_uint32,
                                     C.c_uint32, C.c_size_t]),
        "UnmapViewOfFile": (C.c_int, [C.c_void_p]),
        "CreateEventW": (C.c_void_p, [C.c_void_p, C.c_int, C.c_int, C.c_wchar_p]),
        "CreateMutexW": (C.c_void_p, [C.c_void_p, C.c_int, C.c_wchar_p]),
        "ReleaseMutex": (C.c_int, [C.c_void_p]),
        "WaitForSingleObject": (C.c_uint32, [C.c_void_p, C.c_uint32]),
        "SetEvent": (C.c_int, [C.c_void_p]),
        "CloseHandle": (C.c_int, [C.c_void_p]),
        "OpenProcess": (C.c_void_p, [C.c_uint32, C.c_int, C.c_uint32]),
        "GetProcessTimes": (C.c_int, [C.c_void_p] + [C.c_void_p] * 4),
    }
    for name, (result, arguments) in signatures.items():
        f = getattr(k, name)
        f.restype, f.argtypes = result, arguments
    return k


def _process_creation(k, pid: int) -> tuple[int, int] | None:
    handle = k.OpenProcess(0x100000 | 0x1000, False, pid)
    if not handle:
        error = C.get_last_error()
        if error in (87, 1168):  # Invalid/absent PID; access failure is not death proof.
            return None
        raise OSError(error, "cannot establish capture process identity")
    owner = ProcessHandle(k, handle)
    try:
        values = [C.c_uint64() for _ in range(4)]
        outcome = k.WaitForSingleObject(handle, 0)
        if outcome == 0:
            return None
        if outcome != 258:
            raise OSError(C.get_last_error(), "cannot establish capture process liveness")
        if not k.GetProcessTimes(handle, *(C.byref(v) for v in values)):
            raise OSError(C.get_last_error(), "cannot establish capture process birth")
        return values[0].value & 0xFFFFFFFF, values[0].value >> 32
    finally:
        close_after_error(owner)


@dataclass(frozen=True)
class CaptureStatus:
    producer_pid: int
    generation: int
    state: int
    reason: int
    ack_token: int
    ack_heartbeat: int
    capabilities: int
    rom_open: bool
    producer_created_lo: int
    producer_created_hi: int
    build_id: str


class CaptureControl:
    """Read-only discovery until acquire() explicitly requests ownership."""

    def __init__(self, name: str = "sm64_trainer_gfx_v1"):
        self._k = _kernel()
        self._map = self._view = self._wake = self._mutex = None
        self._lease = None
        self._producer = None
        self._write_locked = False
        self._write_thread = None
        self.name = name + SUFFIX
        try:
            self._map = self._k.OpenFileMappingW(0xF001F, False, self.name)
            if not self._map:
                raise FileNotFoundError(C.get_last_error(), "capture control is not present")
            self._view = self._k.MapViewOfFile(self._map, 0xF001F, 0, 0, PAGE_BYTES)
            if not self._view:
                raise C.WinError(C.get_last_error())
            self.status()
        except BaseException:
            close_after_error(self)
            raise

    def status(self) -> CaptureStatus:
        if not self._view:
            raise RuntimeError("capture control is closed")
        for _ in range(4):
            before = _U32.unpack(C.string_at(self._view + 16, 4))[0]
            if before & 1:
                continue
            values = _STATUS.unpack(C.string_at(self._view, _STATUS.size))
            build_id = C.string_at(self._view + 256, 80).split(b"\0", 1)[0].decode("ascii", "replace")
            after = _U32.unpack(C.string_at(self._view + 16, 4))[0]
            if before != after:
                continue
            if values[:3] != (MAGIC, VERSION, PAGE_BYTES):
                raise RuntimeError("capture control protocol is not ready or supported")
            status = CaptureStatus(*values[4:11], bool(values[11]), *values[12:], build_id)
            if _process_creation(self._k, status.producer_pid) != (
                    status.producer_created_lo, status.producer_created_hi):
                from dataclasses import replace
                status = replace(status, state=CLOSED, reason=OWNER_GONE)
            return status
        raise BlockingIOError("capture control status is being updated")

    @contextmanager
    def _write_lock(self):
        if not self._view:
            raise RuntimeError("capture control is closed")
        if not self._wake:
            self._wake = self._k.CreateEventW(None, False, False, self.name + "_wake")
        if not self._mutex:
            self._mutex = self._k.CreateMutexW(None, False, self.name + "_client")
        if not self._wake or not self._mutex:
            raise C.WinError(C.get_last_error())
        if self._write_locked:
            if self._write_thread is not threading.current_thread():
                raise RuntimeError("capture command mutex still belongs to another thread")
        else:
            outcome = self._k.WaitForSingleObject(self._mutex, 1000)
            if outcome not in (0, 0x80):
                raise TimeoutError("capture owner command is busy")
            self._write_locked = True
            self._write_thread = threading.current_thread()
        try:
            yield
        finally:
            self._release_command_lock()
            # Notify after publishing/releasing. Waking a zero-timeout reader
            # while still holding the mutex can lose the only command wake.
            if not self._k.SetEvent(self._wake):
                raise C.WinError(C.get_last_error())

    def _release_command_lock(self):
        if self._write_locked:
            if self._write_thread is not threading.current_thread():
                raise RuntimeError("capture command mutex close belongs to its owner thread")
            if not self._k.ReleaseMutex(self._mutex):
                raise OSError(C.get_last_error(), "capture command mutex remains owned")
            self._write_locked = False
            self._write_thread = None

    def _request(self):
        return _REQUEST.unpack(C.string_at(self._view + REQUEST_OFFSET, _REQUEST.size))

    def _publish(self, request):
        # Client writers serialize; the background native reader only tries it.
        address = self._view + REQUEST_OFFSET
        odd = ((request[0] + 1) | 1) & 0xFFFFFFFF
        C.c_uint32.from_address(address).value = odd
        payload = _REQUEST.pack(odd, *request[1:])
        C.memmove(address + 4, payload[4:], len(payload) - 4)
        # ReleaseMutex publishes the command. The native worker acquires this
        # same mutex with timeout zero, entirely outside the graphics thread.
        C.c_uint32.from_address(address).value = (odd + 1) & 0xFFFFFFFF

    def acquire(self, *, prepare=None) -> CaptureLease:
        """Recorder-owned operation; viewers must only call status()."""
        with self._write_lock():
            status = self.status()
            if status.state == CLOSED or not _process_creation(self._k, status.producer_pid):
                raise RuntimeError("capture producer is not running")
            old = self._request()
            if old[7] and old[5] == status.generation and old[2] != os.getpid():
                if _process_creation(self._k, old[2]) == (old[3], old[4]):
                    raise RuntimeError("capture is owned by another process")
            created = _process_creation(self._k, os.getpid())
            if created is None:
                raise RuntimeError("cannot establish capture owner identity")
            token = secrets.randbelow(0xFFFFFFFF) + 1
            # Publish dependent immutable configuration before native enable.
            # Failure leaves the preceding owner command unchanged.
            if prepare is not None:
                prepare(status, token, created)
            self._hold_producer(status)
            self._publish((old[0], token, os.getpid(), *created, status.generation, 1, 1))
            lease = CaptureLease(self, status.producer_pid, status.generation, token)
            self._lease = lease
            return lease

    def _hold_producer(self, status):
        if not self._producer:
            self._producer = self._k.OpenProcess(0x101000, False, status.producer_pid)
            if not self._producer:
                raise C.WinError(C.get_last_error())
        values = [C.c_uint64() for _ in range(4)]
        if not self._k.GetProcessTimes(self._producer, *(C.byref(v) for v in values)):
            raise C.WinError(C.get_last_error())
        expected = status.producer_created_lo | (status.producer_created_hi << 32)
        outcome = self._k.WaitForSingleObject(self._producer, 0)
        if outcome not in (0, 258):
            raise OSError(C.get_last_error(), "cannot establish capture producer liveness")
        if values[0].value != expected or outcome == 0:
            raise ProcessLookupError("capture producer replaced during acquisition")

    def producer_gone(self):
        """Only a signaled handle proves this exact process lifetime ended."""
        return bool(self._producer) and self._k.WaitForSingleObject(self._producer, 0) == 0

    def close(self):
        if self._lease is not None:
            self._lease.close()
            self._lease = None
        self._release_command_lock()
        release_many(self, (("_view", "UnmapViewOfFile"),
                            ("_map", "CloseHandle"), ("_wake", "CloseHandle"),
                            ("_mutex", "CloseHandle"), ("_producer", "CloseHandle")))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        close_after_error(self)


class CaptureLease:
    def __init__(self, control, producer_pid, generation, token):
        self.control = control
        self.producer_pid, self.generation, self.token = producer_pid, generation, token
        self._owned = True

    def _command(self, enabled):
        if not self._owned:
            return False
        if not enabled and self.control.producer_gone():
            return False  # Process death does not need a command to its old page.
        if not self.control._view:
            raise RuntimeError("owned capture lease lost its control view")
        with self.control._write_lock():
            status = self.control.status()
            request = self.control._request()
            if status.state == CLOSED or (status.producer_pid, status.generation, request[1]) != (
                    self.producer_pid, self.generation, self.token):
                return False
            self.control._publish((*request[:6], (request[6] + 1) & 0xFFFFFFFF, int(enabled)))
            return True

    def renew(self):
        return self._command(True)

    def transfer(self) -> CaptureLease:
        """Move ownership from acquisition to the selected source with no disable."""
        if not self._owned:
            raise RuntimeError("capture lease already transferred or closed")
        successor = CaptureLease(self.control, self.producer_pid, self.generation, self.token)
        self._owned = False
        self.control._lease = successor
        return successor

    def close(self):
        self._command(False)
        self._owned = False
