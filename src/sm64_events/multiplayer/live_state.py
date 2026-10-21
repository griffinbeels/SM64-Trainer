"""Single-owner live source/seat state, independent of transport and saved PBs.

Room control supplies participant incarnations. Packets cannot grant membership.
These classes do not start capture, load models, spawn workers or write history.
Receive age means time since local receipt; it is not cross-machine pose age.
"""
from __future__ import annotations

from dataclasses import dataclass

from sm64_events.ghost.clip import ClipError, frame_from_bytes
from sm64_events.ghost import native_layout as L
from sm64_events.multiplayer.protocol import (
    LivePose, PoseError, _bounded_bytes, _incarnation, _integer, decode_pose, encode_pose,
)

_U32 = (1 << 32) - 1
_U64 = (1 << 64) - 1


@dataclass(frozen=True)
class SourceIdentity:
    pid: int
    created: int
    mapping_generation: int
    rom_generation: int
    configuration: int
    epoch: int
    qpc_frequency: int
    rom_layout: int

    def __post_init__(self):
        for name in ("pid", "mapping_generation", "rom_generation", "configuration", "epoch"):
            _integer(getattr(self, name), name, _U32)
        _integer(self.created, "producer birth", _U64)
        _integer(self.qpc_frequency, "QPC frequency", (1 << 63) - 1)
        if type(self.rom_layout) is not int or self.rom_layout not in (L.ROM_US, L.ROM_JP):
            raise PoseError("Live source cartridge is unsupported.")

    @classmethod
    def from_producer(cls, producer) -> SourceIdentity:
        """Called by the sole capture owner, never from received network values."""
        return cls(producer.pid, producer.created, producer.generation,
                   producer.rom_generation, producer.configuration, producer.epoch,
                   producer.qpc_frequency, producer.rom_layout)


@dataclass(frozen=True, eq=False)
class SourceLease:
    identity: SourceIdentity
    generation: int


class SourcePublisher:
    """One capture owner's sender clock; retired callbacks cannot mint sources.

    bind_source is a trusted lifecycle operation, not a per-record fallback.
    Callbacks retain the returned object and packet requires that exact lease.
    A detached but unchanged producer retains its generation, clock and fences.
    """

    def __init__(self, participant_incarnation: bytes):
        self.incarnation = _incarnation(participant_incarnation)
        self._source = None
        self._lease = None
        self._exhausted = False
        self._generation = self._continuity = self._sequence = 0
        self._origin_qpc = self._qpc = self._boundary = None

    def bind_source(self, identity: SourceIdentity) -> SourceLease:
        if self._exhausted:
            raise PoseError("Live sender namespace has been exhausted.")
        if not isinstance(identity, SourceIdentity):
            raise PoseError("Live source identity is invalid.")
        if self._lease is not None and self._source == identity:
            return self._lease
        if self._source != identity:
            generation = self._advance(self._generation)
            self._generation, self._continuity, self._sequence = generation, 1, 0
            self._origin_qpc = self._qpc = self._boundary = None
            self._source = identity
        else:
            self._continuity = self._advance(self._continuity)
        self._lease = SourceLease(identity, self._generation)
        return self._lease

    def _advance(self, value: int) -> int:
        if value >= _U64:
            self._exhausted = True
            self._lease = None
            raise PoseError("Live sender namespace has been exhausted.")
        return value + 1

    def detach(self) -> None:
        """Retire callbacks without resetting the producer's high-water marks."""
        self._lease = None

    def packet(self, lease: SourceLease, raw_record: bytes, *, boundary: bool = False) -> bytes:
        if lease is None or lease is not self._lease:
            raise PoseError("Live source callback has been retired.")
        if type(boundary) is not bool:
            raise PoseError("Live presentation boundary is invalid.")
        raw = _bounded_bytes(raw_record, L.RECORD_BYTES, "source record")
        try:
            frame = frame_from_bytes(raw)
        except ClipError as error:
            raise PoseError("Live source record is invalid.") from error
        source = lease.identity
        if (frame.configuration, frame.epoch) != (source.configuration, source.epoch):
            raise PoseError("Live source record belongs to a different source.")
        if frame.sequence <= self._sequence or self._qpc is not None and frame.qpc < self._qpc:
            raise PoseError("Live source occurrence is not newer.")
        origin = frame.qpc if self._origin_qpc is None else self._origin_qpc
        elapsed = (frame.qpc - origin) * 1_000_000_000 // source.qpc_frequency
        if elapsed > _U64:
            self._exhausted, self._lease = True, None
            raise PoseError("Live sender namespace has been exhausted.")
        _integer(elapsed, "capture time", _U64, minimum=0)
        # Countdown changes do not repeatedly invent scene boundaries.
        key = (frame.level, frame.area, frame.playable, frame.pose_hidden,
               frame.pending_warp_op, bool(frame.delayed_warp_timer))
        continuity = self._continuity
        if self._sequence and (boundary or key != self._boundary):
            continuity = self._advance(continuity)
        packet = encode_pose(raw, participant_incarnation=self.incarnation,
            source_generation=lease.generation, elapsed_ns=elapsed, continuity=continuity,
            rom_layout=source.rom_layout, rom_generation=source.rom_generation)
        self._origin_qpc, self._qpc = origin, frame.qpc
        self._sequence, self._boundary, self._continuity = frame.sequence, key, continuity
        return packet


