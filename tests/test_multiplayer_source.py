"""Actual SourcePublisher/capture callback handoff; no capture or provider resources."""
from dataclasses import FrozenInstanceError, replace
import struct
import threading

import pytest

from ghost_native_fixture import frame, producer
from sm64_events.multiplayer.live_state import LiveSeat, SourcePublisher
from sm64_events.multiplayer.protocol import PoseError, decode_pose
from sm64_events.multiplayer.source_handoff import CaptureOwner, SourceHandoff

INCARNATION = bytes(range(16))
PRODUCER = producer(rom_layout=1)


def handoff():
    source = SourceHandoff(INCARNATION, stale_after_ns=50)
    return source, source.begin_capture()


def raw(sequence=1, *, hidden=0, warp=None, **values):
    value = bytearray(frame(sequence, **values))
    value[152] = hidden  # independent native gc_pose.hidden portable-record byte
    if warp is not None:
        struct.pack_into("<Hh", value, 256, *warp)
    return bytes(value)


def decoded(publication):
    return decode_pose(publication.packet, expected_incarnation=INCARNATION)


def test_joining_alone_or_unknown_capture_owner_never_creates_a_pose():
    source = SourceHandoff(INCARNATION, stale_after_ns=50)
    assert source.take_latest(now_ns=0) is None
    for unknown in (None, CaptureOwner()):
        with pytest.raises(PoseError, match="retired"):
            source.observe(unknown, PRODUCER, (raw(),), received_ns=1)
        assert not source.end_capture(unknown) and not source.source_unavailable(unknown)
        assert not source.withdraw_capture(unknown)
    owner = source.begin_capture()
    with pytest.raises(PoseError, match="active owner"):
        source.begin_capture()
    assert source.observe(owner, PRODUCER, (raw(),), received_ns=1) == 1
    assert decoded(source.take_latest(now_ns=1)).source_generation == 1


def test_actual_hidden_boundaries_survive_coalescing_before_network_take():
    source, owner = handoff()
    source.observe(owner, PRODUCER, (raw(1, pad=None),), received_ns=1)
    first = source.take_latest(now_ns=1)
    assert source.current(first, now_ns=1) and source.take_latest(now_ns=1) is None
    batch = (raw(2, hidden=1, pad=None), raw(3, hidden=0, pad=None))
    assert source.observe(owner, PRODUCER, batch, received_ns=2) == 2
    latest = source.take_latest(now_ns=2)
    assert not source.current(first, now_ns=2)
    before, after = decoded(first), decoded(latest)
    assert before.continuity == 1 and after.continuity == 3
    assert after.frame.sequence == 3 and not after.frame.pose_hidden and after.frame.pad is None
    seat = LiveSeat(INCARNATION, stale_after_ns=50)
    seat.ingest(first.packet, received_ns=1)
    assert seat.ingest(latest.packet, received_ns=2).pose.continuity == 3


@pytest.mark.parametrize("middle,final,continuity", [
    ({"valid":29, "pose_refusal":3}, {}, 3),
    ({"level":9}, {}, 3),
    ({"warp":(1, 10)}, {"warp":(1, 9)}, 2),
])
def test_refusal_scene_and_warp_are_observed_before_last_packet_selection(middle, final, continuity):
    source, owner = handoff()
    assert source.observe(owner, PRODUCER, (raw(1), raw(2, **middle), raw(3, **final)), received_ns=4) == 3
    value = decoded(source.take_latest(now_ns=4))
    assert value.continuity == continuity and value.frame.sequence == 3


