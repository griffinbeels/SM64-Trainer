"""Recorder-owned immutable GPU configuration; never creates the old pixel ring."""

import ctypes as C
from dataclasses import dataclass
import os
import secrets
import struct

from sm64_events.replay.capturecontrol import _kernel
from sm64_events.replay.ownedclose import close_after_error, release_many

MAGIC, VERSION, PAGE_BYTES = b"SM64GPR1", 1, 512
NAMESPACE = "Local\\SM64Trainer.GpuRequest.v1."
_FIXED = struct.Struct("<8s8I5Q12I")
_FIELD = struct.Struct("<II")
assert _FIXED.size == 128


@dataclass(frozen=True, slots=True)
class RequestLimits:
    slots: int
    snapshot_budget: int
    rdram_bytes: int
    channel_budget: int
    packet_count: int
    packet_bytes: int
    pending_bytes: int
    pcm_bytes: int
    pcm_blocks: int
    max_age_ms: int
    encoder_duration: int


@dataclass(frozen=True, slots=True)
class RequestIdentity:
    """Exact identity prepared under the existing capture ownership mutex."""

    producer_pid: int
    producer_birth: int
    control_generation: int
    owner_pid: int
    owner_birth: int
    token: int
    nonce: bytes


def pack_request(status, token, created, table, limits, nonce, *, owner_pid=None):
    """Exact C gr_page layout; offsets refer to guarded original RDRAM storage."""
    owner_pid = os.getpid() if owner_pid is None else owner_pid
    validate_limits(limits)
    rows = tuple(table)
    if not 0 < len(rows) <= 16:
        raise ValueError("bounded address table required")
    return _pack_validated(status, token, created, rows, limits, nonce, owner_pid)


def validate_limits(limits) -> None:
    """The native request page's fixed caps, shared with the settings
    coherence test so a shipped default can never exceed what the wrapper
    admits (2026-09-16: a 9 MiB pending budget refused every request)."""
    if not isinstance(limits, RequestLimits):
        raise TypeError("explicit GPU request limits required")
    for field in limits.__dataclass_fields__:
        if type(getattr(limits, field)) is not int or getattr(limits, field) <= 0:
            raise ValueError("positive integer GPU request limits required")
    if (
        limits.slots > 8
        or limits.snapshot_budget > (1 << 40)
        or limits.rdram_bytes > 8 << 20
        or not 4096 <= limits.channel_budget <= 64 << 20
        or limits.packet_count > 8
        or limits.packet_bytes > 4 << 20
        or not limits.packet_bytes
        <= limits.pending_bytes
        <= limits.packet_count * limits.packet_bytes
        or limits.pcm_bytes > 64 << 20
        or limits.pcm_blocks > 4096
        or limits.max_age_ms > 60000
        or limits.encoder_duration > 90000
    ):
        raise ValueError("GPU request limits exceed fixed caps")


def _pack_validated(status, token, created, rows, limits, nonce, owner_pid):
    if (
        type(nonce) is not bytes
        or len(nonce) != 16
        or not any(nonce)
        or any(
            type(v) is not int or not 0 < v <= 0xFFFFFFFF
            for v in (status.producer_pid, owner_pid, status.generation, token)
        )
    ):
        raise ValueError("valid native request identity required")
    birth = status.producer_created_lo | (status.producer_created_hi << 32)
    owner_birth = created[0] | (created[1] << 32)
    if not birth or not owner_birth:
        raise ValueError("native process creation identity required")
    lo, hi = struct.unpack("<QQ", nonce)
    page = bytearray(PAGE_BYTES)
    _FIXED.pack_into(
        page,
        0,
        MAGIC,
        VERSION,
        PAGE_BYTES,
        2,
        status.producer_pid,
        owner_pid,
        status.generation,
        token,
        0,
        birth,
        owner_birth,
        lo,
        hi,
        limits.snapshot_budget,
        limits.slots,
        limits.rdram_bytes,
        len(rows),
        limits.channel_budget,
        limits.packet_count,
        limits.packet_bytes,
        limits.pending_bytes,
        limits.pcm_bytes,
        limits.pcm_blocks,
        limits.max_age_ms,
        limits.encoder_duration,
        0,
    )
    for index, row in enumerate(rows):
        if len(row) != 3:
            raise ValueError("table_for(layout) rows required")
        _, offset, length = row
        if (
            type(offset) is not int
            or type(length) is not int
            or offset < 0
            or not 0 < length <= 128
            or offset & 3
            or length & 3
            or offset + length > limits.rdram_bytes
        ):
            raise ValueError("invalid bounded RDRAM row")
        _FIELD.pack_into(page, 128 + index * 8, offset, length)
    return bytes(page)


