"""Ephemeral friends rooms. One lazy worker owns transport and native delivery.

Provider references, invitations, application source identities and transport
challenges stay private. Room events grant membership; SOURCE_INFO never does.
The sole recorder pump remains the only captured-pose consumer/publication owner.
"""
from __future__ import annotations

import base64
from concurrent.futures import Future, TimeoutError as FutureTimeout
from copy import deepcopy
from dataclasses import dataclass, field
import json
import queue
import re
import secrets
import threading
import time

from sm64_events.core.qpcclock import qpc_ns
from sm64_events.multiplayer.eos_process import TransportError
from sm64_events.multiplayer.native_inbox import NativeInbox, display_name
from sm64_events.multiplayer.native_protocol import STALE_AFTER_NS
from sm64_events.multiplayer.protocol import PACKET_BYTES, PoseError, decode_pose
from sm64_events.multiplayer.source_handoff import SourceHandoff
from sm64_events.replay.ownedclose import CleanupPending, retain

_U64 = (1 << 64) - 1
_HEX = re.compile(r"[0-9a-fA-F]{32}\Z")
_CODE = re.compile(r"ET1-[0-9a-fA-F]{32}\Z")
_HEARTBEAT_NS = 1_000_000_000


class RoomError(RuntimeError):
    """A fixed safe reason, never a vendor message or private reference."""


def _number(value, *, minimum=1, maximum=_U64):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("invalid_number")
    return value


def _code(value):
    if type(value) is not str or not _CODE.fullmatch(value):
        raise ValueError("invalid_identity")
    return "ET1-" + value[4:].lower()


def _nonce(value):
    if type(value) is not str or not _HEX.fullmatch(value) or not any(bytes.fromhex(value)):
        raise ValueError("invalid_nonce")
    return value.lower()


def _qpc_frequency():
    # Same-machine scalar query only; no emulator, SDK or capture acquisition.
    import ctypes
    import sys
    if sys.platform != "win32":
        return 0
    value = ctypes.c_int64()
    return value.value if ctypes.windll.kernel32.QueryPerformanceFrequency(ctypes.byref(value)) else 0


@dataclass
class _Peer:
    ref: int
    member_epoch: int
    transport_epoch: int
    code: str = field(repr=False)
    id: str = field(default_factory=lambda: secrets.token_hex(12))
    name: str = "Player"
    state: str = "connecting"
    local_nonce: str | None = field(default=None, repr=False)
    remote_nonce: str | None = field(default=None, repr=False)
    source_nonce: bytes | None = field(default=None, repr=False)
    ready: bool = False
    sent: int = 0
    data_sequence: int = 0
    control_sequence: int = 0
    last_received: int | None = None
    source_serial: int = 0
    source_state: object = None
    state_ready: bool = False
    info_received: bool = False
    info_sent: bool = False
    required_state_serial: int = 0
    sent_state_serial: int = 0
    publication: object = field(default=None, repr=False)


@dataclass(frozen=True)
class _SourceState:
    state: str = "waiting"
    source_generation: int = 0
    continuity: int = 0
    sequence: int = 0

    @property
    def floor(self):
        return self.source_generation, self.continuity, self.sequence


@dataclass(eq=False)
class _Control:
    op: str
    fields: dict = field(repr=False)
    payload: bytes | None = field(default=None, repr=False)
    kind: str = "command"
    serial: int = 0
    future: object = field(default=None, repr=False)
    attempts: int = 0
    when: int = 0


@dataclass
class _Action:
    name: str
    arguments: tuple = field(repr=False)
    deadline: float
    future: Future = field(default_factory=Future, repr=False)
    cancelled: threading.Event = field(default_factory=threading.Event, repr=False)


