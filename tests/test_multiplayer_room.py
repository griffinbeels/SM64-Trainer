"""Actual lazy room worker with private deterministic transport/native shells."""
import base64
from concurrent.futures import Future
from dataclasses import dataclass
import json
import struct
import threading
import time

import pytest

from ghost_native_fixture import frame, producer
from sm64_events.core.qpcclock import qpc_ns
from sm64_events.multiplayer.eos_process import TransportError
from sm64_events.multiplayer.native_protocol import Update, STALE_AFTER_NS
from sm64_events.multiplayer.protocol import PACKET_BYTES, PoseError, decode_pose, encode_pose
from sm64_events.multiplayer.room import RoomError, RoomService
from sm64_events.replay.ownedclose import CleanupPending

FREQUENCY = 1_000_000_000
LOCAL, REMOTE, OTHER = "ET1-" + "11" * 16, "ET1-" + "22" * 16, "ET1-" + "33" * 16
REMOTE_SOURCE = bytes.fromhex("44" * 16)


def wait_for(predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.005)
    raise AssertionError("room worker did not reach the expected contract state")


def native_frame(sequence=1, **changes):
    raw = bytearray(frame(sequence=sequence, **changes))
    struct.pack_into("<H", raw, 112, sequence & 0xffff)
    return bytes(raw)


@dataclass(frozen=True)
class Endpoint:
    qpc_frequency: int = FREQUENCY
    generation: int = 1


class Recorder:
    def __init__(self):
        self.pair = None, None
        self.endpoint = Endpoint()
        self.ghost_capture = self
        self.paused = False

    def set_live_session(self, outgoing=None, incoming=None):
        self.pair = outgoing, incoming

    def native_live_endpoint(self, incoming):
        return self.endpoint if incoming is self.pair[1] else None

    def live_session_status(self):
        return {"state": "attached" if self.pair[0] else "off", "sharing": bool(self.pair[0]) and not self.paused,
                "paused": self.paused, "source_attached": bool(self.pair[0])}


class Channel:
    def __init__(self, endpoint):
        self.endpoint, self.views, self.closed = endpoint, [], False
        self.close_failures = 0
        self.disable_failures = 0
        self.disable_retired = False

    def write(self, view):
        value = Update.from_buffer_copy(view.wire)
        if not value.enabled:
            if self.disable_retired:
                raise ProcessLookupError("test source capability retired")
            if self.disable_failures:
                self.disable_failures -= 1
                raise BlockingIOError("test disabled view temporarily busy")
        self.views.append(value)

    def close(self):
        if self.close_failures:
            self.close_failures -= 1
            raise BlockingIOError("test mapping temporarily busy")
        self.closed = True


class Transport:
    def __init__(self, notify):
        self.notify, self.calls, self.sent, self.controls = notify, [], [], []
        self.closed = False
        self.close_blocked = False
        self.failures = {}
        self.room_epoch = 0
        self.controls_blocked, self.control_futures = False, []

    def event(self, kind, **fields):
        self.notify({"v": 1, "type": kind, **fields})

    def start(self):
        self.event("ready", protocol=1, max_payload=1024, max_control=512, qpc_frequency=FREQUENCY)
        self.event("identity", user_code=LOCAL)

    def submit(self, op, **fields):
        self.calls.append((op, fields))
        future = Future()
        failures = self.failures.get(op, [])
        if failures:
            future.set_exception(TransportError(failures.pop(0))); return future
        if op == "identity":
            self.event("identity", user_code=LOCAL)
        elif op == "create_room":
            self.room_epoch += 1
            self.event("room", room_epoch=self.room_epoch, state="joined", owner_code=LOCAL,
                       local_is_owner=True, member_count=1)
        elif op == "accept_invite":
            self.room_epoch += 1
            self.event("room", room_epoch=self.room_epoch, state="joined", owner_code=REMOTE,
                       local_is_owner=False, member_count=2)
        elif op == "leave":
            self.event("room", room_epoch=self.room_epoch, state="left", owner_code="",
                       local_is_owner=False, member_count=0)
        elif op == "reset_peer":
            current = {**fields, 'transport_epoch': fields['transport_epoch'] + 1}
            self.event('peer_unready', **current, reason='explicit_reset')
            self.event('peer', **current, user_code=REMOTE, state='awaiting_binding')
        future.set_result(None)
        return future

    def control(self, payload, **fields):
        self.controls.append((payload, fields)); future = Future(); self.control_futures.append(future)
        if not self.controls_blocked:
            future.set_result(None)
        return future

    def latest(self, payload, **fields):
        self.sent.append((payload, fields)); return True

    def close(self):
        if self.close_blocked:
            raise TransportError("transport_cleanup_pending")
        self.closed = True


@pytest.fixture
def rooms():
    services = []

    def make(**options):
        recorder, transports, channels = Recorder(), [], []

        def factory(notify):
            value = Transport(notify); transports.append(value); return value

        def channel_factory(endpoint):
            value = Channel(endpoint); channels.append(value); return value

        options.setdefault("qpc_frequency", lambda: FREQUENCY)
        options.setdefault("channel_factory", channel_factory)
        service = RoomService(recorder, factory, player_name=lambda: "Local name", **options)
        services.append(service)
        return service, recorder, transports, channels

    yield make
    for service in services:
        service.close()


def peer_ready(service, transport, *, epoch=1, ref=1, member=1, transport_epoch=1, code=REMOTE):
    fields = {"room_epoch": epoch, "peer_ref": ref, "member_epoch": member, "transport_epoch": transport_epoch}
    before = len(transport.calls)
    transport.event("peer", **fields, user_code=code, state="awaiting_binding")
    binding = wait_for(lambda: next((f for op, f in transport.calls[before:] if op == "bind_peer"), None))
    assert set(binding) == {"room_epoch", "peer_ref", "member_epoch", "transport_epoch", "local_nonce"}
    assert all(binding[key] == value for key, value in fields.items())
    assert len(bytes.fromhex(binding["local_nonce"])) == 16
    nonce = binding["local_nonce"]
    transport.event("peer_ready", **fields, local_nonce=nonce, remote_nonce="55" * 16, route="relay")
    wait_for(lambda: any(f["transport_epoch"] == transport_epoch for _, f in transport.controls))
    return {**fields, "local_nonce": nonce, "remote_nonce": "55" * 16}