class GpuRequest:
    """Own config mapping and lease together; publish is authorized only by recorder."""

    def __init__(self):
        self._k = _kernel()
        self._k.CreateFileMappingW.restype = C.c_void_p
        self._k.CreateFileMappingW.argtypes = [
            C.c_void_p,
            C.c_void_p,
            C.c_uint32,
            C.c_uint32,
            C.c_uint32,
            C.c_wchar_p,
        ]
        self._map = self._view = self.lease = None
        self.name = ""
        self.nonce = secrets.token_bytes(16)
        self.identity = None

    @classmethod
    def acquire(cls, control, table, limits, *, validate=None):
        result = cls()
        try:

            def prepare(status, token, created):
                if validate is not None:
                    validate(status)
                page = pack_request(status, token, created, table, limits, result.nonce)
                result.name = f"{NAMESPACE}{status.producer_pid}.{os.getpid()}.{token}"
                C.set_last_error(0)
                result._map = result._k.CreateFileMappingW(
                    C.c_void_p(-1), None, 4, 0, PAGE_BYTES, result.name
                )
                if not result._map:
                    raise C.WinError(C.get_last_error())
                if C.get_last_error() == 183:
                    raise RuntimeError("GPU request name already owned")
                result._view = result._k.MapViewOfFile(result._map, 2, 0, 0, PAGE_BYTES)
                if not result._view:
                    raise C.WinError(C.get_last_error())
                # The lease mutex release publishes these immutable bytes.
                C.c_uint32.from_address(result._view + 16).value = 1
                C.memmove(result._view, page[:16], 16)
                C.memmove(result._view + 20, page[20:], PAGE_BYTES - 20)
                C.c_uint32.from_address(result._view + 16).value = 2
                result.identity = RequestIdentity(
                    status.producer_pid,
                    status.producer_created_lo | (status.producer_created_hi << 32),
                    status.generation,
                    os.getpid(),
                    created[0] | (created[1] << 32),
                    token,
                    result.nonce,
                )

            result.lease = control.acquire(prepare=prepare)
            return result
        except BaseException:
            # acquire() publishes before releasing/notifying its mutex. If
            # that final step failed, its return value never reached us, but
            # this request must still disable its lease before freeing config.
            result._retain_published_lease(control)
            close_after_error(result)
            raise

    def _retain_published_lease(self, control):
        lease = getattr(control, "_lease", None)
        identity = self.identity
        if (self.lease is None and lease is not None and identity is not None
                and (lease.producer_pid, lease.generation, lease.token) == (
                    identity.producer_pid, identity.control_generation, identity.token)):
            self.lease = lease

    def renew(self):
        return self.lease is not None and self.lease.renew()

    def close(self):
        # A failed disable RETAINS the page and the lease: the demand
        # supervisor retries this close (gpudemand._retire) and the request
        # must still own what it will release then. tests/test_gpu_owner_recovery.py.
        if self.lease is not None:
            self.lease.close()
            self.lease = None
        release_many(self, (("_view", "UnmapViewOfFile"), ("_map", "CloseHandle")))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