@pytest.mark.parametrize("change", [
    {"pid":124}, {"created":457}, {"generation":3}, {"rom_generation":4},
    {"configuration":5}, {"epoch":6}, {"qpc_frequency":60}, {"rom_layout":2},
])
def test_actual_local_producer_identity_restarts_source_generation(change):
    source, owner = handoff()
    source.observe(owner, PRODUCER, (raw(10),), received_ns=1)
    first = source.take_latest(now_ns=1)
    changed = producer(rom_layout=1, **change) if "rom_layout" not in change else producer(**change)
    source.observe(owner, changed, (raw(1, configuration=changed.configuration, epoch=changed.epoch),), received_ns=2)
    latest = source.take_latest(now_ns=2)
    assert not source.current(first, now_ns=2)
    assert decoded(first).source_generation == 1
    value = decoded(latest)
    assert value.source_generation == 2 and value.elapsed_ns == 0
    assert value.rom_layout == changed.rom_layout and value.rom_generation == changed.rom_generation


def test_unavailable_without_records_withdraws_taken_data_and_preserves_source_fences():
    source, owner = handoff()
    source.observe(owner, PRODUCER, (raw(10, qpc=100),), received_ns=1)
    first = source.take_latest(now_ns=1)
    assert source.source_unavailable(owner)
    assert not source.current(first, now_ns=2) and source.take_latest(now_ns=2) is None
    with pytest.raises(PoseError, match="newer"):
        source.observe(owner, PRODUCER, (raw(10, qpc=100),), received_ns=2)
    source.observe(owner, PRODUCER, (raw(11, qpc=101),), received_ns=3)
    value = decoded(source.take_latest(now_ns=3))
    assert value.source_generation == 1 and value.continuity > 1
    assert value.elapsed_ns == 1_000_000_000 // PRODUCER.qpc_frequency


def test_retired_owner_cannot_clear_rebind_or_publish_into_a_new_capture():
    source, old = handoff()
    source.observe(old, PRODUCER, (raw(10),), received_ns=1)
    first = source.take_latest(now_ns=1)
    assert source.end_capture(old) and not source.current(first, now_ns=1)
    current = source.begin_capture()
    with pytest.raises(PoseError, match="retired"):
        source.observe(old, producer(created=999, rom_layout=1), (raw(999),), received_ns=2)
    assert not source.end_capture(old) and not source.source_unavailable(old)
    source.observe(current, PRODUCER, (raw(11),), received_ns=3)
    value = source.take_latest(now_ns=3)
    assert not source.withdraw_capture(old)
    assert decoded(value).source_generation == 1 and decoded(value).continuity == 2
    assert source.current(value, now_ns=3)


def test_room_revocation_is_permanent_and_invalidates_pending_and_taken_delivery():
    source, owner = handoff()
    source.observe(owner, PRODUCER, (raw(),), received_ns=1)
    taken = source.take_latest(now_ns=1)
    source.observe(owner, PRODUCER, (raw(2),), received_ns=2)
    source.revoke()
    assert not source.current(taken, now_ns=2) and source.take_latest(now_ns=2) is None
    with pytest.raises(PoseError, match="revoked"):
        source.begin_capture()
    with pytest.raises(PoseError, match="retired"):
        source.observe(owner, PRODUCER, (raw(3),), received_ns=3)
    assert source.end_capture(owner)
    with pytest.raises(PoseError, match="revoked"):
        source.begin_capture()


def test_empty_batch_does_not_bind_source_or_publish_and_status_loss_is_explicit():
    source, owner = handoff()
    assert source.observe(owner, None, (), received_ns=1) == 0
    assert source.take_latest(now_ns=1) is None
    source.observe(owner, PRODUCER, (raw(),), received_ns=1)
    value = source.take_latest(now_ns=1)
    assert source.observe(owner, None, (), received_ns=2) == 0
    assert source.current(value, now_ns=2)
    source.source_unavailable(owner)
    assert not source.current(value, now_ns=2)


