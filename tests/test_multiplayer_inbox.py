import ctypes as C
from dataclasses import replace

import pytest

from ghost_native_fixture import frame
from sm64_events.multiplayer.native_inbox import NativeInbox, display_name
from sm64_events.multiplayer.native_protocol import Appearance, Header, LocalOwner, Peer, Update
from sm64_events.multiplayer.protocol import PoseError, encode_pose

NONCE, ROOM = bytes(range(16)), b"r" * 16


def packet(sequence=1, *, nonce=NONCE):
    return encode_pose(frame(sequence), participant_incarnation=nonce, source_generation=1,
        elapsed_ns=sequence, continuity=1, rom_layout=1, rom_generation=3)


def view(inbox, now=1):
    return Update.from_buffer_copy(inbox.snapshot(now_ns=now).wire)


def test_native_inbox_layout_and_initial_room_do_not_invent_a_pose():
    assert (C.sizeof(LocalOwner), C.sizeof(Peer), Peer.packet.offset, C.sizeof(Update), Update.peers.offset) == (40, 524, 100, 1692, 120)
    assert C.sizeof(Header) == 256
    value = view(NativeInbox(ROOM))
    assert value.enabled and not any(p.member or p.present for p in value.peers)
    assert value.owner.token == value.owner.model_id == 0


def test_authentication_precedes_data_and_expiry_does_not_reset_source_fences():
    inbox = NativeInbox(ROOM, stale_after_ns=5)
    with pytest.raises(PoseError, match="not admitted"):
        inbox.ingest("p1", packet(), received_ns=1)
    assert inbox.admit("p1", NONCE, name="Griff", hue=0) == 0
    inbox.ingest("p1", packet(), received_ns=1)
    first = inbox.snapshot(now_ns=1)
    value = Update.from_buffer_copy(first.wire)
    assert value.peers[0].present and bytes(value.peers[0].packet) == packet()
    assert not view(inbox, 7).peers[0].present
    with pytest.raises(PoseError, match="not newer"):
        inbox.ingest("p1", packet(), received_ns=8)
    inbox.ingest("p1", packet(2), received_ns=8)
    assert view(inbox, 8).peers[0].present and inbox.current(first)


def test_reconnect_clear_retains_member_and_packet_high_water_marks():
    inbox = NativeInbox(ROOM)
    inbox.admit("p1", NONCE, name="Player one", hue=120)
    inbox.ingest("p1", packet(4), received_ns=1)
    inbox.clear("p1")
    assert view(inbox).peers[0].member and not view(inbox).peers[0].present
    assert inbox.admit("p1", NONCE, name="Player one", hue=120) == 0
    with pytest.raises(PoseError):
        inbox.ingest("p1", packet(3), received_ns=2)
    inbox.ingest("p1", packet(5), received_ns=2)
    assert view(inbox, 2).peers[0].present


def test_control_revision_tracks_members_style_and_permanent_room_revocation():
    inbox = NativeInbox(ROOM)
    old = inbox.snapshot(now_ns=1)
    inbox.admit("p1", NONCE, name="Name", hue=40)
    assert not inbox.current(old)
    admitted = inbox.snapshot(now_ns=1)
    inbox.appearance(Appearance(), hidden=True)
    assert not inbox.current(admitted) and view(inbox).hidden
    assert inbox.remove("p1") and not view(inbox).peers[0].member
    with pytest.raises(PoseError, match="reused"):
        inbox.admit("p2", NONCE, name="Other", hue=60)
    inbox.revoke()
    assert not view(inbox).enabled
    with pytest.raises(PoseError, match="ended"):
        inbox.admit("p3", b"s" * 16, name="Other", hue=80)


def test_members_are_bounded_and_bad_packet_never_replaces_an_accepted_value():
    inbox = NativeInbox(ROOM)
    for i in range(3):
        inbox.admit(str(i), bytes([i + 1]) * 16, name=str(i), hue=i)
    with pytest.raises(PoseError, match="three"):
        inbox.admit("4", b"x" * 16, name="Four", hue=3)
    accepted = packet(nonce=b"\x01" * 16)
    inbox.ingest("0", accepted, received_ns=1)
    with pytest.raises(PoseError):
        inbox.ingest("0", b"bad", received_ns=2)
    assert bytes(view(inbox, 2).peers[0].packet) == accepted


@pytest.mark.parametrize("value", ["", "a\x00b", "line\nname", "é" * 32, None])
def test_labels_are_bounded_utf8_with_no_controls(value):
    with pytest.raises(ValueError):
        display_name(value)


@pytest.mark.parametrize("change", [{"opacity":float("nan")}, {"names_enabled":1},
    {"proximity_distance":401}, {"name_background":3}, {"input_mode":True}])
def test_appearance_does_not_publish_invalid_controls(change):
    with pytest.raises(ValueError):
        replace(Appearance(), **change).native()