def source_info(transport, fields, *, sequence=1, source=REMOTE_SOURCE, name="Remote name", state="playing", **extra):
    value = {"v": 1, "type": "SOURCE_INFO", "source_nonce": source.hex(), "name": name, **extra}
    received = qpc_ns()
    transport.event("control_received", **fields, sequence=sequence, received_qpc=received,
                    payload_b64=base64.b64encode(json.dumps(value).encode()).decode())
    if state is not None and not extra:
        source_state(transport, fields, sequence=sequence + 1, serial=sequence + fields["transport_epoch"] - 1,
                     source=source, state=state)
    return received


def source_state(transport, fields, *, sequence, serial, source=REMOTE_SOURCE, state="playing",
                 source_generation=1, continuity=1, floor=1, **extra):
    value = {"v": 1, "type": "SOURCE_STATE", "source_nonce": source.hex(), "serial": serial,
             "state": state, "source_generation": source_generation, "continuity": continuity, "sequence": floor, **extra}
    transport.event("control_received", **fields, sequence=sequence, received_qpc=qpc_ns(),
                    payload_b64=base64.b64encode(json.dumps(value).encode()).decode())


def pose(transport, fields, *, packet_sequence=1, transport_sequence=1, received=None, source=REMOTE_SOURCE,
         source_generation=1, continuity=1):
    packet = encode_pose(native_frame(packet_sequence), participant_incarnation=source,
        source_generation=source_generation, elapsed_ns=packet_sequence, continuity=continuity, rom_layout=1, rom_generation=3)
    transport.event("data_received", **fields, sequence=transport_sequence,
        received_qpc=qpc_ns() if received is None else received,
        payload_b64=base64.b64encode(packet).decode())
    return packet


def test_cached_status_is_lazy_public_and_mutation_safe(rooms):
    service, recorder, transports, _ = rooms()
    assert service.snapshot()["available"] and service.snapshot()["phase"] == "idle"
    assert service.snapshot()["room"] is None and not transports and recorder.pair == (None, None)
    service.snapshot()["source"]["state"] = "corrupted"
    assert service.snapshot()["source"]["state"] == "off"
    assert service.identity() == LOCAL and len(transports) == 1
    public = json.dumps(service.snapshot())
    assert LOCAL not in public and "ET1-" not in public and "nonce" not in public
    assert recorder.pair == (None, None)


def test_unavailable_build_starts_nothing_and_explains_it(rooms):
    service, _, transports, _ = rooms(available=False)
    assert not service.snapshot()["available"] and "build" in service.snapshot()["unavailable_reason"]
    with pytest.raises(RoomError, match="transport_unavailable"):
        service.host()
    assert not transports


def test_host_membership_control_and_native_mapping_are_reachable(rooms):
    service, recorder, transports, channels = rooms()
    snapshot = service.host(); transport = transports[0]
    assert snapshot["phase"] == "hosting" and snapshot["room"]["owner"] is True
    assert snapshot["room"]["max_members"] == 4 and snapshot["can_invite"]
    fields = peer_ready(service, transport)
    assert len(service.snapshot()["room"]["members"]) == 2
    source_info(transport, fields)
    wait_for(lambda: service.snapshot()["room"]["members"][1]["name"] == "Remote name")
    stamp = qpc_ns();pose(transport, fields, received=stamp)
    native = wait_for(lambda: channels and channels[-1].views and
        next((v for v in reversed(channels[-1].views) if v.peers[0].present), None))
    assert native.peers[0].received_ns == stamp
    assert native.peers[0].member and bytes(native.peers[0].incarnation) == REMOTE_SOURCE
    assert wait_for(lambda: service.snapshot()["room"]["members"][1]["state"] == "playing")
    public = json.dumps(service.snapshot())
    assert all(secret not in public for secret in (LOCAL, REMOTE, REMOTE_SOURCE.hex(), fields["local_nonce"]))
    recorder.paused = True
    assert wait_for(lambda: service.snapshot()["room"]["members"][0]["state"] == "paused")
    assert "paused" in service.snapshot()["source"]["reason"]


def test_source_info_cannot_create_members_and_unknown_fields_are_rejected(rooms):
    service, _, transports, channels = rooms();service.host();transport = transports[0]
    unknown = dict(room_epoch=1, peer_ref=7, member_epoch=7, transport_epoch=1,
                   local_nonce="66" * 16, remote_nonce="55" * 16)
    source_info(transport, unknown)
    fields = peer_ready(service, transport)
    source_info(transport, fields, admin=True)
    pose(transport, fields)
    time.sleep(.04)
    assert service.snapshot()["room"]["members"][1]["name"] == "Player"
    assert not any(v.peers[0].member for c in channels for v in c.views)
    source_info(transport, fields, sequence=2)
    assert wait_for(lambda: service.snapshot()["room"]["members"][1]["name"] == "Remote name")


def test_local_dequeue_receipt_is_not_replaced_by_parent_processing_time(rooms):
    service, _, transports, channels = rooms();service.host();transport = transports[0]
    fields = peer_ready(service, transport);source_info(transport, fields)
    old = qpc_ns() - STALE_AFTER_NS - 10_000_000
    pose(transport, fields, received=old)
    time.sleep(.04)
    assert not any(v.peers[0].present for c in channels for v in c.views)
    assert service.snapshot()["room"]["members"][1]["state"] == "waiting"


