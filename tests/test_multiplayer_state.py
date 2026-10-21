"""Live lifetime guards exercise real packets and independent source occurrences."""
from dataclasses import replace
import struct

import pytest

from ghost_native_fixture import frame, producer
from sm64_events.multiplayer.live_state import LiveSeat, SourceIdentity, SourceLease, SourcePublisher
from sm64_events.multiplayer.protocol import PoseError, decode_pose, encode_pose

INCARNATION = bytes(range(16))
OTHER = b"b" * 16
IDENTITY = SourceIdentity.from_producer(producer(rom_layout=1))


def packet(sequence=1, *, source=1, continuity=1, elapsed=0, incarnation=INCARNATION,
           configuration=4, epoch=5, rom=1, rom_generation=3, **source_values):
    return encode_pose(frame(sequence, configuration=configuration, epoch=epoch, **source_values),
        participant_incarnation=incarnation, source_generation=source, elapsed_ns=elapsed,
        continuity=continuity, rom_layout=rom, rom_generation=rom_generation)


def test_unseen_delayed_source_and_duplicates_cannot_reappear_after_expiry():
    seat = LiveSeat(INCARNATION, stale_after_ns=50)
    seat.ingest(packet(9, source=2, elapsed=100), received_ns=10)
    assert seat.latest(now_ns=60) is not None
    assert seat.latest(now_ns=61) is None
    for old in (packet(900, source=1, elapsed=900), packet(9, source=2, elapsed=100)):
        with pytest.raises(PoseError):
            seat.ingest(old, received_ns=70)
    assert seat.latest(now_ns=80) is None
    seat.ingest(packet(10, source=2, elapsed=100), received_ns=90)
    assert seat.latest(now_ns=100).pose.frame.sequence == 10


@pytest.mark.parametrize("change", [
    {"configuration":5}, {"epoch":6}, {"rom":2}, {"rom_generation":4},
    {"continuity":1}, {"elapsed":4},
])
def test_same_source_cannot_change_identity_or_move_clocks_backward(change):
    seat = LiveSeat(INCARNATION, stale_after_ns=100)
    accepted = seat.ingest(packet(10, continuity=2, elapsed=5), received_ns=1)
    candidate = {"continuity":2, "elapsed":5, **change}
    with pytest.raises(PoseError):
        seat.ingest(packet(11, **candidate), received_ns=2)
    assert seat.latest(now_ns=3) is accepted


def test_clear_reconnect_retains_fences_but_new_generation_can_restart():
    seat = LiveSeat(INCARNATION, stale_after_ns=100)
    seat.ingest(packet(100, continuity=5, elapsed=400), received_ns=1)
    seat.clear()
    with pytest.raises(PoseError):
        seat.ingest(packet(101, continuity=4, elapsed=500), received_ns=2)
    with pytest.raises(PoseError):
        seat.ingest(packet(100, continuity=6, elapsed=500), received_ns=2)
    current = seat.ingest(packet(1, source=2, elapsed=0, epoch=6), received_ns=2)
    assert current.pose.frame.sequence == 1
    with pytest.raises(PoseError):
        seat.ingest(packet(999, source=1, continuity=99, elapsed=999), received_ns=3)


def test_membership_cannot_be_learned_from_pose_and_revocation_is_permanent():
    seat = LiveSeat(INCARNATION, stale_after_ns=100)
    with pytest.raises(PoseError, match="participant"):
        seat.ingest(packet(incarnation=OTHER), received_ns=1)
    seat.ingest(packet(), received_ns=1)
    seat.revoke()
    with pytest.raises(PoseError, match="revoked"):
        seat.ingest(packet(2, source=999), received_ns=2)
    assert seat.latest(now_ns=3) is None
    newly_admitted = LiveSeat(OTHER, stale_after_ns=100)
    newly_admitted.ingest(packet(incarnation=OTHER), received_ns=3)
    with pytest.raises(PoseError, match="participant"):
        newly_admitted.ingest(packet(2), received_ns=4)


def test_local_age_and_backward_receipt_do_not_masquerade_as_sender_age():
    seat = LiveSeat(INCARNATION, stale_after_ns=50)
    value = seat.ingest(packet(elapsed=2**60), received_ns=100)
    assert seat.latest(now_ns=101) is value
    with pytest.raises(PoseError, match="receipt time"):
        seat.ingest(packet(2, elapsed=2**60), received_ns=99)
    with pytest.raises(PoseError, match="observation time"):
        seat.latest(now_ns=99)
    assert seat.latest(now_ns=151) is None


