"""Worker-owned GPU metadata channel; no raw pictures and no implicit GPU release.

Wire V1 is independent of capture-control V1. This reader is qualified only for
CPython on Windows x86/x64: naturally aligned uint32 loads/stores are atomic and
ordered on normal shared memory. The ctypes copies and publication accesses are
separate interpreter operations. Native uses Interlocked publication barriers.
No process-wide flush, spin loop or renderer-thread call belongs here.
"""

from __future__ import annotations

import ctypes as C
from dataclasses import dataclass
import os
import platform
import sys
import threading

from sm64_events.replay.gpudiagnostics import native_reason
from sm64_events.replay.capturecontrol import _process_creation
from sm64_events.replay.ownedclose import close_after_error, release, release_many

U8, U16, U32, U64, I32, I64 = (
    C.c_uint8,
    C.c_uint16,
    C.c_uint32,
    C.c_uint64,
    C.c_int32,
    C.c_int64,
)
VERSION, HEADER_BYTES, MAX_OFFERS, STAMP_BYTES = 1, 2048, 8, 2048
MAX_MAP, MAX_PACKET, MAX_DIMENSION = 64 * 1024 * 1024, 4 * 1024 * 1024, 8192
MAGIC = b"SM64GPC1"
NAMESPACE = "Local\\SM64Trainer.GpuChannel.v1."
PREPARING, ACTIVE, CLOSED, FAULT = 1, 2, 3, 4
SELECTED, COALESCED, SUPPRESSED, FAILED = 1, 2, 3, 4


class ChannelStopped(RuntimeError):
    """Retain native terminal facts; CLOSED by itself is not a clean stop."""

    def __init__(self, state, reason):
        self.state, self.reason = state, reason
        super().__init__(
            f"producer channel stopped: state={state} reason={reason} "
            f"({native_reason(reason, channel=True)})"
        )

    @property
    def cancelled(self):
        # Untagged zero supports R31. Tagged delivery reasons are from
        # gpu_channel.h: CONTROL_OWNER_GONE, GD_CANCELLED. A supervisor's own
        # explicit cancellation is ALSO required before these are benign.
        return self.state == CLOSED and self.reason in (0, 0x20000002, 0x20002710)


class Wire(C.LittleEndianStructure):
    _pack_ = 8


class Field(Wire):
    _fields_ = [("offset", U32), ("length", U32)]


class Header(Wire):
    _fields_ = (
        [("seq", U32), ("version", U32), ("magic", U8 * 8)]
        + [
            (n, U32)
            for n in ("header_bytes", "total_bytes", "producer_pid", "owner_pid")
        ]
        + [(n, U64) for n in ("producer_birth", "owner_birth", "nonce_lo", "nonce_hi")]
        + [("epoch", U32), ("generation", U32), ("qpc_frequency", U64)]
        + [
            (n, U32)
            for n in (
                "width",
                "height",
                "even_width",
                "even_height",
                "format",
                "sample_stride",
            )
        ]
        + [("luid_low", U32), ("luid_high", I32)]
        + [
            (n, U32)
            for n in (
                "offer_count",
                "bridge_count",
                "table_count",
                "stamp_capacity",
                "sample_bytes",
                "sample_capacity",
                "packet_count",
                "packet_bytes",
                "pending_bytes",
                "ram_budget",
                "offer_offset",
                "decision_offset",
                "bridge_offset",
                "receipt_offset",
                "payload_offset",
                "payload_stride",
                "status_offset",
                "status_bytes",
                "client_offset",
                "client_bytes",
            )
        ]
        + [
            ("table", Field * 16),
            ("texture_names", (U16 * 128) * 2),
            ("publication_offset", U32),
            ("publication_bytes", U32),
            ("reserved", U8 * 1208),
        ]
    )


class Status(Wire):
    _fields_ = (
        [(n, U32) for n in ("seq", "state", "reason", "reserved0")]
        + [("nonce_lo", U64), ("nonce_hi", U64), ("epoch", U32), ("generation", U32)]
        + [
            (n, U64)
            for n in (
                "heartbeat_qpc",
                "frontier_qpc",
                "frontier_occurrence",
                "frontier_serial",
            )
        ]
        + [("reserved", U8 * 184)]
    )