def test_reconnect_uses_fresh_transport_nonce_but_retains_source_and_replay_fences(rooms):
    service, recorder, transports, channels = rooms();service.host();transport = transports[0]
    first = peer_ready(service, transport);source_info(transport, first)
    pose(transport, first, packet_sequence=9)
    wait_for(lambda: channels and any(v.peers[0].present for v in channels[-1].views))
    outgoing = recorder.pair[0]
    local_source = json.loads(transport.controls[0][0])["source_nonce"]
    transport.event("peer_unready", room_epoch=1, peer_ref=1, member_epoch=1, transport_epoch=2, reason="transport_interrupted")
    assert wait_for(lambda: service.snapshot()["room"]["members"][1]["state"] == "reconnecting")
    second = peer_ready(service, transport, transport_epoch=2)
    assert first["local_nonce"] != second["local_nonce"] and recorder.pair[0] is outgoing
    assert json.loads(transport.controls[-1][0])["source_nonce"] == local_source
    source_info(transport, second)
    pose(transport, second, packet_sequence=9)  # transport sequence restarted; application sequence cannot.
    time.sleep(.04)
    assert not channels[-1].views[-1].peers[0].present
    pose(transport, second, packet_sequence=10, transport_sequence=2)
    wait_for(lambda: channels[-1].views[-1].peers[0].present)
    before = len(transport.controls)
    source_info(transport, first, sequence=99, name="Retired transport")
    time.sleep(.03)
    assert len(transport.controls) == before and service.snapshot()["room"]["members"][1]["name"] == "Remote name"


def test_metadata_cannot_replace_source_identity_within_membership(rooms):
    service, _, transports, channels = rooms();service.host();transport = transports[0]
    fields = peer_ready(service, transport);source_info(transport, fields)
    source_info(transport, fields, sequence=2, source=bytes.fromhex("77" * 16), name="Replacement")
    pose(transport, fields, source=bytes.fromhex("77" * 16))
    time.sleep(.04)
    assert service.snapshot()["room"]["members"][1]["name"] == "Remote name"
    assert not any(v.peers[0].present for c in channels for v in c.views)


def test_departed_membership_does_not_return_from_late_announcement(rooms):
    service, _, transports, _ = rooms();service.host();transport = transports[0]
    fields = peer_ready(service, transport);source_info(transport, fields)
    transport.event("peer_left", room_epoch=1, peer_ref=1, member_epoch=1, transport_epoch=1, reason="member_removed")
    wait_for(lambda: len(service.snapshot()["room"]["members"]) == 1)
    transport.event("peer", room_epoch=1, peer_ref=1, member_epoch=1, transport_epoch=1, user_code=REMOTE, state="awaiting_binding")
    time.sleep(.04)
    assert len(service.snapshot()["room"]["members"]) == 1


def test_current_member_can_invite_but_only_host_can_remove_players(rooms):
    service, _, transports, _ = rooms();service.identity();transport = transports[0]
    transport.event("invitations", revision=1, entries=[{"invite_ref": 3, "owner_code": REMOTE, "member_count": 1, "max_members": 4}])
    invitation = wait_for(lambda: service.snapshot()["invitations"])[0]
    assert REMOTE not in json.dumps(invitation) and invitation["id"] != "3"
    snapshot = service.join(invitation["id"])
    assert snapshot["phase"] == "connected" and snapshot["room"]["owner"] is False and snapshot["can_invite"]
    service.invite(OTHER)
    assert transports[0].calls[-1] == ("invite", {"room_epoch": 1, "user_code": OTHER})
    with pytest.raises(RoomError, match="cannot_remove_player"):
        service.kick("self")


def test_busy_control_retry_is_bounded_and_leave_revokes_before_sdk(rooms):
    service, recorder, transports, channels = rooms();service.identity();transport = transports[0]
    transport.failures["create_room"] = ["busy", "busy"]
    service.host()
    assert len([1 for op, _ in transport.calls if op == "create_room"]) == 3
    fields = peer_ready(service, transport);source_info(transport, fields)
    outgoing, incoming = recorder.pair
    original = transport.submit

    def checked(op, **fields):
        if op == "leave":
            assert recorder.pair == (None, None)
            with pytest.raises(PoseError):
                outgoing.begin_capture()
            assert not incoming.current(incoming.snapshot(now_ns=qpc_ns()))
        return original(op, **fields)

    transport.submit = checked
    assert service.leave()["room"] is None
    wait_for(lambda: channels[-1].closed)


def test_outgoing_uses_current_handoff_without_another_source_consumer(rooms):
    service, recorder, transports, _ = rooms();service.host();transport = transports[0]
    fields = peer_ready(service, transport)
    outgoing = recorder.pair[0];owner = outgoing.begin_capture()
    captured = qpc_ns() - 20_000_000
    outgoing.observe(owner, producer(rom_layout=1, qpc_frequency=FREQUENCY), (native_frame(qpc=captured),),
                     received_ns=qpc_ns())
    payload, sent = wait_for(lambda: transport.sent and transport.sent[-1])
    assert len(payload) == PACKET_BYTES and sent["sample_id"] == 1 and sent["sample_qpc"] == captured
    outgoing.source_unavailable(owner)
    count = len(transport.sent);time.sleep(.04)
    assert len(transport.sent) == count
    assert fields["transport_epoch"] == sent["transport_epoch"]


def test_wrong_helper_clock_fails_closed_without_recording_demand(rooms):
    service, recorder, transports, _ = rooms(qpc_frequency=lambda: 10_000_000)
    with pytest.raises(RoomError):
        service.identity()
    assert recorder.pair == (None, None) and transports[0].closed
    assert service.snapshot()["phase"] == "error" and "ET1-" not in json.dumps(service.snapshot())