class RoomService:
    def __init__(self, recorder, transport_factory, *, player_name=lambda: "You", channel_factory=None,
                 clock_ns=qpc_ns, qpc_frequency=_qpc_frequency, available=True, appearance=None,
                 action_timeout_s=35):
        self._recorder, self._factory, self._name = recorder, transport_factory, player_name
        self._channel_factory = channel_factory
        self._clock, self._local_frequency, self._appearance = clock_ns, qpc_frequency, appearance
        self._lock = threading.RLock()
        self._wake, self._stop = threading.Event(), threading.Event()
        self._events, self._actions = queue.Queue(128), queue.Queue(8)
        self._latest_events = {}
        self._overflow = threading.Event()
        self._thread = self._transport = None
        self._retiring_transport = None
        self._generation, self._frequency = 0, 0
        self._ready, self._identity, self._local_name = False, None, "You"
        self._phase, self._error = "idle", None
        self._available = bool(available)
        self._room_epoch = 0
        self._owner_code, self._local_owner = None, False
        self._peers, self._retired, self._invites, self._known_names = {}, set(), {}, {}
        self._invite_revision = 0
        self._outgoing = self._incoming = self._source_nonce = None
        self._channel = self._endpoint = None
        self._channel_retiring = False
        self._channel_writer = None
        self._disabled_delivery = None
        self._controls = []
        self._source_state = _SourceState()
        self._state_serial = self._heartbeat_at = 0
        self._publication = self._pose = None
        self._leaving = False
        self._action_timeout = action_timeout_s
        self._active_action = None
        self._cache = {}
        self._source_status = {"state": "off", "sharing": False, "paused": False, "source_attached": False}
        self._publish()

    def snapshot(self):
        """Cached public state. Reading never starts identity, capture or IO."""
        with self._lock:
            return deepcopy(self._cache)

    def _publish(self):
        room = None
        if self._outgoing is not None:
            local_state = self._source_state.state
            members = [{"id": "self", "name": self._local_name, "state": local_state, "self": True}]
            states = {"playing": "playing", "paused": "paused", "reconnecting": "reconnecting", "disconnected": "disconnected"}
            members.extend({"id": p.id, "name": p.name, "state": states.get(p.state, "waiting"), "self": False}
                           for p in self._peers.values())
            room = {"owner": bool(self._local_owner), "members": members, "max_members": 4}
        source = dict(self._source_status)
        source["reason"] = ("Your sharing is paused." if source["paused"] else
            "Waiting for your game." if self._outgoing is not None and not source["source_attached"] else None)
        phase = "reconnecting" if room is not None and not self._leaving \
            and any(p.state == "reconnecting" for p in self._peers.values()) else self._phase
        value = {"phase": phase if self._available else "idle", "room": room, "available": self._available,
            "unavailable_reason": None if self._available else "Friends isn’t set up in this build.",
            "invitations": [{"id": key, "from_name": self._known_names.get(value[1], "Player"), "member_count": value[2]}
                            for key, value in self._invites.items()],
            "source": source, "error": _message(self._error) if self._available else None,
            "can_invite": room is not None and len(self._peers) < 3 and not self._leaving}
        with self._lock:
            self._cache = value

    def _post(self, action, *arguments):
        with self._lock:
            if self._stop.is_set():
                raise RoomError("room_closed")
            if not self._available:
                raise RoomError("transport_unavailable")
            request = _Action(action, arguments, time.monotonic() + self._action_timeout)
            try:
                self._actions.put_nowait(request)
            except queue.Full as error:
                raise RoomError("room_busy") from error
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="friends-room", daemon=True)
                self._thread.start()
        self._wake.set()
        try:
            return request.future.result(timeout=max(0, request.deadline - time.monotonic()))
        except FutureTimeout as error:
            # Future.cancel cannot stop a running action. Admission checks this
            # exact cancellation while holding the same lock before mounting.
            with self._lock:
                request.cancelled.set()
                request.future.cancel()
            self._local_revoke()
            self._wake.set()
            raise RoomError("room_timeout") from error

    def _check_action(self):
        action = self._active_action
        if action is not None and (action.cancelled.is_set() or time.monotonic() >= action.deadline):
            # The remote mutation's outcome may be unknown. Retire its generation
            # before draining any late room event or starting a replacement.
            self._transport_failed("room_timeout")
            raise RoomError("room_timeout")
        if self._stop.is_set():
            raise RoomError("room_closed")

    def identity(self):
        return self._post("identity")

    def host(self):
        return self._post("host")

    def invite(self, code):
        try:
            code = _code(code.strip() if type(code) is str else code)
        except ValueError as error:
            raise RoomError("invalid_identity") from error
        return self._post("invite", code)

    def join(self, invite_id):
        return self._post("join", invite_id)

    def decline(self, invite_id):
        return self._post("decline", invite_id)

    def leave(self):
        self._local_revoke()
        return self._post("leave")

    def kick(self, member_id):
        return self._post("kick", member_id)

    def retry(self):
        return self._post("retry")

    def retry_peer(self, member_id):
        return self._post("retry_peer", member_id)

    def _local_revoke(self):
        with self._lock:
            outgoing, incoming = self._outgoing, self._incoming
        if outgoing is not None:
            outgoing.revoke()
        if incoming is not None:
            incoming.revoke()
        if outgoing is not None or incoming is not None:
            self._recorder.set_live_session(None, None)
        self._wake.set()

    def _notify(self, generation, event):
        if self._stop.is_set() or generation != self._generation:
            return
        if type(event) is dict and event.get("type") == "data_received" and type(event.get("peer_ref")) is int:
            # Membership/control keeps its ordered bounded queue. Pose samples
            # retain only the latest per peer, including after the helper pipe.
            with self._lock:
                key = generation, event["peer_ref"]
                if key not in self._latest_events and len(self._latest_events) == 3:
                    self._latest_events.pop(next(iter(self._latest_events)))
                self._latest_events[key] = event
            self._wake.set()
            return
        try:
            self._events.put_nowait((generation, event))
        except queue.Full:
            self._overflow.set()
            self._local_revoke()
        self._wake.set()

    def _start_transport(self):
        self._check_action()
        if self._transport is not None:
            return
        if not self._close_retiring_transport():
            raise RoomError("transport_cleanup_pending")
        self._phase, self._error = "starting", None
        self._publish()
        self._generation += 1
        generation = self._generation
        self._ready, self._identity, self._frequency = False, None, 0
        try:
            self._transport = self._factory(lambda value: self._notify(generation, value))
            self._transport.start()
        except Exception as error:  # noqa: BLE001 - external provider setup yields a safe fixed reason
            failed, self._transport = self._transport, None
            if failed is not None:
                self._retiring_transport = failed
            pending = not self._close_retiring_transport()
            self._generation += 1
            self._ready, self._identity, self._frequency = False, None, 0
            self._phase, self._error = "error", "transport_cleanup_pending" if pending else "transport_unavailable"
            if pending:
                raise RoomError("transport_cleanup_pending") from error
            raise RoomError("transport_unavailable") from error
        deadline = self._clock() + 15_000_000_000
        while not self._ready or self._identity is None:
            self._check_action()
            self._drain_events()
            if self._transport is None or self._stop.is_set():
                raise RoomError(self._error or "room_closed")
            if self._clock() >= deadline:
                self._transport_failed("transport_timeout")
                raise RoomError("transport_timeout")
            self._wake.wait(.01); self._wake.clear()
        self._phase = "idle"

    def _command(self, op, **fields):
        for attempt in range(6):
            self._check_action()
            if self._transport is None or self._stop.is_set():
                raise RoomError("transport_unavailable")
            try:
                future = self._transport.submit(op, **fields)
                deadline = self._clock() + 12_000_000_000
                while not future.done():
                    self._check_action()
                    self._tick()
                    if self._clock() >= deadline or self._stop.is_set():
                        self._transport_failed("transport_timeout")
                        raise RoomError("transport_timeout")
                    self._wake.wait(.01); self._wake.clear()
                self._check_action()
                future.result()
                self._drain_events()
                return
            except TransportError as error:
                if str(error) not in ("busy", "transport_busy", "rate_limited", "queue_full") or attempt == 5:
                    raise RoomError("room_operation_failed") from error
                until = self._clock() + (attempt + 1) * 50_000_000
                while self._clock() < until and not self._stop.is_set():
                    self._check_action()
                    self._tick(); self._wake.wait(.01); self._wake.clear()
        raise RoomError("room_busy")

    def _execute(self, action, arguments):
        if action == "leave" and self._transport is None:
            self._end_room();self._publish();return self.snapshot()
        self._start_transport()
        self._error = None
        if action == "identity":
            self._command("identity")
            return self._identity
        if action == "host":
            if self._outgoing is not None:
                raise RoomError("already_in_room")
            self._phase = "starting";self._publish();self._command("create_room")
        elif action in ("join", "decline"):
            self._invitation_action(action, arguments[0])
        elif action == "invite":
            if self._outgoing is None or self._leaving or len(self._peers) >= 3:
                raise RoomError("cannot_invite")
            if arguments[0] == self._identity:
                raise RoomError("cannot_invite_self")
            self._command("invite", room_epoch=self._room_epoch, user_code=arguments[0])
        elif action == "kick":
            peer = next((p for p in self._peers.values() if p.id == arguments[0]), None)
            if not self._local_owner or peer is None or self._incoming is None:
                raise RoomError("cannot_remove_player")
            self._incoming.clear(peer.id); peer.ready = False; peer.state = "disconnecting"
            self._command("kick", room_epoch=self._room_epoch, peer_ref=peer.ref, member_epoch=peer.member_epoch)
        elif action in ("retry", "retry_peer"):
            self._retry_connections(arguments[0] if action == "retry_peer" else None)
        elif action == "leave":
            self._leave_room()
        else:
            raise RoomError("invalid_room_action")
        self._publish()
        return self.snapshot()

    def _retry_connections(self, member_id):
        peers = [p for p in self._peers.values() if p.state == "disconnected"
                 and (member_id is None or p.id == member_id)]
        if member_id is not None and not peers:
            raise RoomError("cannot_retry_player")
        for peer in peers:
            self._command("reset_peer", **self._fields(peer))
        if member_id is None:
            self._close_channel()
        self._phase = "hosting" if self._outgoing is not None and self._local_owner else \
            "connected" if self._outgoing is not None else "idle"

    def _leave_room(self):
        epoch = self._room_epoch
        self._leaving = True; self._phase = "leaving"; self._publish()
        try:
            self._end_room()
            if epoch:
                self._command("leave", room_epoch=epoch)
        except RoomError:
            self._transport_failed("room_operation_failed")
            raise
        finally:
            self._leaving = False
        self._phase = "idle"

    def _invitation_action(self, action, reference):
        invitation = self._invites.get(reference) if type(reference) is str else None
        if invitation is None:
            raise RoomError("invitation_expired")
        if action == "join" and self._outgoing is not None:
            raise RoomError("already_in_room")
        if action == "join":
            self._phase = "joining";self._publish()
        self._command("accept_invite" if action == "join" else "decline_invite", invite_ref=invitation[0])
        self._invites.pop(reference, None)

    def _fields(self, peer):
        return {"room_epoch": self._room_epoch, "peer_ref": peer.ref,
                "member_epoch": peer.member_epoch, "transport_epoch": peer.transport_epoch}

    def _queue_control(self, op, fields, payload=None, *, kind="command", serial=0):
        # At most one unsent latest state follows an in-flight reliable state.
        # Repeated source boundaries cannot create a reliable backlog.
        if kind == "state":
            for item in self._controls:
                if item.kind == kind and item.fields == fields and item.future is None:
                    item.payload, item.serial = payload, serial
                    item.attempts, item.when = 0, self._clock()
                    return
        if len(self._controls) >= 24:
            raise ValueError("control_queue_full")
        self._controls.append(_Control(op, fields, payload, kind, serial, when=self._clock()))

    def _send_controls(self):
        if self._transport is None:
            self._controls.clear(); return
        now = self._clock()
        blocked = set()
        for item in list(self._controls):
            fields = item.fields
            peer = self._peers.get(fields["peer_ref"])
            if peer is None or any(fields.get(key) != value for key, value in self._fields(peer).items()):
                self._controls.remove(item); continue
            if peer.ref in blocked:
                continue
            try:
                if item.future is not None:
                    if not item.future.done():
                        blocked.add(peer.ref)
                        if now - item.when > 12_000_000_000:
                            raise TransportError("transport_timeout")
                        continue
                    item.future.result()
                    if item.kind == "info":
                        peer.info_sent = True
                    elif item.kind == "state":
                        peer.sent_state_serial = item.serial
                    self._controls.remove(item)
                    continue
                blocked.add(peer.ref)
                if now < item.when:
                    continue
                item.future = (self._transport.control(item.payload, **fields) if item.payload is not None
                               else self._transport.submit(item.op, **fields))
                item.when = now
            except TransportError as error:
                blocked.add(peer.ref)
                if str(error) in ("busy", "transport_busy", "rate_limited", "queue_full") and item.attempts < 5:
                    item.future, item.attempts = None, item.attempts + 1
                    item.when = now + item.attempts * 50_000_000
                else:
                    self._controls.remove(item); peer.ready = False; peer.state = "disconnected"
                    if self._incoming is not None:
                        self._incoming.clear(peer.id)

    def _peer(self, event, *, transport=True):
        if _number(event["room_epoch"]) != self._room_epoch or self._outgoing is None:
            return None
        peer = self._peers.get(_number(event["peer_ref"]))
        if peer is None or _number(event["member_epoch"]) != peer.member_epoch:
            return None
        if transport and _number(event["transport_epoch"]) != peer.transport_epoch:
            return None
        return peer

    def _receive(self, event, control):
        peer = self._peer(event)
        if peer is None or not peer.ready or _nonce(event["local_nonce"]) != peer.local_nonce \
                or _nonce(event["remote_nonce"]) != peer.remote_nonce:
            return
        sequence = _number(event["sequence"])
        if sequence <= (peer.control_sequence if control else peer.data_sequence):
            return
        encoded = event["payload_b64"]
        maximum = 512 if control else PACKET_BYTES
        if type(encoded) is not str or len(encoded) > 4 * ((maximum + 2) // 3):
            return
        try:
            payload = base64.b64decode(encoded, validate=True)
        except ValueError:
            return
        if not 0 < len(payload) <= maximum:
            return
        received = _number(event["received_qpc"], minimum=0) * 1_000_000_000 // self._frequency
        if received > self._clock():
            return
        if control:
            peer.control_sequence = sequence
            try:
                value = json.loads(payload, object_pairs_hook=_unique_object)
                self._receive_control(peer, value)
            except (ValueError, UnicodeError, RecursionError):
                return
        elif peer.source_nonce is not None and peer.state_ready and peer.source_state.state == "playing" and len(payload) == PACKET_BYTES:
            peer.data_sequence = sequence
            try:
                pose = decode_pose(payload, expected_incarnation=peer.source_nonce)
                floor = peer.source_state
                if (pose.source_generation, pose.continuity) != (floor.source_generation, floor.continuity) \
                        or pose.frame.sequence < floor.sequence:
                    return
                self._incoming.ingest(peer.id, payload, received_ns=received)
                peer.last_received = received
                peer.state = "playing" if pose.frame.playable and not pose.frame.pose_hidden else "waiting"
            except PoseError:
                pass  # NativeInbox retains every accepted source high-water fence.

    def _receive_control(self, peer, value):
        if type(value) is not dict or type(value.get("v")) is not int or value["v"] != 1:
            return
        if value.get("type") == "SOURCE_INFO" and set(value) == {"v", "type", "source_nonce", "name"}:
            source = bytes.fromhex(_nonce(value["source_nonce"]))
            name = display_name(value["name"])
            if peer.source_nonce is not None and source != peer.source_nonce:
                return
            self._incoming.admit(peer.id, source, name=name, hue=(list(self._peers).index(peer.ref) * 120 + 130) % 360)
            peer.source_nonce, peer.name = source, name
            peer.info_received = True
            if peer.state not in ("playing", "paused"):
                peer.state = "waiting"
            self._known_names[peer.code] = name
        elif value.get("type") == "SOURCE_STATE" and set(value) == {
                "v", "type", "source_nonce", "serial", "state", "source_generation", "continuity", "sequence"}:
            if not peer.info_received or peer.source_nonce is None or bytes.fromhex(_nonce(value["source_nonce"])) != peer.source_nonce:
                return
            serial = _number(value["serial"])
            if serial <= peer.source_serial or value["state"] not in ("playing", "paused", "waiting"):
                return
            floor = tuple(_number(value[key], minimum=0) for key in ("source_generation", "continuity", "sequence"))
            if any(floor) and not all(floor) or value["state"] == "playing" and not all(floor):
                return
            previous = peer.source_state
            if previous is not None:
                generation, continuity, sequence = floor
                old_generation, old_continuity, old_sequence = previous.floor
                if generation < old_generation or generation == old_generation and (continuity < old_continuity or sequence < old_sequence):
                    return
                if value["state"] == "playing" and previous.state != "playing" and floor == previous.floor:
                    return
            state = _SourceState(value["state"], *floor)
            if state.state != "playing" or previous is None or state.floor != previous.floor:
                self._incoming.clear(peer.id)
            peer.source_serial, peer.source_state, peer.state_ready = serial, state, True
            if state.state != "playing":
                peer.state = state.state
            elif peer.last_received is None or self._clock() - peer.last_received > STALE_AFTER_NS or previous != state:
                peer.state = "waiting"

    def _handle(self, event):
        if type(event) is not dict or type(event.get("v")) is not int or event["v"] != 1 or len(event) > 20:
            raise ValueError("invalid_event")
        kind = event.get("type")
        handlers = {"ready": self._ready_event, "identity": self._identity_event,
            "invitations": self._invites_event, "room": self._room_event, "peer": self._member_event,
            "peer_ready": self._peer_ready_event, "peer_unready": self._peer_unready_event, "peer_left": self._peer_left_event}
        if kind in ("data_received", "control_received"):
            self._receive(event, kind == "control_received")
        elif kind == "fatal":
            self._transport_failed("transport_failed")
        elif kind in handlers:
            handlers[kind](event)
        elif kind != "stats":
            raise ValueError("unknown_event")

    def _ready_event(self, event):
        frequency = _number(event["qpc_frequency"], maximum=1_000_000_000_000)
        if (event.get("protocol"), event.get("max_payload"), event.get("max_control")) != (1, 1024, 512) \
                or frequency != self._local_frequency():
            raise ValueError("clock_or_protocol_mismatch")
        self._frequency, self._ready = frequency, True

    def _identity_event(self, event):
        code = _code(event["user_code"])
        if self._identity is not None and code != self._identity:
            raise ValueError("identity_changed")
        self._identity = code

    def _invites_event(self, event):
        revision = _number(event["revision"])
        if revision <= self._invite_revision:
            return
        entries = event["entries"]
        if type(entries) is not list or len(entries) > 16:
            raise ValueError("invalid_invitation_list")
        previous = {value[0]: key for key, value in self._invites.items()}
        invites = {}
        for entry in entries:
            if type(entry) is not dict or set(entry) != {"invite_ref", "owner_code", "member_count", "max_members"} \
                    or entry["max_members"] != 4:
                raise ValueError("invalid_invitation")
            ref = _number(entry["invite_ref"])
            if any(value[0] == ref for value in invites.values()):
                raise ValueError("duplicate_invitation")
            invites[previous.get(ref, secrets.token_hex(12))] = (ref, _code(entry["owner_code"]),
                _number(entry["member_count"], maximum=4))
        self._invites, self._invite_revision = invites, revision

    def _room_event(self, event):
        epoch = _number(event["room_epoch"], minimum=0)
        if epoch < self._room_epoch:
            return
        if event["state"] == "left":
            if epoch == self._room_epoch:
                self._end_room(); self._phase = "idle"
            return
        if event["state"] != "joined":
            raise ValueError("invalid_room_state")
        if self._leaving:
            return
        owner = _code(event["owner_code"])
        _number(event["member_count"], maximum=4)
        if type(event["local_is_owner"]) is not bool or event["local_is_owner"] != (owner == self._identity) or not epoch:
            raise ValueError("invalid_room")
        if self._outgoing is None:
            if epoch <= self._room_epoch:
                return
            self._admit_room(epoch)
        elif epoch != self._room_epoch:
            raise ValueError("overlapping_rooms")
        self._owner_code, self._local_owner = owner, event["local_is_owner"]
        self._phase = "hosting" if self._local_owner else "connected"

    def _admit_room(self, epoch):
        # Coordinate with caller cancellation. A timed-out request cannot mount
        # a capture handoff after its caller has revoked local room authority.
        with self._lock:
            self._check_action()
            self._mount_room(epoch)

    def _mount_room(self, epoch):
        self._room_epoch = epoch
        self._source_nonce = secrets.token_bytes(16)
        self._outgoing = SourceHandoff(self._source_nonce, stale_after_ns=STALE_AFTER_NS)
        self._incoming = NativeInbox(secrets.token_bytes(16))
        self._source_state = _SourceState()
        self._state_serial, self._heartbeat_at = 0, self._clock()
        self._publication = self._pose = None
        self._retired.clear()
        try:
            self._local_name = display_name(self._name())
        except Exception:  # noqa: BLE001 - an optional cached display label cannot end a room
            self._local_name = "Player"
        self._recorder.set_live_session(self._outgoing, self._incoming)

    def _member_event(self, event):
        epoch, ref, member, transport = (_number(event[key]) for key in ("room_epoch", "peer_ref", "member_epoch", "transport_epoch"))
        if self._outgoing is None or epoch != self._room_epoch or (ref, member) in self._retired:
            return
        code = _code(event["user_code"])
        peer = self._peers.get(ref)
        if peer is None:
            if len(self._peers) >= 3 or any(p.code == code for p in self._peers.values()) or code == self._identity:
                raise ValueError("invalid_roster")
            peer = self._peers[ref] = _Peer(ref, member, transport, code)
        elif member != peer.member_epoch or code != peer.code:
            raise ValueError("member_identity_changed")
        if transport < peer.transport_epoch:
            return
        if transport != peer.transport_epoch:
            self._incoming.clear(peer.id)
            peer.transport_epoch, peer.local_nonce, peer.remote_nonce = transport, None, None
            peer.sent = peer.data_sequence = peer.control_sequence = 0
            self._reset_peer_transport(peer)
            peer.ready = False
        if event["state"] == "awaiting_binding":
            if peer.state == "disconnected":
                return  # only a fresh transport epoch may leave a terminal failure
            peer.state = "reconnecting" if peer.source_nonce is not None else "connecting"
            if peer.local_nonce is None:
                peer.local_nonce = secrets.token_hex(16)
                self._queue_control("bind_peer", {**self._fields(peer), "local_nonce": peer.local_nonce})
        elif event["state"] == "failed":
            peer.ready, peer.state = False, "disconnected"; self._incoming.clear(peer.id)
            self._controls = [item for item in self._controls if item.fields.get("peer_ref") != peer.ref]
        else:
            raise ValueError("invalid_peer_state")

    def _peer_ready_event(self, event):
        peer = self._peer(event)
        if peer is None or peer.state == "disconnected" or _nonce(event["local_nonce"]) != peer.local_nonce or event["route"] != "relay" or peer.ready:
            return
        peer.remote_nonce = _nonce(event["remote_nonce"])
        peer.ready = True
        payload = json.dumps({"v": 1, "type": "SOURCE_INFO", "source_nonce": self._source_nonce.hex(),
            "name": self._local_name}, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        self._queue_control("send_control", self._fields(peer), payload, kind="info")
        self._emit_source_state((peer,))

    def _peer_unready_event(self, event):
        peer = self._peer(event, transport=False)
        if peer is None:
            return
        transport = _number(event["transport_epoch"])
        if transport < peer.transport_epoch:
            return
        self._incoming.clear(peer.id)
        peer.transport_epoch, peer.local_nonce, peer.remote_nonce = transport, None, None
        peer.ready, peer.state = False, "reconnecting"
        peer.sent = peer.data_sequence = peer.control_sequence = 0
        self._reset_peer_transport(peer)

    @staticmethod
    def _reset_peer_transport(peer):
        # Source state serial, resume floor and NativeInbox fences survive.
        peer.state_ready = peer.info_received = peer.info_sent = False
        peer.required_state_serial = peer.sent_state_serial = 0
        peer.publication = None

    def _peer_left_event(self, event):
        peer = self._peer(event, transport=False)
        if peer is not None:
            self._incoming.remove(peer.id); self._peers.pop(peer.ref)
            self._retired.add((peer.ref, peer.member_epoch))
            if len(self._retired) > 256:
                raise ValueError("membership_history_exhausted")

    def _drain_events(self):
        for _ in range(128):
            self._check_action()
            try:
                generation, event = self._events.get_nowait()
            except queue.Empty:
                break
            if generation != self._generation:
                continue
            try:
                self._handle(event)
            except (KeyError, ValueError, TypeError, OverflowError, RecursionError):
                self._transport_failed("transport_protocol_error")
                break
        with self._lock:
            latest, self._latest_events = self._latest_events, {}
        for (generation, _), event in latest.items():
            self._check_action()
            if generation != self._generation:
                continue
            try:
                self._handle(event)
            except (KeyError, ValueError, TypeError, OverflowError, RecursionError):
                self._transport_failed("transport_protocol_error")
                break

    def _flush_native(self):
        inbox = self._incoming
        if self._channel_retiring and not self._close_channel():
            return
        if inbox is None:
            if self._close_channel():
                self._endpoint = None
            return
        capture = self._recorder.ghost_capture
        endpoint = capture.native_live_endpoint(inbox) if capture is not None and inbox is not None else None
        if endpoint != self._endpoint:
            if not self._close_channel():
                return
            self._endpoint = endpoint
        if inbox is None or endpoint is None:
            return
        if endpoint.qpc_frequency != self._frequency:
            self._close_channel(); self._error = "native_clock_mismatch"; return
        try:
            if self._channel is None:
                if self._channel_factory is None:
                    from sm64_events.ghost.live_channel import LiveChannel
                    self._channel_factory = LiveChannel
                self._channel = self._channel_factory(endpoint)
                self._channel_writer = self._channel
            if self._appearance is not None:
                appearance, hidden = self._appearance()
                inbox.appearance(appearance, hidden=hidden)
            view = inbox.snapshot(now_ns=self._clock())
            if inbox.current(view):
                self._channel.write(view)
                if self._error in ("native_receiver_unavailable", "native_receiver_cleanup_pending", "native_clock_mismatch"):
                    self._error = None
        except CleanupPending as error:
            self._channel = retain(self._channel, error) if self._channel is not None else error.owner
            self._channel_retiring = True
            self._error = "native_receiver_cleanup_pending"
        except (OSError, RuntimeError, ValueError):
            self._close_channel(); self._error = "native_receiver_unavailable"

    def _close_channel(self):
        channel = self._channel
        if channel is not None:
            self._channel_retiring = True
            try:
                if self._disabled_delivery is not None:
                    target, view = self._disabled_delivery
                    try:
                        target.write(view)
                    except ProcessLookupError:
                        pass  # The original source capability already retired.
                    self._disabled_delivery = None
                channel.close()
            except CleanupPending as error:
                self._channel = retain(channel, error)
                self._error = "native_receiver_cleanup_pending"
                return False
            except (OSError, RuntimeError, ValueError):
                self._error = "native_receiver_cleanup_pending"
                return False
        self._channel = None
        self._channel_retiring = False
        self._channel_writer = None
        return True

    def _end_room(self):
        incoming = self._incoming
        self._local_revoke()
        # An actual room departure revokes the native scope. Temporary channel
        # close/reopen is only suspension, owned by LiveChannel.close itself.
        # Keep failed writes and the real mapping owner until disposal succeeds.
        if incoming is not None and self._channel_writer is not None:
            self._disabled_delivery = self._channel_writer, incoming.snapshot(now_ns=self._clock())
        self._close_channel()
        with self._lock:
            self._outgoing = self._incoming = self._source_nonce = None
        if self._channel is None:
            self._endpoint = None
        self._peers.clear(); self._controls.clear()
        self._publication = self._pose = None
        self._source_state = _SourceState()
        self._local_owner, self._owner_code = False, None

    def _transport_failed(self, reason):
        self._end_room()
        transport, self._transport = self._transport, None
        self._generation += 1
        self._ready, self._identity, self._frequency = False, None, 0
        self._room_epoch = 0  # epoch scope is the retired helper, not a replacement helper
        self._invites.clear(); self._invite_revision = 0
        with self._lock:
            self._latest_events.clear()
        self._phase, self._error = "error", reason
        if transport is not None:
            self._retiring_transport = transport
            self._close_retiring_transport()

    def _close_retiring_transport(self):
        transport = self._retiring_transport
        if transport is not None:
            try:
                transport.close()
            except (TransportError, OSError):
                self._error = "transport_cleanup_pending"
                return False
        self._retiring_transport = None
        return True

    def _tick(self):
        self._close_retiring_transport()
        if self._overflow.is_set():
            self._overflow.clear(); self._transport_failed("transport_event_overflow")
        self._drain_events()
        status = self._recorder.live_session_status()
        self._source_status = {key: status.get(key, False if key != "state" else "waiting")
                               for key in ("state", "sharing", "paused", "source_attached")}
        outgoing = self._outgoing
        if outgoing is not None and self._transport is not None and self._frequency:
            self._update_source_state(outgoing)
        self._send_controls()
        if outgoing is self._outgoing and outgoing is not None and self._publication is not None:
            self._send_publication(outgoing, self._publication)
        self._flush_native()
        for peer in self._peers.values():
            if peer.state == "playing" and peer.last_received is not None and self._clock() - peer.last_received > STALE_AFTER_NS:
                peer.state = "waiting"
        self._publish()

    def _update_source_state(self, outgoing):
        now = self._clock()
        value = outgoing.take_latest(now_ns=now)
        if value is not None:
            if value.source_frequency != self._frequency:
                self._error = "source_clock_mismatch"
                value = None
            else:
                self._publication = value
                self._pose = decode_pose(value.packet, expected_incarnation=self._source_nonce)
        old = self._source_state
        fresh = self._publication is not None and outgoing.current(self._publication, now_ns=now)
        state = "paused" if self._source_status["paused"] else "playing" if (
            self._source_status["sharing"] and self._source_status["source_attached"] and fresh
            and self._pose.frame.playable and not self._pose.frame.pose_hidden
            and (value is not None or old.state == "playing")) else "waiting"
        floor = (self._pose.source_generation, self._pose.continuity, self._pose.frame.sequence) if self._pose is not None else (0, 0, 0)
        if old.state == state and old.floor[:2] == floor[:2]:
            floor = old.floor
        current = _SourceState(state, *floor)
        if current != old:
            self._source_state = current
            self._emit_source_state(tuple(p for p in self._peers.values() if p.ready))
            self._heartbeat_at = now
        elif now - self._heartbeat_at >= _HEARTBEAT_NS:
            self._emit_source_state(tuple(p for p in self._peers.values() if p.ready))
            self._heartbeat_at = now

    def _emit_source_state(self, peers):
        if self._state_serial == _U64:
            self._transport_failed("transport_namespace_exhausted")
            return
        self._state_serial += 1
        state = self._source_state
        payload = json.dumps({"v": 1, "type": "SOURCE_STATE", "source_nonce": self._source_nonce.hex(),
            "serial": self._state_serial, "state": state.state, "source_generation": state.source_generation,
            "continuity": state.continuity, "sequence": state.sequence}, separators=(",", ":")).encode("ascii")
        for peer in peers:
            peer.required_state_serial = self._state_serial
            self._queue_control("send_control", self._fields(peer), payload, kind="state", serial=self._state_serial)

    def _send_publication(self, outgoing, value):
        if self._source_state.state != "playing":
            return
        captured_qpc = self._pose.frame.qpc
        if captured_qpc <= 0:
            return
        for peer in list(self._peers.values()):
            if (not peer.ready or not peer.info_sent or peer.sent_state_serial < peer.required_state_serial
                    or peer.publication is value or not outgoing.current(value, now_ns=self._clock())):
                continue
            peer.sent += 1
            if peer.sent > _U64:
                self._transport_failed("transport_namespace_exhausted");break
            try:
                if self._transport.latest(payload=value.packet, **self._fields(peer), sample_id=peer.sent, sample_qpc=captured_qpc):
                    peer.publication = value
            except TransportError:
                self._transport_failed("transport_failed");break

    def _run(self):
        try:
            while not self._stop.is_set():
                try:
                    request = self._actions.get_nowait()
                except queue.Empty:
                    self._observe_tick(); self._wake.wait(.01); self._wake.clear(); continue
                future = request.future
                if not future.set_running_or_notify_cancel():
                    continue
                self._active_action = request
                try:
                    self._check_action()
                    result = self._execute(request.name, request.arguments)
                    self._check_action()
                except RoomError as error:
                    self._error = str(error)
                    if self._phase in ("starting", "joining", "leaving"):
                        self._phase = "hosting" if self._outgoing is not None and self._local_owner else \
                            "connected" if self._outgoing is not None else "idle"
                    self._publish();future.set_exception(error)
                except Exception:  # noqa: BLE001 - a private provider cannot stop recorder ownership
                    self._transport_failed("room_operation_failed")
                    future.set_exception(RoomError("room_operation_failed"))
                else:
                    future.set_result(result)
                finally:
                    self._active_action = None
                self._observe_tick()
        finally:
            self._finish_shutdown()

    def _finish_shutdown(self):
        self._end_room()
        transport, self._transport = self._transport, None
        if transport is not None:
            self._retiring_transport = transport
        while not self._actions.empty():
            future = self._actions.get_nowait().future
            if not future.done():
                future.set_exception(RoomError("room_closed"))
        # A bounded public join may report pending, but this sole executor
        # keeps the exact retained owners until release really succeeds.
        while True:
            channel_closed = self._close_channel()
            transport_closed = self._close_retiring_transport()
            if channel_closed and transport_closed:
                break
            self._phase = "closing"; self._publish()
            self._wake.wait(.25); self._wake.clear()
        self._phase = "closed"; self._publish()

    def _observe_tick(self):
        try:
            self._tick()
        except Exception:  # noqa: BLE001 - a passive observation cannot kill the room executor
            self._transport_failed("room_observation_failed")
            self._publish()

    def close(self, timeout_s=15):
        self._local_revoke(); self._stop.set(); self._wake.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout_s)
            if thread.is_alive():
                raise RoomError("room_cleanup_pending")
            if self._channel is not None:
                raise RoomError("room_cleanup_pending")
            if self._retiring_transport is not None:
                raise RoomError("room_cleanup_pending")
        if thread is None:
            self._phase = "closed"; self._publish()


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate_field")
        value[key] = item
    return value


def _message(reason):
    if reason is None:
        return None
    return {"transport_unavailable": "Friends couldn’t connect. Try again.",
        "transport_timeout": "The connection timed out. Try again.",
        "invitation_expired": "That invite expired. Ask your friend for a new invite.",
        "invalid_identity": "Check that player code and try again.",
        "cannot_invite": "This room is full or closing.",
        "cannot_invite_self": "Use your friend’s player code.",
        "cannot_remove_player": "Only the host can remove another player.",
        "already_in_room": "Leave your current room first.",
        "native_receiver_unavailable": "Ghosts are temporarily unavailable for this game.",
        "native_clock_mismatch": "Ghosts are unavailable for this game.",
        "source_clock_mismatch": "Waiting for the game’s clock to reconnect.",
        "native_receiver_cleanup_pending": "Finishing the previous ghost connection.",
        "transport_cleanup_pending": "Finishing the previous connection.",
    }.get(reason, "The connection stopped. Try again.")
