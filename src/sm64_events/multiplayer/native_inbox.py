"""Admitted room members -> immutable fixed native updates.

One retained LiveSeat per logical membership keeps its replay fences through
transport reconnects, empty/expired display and local source/model retargets.
Provider identities and invitations never cross this local receiver boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
import threading
import unicodedata

from sm64_events.multiplayer.live_state import LiveSeat
from sm64_events.multiplayer.native_protocol import Appearance, LocalOwner, MAX_PEERS, STALE_AFTER_NS, Update
from sm64_events.multiplayer.protocol import PoseError, _incarnation, _integer


def display_name(value: str) -> str:
    if type(value) is not str:
        raise ValueError("Player name is invalid.")
    value = value.strip()
    if not value or len(value.encode("utf-8")) > 63 or any(unicodedata.category(c).startswith("C") for c in value):
        raise ValueError("Player name is invalid.")
    return value


@dataclass(frozen=True)
class NativeView:
    wire: bytes
    revision: int


@dataclass
class _Member:
    key: str
    name: str
    hue: int
    seat: LiveSeat
    packet: bytes | None = None


class NativeInbox:
    def __init__(self, room: bytes, *, stale_after_ns: int = STALE_AFTER_NS):
        self._room = _incarnation(room)
        self._stale_after_ns = _integer(stale_after_ns, "live receipt timeout", (1 << 64) - 1)
        self._lock = threading.RLock()
        self._members = [None] * MAX_PEERS
        self._seen = set()
        self._revision, self._active, self._hidden = 1, True, False
        self._appearance = Appearance()

    def _advance(self):
        if self._revision == (1 << 64) - 1:
            self._active = False
            for member in self._members:
                if member is not None:
                    member.seat.revoke()
            raise PoseError("Live room namespace has been exhausted.")
        self._revision += 1

    def admit(self, key: str, incarnation: bytes, *, name: str, hue: int) -> int:
        nonce, label = _incarnation(incarnation), display_name(name)
        if type(key) is not str or not key or len(key) > 64 or type(hue) is not int or not 0 <= hue <= 359:
            raise ValueError("Live member metadata is invalid.")
        with self._lock:
            if not self._active:
                raise PoseError("Live room has ended.")
            for index, member in enumerate(self._members):
                if member is not None and member.key == key:
                    if member.seat.incarnation != nonce:
                        raise PoseError("Live member source identity changed.")
                    if (member.name, member.hue) != (label, hue):
                        self._advance(); member.name, member.hue = label, hue
                    return index
            if nonce in self._seen or len(self._seen) >= 256:
                raise PoseError("Live member namespace cannot be reused.")
            index = next((i for i, member in enumerate(self._members) if member is None), None)
            if index is None:
                raise PoseError("The room already has three other players.")
            self._advance()
            self._seen.add(nonce)
            self._members[index] = _Member(key, label, hue, LiveSeat(nonce, stale_after_ns=self._stale_after_ns))
            return index

    def remove(self, key: str) -> bool:
        with self._lock:
            for index, member in enumerate(self._members):
                if member is not None and member.key == key:
                    self._advance(); member.seat.revoke(); self._members[index] = None
                    return True
            return False

    def clear(self, key: str) -> None:
        with self._lock:
            member = next((m for m in self._members if m is not None and m.key == key), None)
            if member is not None:
                member.seat.clear()
                member.packet = None

    def ingest(self, key: str, packet: bytes, *, received_ns: int) -> None:
        with self._lock:
            if not self._active:
                raise PoseError("Live room has ended.")
            member = next((m for m in self._members if m is not None and m.key == key), None)
            if member is None:
                raise PoseError("Live sender is not admitted.")
            member.seat.ingest(packet, received_ns=received_ns)
            member.packet = bytes(packet)

    def appearance(self, value: Appearance, *, hidden: bool) -> None:
        value.native()
        if type(hidden) is not bool:
            raise ValueError("Live visibility is invalid.")
        with self._lock:
            if (value, hidden) != (self._appearance, self._hidden):
                self._advance(); self._appearance, self._hidden = value, hidden

    def revoke(self) -> None:
        with self._lock:
            if self._active:
                self._advance(); self._active = False
                for member in self._members:
                    if member is not None:
                        member.seat.revoke()

    def snapshot(self, *, now_ns: int) -> NativeView:
        now = _integer(now_ns, "native live observation time", (1 << 64) - 1, minimum=0)
        with self._lock:
            value = Update()
            value.room[:] = self._room
            value.revision, value.enabled, value.hidden = self._revision, int(self._active), int(self._hidden)
            value.controls = self._appearance.native()
            for index, member in enumerate(self._members):
                if member is None or not self._active:
                    continue
                peer = value.peers[index]
                peer.member, peer.hue_degrees, peer.label = 1, member.hue, member.name.encode("utf-8")
                peer.incarnation[:] = member.seat.incarnation
                latest = member.seat.latest(now_ns=now)
                if latest is not None:
                    peer.present, peer.received_ns = 1, latest.received_ns
                    peer.packet[:] = member.packet
            return NativeView(bytes(value), self._revision)

    def current(self, view: NativeView) -> bool:
        with self._lock:
            return self._active and type(view) is NativeView and view.revision == self._revision


def bind_owner(view: NativeView, owner: LocalOwner) -> Update:
    value = Update.from_buffer_copy(view.wire)
    value.owner = owner
    return value