def test_close_is_lazy_and_retired_helper_events_cannot_resurrect_room(rooms):
    service, recorder, transports, _ = rooms();service.close()
    assert not transports and service.snapshot()["phase"] == "closed"
    other, recorder, transports, _ = rooms();other.host();transport = transports[0]
    transport.event("fatal", code="authentication_lost")
    wait_for(lambda: other.snapshot()["phase"] == "error")
    assert recorder.pair == (None, None) and transport.closed
    transport.event("room", room_epoch=99, state="joined", owner_code=LOCAL, local_is_owner=True, member_count=1)
    time.sleep(.04)
    assert other.snapshot()["room"] is None
    assert "nonce" not in json.dumps(other.snapshot())


def test_channel_cleanup_retains_and_retries_a_temporarily_busy_handle(rooms):
    service, _, _, channels = rooms();service.host()
    wait_for(lambda: channels)
    channel = channels[-1];channel.close_failures = 2
    service.leave()
    wait_for(lambda: channel.closed)
    assert channel.close_failures == 0


def test_partial_native_constructor_owner_retires_before_replacement(rooms):
    partial, calls = Channel(Endpoint()), []
    partial.close_failures = 2

    def factory(endpoint):
        calls.append(endpoint)
        if len(calls) == 1:
            raise CleanupPending(partial, OSError("test constructor release failed"))
        assert partial.closed
        return Channel(endpoint)

    service, _, _, _ = rooms(channel_factory=factory)
    service.host()
    wait_for(lambda: len(calls) == 2)
    assert partial.closed and partial.close_failures == 0


def test_retiring_transport_blocks_replacement_until_close_succeeds(rooms):
    service, recorder, transports, _ = rooms();service.host();transport = transports[0]
    transport.close_blocked = True
    transport.event("fatal", code="authentication_lost")
    wait_for(lambda: service.snapshot()["phase"] == "error")
    try:
        with pytest.raises(RoomError, match="transport_cleanup_pending"):
            service.identity()
        assert len(transports) == 1 and recorder.pair == (None, None)
    finally:
        transport.close_blocked = False
    wait_for(lambda: transport.closed)
    assert service.identity() == LOCAL and len(transports) == 2
    assert service.host()["room"] is not None  # replacement helper epochs restart at one


def test_source_clock_must_match_verified_helper_clock(rooms):
    service, recorder, transports, _ = rooms();service.host();transport = transports[0]
    peer_ready(service, transport)
    outgoing = recorder.pair[0];owner = outgoing.begin_capture()
    outgoing.observe(owner, producer(rom_layout=1, qpc_frequency=30), (native_frame(),), received_ns=qpc_ns())
    wait_for(lambda: service.snapshot()["error"] is not None)
    assert not transport.sent


def test_temporary_start_failure_keeps_configured_service_retryable(rooms, monkeypatch):
    original, calls = Transport.start, []

    def fail_once(transport):
        calls.append(transport)
        if len(calls) == 1:
            raise TransportError("private login details must not reach public state")
        return original(transport)

    monkeypatch.setattr(Transport, "start", fail_once)
    service, recorder, transports, _ = rooms()
    with pytest.raises(RoomError, match="transport_unavailable"):
        service.identity()
    snapshot = service.snapshot()
    assert snapshot["available"] and snapshot["phase"] == "error" and transports[0].closed
    assert "private login" not in json.dumps(snapshot) and recorder.pair == (None, None)
    assert service.host()["room"] is not None and len(transports) == 2


def controls(transport, kind):
    return [json.loads(payload) for payload, _fields in transport.controls if json.loads(payload)["type"] == kind]


def present(channels):
    return bool(channels and channels[-1].views and channels[-1].views[-1].peers[0].present)


def test_generated_bind_is_submitted_before_info_state_and_data(rooms):
    service, recorder, transports, _ = rooms();service.host();transport = transports[0]
    transport.controls_blocked = True
    fields = peer_ready(service, transport)
    binding = next(fields for op, fields in transport.calls if op == "bind_peer")
    assert binding == {key: fields[key] for key in ("room_epoch", "peer_ref", "member_epoch", "transport_epoch", "local_nonce")}
    assert [json.loads(payload)["type"] for payload, _ in transport.controls] == ["SOURCE_INFO"]
    outgoing = recorder.pair[0];owner = outgoing.begin_capture()
    outgoing.observe(owner, producer(rom_layout=1, qpc_frequency=FREQUENCY),
        (native_frame(5, qpc=qpc_ns()),), received_ns=qpc_ns())
    time.sleep(.03)
    assert not transport.sent and not controls(transport, "SOURCE_STATE")
    transport.control_futures[0].set_result(None)
    state = wait_for(lambda: controls(transport, "SOURCE_STATE"))[-1]
    assert state["state"] == "playing" and state["sequence"] == 5
    assert len(json.dumps(state).encode()) <= 512 and not transport.sent
    transport.controls_blocked = False;transport.control_futures[-1].set_result(None)
    wait_for(lambda: transport.sent)


def test_info_alone_never_admits_data_and_playing_state_sets_resume_floor(rooms):
    service, _, transports, channels = rooms();service.host();transport = transports[0]
    fields = peer_ready(service, transport);source_info(transport, fields, state=None)
    pose(transport, fields)
    time.sleep(.03)
    assert not present(channels)
    source_state(transport, fields, sequence=2, serial=1, floor=5)
    pose(transport, fields, packet_sequence=4, transport_sequence=2)
    time.sleep(.03)
    assert not present(channels)
    pose(transport, fields, packet_sequence=5, transport_sequence=3)
    wait_for(lambda: present(channels))