class OfferWire(Wire):
    _fields_ = (
        [
            ("seq", U32),
            ("kind", U32),
            ("token", U64),
            ("occurrence", U64),
            ("epoch", U32),
            ("generation", U32),
            ("list_qpc", I64),
            ("boundary_qpc", I64),
        ]
        + [
            (n, U32)
            for n in (
                "width",
                "height",
                "stamp_bytes",
                "sample_bytes",
                "table_count",
                "vi_origin",
                "lists_since",
                "outcome",
                "stamp_offset",
                "sample_offset",
            )
        ]
        + [
            ("lengths", U32 * 16),
            ("nonce_lo", U64),
            ("nonce_hi", U64),
            ("reserved", U8 * 88),
        ]
    )


class Decision(Wire):
    _fields_ = (
        [("seq", U32), ("kind", U32)]
        + [(n, U64) for n in ("token", "occurrence", "nonce_lo", "nonce_hi")]
        + [("epoch", U32), ("generation", U32)]
        + [
            (n, U64)
            for n in (
                "encode_serial",
                "pts",
                "nominal_duration",
                "retained_occurrence",
                "retained_serial",
            )
        ]
        + [("flags", U32), ("reason", U32), ("reserved", U8 * 32)]
    )


class BridgeWire(Wire):
    _fields_ = (
        [("seq", U32), ("ready", U32)]
        + [
            (n, U64)
            for n in (
                "token",
                "occurrence",
                "encode_serial",
                "pts",
                "nominal_duration",
                "nonce_lo",
                "nonce_hi",
            )
        ]
        + [(n, U32) for n in ("epoch", "generation", "texture_index", "flags")]
        + [("reserved", U8 * 48)]
    )


class Receipt(Wire):
    _fields_ = (
        [("seq", U32), ("accepted", U32)]
        + [
            (n, U64)
            for n in ("token", "occurrence", "encode_serial", "nonce_lo", "nonce_hi")
        ]
        + [(n, U32) for n in ("epoch", "generation", "texture_index", "reason")]
        + [("reserved", U8 * 64)]
    )


class ClientWire(Wire):
    _fields_ = [
        ("seq", U32),
        ("state", U32),
        ("nonce_lo", U64),
        ("nonce_hi", U64),
        ("epoch", U32),
        ("generation", U32),
        ("owner_pid", U32),
        ("reserved0", U32),
        ("owner_birth", U64),
        ("reserved", U8 * 80),
    ]


class Publication(Wire):
    _fields_ = [
        ("seq", U32),
        ("reserved0", U32),
        ("last_token", U64),
        ("last_bridge_token", U64),
        ("reserved", U8 * 104),
    ]


assert C.sizeof(Publication) == 128
assert C.sizeof(ClientWire) == 128
assert [
    C.sizeof(t) for t in (Header, Status, OfferWire, Decision, BridgeWire, Receipt)
] == [2048, 256, 256, 128, 128, 128]
assert Header.table.offset == 192 and Header.texture_names.offset == 320


class ProtocolError(ValueError):
    pass


def _require(test, message):
    if not test:
        raise ProtocolError(message)


def _text(value):
    raw = bytes(value)
    units = list(value)
    _require(0 in units and units[0] != 0, "unterminated or empty texture name")
    end = units.index(0)
    _require(not any(units[end:]), "texture name padding")
    try:
        return raw[: end * 2].decode("utf-16-le", "strict")
    except UnicodeError as exc:
        raise ProtocolError("invalid UTF16 texture name") from exc