@pytest.mark.parametrize("frames", [[], (bytearray(raw()),), (raw()[:-1],), (raw(),) * 513])
def test_batch_bound_rejects_before_publisher_work_and_withdraws_old_value(frames):
    source, owner = handoff()
    source.observe(owner, PRODUCER, (raw(),), received_ns=1)
    old = source.take_latest(now_ns=1)
    with pytest.raises(PoseError):
        source.observe(owner, PRODUCER, frames, received_ns=2)
    assert not source.current(old, now_ns=2) and source.take_latest(now_ns=2) is None
    source.observe(owner, PRODUCER, (raw(2),), received_ns=3)
    assert decoded(source.take_latest(now_ns=3)).frame.sequence == 2


def test_unbounded_generator_is_never_advanced():
    source, owner = handoff()
    def forbidden():
        pytest.fail("unbounded iterator was consumed")
        yield raw()
    with pytest.raises(PoseError, match="bound"):
        source.observe(owner, PRODUCER, forbidden(), received_ns=1)


def test_actual_maximum_512_record_batch_produces_only_its_final_packet():
    source, owner = handoff()
    batch = tuple(raw(sequence) for sequence in range(1, 513))
    assert source.observe(owner, PRODUCER, batch, received_ns=1) == 512
    assert decoded(source.take_latest(now_ns=1)).frame.sequence == 512
    assert source.take_latest(now_ns=1) is None


def test_partial_batch_error_withdraws_and_keeps_already_observed_sequence_fences():
    source, owner = handoff()
    source.observe(owner, PRODUCER, (raw(),), received_ns=1)
    old = source.take_latest(now_ns=1)
    broken = bytearray(raw(3))
    struct.pack_into("<f", broken, 76, float("nan"))
    with pytest.raises(PoseError, match="invalid"):
        source.observe(owner, PRODUCER, (raw(2), bytes(broken)), received_ns=2)
    assert not source.current(old, now_ns=2) and source.take_latest(now_ns=2) is None
    with pytest.raises(PoseError, match="newer"):
        source.observe(owner, PRODUCER, (raw(2),), received_ns=3)
    source.observe(owner, PRODUCER, (raw(4),), received_ns=4)
    assert decoded(source.take_latest(now_ns=4)).frame.sequence == 4


@pytest.mark.parametrize("invalid", [True, -1, 1.5, 2**64])
def test_times_and_timeout_require_bounded_actual_integers(invalid):
    with pytest.raises(PoseError):
        SourceHandoff(INCARNATION, stale_after_ns=invalid)
    source, owner = handoff()
    with pytest.raises(PoseError):
        source.observe(owner, PRODUCER, (raw(),), received_ns=invalid)
    with pytest.raises(PoseError):
        source.take_latest(now_ns=invalid)
    with pytest.raises(PoseError):
        source.current(None, now_ns=invalid)


def test_zero_timeout_is_refused():
    with pytest.raises(PoseError):
        SourceHandoff(INCARNATION, stale_after_ns=0)


def test_ttl_is_local_receipt_age_and_expired_taken_data_cannot_reappear():
    source, owner = handoff()
    source.observe(owner, PRODUCER, (raw(qpc=2**60),), received_ns=100)
    value = source.take_latest(now_ns=100)
    assert decoded(value).elapsed_ns == 0 and source.current(value, now_ns=150)
    assert not source.current(value, now_ns=151) and not source.current(value, now_ns=100)
    source.observe(owner, PRODUCER, (raw(2, qpc=2**60 + 1),), received_ns=152)
    assert source.take_latest(now_ns=203) is None


def test_backward_receipt_withdraws_delivery_without_resetting_local_floor():
    source, owner = handoff()
    source.observe(owner, PRODUCER, (raw(),), received_ns=100)
    value = source.take_latest(now_ns=100)
    with pytest.raises(PoseError, match="backward"):
        source.observe(owner, PRODUCER, (raw(2),), received_ns=99)
    assert not source.current(value, now_ns=100)
    source.observe(owner, PRODUCER, (raw(2),), received_ns=101)
    assert decoded(source.take_latest(now_ns=101)).frame.sequence == 2