def test_pause_clears_immediately_and_resume_refuses_unseen_pre_pause_frames(rooms):
    service, _, transports, channels = rooms();service.host();transport = transports[0]
    fields = peer_ready(service, transport);source_info(transport, fields)
    pose(transport, fields, packet_sequence=10)
    wait_for(lambda: present(channels))
    source_state(transport, fields, sequence=3, serial=2, state="paused", floor=10)
    wait_for(lambda: service.snapshot()["room"]["members"][1]["state"] == "paused" and not present(channels))
    pose(transport, fields, packet_sequence=11, transport_sequence=2)
    time.sleep(.03)
    assert not present(channels)
    source_state(transport, fields, sequence=4, serial=3, continuity=2, floor=20)
    for serial, sequence, continuity in ((3, 19, 1), (4, 21, 1), (5, 19, 2)):
        pose(transport, fields, packet_sequence=sequence, transport_sequence=serial, continuity=continuity)
    time.sleep(.03)
    assert not present(channels)
    pose(transport, fields, packet_sequence=20, transport_sequence=6, continuity=2)
    wait_for(lambda: present(channels))
    assert service.snapshot()["room"]["members"][1]["state"] == "playing"


def test_waiting_source_state_clears_without_granting_membership_or_reauthorizing(rooms):
    service, recorder, transports, channels = rooms();service.host();transport = transports[0]
    fields = peer_ready(service, transport);source_info(transport, fields)
    incoming = recorder.pair[1]
    pose(transport, fields, packet_sequence=9)
    wait_for(lambda: present(channels))
    source_state(transport, fields, sequence=3, serial=2, state="waiting", floor=9)
    wait_for(lambda: not present(channels))
    source_state(transport, fields, sequence=4, serial=3, floor=9)  # no actual new occurrence
    pose(transport, fields, packet_sequence=10, transport_sequence=2)
    time.sleep(.03)
    assert not present(channels) and recorder.pair[1] is incoming
    source_state(transport, fields, sequence=5, serial=4, floor=11)
    pose(transport, fields, packet_sequence=10, transport_sequence=3)
    time.sleep(.03)
    assert not present(channels)
    pose(transport, fields, packet_sequence=11, transport_sequence=4)
    wait_for(lambda: present(channels))
    source_info(transport, fields, sequence=6, state=None)
    source_state(transport, fields, sequence=7, serial=5, floor=11)
    received = channels[-1].views[-1].peers[0].received_ns
    pose(transport, fields, packet_sequence=11, transport_sequence=5)
    time.sleep(.03)
    assert channels[-1].views[-1].peers[0].received_ns == received


def test_state_requires_current_member_and_source_info_nonce(rooms):
    service, _, transports, channels = rooms();service.host();transport = transports[0]
    fields = peer_ready(service, transport)
    source_state(transport, fields, sequence=1, serial=99)
    source_info(transport, fields, sequence=2, state=None)
    source_state(transport, fields, sequence=3, serial=1)
    pose(transport, fields)
    wait_for(lambda: present(channels))
    source_state(transport, fields, sequence=4, serial=99, source=bytes.fromhex("77" * 16), state="paused")
    outsider = {**fields, "peer_ref": 99}
    source_state(transport, outsider, sequence=5, serial=99, state="paused")
    time.sleep(.03)
    assert present(channels) and service.snapshot()["room"]["members"][1]["state"] == "playing"


@pytest.mark.parametrize("change", [
    {"serial": 1}, {"serial": 0}, {"serial": True}, {"serial": 1 << 64}, {"state": "admin"},
    {"source_generation": 0}, {"continuity": 0}, {"floor": 0}, {"floor": True}, {"admin": True},
])
def test_stale_or_malformed_state_cannot_withdraw_current_peer(rooms, change):
    service, _, transports, channels = rooms();service.host();transport = transports[0]
    fields = peer_ready(service, transport);source_info(transport, fields)
    pose(transport, fields, packet_sequence=9)
    wait_for(lambda: present(channels))
    values = {"sequence": 3, "serial": 2, "state": "paused", **change}
    source_state(transport, fields, **values)
    time.sleep(.025)
    assert present(channels) and service.snapshot()["room"]["members"][1]["state"] == "playing"


def test_state_floor_never_regresses_generation_continuity_or_sequence(rooms):
    service, _, transports, channels = rooms();service.host();transport = transports[0]
    fields = peer_ready(service, transport);source_info(transport, fields, state=None)
    source_state(transport, fields, sequence=2, serial=5, source_generation=2, continuity=3, floor=20)
    pose(transport, fields, packet_sequence=20, source_generation=2, continuity=3)
    wait_for(lambda: present(channels))
    for sequence, generation, continuity, floor in ((3, 1, 9, 99), (4, 2, 2, 99), (5, 2, 3, 19)):
        source_state(transport, fields, sequence=sequence, serial=6, state="paused",
                     source_generation=generation, continuity=continuity, floor=floor)
    time.sleep(.03)
    assert present(channels)
    source_state(transport, fields, sequence=6, serial=6, state="paused", source_generation=2, continuity=3, floor=20)
    wait_for(lambda: service.snapshot()["room"]["members"][1]["state"] == "paused")


class Clock:
    def __init__(self):
        self.now = qpc_ns() + 1_000_000_000

    def __call__(self):
        return self.now


def test_heartbeat_never_refreshes_pose_receipt_or_replays_a_pose(rooms):
    clock = Clock()
    service, _, transports, channels = rooms(clock_ns=clock);service.host();transport = transports[0]
    fields = peer_ready(service, transport);source_info(transport, fields)
    receipt = clock.now
    pose(transport, fields, received=receipt)
    wait_for(lambda: present(channels))
    clock.now += STALE_AFTER_NS + 1
    source_state(transport, fields, sequence=3, serial=2)
    wait_for(lambda: not present(channels))
    peer = next(iter(service._peers.values()))
    assert peer.source_serial == 2 and peer.last_received == receipt
    assert service.snapshot()["room"]["members"][1]["state"] == "waiting"
    source_state(transport, fields, sequence=4, serial=3)
    time.sleep(.025)
    assert not present(channels) and peer.last_received == receipt
    pose(transport, fields, packet_sequence=2, transport_sequence=2, received=clock.now)
    wait_for(lambda: present(channels))


