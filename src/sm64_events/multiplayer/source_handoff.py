"""The sole capture pump's bounded, in-memory live publication handoff.

Only the pump calls begin/observe/unavailable/end and mutates SourcePublisher.
Lifecycle control may withdraw a capture; room control may permanently revoke.
The network worker takes/checks values without retaining the lock during IO.
This module starts no resources and grants no room membership. Receipt age is
local pump age, not remote age.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import threading

from sm64_events.ghost.native_layout import RECORD_BYTES
from sm64_events.multiplayer.live_state import SourceIdentity, SourcePublisher
from sm64_events.multiplayer.protocol import PoseError, _integer

_U64 = (1 << 64) - 1
MAX_BATCH_RECORDS = 512


class CaptureOwner:
    """Opaque identity for one trusted capture pump lifetime, including reconnect."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "CaptureOwner()"


@dataclass(frozen=True, eq=False, slots=True)
class Publication:
    """One owned packet. Recheck current immediately before attempting send."""

    packet: bytes = field(repr=False)
    received_ns: int
    source_frequency: int = field(repr=False)
    _owner: CaptureOwner = field(repr=False)
    _epoch: object = field(repr=False)


class SourceHandoff:
    """One room incarnation, one capture writer, at most one latest value.

    A new room admission needs a new object after revoke. The capture owner
    survives channel reconnect: source_unavailable clears delivery and detaches
    the publisher, while a subsequent observation rebinds with preserved fences.
    Source status changes with no records need explicit source_unavailable.
    """

    def __init__(self, participant_incarnation: bytes, *, stale_after_ns: int):
        self._stale_after_ns = _integer(stale_after_ns, "local publication timeout", _U64)
        self._publisher = SourcePublisher(participant_incarnation)
        self._lock = threading.Lock()
        self._owner = None
        self._epoch = object()
        self._latest = None
        self._pending = False
        self._revoked = False
        self._withdrawn = False
        # These fields belong only to the pump, never the consumer/controller.
        self._lease = None
        self._received_floor = None

    def _invalidate_locked(self) -> None:
        self._epoch = object()
        self._latest = None
        self._pending = False

    def begin_capture(self) -> CaptureOwner:
        """Pump only. An active writer must end before another may begin."""
        with self._lock:
            if self._revoked:
                raise PoseError("Live room publication has been revoked.")
            if self._owner is not None:
                raise PoseError("Live capture already has an active owner.")
            owner = CaptureOwner()
            self._owner = owner
            self._withdrawn = False
            self._invalidate_locked()
        self._publisher.detach()
        self._lease = None
        return owner

    def source_unavailable(self, owner: CaptureOwner) -> bool:
        """Pump only. Withdraw pending/taken data without retiring the owner."""
        with self._lock:
            if self._owner is None or self._owner is not owner:
                return False
            self._invalidate_locked()
        self._publisher.detach()
        self._lease = None
        return True

    def end_capture(self, owner: CaptureOwner) -> bool:
        """Pump only. Withdraw delivery before the source lifetime ends."""
        with self._lock:
            if self._owner is None or self._owner is not owner:
                return False
            self._invalidate_locked()
            self._owner = None
        self._publisher.detach()
        self._lease = None
        return True

    def revoke(self) -> None:
        """Trusted room control; permanent and independent of a stalled codec."""
        with self._lock:
            self._revoked = True
            self._invalidate_locked()

    def withdraw_capture(self, owner: CaptureOwner) -> bool:
        """Lifecycle control: stop this writer immediately, before a pump join.

        This does not mutate SourcePublisher or release the active writer.
        Only the pump's later end_capture permits a new begin_capture.
        """
        with self._lock:
            if self._owner is None or self._owner is not owner:
                return False
            self._withdrawn = True
            self._invalidate_locked()
            return True

    @staticmethod
    def _checked_batch(frames: tuple[bytes, ...]) -> None:
        if type(frames) is not tuple or len(frames) > MAX_BATCH_RECORDS:
            raise PoseError("Live source batch is outside its bound.")
        if any(type(raw) is not bytes or len(raw) != RECORD_BYTES for raw in frames):
            raise PoseError("Live source batch contains an invalid record.")

    def observe(self, owner: CaptureOwner, producer, frames: tuple[bytes, ...], *, received_ns: int) -> int:
        """Pump only. Observe every record before replacing the single packet.

        Return the record count if the batch publishes, otherwise zero for an
        empty batch or concurrent control withdrawal. Invalid evidence withdraws
        delivery and detaches on this pump before propagating PoseError. A
        valid earlier record's source fences survive a later batch error.
        """
        try:
            self._checked_batch(frames)
            received = _integer(received_ns, "local source receipt time", _U64, minimum=0)
            with self._lock:
                if self._owner is None or self._owner is not owner or self._revoked or self._withdrawn:
                    raise PoseError("Live capture owner has been retired.")
                epoch = self._epoch
            if not frames:
                return 0
            if self._received_floor is not None and received < self._received_floor:
                raise PoseError("Live local source receipt time moved backward.")
            identity = SourceIdentity.from_producer(producer)
            if self._lease is None or self._lease.identity != identity:
                self._lease = self._publisher.bind_source(identity)
            for raw in frames:
                packet = self._publisher.packet(self._lease, raw)
            self._received_floor = received
            value = Publication(packet, received, identity.qpc_frequency, owner, epoch)
            with self._lock:
                if self._revoked or self._withdrawn or self._owner is not owner or self._epoch is not epoch:
                    return 0
                self._latest = value
                self._pending = True
            return len(frames)
        except PoseError:
            self.source_unavailable(owner)
            raise

    def _fresh_locked(self, value: Publication, now: int) -> bool:
        return (not self._revoked and not self._withdrawn and self._owner is value._owner and self._epoch is value._epoch
                and self._latest is value and 0 <= now - value.received_ns <= self._stale_after_ns)

    def take_latest(self, *, now_ns: int) -> Publication | None:
        """Network worker: take once, release the lock, then recheck before IO."""
        now = _integer(now_ns, "local publication observation time", _U64, minimum=0)
        with self._lock:
            value = self._latest
            if value is None:
                return None
            if not self._fresh_locked(value, now):
                self._latest = None
                self._pending = False
                return None
            if not self._pending:
                return None
            self._pending = False
            return value

    def current(self, value: Publication, *, now_ns: int) -> bool:
        """Local validity immediately before send; this cannot cancel sent IO."""
        now = _integer(now_ns, "local publication observation time", _U64, minimum=0)
        if type(value) is not Publication:
            return False
        with self._lock:
            if self._fresh_locked(value, now):
                return True
            if self._latest is value:
                self._latest = None
                self._pending = False
            return False