def test_refused_pose_replaces_old_pose_and_optional_pad_remains_unknown():
    seat = LiveSeat(INCARNATION, stale_after_ns=50)
    seat.ingest(packet(), received_ns=0)
    refused = seat.ingest(packet(2, valid=29, pose_refusal=3, pad=None), received_ns=1)
    assert not refused.pose.frame.playable
    assert refused.pose.frame.pad is None
    assert seat.latest(now_ns=2) is refused


def test_three_seats_remain_latest_only_and_independent():
    seats = [LiveSeat(bytes([i]) * 16, stale_after_ns=10) for i in (1, 2, 3)]
    for sequence in range(1, 500):
        for i, seat in enumerate(seats, 1):
            seat.ingest(packet(sequence, incarnation=bytes([i]) * 16), received_ns=sequence)
    seats[0].clear()
    assert seats[0].latest(now_ns=500) is None
    assert [seat.latest(now_ns=500).pose.frame.sequence for seat in seats[1:]] == [499, 499]
    assert seats[1].latest(now_ns=510) is None


def test_sender_uses_actual_frequency_immutable_origin_and_quantized_ties():
    sender = SourcePublisher(INCARNATION)
    lease = sender.bind_source(replace(IDENTITY, qpc_frequency=3))
    first = decode_pose(sender.packet(lease, frame(1, qpc=100)), expected_incarnation=INCARNATION)
    second = decode_pose(sender.packet(lease, frame(2, qpc=101)), expected_incarnation=INCARNATION)
    tied = decode_pose(sender.packet(lease, frame(3, qpc=101)), expected_incarnation=INCARNATION)
    assert (first.elapsed_ns, second.elapsed_ns, tied.elapsed_ns) == (0, 333_333_333, 333_333_333)
    assert first.source_generation == second.source_generation == tied.source_generation == 1
    assert first.continuity == second.continuity == tied.continuity == 1


def test_retired_callback_cannot_publish_or_mint_a_fresh_generation():
    sender = SourcePublisher(INCARNATION)
    old = sender.bind_source(IDENTITY)
    sender.packet(old, frame(100))
    current = sender.bind_source(replace(IDENTITY, created=IDENTITY.created + 1))
    assert current.generation == 2
    with pytest.raises(PoseError, match="retired"):
        sender.packet(old, frame(999))
    with pytest.raises(PoseError, match="retired"):
        sender.packet(SourceLease(current.identity, current.generation), frame(1))
    value = decode_pose(sender.packet(current, frame(1)), expected_incarnation=INCARNATION)
    assert value.source_generation == 2 and value.elapsed_ns == 0


def test_detached_same_source_retains_origin_sequence_and_changes_continuity():
    sender = SourcePublisher(INCARNATION)
    old = sender.bind_source(IDENTITY)
    sender.packet(old, frame(10, qpc=100))
    sender.detach()
    with pytest.raises(PoseError, match="retired"):
        sender.packet(old, frame(11, qpc=101))
    resumed = sender.bind_source(IDENTITY)
    assert resumed is not old and resumed.generation == 1
    with pytest.raises(PoseError, match="newer"):
        sender.packet(resumed, frame(10, qpc=100))
    pose = decode_pose(sender.packet(resumed, frame(11, qpc=101)), expected_incarnation=INCARNATION)
    assert pose.elapsed_ns == 1_000_000_000 // IDENTITY.qpc_frequency
    assert pose.continuity == 2


@pytest.mark.parametrize("change", [{"epoch":6}, {"configuration":5}, {"rom_layout":2},
                                   {"mapping_generation":3}, {"qpc_frequency":60}, {"rom_generation":4}])
def test_replaced_source_identity_always_advances_generation(change):
    sender = SourcePublisher(INCARNATION)
    old = sender.bind_source(IDENTITY)
    changed = sender.bind_source(replace(IDENTITY, **change))
    assert changed.generation == old.generation + 1


def test_source_mismatch_and_corruption_do_not_advance_sender_fences():
    sender = SourcePublisher(INCARNATION)
    lease = sender.bind_source(IDENTITY)
    with pytest.raises(PoseError, match="different source"):
        sender.packet(lease, frame(100, epoch=6))
    malformed = bytearray(frame(100))
    struct.pack_into("<f", malformed, 76, float("nan"))
    with pytest.raises(PoseError, match="invalid"):
        sender.packet(lease, malformed)
    pose = decode_pose(sender.packet(lease, frame(1)), expected_incarnation=INCARNATION)
    assert pose.elapsed_ns == 0 and pose.continuity == 1