def test_local_heartbeat_is_bounded_and_unpause_without_pose_stays_waiting(rooms):
    clock = Clock()
    service, recorder, transports, _ = rooms(clock_ns=clock);service.host();transport = transports[0]
    peer_ready(service, transport)
    first = wait_for(lambda: controls(transport, "SOURCE_STATE"))[-1]
    assert first["state"] == "waiting" and (first["source_generation"], first["continuity"], first["sequence"]) == (0, 0, 0)
    clock.now += 1_000_000_000
    second = wait_for(lambda: [v for v in controls(transport, "SOURCE_STATE") if v["serial"] > first["serial"]])[-1]
    count = len(controls(transport, "SOURCE_STATE"));time.sleep(.035)
    assert len(controls(transport, "SOURCE_STATE")) == count and not transport.sent
    recorder.paused = True
    paused = wait_for(lambda: [v for v in controls(transport, "SOURCE_STATE") if v["state"] == "paused"])[-1]
    recorder.paused = False
    resumed = wait_for(lambda: [v for v in controls(transport, "SOURCE_STATE") if v["serial"] > paused["serial"]])[-1]
    assert resumed["state"] == "waiting" and resumed["sequence"] == 0 and second["serial"] < paused["serial"]
    assert not transport.sent


def test_sender_resume_uses_first_actual_new_publication_and_no_fake_pose(rooms):
    service, recorder, transports, _ = rooms();service.host();transport = transports[0]
    peer_ready(service, transport)
    outgoing = recorder.pair[0];owner = outgoing.begin_capture()
    source = producer(rom_layout=1, qpc_frequency=FREQUENCY)
    outgoing.observe(owner, source, (native_frame(10, qpc=qpc_ns()),), received_ns=qpc_ns())
    wait_for(lambda: transport.sent)
    outgoing.withdraw_capture(owner);outgoing.end_capture(owner);recorder.paused = True
    paused = wait_for(lambda: [v for v in controls(transport, "SOURCE_STATE") if v["state"] == "paused"])[-1]
    recorder.paused = False
    waiting = wait_for(lambda: [v for v in controls(transport, "SOURCE_STATE") if v["serial"] > paused["serial"]])[-1]
    assert waiting["state"] == "waiting"
    count = len(transport.sent);time.sleep(.03)
    assert len(transport.sent) == count
    owner = outgoing.begin_capture()
    outgoing.observe(owner, source, (native_frame(20, qpc=qpc_ns()),), received_ns=qpc_ns())
    packet, _fields = wait_for(lambda: len(transport.sent) > count and transport.sent[-1])
    state = controls(transport, "SOURCE_STATE")[-1]
    decoded = decode_pose(packet, expected_incarnation=bytes.fromhex(state["source_nonce"]))
    assert state["state"] == "playing" and state["serial"] > waiting["serial"]
    assert (state["source_generation"], state["continuity"], state["sequence"]) == (
        decoded.source_generation, decoded.continuity, decoded.frame.sequence) == (1, 2, 20)


def test_fast_source_withdraw_resume_sets_fresh_floor_even_when_pause_boolean_is_unseen(rooms):
    service, recorder, transports, _ = rooms();service.host();transport = transports[0]
    peer_ready(service, transport)
    outgoing = recorder.pair[0];owner = outgoing.begin_capture();source = producer(rom_layout=1, qpc_frequency=FREQUENCY)
    outgoing.observe(owner, source, (native_frame(10, qpc=qpc_ns()),), received_ns=qpc_ns())
    wait_for(lambda: transport.sent)
    outgoing.withdraw_capture(owner);outgoing.end_capture(owner)
    owner = outgoing.begin_capture()
    outgoing.observe(owner, source, (native_frame(30, qpc=qpc_ns()),), received_ns=qpc_ns())
    wait_for(lambda: len(transport.sent) == 2)
    state = controls(transport, "SOURCE_STATE")[-1]
    assert not recorder.paused and state["state"] == "playing" and state["sequence"] == 30 and state["continuity"] == 2


def test_actual_source_handoff_state_and_pose_cross_two_room_workers_without_stale_resume(rooms):
    sender, recorder, send_transports, _ = rooms();sender.host();sent = send_transports[0]
    peer_ready(sender, sent)
    receiver, _, receive_transports, channels = rooms();receiver.host();received = receive_transports[0]
    fields = peer_ready(receiver, received)
    forwarded = 0

    def relay_controls():
        nonlocal forwarded
        while forwarded < len(sent.controls):
            payload, _ = sent.controls[forwarded];forwarded += 1
            received.event("control_received", **fields, sequence=forwarded, received_qpc=qpc_ns(),
                           payload_b64=base64.b64encode(payload).decode())

    def relay_pose(packet, sequence):
        received.event("data_received", **fields, sequence=sequence, received_qpc=qpc_ns(),
                       payload_b64=base64.b64encode(packet).decode())

    outgoing = recorder.pair[0];owner = outgoing.begin_capture();source = producer(rom_layout=1, qpc_frequency=FREQUENCY)
    outgoing.observe(owner, source, (native_frame(10, qpc=qpc_ns()),), received_ns=qpc_ns())
    wait_for(lambda: sent.sent);relay_controls();relay_pose(sent.sent[-1][0], 1)
    wait_for(lambda: present(channels))
    outgoing.observe(owner, source, (native_frame(11, qpc=qpc_ns()),), received_ns=qpc_ns())
    wait_for(lambda: len(sent.sent) == 2)
    delayed = sent.sent[-1][0]
    outgoing.withdraw_capture(owner);outgoing.end_capture(owner);recorder.paused = True
    wait_for(lambda: any(v["state"] == "paused" for v in controls(sent, "SOURCE_STATE")))
    relay_controls()
    wait_for(lambda: not present(channels) and receiver.snapshot()["room"]["members"][1]["state"] == "paused")
    recorder.paused = False;owner = outgoing.begin_capture()
    outgoing.observe(owner, source, (native_frame(20, qpc=qpc_ns()),), received_ns=qpc_ns())
    wait_for(lambda: len(sent.sent) == 3);relay_controls()
    relay_pose(delayed, 2)
    time.sleep(.025)
    assert not present(channels)
    relay_pose(sent.sent[-1][0], 3)
    wait_for(lambda: present(channels))
    native = channels[-1].views[-1].peers[0]
    state = controls(sent, "SOURCE_STATE")[-1]
    assert decode_pose(bytes(native.packet), expected_incarnation=bytes.fromhex(state["source_nonce"])).frame.sequence == 20