def validate_header(h: Header, mapped_bytes: int):
    _require(
        h.seq
        and not h.seq & 1
        and bytes(h.magic) == MAGIC
        and h.version == VERSION
        and h.header_bytes == HEADER_BYTES,
        "unsupported or incomplete header",
    )
    _require(
        h.producer_pid
        and h.owner_pid
        and h.producer_birth
        and h.owner_birth
        and (h.nonce_lo or h.nonce_hi)
        and h.epoch
        and h.generation
        and h.qpc_frequency,
        "missing identity",
    )
    _require(
        2 <= h.width <= MAX_DIMENSION
        and 2 <= h.height <= MAX_DIMENSION
        and h.even_width == h.width & ~1
        and h.even_height == h.height & ~1,
        "dimensions",
    )
    _require(
        h.format == 1 and h.sample_stride == 8 and h.stamp_capacity == STAMP_BYTES,
        "sample format",
    )
    _require(
        1 <= h.offer_count <= MAX_OFFERS
        and h.bridge_count == 2
        and 1 <= h.table_count <= 16,
        "slot/table count",
    )
    _require(
        1 <= h.packet_count <= MAX_OFFERS
        and 0 < h.packet_bytes <= MAX_PACKET
        and h.packet_bytes <= h.pending_bytes <= h.packet_count * h.packet_bytes,
        "packet budget",
    )
    _require(not any(h.reserved), "reserved header bytes")
    total_stamp = 0
    for i, field in enumerate(h.table):
        if i < h.table_count:
            _require(
                field.length and field.offset + field.length <= 2**32, "RAM table range"
            )
            total_stamp += field.length
        else:
            _require(not field.offset and not field.length, "unused table field")
    _require(total_stamp <= STAMP_BYTES, "stamp budget")
    names = tuple(_text(n) for n in h.texture_names)
    _require(names[0] != names[1], "duplicate texture names")
    sample = ((h.width + 7) // 8) * ((h.height + 7) // 8) * 4
    _require(h.sample_bytes == h.sample_capacity == sample, "sample capacity")
    expected = {
        "status_offset": HEADER_BYTES,
        "status_bytes": 256,
        "offer_offset": HEADER_BYTES + 256,
        "decision_offset": HEADER_BYTES + 256 + h.offer_count * 256,
        "bridge_offset": HEADER_BYTES + 256 + h.offer_count * 384,
        "receipt_offset": HEADER_BYTES + 256 + h.offer_count * 384 + 256,
        "client_offset": HEADER_BYTES + 256 + h.offer_count * 384 + 512,
        "client_bytes": 128,
        "publication_offset": HEADER_BYTES + 256 + h.offer_count * 384 + 640,
        "publication_bytes": 128,
        "payload_offset": HEADER_BYTES + 256 + h.offer_count * 384 + 768,
        "payload_stride": (STAMP_BYTES + sample + 63) & ~63,
    }
    for key, value in expected.items():
        _require(getattr(h, key) == value, "noncanonical offset: " + key)
    total = h.payload_offset + h.offer_count * h.payload_stride
    _require(
        h.total_bytes == total <= h.ram_budget <= MAX_MAP and total <= mapped_bytes,
        "mapping budget",
    )
    return names


def _kernel():
    k = C.WinDLL("kernel32", use_last_error=True)
    specs = {
        "OpenFileMappingW": (C.c_void_p, [U32, C.c_int, C.c_wchar_p]),
        "MapViewOfFile": (C.c_void_p, [C.c_void_p, U32, U32, U32, C.c_size_t]),
        "UnmapViewOfFile": (C.c_int, [C.c_void_p]),
        "CloseHandle": (C.c_int, [C.c_void_p]),
        "OpenProcess": (C.c_void_p, [U32, C.c_int, U32]),
        "GetProcessTimes": (C.c_int, [C.c_void_p] + [C.c_void_p] * 4),
        "WaitForSingleObject": (U32, [C.c_void_p, U32]),
        "CreateMutexW": (C.c_void_p, [C.c_void_p, C.c_int, C.c_wchar_p]),
        "ReleaseMutex": (C.c_int, [C.c_void_p]),
        "OpenEventW": (C.c_void_p, [U32, C.c_int, C.c_wchar_p]),
        "SetEvent": (C.c_int, [C.c_void_p]),
    }
    for name, (result, args) in specs.items():
        getattr(k, name).restype, getattr(k, name).argtypes = result, args
    return k


def process_birth(pid):
    birth = _process_creation(_kernel(), pid)
    if birth is None:
        raise ProcessLookupError(pid)
    return birth[0] | (birth[1] << 32)


@dataclass(frozen=True, slots=True)
class Offer:
    slot: int
    token: int
    occurrence: int
    list_qpc: int
    boundary_qpc: int
    width: int
    height: int
    vi_origin: int
    lists_since: int
    outcome: int
    lengths: tuple[int, ...]
    stamp_bytes: bytes
    sample: bytes


@dataclass(frozen=True, slots=True)
class Bridge:
    texture_index: int
    token: int
    occurrence: int
    encode_serial: int
    pts: int
    nominal_duration: int
    force_idr: bool


class Client:
    """One serialized media-worker owner. No read or write waits for its peer.

    open and lifecycle may acquire OS handles; capture callbacks must never call
    this class. Producer identity must come from the existing verified owner.
    Event handles are exposed for the outer worker's bounded wait/deadline loop.
    """

    def __init__(
        self,
        *,
        nonce: tuple[int, int],
        epoch: int | None = None,
        generation: int,
        producer_pid: int,
        producer_birth: int,
    ):
        _require(
            sys.implementation.name == "cpython"
            and os.name == "nt"
            and platform.machine().lower()
            in {"amd64", "x86", "x86_64", "i386", "i686"},
            "unsupported publication platform",
        )
        self._thread = threading.current_thread()
        self._k = _kernel()
        self._view = self._map = self._mutex = self._process = self.wake_event = (
            self.result_event
        ) = None
        self._owns_mutex = False
        self._client_open = False
        self._pending = {}
        self._bridge_pending = {}
        self._offers_seen = [0] * MAX_OFFERS
        self._bridges_seen = [0] * 2
        self._last_occurrence = self._last_serial = self._last_token = (
            self.dispositioned_through
        ) = 0
        self._last_pts = None
        self._last_bridge_token = self._last_bridge_serial = 0
        lo, hi = nonce
        _require(
            type(lo) is int
            and type(hi) is int
            and 0 <= lo < 2**64
            and 0 <= hi < 2**64
            and (lo or hi),
            "nonce",
        )
        self.name = f"{NAMESPACE}{hi:016x}{lo:016x}"
        try:
            self._open(lo, hi, epoch, generation, producer_pid, producer_birth)
        except BaseException:
            close_after_error(self)
            raise

    def _open(self, lo, hi, epoch, generation, producer_pid, producer_birth):
        self._map = self._k.OpenFileMappingW(0xF001F, False, self.name)
        if not self._map:
            raise FileNotFoundError(self.name)
        # First map only the fixed header. Never trust peer lengths before validation.
        self._view = self._k.MapViewOfFile(self._map, 0xF001F, 0, 0, HEADER_BYTES)
        if not self._view:
            raise C.WinError(C.get_last_error())
        h = self._stable(0, Header)
        _require(h is not None, "torn header")
        self.texture_names = validate_header(h, MAX_MAP)
        _require(
            (h.nonce_lo, h.nonce_hi, h.generation) == (lo, hi, generation)
            and (epoch is None or h.epoch == epoch)
            and (h.producer_pid, h.producer_birth) == (producer_pid, producer_birth),
            "stale session",
        )
        _require(
            h.owner_pid == os.getpid() and h.owner_birth == process_birth(os.getpid()),
            "wrong client owner",
        )
        release(self, "_view", "UnmapViewOfFile")
        # Windows refuses a view larger than the real mapping, preventing a
        # forged logical size from authorizing an out-of-bounds read.
        self._view = self._k.MapViewOfFile(self._map, 0xF001F, 0, 0, h.total_bytes)
        if not self._view:
            raise ProtocolError("declared mapping exceeds backing section")
        self._header = h
        self._header_bytes = bytes(h)
        _require(
            self._stable(0, Header) is not None
            and C.string_at(self._view, HEADER_BYTES) == self._header_bytes,
            "header changed while opening",
        )
        self._process = self._k.OpenProcess(0x101000, False, producer_pid)
        _require(
            self._process and process_birth(producer_pid) == producer_birth,
            "producer replaced",
        )
        self._mutex = self._k.CreateMutexW(None, False, self.name + ".reader")
        _require(self._mutex and C.get_last_error() != 183, "client already exists")
        acquired = self._k.WaitForSingleObject(self._mutex, 0)
        if acquired in {0, 128}:
            self._owns_mutex = True
        # Abandoned/reopened ownership cannot resume command serials safely.
        _require(acquired == 0, "client already owned or abandoned")
        self.wake_event = self._k.OpenEventW(0x100002, False, self.name + ".client")
        self.result_event = self._k.OpenEventW(0x100002, False, self.name + ".producer")
        _require(self.wake_event and self.result_event, "missing worker events")
        for i in range(h.offer_count):
            _require(
                self._word(h.decision_offset + i * 128) == 0,
                "session client already used",
            )
        self.status()
        self._open_client()

    def _open_client(self):
        h = self._header
        _require(self._word(h.client_offset) == 0, "session client already used")
        # A failed event wake can follow a successful publication. Keep the
        # closing command owed even when _write never returns to its caller.
        self._client_open = True
        self._write(
            h.client_offset,
            ClientWire(
                state=1,
                nonce_lo=h.nonce_lo,
                nonce_hi=h.nonce_hi,
                epoch=h.epoch,
                generation=h.generation,
                owner_pid=h.owner_pid,
                owner_birth=h.owner_birth,
            ),
        )

    def encoder_ready(self):
        """Publish only after the isolated encoder opened these exact textures.

        Native preparation exposes immutable names before picture capture starts.
        This one-way acknowledgement prevents startup from filling source slots.
        """
        self._check()
        h = self._header
        if not self._client_open:
            raise RuntimeError("channel client is not open")
        if getattr(self, "_encoder_ready", False):
            return
        self.status()
        self._write(
            h.client_offset,
            ClientWire(
                state=3,
                nonce_lo=h.nonce_lo,
                nonce_hi=h.nonce_hi,
                epoch=h.epoch,
                generation=h.generation,
                owner_pid=h.owner_pid,
                owner_birth=h.owner_birth,
            ),
        )
        self._encoder_ready = True

    @property
    def header(self):
        return Header.from_buffer_copy(self._header_bytes)

    def _word(self, offset):
        return U32.from_address(self._view + offset).value

    def _stable(self, offset, kind):
        for _ in range(3):
            seq = self._word(offset)
            if seq & 1:
                continue
            result = kind.from_buffer_copy(
                C.string_at(self._view + offset, C.sizeof(kind))
            )
            if seq == self._word(offset):
                return result
        return None

    def _identity(self, value):
        h = self._header
        _require(
            (value.nonce_lo, value.nonce_hi, value.epoch, value.generation)
            == (h.nonce_lo, h.nonce_hi, h.epoch, h.generation),
            "stale message identity",
        )

    def _check(self):
        if threading.current_thread() != self._thread:
            raise RuntimeError("channel belongs to its serialized media worker")
        if not self._view:
            raise RuntimeError("channel is closed")
        if self._process and self._k.WaitForSingleObject(self._process, 0) != 258:
            raise ProcessLookupError("producer exited")
        _require(
            C.string_at(self._view, HEADER_BYTES) == self._header_bytes,
            "immutable header changed",
        )

    def status(self):
        self._check()
        s = self._stable(self._header.status_offset, Status)
        if s is None:
            raise BlockingIOError("torn producer status")
        self._identity(s)
        _require(
            not s.reserved0
            and not any(s.reserved)
            and s.state in {PREPARING, ACTIVE, CLOSED, FAULT},
            "status fields",
        )
        if s.state in {CLOSED, FAULT}:
            raise ChannelStopped(s.state, s.reason)
        return s

    def _scan_offers(self):
        h = self._header
        available = []
        for slot in range(h.offer_count):
            offset = h.offer_offset + slot * 256
            seq = self._word(offset)
            if seq & 1:
                return None
            o = OfferWire.from_buffer_copy(C.string_at(self._view + offset, 256))
            if seq != self._word(offset):
                return None
            if not o.kind or o.token == self._offers_seen[slot]:
                continue
            self._identity(o)
            _require(
                o.kind == 1
                and o.token
                and o.occurrence
                and not any(o.reserved)
                and o.width == h.width
                and o.height == h.height
                and 0 <= o.list_qpc <= o.boundary_qpc,
                "offer fields",
            )
            _require(
                o.table_count == h.table_count
                and o.stamp_bytes <= STAMP_BYTES
                and o.sample_bytes == h.sample_bytes
                and o.stamp_offset == h.payload_offset + slot * h.payload_stride
                and o.sample_offset == o.stamp_offset + STAMP_BYTES,
                "offer sizes/offsets",
            )
            _require(
                all(n <= f.length for n, f in zip(o.lengths, h.table, strict=True))
                and sum(o.lengths) == o.stamp_bytes,
                "stamp lengths",
            )
            stamps = C.string_at(self._view + o.stamp_offset, o.stamp_bytes)
            sample = C.string_at(self._view + o.sample_offset, o.sample_bytes)
            if seq != self._word(offset):
                return None
            available.append(
                Offer(
                    slot,
                    o.token,
                    o.occurrence,
                    o.list_qpc,
                    o.boundary_qpc,
                    o.width,
                    o.height,
                    o.vi_origin,
                    o.lists_since,
                    o.outcome,
                    tuple(o.lengths),
                    stamps,
                    sample,
                )
            )
        return available

    def offers(self):
        self.status()
        for _ in range(3):
            before = self._word(self._header.publication_offset)
            if before & 1:
                continue
            available = self._scan_offers()
            publication = self._stable(self._header.publication_offset, Publication)
            if available is None or publication is None or before != publication.seq:
                del available
                continue
            _require(
                not publication.reserved0 and not any(publication.reserved),
                "publication fields",
            )
            available.sort(key=lambda value: value.token)
            expected = self._last_token
            last_occurrence = self._last_occurrence
            for value in available:
                expected += 1
                _require(
                    value.token == expected
                    and value.occurrence > last_occurrence
                    and value.slot not in self._pending,
                    "missing, repeated or unacknowledged offer",
                )
                last_occurrence = value.occurrence
            _require(expected == publication.last_token, "undisclosed earlier offer")
            for value in available:
                self._pending[value.slot] = value
                self._offers_seen[value.slot] = value.token
            self._last_token, self._last_occurrence = expected, last_occurrence
            return available
        return []  # bounded deferral, never consume past an unstable earlier slot

    def frontier(self):
        s = self.status()
        if not s.frontier_qpc or s.frontier_serial > self.dispositioned_through:
            return None
        return (s.frontier_qpc, s.frontier_occurrence, s.frontier_serial)

    def _write(self, offset, value):
        seq = self._word(offset)
        _require(
            not seq & 1 and seq < 0xFFFFFFFE, "publication exhausted or interrupted"
        )
        U32.from_address(self._view + offset).value = seq + 1
        C.memmove(self._view + offset + 4, bytes(value)[4:], C.sizeof(value) - 4)
        U32.from_address(self._view + offset).value = seq + 2
        if not self._k.SetEvent(self.wake_event):
            raise C.WinError(C.get_last_error())

    def reply(
        self,
        offer: Offer,
        kind: int,
        *,
        encode_serial=0,
        pts=0,
        nominal_duration=0,
        force_idr=False,
        retained_occurrence=0,
        retained_serial=0,
        reason=0,
    ):
        self.status()
        _require(
            self._pending.get(offer.slot) == offer
            and offer == min(self._pending.values(), key=lambda v: v.occurrence),
            "stale, repeated or out-of-order reply",
        )
        values = (
            encode_serial,
            pts,
            nominal_duration,
            retained_occurrence,
            retained_serial,
            reason,
        )
        _require(
            all(type(v) is int and 0 <= v < 2**64 for v in values)
            and reason < 2**32
            and type(force_idr) is bool,
            "decision integer fields",
        )
        if kind == SELECTED:
            _require(
                encode_serial > self._last_serial
                and nominal_duration > 0
                and pts + nominal_duration <= 2**63 - 1
                and (self._last_pts is None or pts > self._last_pts)
                and not retained_occurrence
                and not retained_serial
                and not reason,
                "selected ticket/clock",
            )
        elif kind == COALESCED:
            _require(
                retained_occurrence > 0
                and retained_serial > 0
                and not any((encode_serial, pts, nominal_duration, force_idr, reason)),
                "coalesced disposition",
            )
        else:
            _require(
                kind in {SUPPRESSED, FAILED}
                and reason
                and not any(
                    (
                        encode_serial,
                        pts,
                        nominal_duration,
                        force_idr,
                        retained_occurrence,
                        retained_serial,
                    )
                ),
                "failed/suppressed disposition",
            )
        h = self._header
        d = Decision(
            kind=kind,
            token=offer.token,
            occurrence=offer.occurrence,
            nonce_lo=h.nonce_lo,
            nonce_hi=h.nonce_hi,
            epoch=h.epoch,
            generation=h.generation,
            encode_serial=encode_serial,
            pts=pts,
            nominal_duration=nominal_duration,
            retained_occurrence=retained_occurrence,
            retained_serial=retained_serial,
            flags=int(force_idr),
            reason=reason,
        )
        self._write(h.decision_offset + offer.slot * 128, d)
        del self._pending[offer.slot]
        self.dispositioned_through = offer.token
        if kind == SELECTED:
            self._last_serial, self._last_pts = encode_serial, pts

    def _scan_bridges(self):
        result = []
        for i in range(2):
            b = self._stable(self._header.bridge_offset + i * 128, BridgeWire)
            if b is None:
                return None
            if not b.ready or b.token == self._bridges_seen[i]:
                continue
            self._identity(b)
            _require(
                b.ready == 1
                and b.token
                and b.occurrence
                and b.encode_serial
                and b.nominal_duration
                and b.texture_index == i
                and not b.flags & ~1
                and not any(b.reserved)
                and b.pts + b.nominal_duration <= 2**63 - 1,
                "bridge fields",
            )
            _require(i not in self._bridge_pending, "unacknowledged bridge replacement")
            result.append(
                Bridge(
                    i,
                    b.token,
                    b.occurrence,
                    b.encode_serial,
                    b.pts,
                    b.nominal_duration,
                    bool(b.flags),
                )
            )
        return result

    def bridges(self):
        self.status()
        for _ in range(3):
            before = self._word(self._header.publication_offset)
            if before & 1:
                continue
            result = self._scan_bridges()
            publication = self._stable(self._header.publication_offset, Publication)
            if result is None or publication is None or before != publication.seq:
                continue
            _require(
                not publication.reserved0 and not any(publication.reserved),
                "publication fields",
            )
            result.sort(key=lambda b: b.token)
            token, serial = self._last_bridge_token, self._last_bridge_serial
            for value in result:
                token += 1
                _require(
                    value.token == token and value.encode_serial > serial,
                    "missing or reordered bridge",
                )
                serial = value.encode_serial
            _require(
                token == publication.last_bridge_token, "undisclosed earlier bridge"
            )
            for value in result:
                self._bridge_pending[value.texture_index] = value
                self._bridges_seen[value.texture_index] = value.token
            self._last_bridge_token, self._last_bridge_serial = token, serial
            return result
        return []

    def acknowledge_metadata(self, bridge: Bridge):
        self.status()
        _require(
            self._bridge_pending.get(bridge.texture_index) == bridge,
            "stale or repeated metadata receipt",
        )
        h = self._header
        r = Receipt(
            accepted=1,
            token=bridge.token,
            occurrence=bridge.occurrence,
            encode_serial=bridge.encode_serial,
            nonce_lo=h.nonce_lo,
            nonce_hi=h.nonce_hi,
            epoch=h.epoch,
            generation=h.generation,
            texture_index=bridge.texture_index,
        )
        self._write(h.receipt_offset + bridge.texture_index * 128, r)
        del self._bridge_pending[bridge.texture_index]

    def close(self):
        if threading.current_thread() != self._thread:
            raise RuntimeError("channel close belongs to its serialized media worker")
        if self._client_open and self._view:
            h = self._header
            gone = self._process and self._k.WaitForSingleObject(self._process, 0) == 0
            if not gone:
                # An interrupted client word raises here and RETAINS the view,
                # mutex and handles for the owner's retry (test_gpu_owner_recovery).
                self._write(
                    h.client_offset,
                    ClientWire(
                        state=2,
                        nonce_lo=h.nonce_lo,
                        nonce_hi=h.nonce_hi,
                        epoch=h.epoch,
                        generation=h.generation,
                        owner_pid=h.owner_pid,
                        owner_birth=h.owner_birth,
                    ),
                )
            self._client_open = False
        errors = []
        if self._owns_mutex:
            if self._k.ReleaseMutex(self._mutex):
                self._owns_mutex = False
            else:
                errors.append(OSError(C.get_last_error(), "channel reader mutex remains owned"))
        attributes = [("_view", "UnmapViewOfFile")]
        attributes.extend((attr, "CloseHandle") for attr in
                          ("_map", "_process", "wake_event", "result_event"))
        if not self._owns_mutex:
            attributes.append(("_mutex", "CloseHandle"))
        try:
            release_many(self, attributes)
        except OSError as exc:
            errors.append(exc)
        if errors:
            raise errors[0]
        self._pending.clear()
        self._bridge_pending.clear()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