def test_scene_refusal_and_explicit_boundaries_do_not_use_countdown_as_a_clock():
    sender = SourcePublisher(INCARNATION)
    lease = sender.bind_source(IDENTITY)
    decoded = []
    for sequence, values in enumerate(({}, {"level":9}, {"level":9},
            {"level":9, "valid":29, "pose_refusal":3}, {"level":9}), 1):
        decoded.append(decode_pose(sender.packet(lease, frame(sequence, **values)),
                                  expected_incarnation=INCARNATION))
    assert [pose.continuity for pose in decoded] == [1, 2, 2, 3, 4]
    pose = decode_pose(sender.packet(lease, frame(6, level=9), boundary=True), expected_incarnation=INCARNATION)
    assert pose.continuity == 5


def test_new_warp_operation_breaks_continuity_but_countdown_does_not():
    sender = SourcePublisher(INCARNATION)
    lease = sender.bind_source(IDENTITY)
    continuities = []
    for sequence, (operation, timer) in enumerate(((0, 0), (1, 10), (1, 9), (2, 8)), 1):
        raw = bytearray(frame(sequence))
        struct.pack_into("<Hh", raw, 256, operation, timer)
        pose = decode_pose(sender.packet(lease, raw), expected_incarnation=INCARNATION)
        continuities.append(pose.continuity)
    assert continuities == [1, 2, 2, 3]


def test_transient_hidden_source_survives_latest_only_network_coalescing():
    sender = SourcePublisher(INCARNATION)
    lease = sender.bind_source(IDENTITY)
    packets = []
    for sequence, hidden in enumerate((0, 1, 0), 1):
        raw = bytearray(frame(sequence))
        raw[152] = hidden  # independent GC8 pose hidden byte
        packets.append(sender.packet(lease, raw))
    decoded = [decode_pose(value, expected_incarnation=INCARNATION) for value in packets]
    assert [value.frame.pose_hidden for value in decoded] == [False, True, False]
    assert all(value.frame.playable for value in decoded)
    assert [value.continuity for value in decoded] == [1, 2, 3]
    # Drop the middle outbound packet only after observing its boundary.
    seat = LiveSeat(INCARNATION, stale_after_ns=50)
    before = seat.ingest(packets[0], received_ns=0)
    after = seat.ingest(packets[2], received_ns=1)
    assert after.pose.continuity > before.pose.continuity


def test_sender_counter_exhaustion_retires_old_callback_and_entire_namespace():
    sender = SourcePublisher(INCARNATION)
    lease = sender.bind_source(IDENTITY)
    sender._generation = 2**64 - 1
    with pytest.raises(PoseError):
        sender.bind_source(replace(IDENTITY, epoch=6))
    with pytest.raises(PoseError, match="exhausted"):
        sender.bind_source(IDENTITY)
    with pytest.raises(PoseError, match="retired"):
        sender.packet(lease, frame(1))


def test_failed_continuity_advance_cannot_be_bypassed_by_omitting_boundary():
    sender = SourcePublisher(INCARNATION)
    lease = sender.bind_source(IDENTITY)
    sender.packet(lease, frame(1))
    sender._continuity = 2**64 - 1
    with pytest.raises(PoseError, match="exhausted"):
        sender.packet(lease, frame(2), boundary=True)
    with pytest.raises(PoseError, match="retired"):
        sender.packet(lease, frame(2), boundary=False)
    with pytest.raises(PoseError, match="exhausted"):
        sender.bind_source(replace(IDENTITY, epoch=6))


def test_unrepresentable_elapsed_time_retires_sender_without_wrapping():
    sender = SourcePublisher(INCARNATION)
    lease = sender.bind_source(replace(IDENTITY, qpc_frequency=1))
    sender.packet(lease, frame(1, qpc=0))
    with pytest.raises(PoseError, match="exhausted"):
        sender.packet(lease, frame(2, qpc=2**63 - 1))
    with pytest.raises(PoseError, match="retired"):
        sender.packet(lease, frame(2, qpc=1))


@pytest.mark.parametrize("change", [{"pid":True}, {"created":0}, {"epoch":0},
                                   {"qpc_frequency":0}, {"rom_layout":True}])
def test_source_identity_cannot_smuggle_missing_or_boolean_values(change):
    with pytest.raises(PoseError):
        replace(IDENTITY, **change)