def test_source_state_counter_exhaustion_retires_room_instead_of_reusing_serial(rooms):
    service, recorder, transports, _ = rooms();service.host();transport = transports[0]
    peer_ready(service, transport)
    outgoing = recorder.pair[0]
    service._state_serial = (1 << 64) - 1;recorder.paused = True
    wait_for(lambda: service.snapshot()["phase"] == "error")
    assert service.snapshot()["room"] is None and recorder.pair == (None, None)
    with pytest.raises(PoseError):
        outgoing.begin_capture()


def test_unsent_source_boundaries_coalesce_behind_ordered_inflight_controls(rooms):
    service, recorder, transports, _ = rooms();service.host();transport = transports[0]
    transport.controls_blocked = True;peer_ready(service, transport)
    outgoing = recorder.pair[0];owner = outgoing.begin_capture();source = producer(rom_layout=1, qpc_frequency=FREQUENCY)
    for sequence in range(1, 9):
        if sequence > 1:
            outgoing.source_unavailable(owner)
        outgoing.observe(owner, source, (native_frame(sequence, qpc=qpc_ns()),), received_ns=qpc_ns())
        wait_for(lambda expected=sequence: service._source_state.sequence == expected)
        assert len(service._controls) <= 2
    assert not transport.sent and not controls(transport, "SOURCE_STATE")
    transport.control_futures[0].set_result(None)
    wait_for(lambda: controls(transport, "SOURCE_STATE"))
    for sequence in range(9, 17):
        outgoing.source_unavailable(owner)
        outgoing.observe(owner, source, (native_frame(sequence, qpc=qpc_ns()),), received_ns=qpc_ns())
        wait_for(lambda expected=sequence: service._source_state.sequence == expected)
        assert len(service._controls) <= 2
    assert not transport.sent
    transport.controls_blocked = False;transport.control_futures[-1].set_result(None)
    packet, _fields = wait_for(lambda: transport.sent and transport.sent[-1])
    latest = controls(transport, "SOURCE_STATE")[-1]
    assert latest["sequence"] == 16 and latest["continuity"] == 16
    assert decode_pose(packet, expected_incarnation=bytes.fromhex(latest["source_nonce"])).frame.sequence == 16
    assert len(controls(transport, "SOURCE_STATE")) == 2


def test_reconnect_requires_fresh_info_then_newer_state_without_resetting_source_fences(rooms):
    service, recorder, transports, channels = rooms();service.host();transport = transports[0]
    first = peer_ready(service, transport);source_info(transport, first)
    pose(transport, first, packet_sequence=9)
    wait_for(lambda: present(channels))
    incoming = recorder.pair[1]
    transport.event("peer_unready", room_epoch=1, peer_ref=1, member_epoch=1, transport_epoch=2, reason="transport_interrupted")
    wait_for(lambda: service.snapshot()["room"]["members"][1]["state"] == "reconnecting")
    second = peer_ready(service, transport, transport_epoch=2)
    source_state(transport, second, sequence=1, serial=3)  # before this transport's INFO
    source_info(transport, second, sequence=2, state=None)
    source_state(transport, second, sequence=3, serial=1)  # application serial cannot restart
    pose(transport, second, packet_sequence=10)
    time.sleep(.03)
    assert not present(channels) and recorder.pair[1] is incoming
    source_state(transport, second, sequence=4, serial=4, continuity=2, floor=20)
    pose(transport, second, packet_sequence=19, transport_sequence=2, continuity=2)
    time.sleep(.03)
    assert not present(channels)
    pose(transport, second, packet_sequence=20, transport_sequence=3, continuity=2)
    wait_for(lambda: present(channels))


def test_real_room_departure_writes_disabled_scope_before_channel_close_and_retries_busy_write(rooms):
    service, _, _, channels = rooms();service.host()
    channel = wait_for(lambda: channels and channels[-1].views and channels[-1])
    before = channel.views[-1].revision
    channel.disable_failures = 2
    service.leave()
    wait_for(lambda: channel.closed)
    assert not channel.views[-1].enabled and channel.views[-1].revision > before
    assert channel.disable_failures == 0


def test_retired_source_capability_skips_disabled_write_and_still_closes_original_handles(rooms):
    service, _, _, channels = rooms();service.host()
    channel = wait_for(lambda: channels and channels[-1].views and channels[-1])
    channel.disable_retired = True
    service.leave()
    wait_for(lambda: channel.closed)
    assert service.snapshot()["room"] is None and service._disabled_delivery is None