@dataclass(frozen=True)
class ReceivedPose:
    pose: LivePose
    received_ns: int


class LiveSeat:
    """Latest-only received value plus fences that survive empty/faded views.

    The authenticated room controller owns this object for one admitted
    participant incarnation, including bounded transport reconnects. It must
    revoke before removing that membership, and retain/reuse the same seat
    through a reconnect. This is single-worker state, not a render-thread API.
    """

    def __init__(self, participant_incarnation: bytes, *, stale_after_ns: int):
        self.incarnation = _incarnation(participant_incarnation)
        self.stale_after_ns = _integer(stale_after_ns, "local receipt timeout", _U64)
        self._revoked = False
        self._latest = None
        self._generation = self._continuity = self._sequence = self._elapsed = 0
        self._fixed = None
        self._received_floor = None

    def clear(self) -> None:
        """Clear presentation on absence/navigation, retaining every replay fence."""
        self._latest = None

    def revoke(self) -> None:
        """Permanent for this membership; only room control can admit a new one."""
        self._revoked = True
        self.clear()

    def ingest(self, packet: bytes, *, received_ns: int) -> ReceivedPose:
        if self._revoked:
            raise PoseError("Live participant membership has been revoked.")
        received = _integer(received_ns, "local receipt time", _U64, minimum=0)
        pose = decode_pose(packet, expected_incarnation=self.incarnation)
        if self._received_floor is not None and received < self._received_floor:
            raise PoseError("Live local receipt time moved backward.")
        frame = pose.frame
        fixed = (pose.rom_layout, pose.rom_generation, frame.configuration, frame.epoch)
        if pose.source_generation < self._generation:
            raise PoseError("Live source generation has been retired.")
        if pose.source_generation == self._generation:
            if fixed != self._fixed:
                raise PoseError("Live source identity changed within its generation.")
            if (pose.continuity < self._continuity or frame.sequence <= self._sequence
                    or pose.elapsed_ns < self._elapsed):
                raise PoseError("Live source occurrence is not newer.")
        latest = ReceivedPose(pose, received)
        self._generation, self._continuity = pose.source_generation, pose.continuity
        self._sequence, self._elapsed = frame.sequence, pose.elapsed_ns
        self._fixed, self._received_floor, self._latest = fixed, received, latest
        return latest

    def latest(self, *, now_ns: int) -> ReceivedPose | None:
        """Expire by local receipt age; do not invent synchronized sender age."""
        now = _integer(now_ns, "local observation time", _U64, minimum=0)
        if self._latest is None:
            return None
        if now < self._latest.received_ns:
            raise PoseError("Live local observation time moved backward.")
        if now - self._latest.received_ns > self.stale_after_ns:
            self.clear()
        return self._latest