def test_observation_earlier_than_receipt_cannot_admit_or_resurrect_delivery():
    source, owner = handoff()
    source.observe(owner, PRODUCER, (raw(),), received_ns=100)
    assert source.take_latest(now_ns=99) is None
    assert source.take_latest(now_ns=100) is None
    source.observe(owner, PRODUCER, (raw(2),), received_ns=101)
    value = source.take_latest(now_ns=101)
    assert not source.current(value, now_ns=100) and not source.current(value, now_ns=101)


def test_publication_is_immutable_identity_checked_and_repr_omits_packet_and_grants():
    source, owner = handoff()
    source.observe(owner, PRODUCER, (raw(),), received_ns=1)
    value = source.take_latest(now_ns=1)
    assert repr(owner) == "CaptureOwner()" and repr(value) == "Publication(received_ns=1)"
    with pytest.raises(FrozenInstanceError):
        value.received_ns = 99
    assert not source.current(replace(value), now_ns=1)
    assert source.current(value, now_ns=1)


def test_stalled_network_consumer_cannot_hold_up_latest_only_capture():
    source, owner = handoff()
    source.observe(owner, PRODUCER, (raw(),), received_ns=1)
    entered, release = threading.Event(), threading.Event()
    taken = []
    def network_worker():
        taken.append(source.take_latest(now_ns=1))
        entered.set()
        release.wait(3)  # stand-in for stalled IO after take_latest returned
    worker = threading.Thread(target=network_worker, name="owned-live-consumer", daemon=True)
    worker.start()
    try:
        assert entered.wait(1)
        for sequence in range(2, 1_002):
            source.observe(owner, PRODUCER, (raw(sequence),), received_ns=sequence)
        assert worker.is_alive(), "capture waited until the stalled consumer left IO"
        latest = source.take_latest(now_ns=1_001)
        assert decoded(latest).frame.sequence == 1_001
        assert not source.current(taken[0], now_ns=1_001) and source.take_latest(now_ns=1_001) is None
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive()


@pytest.mark.parametrize("withdraw", [False, True])
def test_codec_stall_holds_no_handoff_lock_and_control_prevents_late_publication(monkeypatch, withdraw):
    source, owner = handoff()
    source.observe(owner, PRODUCER, (raw(),), received_ns=1)
    first = source.take_latest(now_ns=1)
    entered, release = threading.Event(), threading.Event()
    original, result, errors = SourcePublisher.packet, [], []
    def blocked(publisher, lease, record, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(publisher, lease, record, **kwargs)
    monkeypatch.setattr(SourcePublisher, "packet", blocked)
    def pump():
        try:
            result.append(source.observe(owner, PRODUCER, (raw(2),), received_ns=2))
        except Exception as error:  # noqa: BLE001 - return thread errors to this test
            errors.append(error)
    worker = threading.Thread(target=pump, name="owned-live-codec", daemon=True)
    worker.start()
    try:
        assert entered.wait(1)
        assert source.current(first, now_ns=2)
        if withdraw:
            assert source.withdraw_capture(owner)
            with pytest.raises(PoseError, match="active owner"):
                source.begin_capture()
        else:
            source.revoke()
        assert not source.current(first, now_ns=2) and source.take_latest(now_ns=2) is None
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive() and not errors and result == [0]
    assert source.take_latest(now_ns=3) is None
    if withdraw:
        with pytest.raises(PoseError, match="retired"):
            source.observe(owner, PRODUCER, (raw(3),), received_ns=3)
        assert source.end_capture(owner)
        fresh = source.begin_capture()
        with pytest.raises(PoseError, match="newer"):
            source.observe(fresh, PRODUCER, (raw(2),), received_ns=3)
        source.observe(fresh, PRODUCER, (raw(3),), received_ns=4)
        value = decoded(source.take_latest(now_ns=4))
        assert value.source_generation == 1 and value.continuity > 1