def test_temporary_same_endpoint_channel_reopen_keeps_room_authority_and_replay_fences(rooms):
    service, recorder, transports, channels = rooms();service.host();transport = transports[0]
    fields = peer_ready(service, transport);source_info(transport, fields)
    pose(transport, fields, packet_sequence=9)
    wait_for(lambda: present(channels))
    first = channels[-1];incoming = recorder.pair[1]
    room, revision, receipt = bytes(first.views[-1].room), first.views[-1].revision, first.views[-1].peers[0].received_ns
    def unavailable(_view):
        raise OSError("test temporary mapping error")
    first.write = unavailable
    wait_for(lambda: first.closed and len(channels) == 2 and present(channels))
    wait_for(lambda: service.snapshot()["error"] is None)
    latest = channels[-1].views[-1]
    assert latest.enabled and bytes(latest.room) == room and latest.revision == revision
    assert recorder.pair[1] is incoming and all(view.enabled for view in first.views)
    pose(transport, fields, packet_sequence=9, transport_sequence=2)
    time.sleep(.03)
    assert channels[-1].views[-1].peers[0].received_ns == receipt
    pose(transport, fields, packet_sequence=10, transport_sequence=3)
    wait_for(lambda: channels[-1].views[-1].peers[0].received_ns > receipt)


def test_one_action_budget_cannot_admit_a_late_room_after_busy_retries(rooms):
    service, recorder, transports, _ = rooms(action_timeout_s=.08)
    assert service.identity() == LOCAL  # timely control uses the same entry point
    transport = transports[0]
    transport.failures['create_room'] = ['busy', 'busy']
    with pytest.raises(RoomError, match='room_timeout'):
        service.host()
    transport.event('room', room_epoch=1, state='joined', owner_code=LOCAL,
                    local_is_owner=True, member_count=1)
    wait_for(lambda: transport.closed)
    time.sleep(.15)
    assert service.snapshot()['room'] is None and recorder.pair == (None, None)
    assert service.host()['room'] is not None and len(transports) == 2


def test_background_tick_failure_keeps_executor_and_explicit_recovery(rooms):
    service, recorder, transports, _ = rooms()
    service.host()
    original = recorder.live_session_status
    failed = threading.Event()

    def fail_once():
        recorder.live_session_status = original
        failed.set()
        raise RuntimeError('private synthetic observation failure')

    recorder.live_session_status = fail_once
    assert failed.wait(2)
    wait_for(lambda: service.snapshot()['phase'] == 'error')
    assert service.snapshot()['room'] is None and transports[0].closed
    assert service._thread.is_alive()
    assert service.identity() == LOCAL
    assert service.host()['room'] is not None and len(transports) == 2


def test_shutdown_retains_cleanup_executor_past_the_old_retry_window(rooms):
    service, recorder, _, channels = rooms()
    service.host()
    channel = wait_for(lambda: channels and channels[-1])
    channel.close_failures = 1000
    with pytest.raises(RoomError, match='room_cleanup_pending'):
        service.close(timeout_s=.05)
    assert recorder.pair == (None, None) and service._thread.is_alive()
    channel.close_failures = 0
    wait_for(lambda: channel.closed)
    service.close(timeout_s=2)
    assert not service._thread.is_alive() and service._channel is None


def test_failed_leave_does_not_poison_the_next_room(rooms):
    service, recorder, transports, _ = rooms()
    service.host()
    transport = transports[0]
    transport.failures['leave'] = ['sdk_failure']
    with pytest.raises(RoomError):
        service.leave()
    assert recorder.pair == (None, None)
    assert not service._leaving
    assert transport.closed  # uncertain provider membership must be retired
    assert service.host()['room'] is not None and len(transports) == 2


def test_terminal_peer_failure_is_visible_and_retry_rebinds_the_same_membership(rooms):
    service, recorder, transports, channels = rooms()
    service.host(); transport = transports[0]
    fields = peer_ready(service, transport); source_info(transport, fields)
    pose(transport, fields, packet_sequence=9)
    wait_for(lambda: present(channels))
    incoming = recorder.pair[1]
    transport.event('peer', **{key: fields[key] for key in ('room_epoch', 'peer_ref', 'member_epoch', 'transport_epoch')},
                    user_code=REMOTE, state='failed')
    wait_for(lambda: service.snapshot()['room']['members'][1]['state'] == 'disconnected')
    assert not present(channels)
    member = service.snapshot()['room']['members'][1]['id']
    service.retry_peer(member)
    binding = wait_for(lambda: next((f for op, f in transport.calls if op == 'bind_peer' and f['transport_epoch'] == 2), None))
    assert binding['local_nonce'] != fields['local_nonce']
    second = {**{key: binding[key] for key in ('room_epoch', 'peer_ref', 'member_epoch', 'transport_epoch')},
              'local_nonce': binding['local_nonce'], 'remote_nonce': '66' * 16}
    transport.event('peer_ready', **second, route='relay')
    wait_for(lambda: any(f['transport_epoch'] == 2 for _, f in transport.controls))
    source_info(transport, second, sequence=2, state=None)
    source_state(transport, second, sequence=3, serial=4, continuity=2, floor=20)
    pose(transport, second, packet_sequence=19, transport_sequence=1, continuity=2)
    time.sleep(.025)
    assert not present(channels) and recorder.pair[1] is incoming
    pose(transport, second, packet_sequence=20, transport_sequence=2, continuity=2)
    wait_for(lambda: present(channels))


def test_pose_burst_coalesces_without_overflowing_membership_controls(rooms):
    service, recorder, transports, channels = rooms()
    service.host(); transport = transports[0]
    fields = peer_ready(service, transport); source_info(transport, fields)
    entered, release = threading.Event(), threading.Event()
    original = recorder.live_session_status

    def stalled_observer():
        entered.set()
        assert release.wait(3)
        return original()

    recorder.live_session_status = stalled_observer
    assert entered.wait(2)
    try:
        for sequence in range(1, 301):
            pose(transport, fields, packet_sequence=sequence, transport_sequence=sequence)
        assert len(service._latest_events) == 1 and not service._overflow.is_set()
    finally:
        recorder.live_session_status = original
        release.set()
    wait_for(lambda: present(channels))
    assert not transport.closed and recorder.pair[1] is not None
    latest = channels[-1].views[-1].peers[0]
    assert decode_pose(bytes(latest.packet), expected_incarnation=REMOTE_SOURCE).frame.sequence == 300
