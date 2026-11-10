"""Simulated faults and injected lab boundaries; never acquire a real source."""
from types import SimpleNamespace

from fastapi import FastAPI, Response
import pytest

from ghost_native_fixture import frame, producer
from sm64_events.ghost.native_layout import ROM_US
from sm64_events.multiplayer.native_protocol import Update
from sm64_events.multiplayer.protocol import decode_pose
from sm64_events.replay.ownedclose import CleanupPending
from tools import m3_live_lab as module
from tools.m3_live_lab_queue import FaultQueue, Faults


def queue_trace(seed):
    queue = FaultQueue(Faults(seats=3, delay_ms=80, jitter_ms=30, loss_percent=20,
                            reorder_percent=50, duplicate_percent=50, seed=seed), (1, 1))
    for at in range(20):
        for peer in range(3):
            queue.put(peer, str(at).encode(), now_ns=at * 1_000_000, source_received_ns=at, epoch=(1, 1))
    arrivals = queue.take_due(now_ns=2_000_000_000, epoch=(1, 1))
    return [(item.peer, item.packet, item.due_ns) for item in arrivals], queue.snapshot(2_000_000_000)


def test_fault_trace_is_seeded_repeatable_and_not_claimed_network_measurement():
    assert queue_trace(42) == queue_trace(42)
    assert queue_trace(42) != queue_trace(43)
    arrivals, state = queue_trace(42)
    assert arrivals and state["counts"]["loss"] > 0 and state["counts"]["duplicates"] > 0
    assert len(state["trace"]) <= 128
    assert "network_latency" not in state


def test_queue_bounds_epochs_and_deterministic_complete_loss_or_outage():
    queue = FaultQueue(Faults(delay_ms=1500, duplicate_percent=100), (1, 1))
    for _ in range(300):
        queue.put(0, b"pose", now_ns=0, source_received_ns=0, epoch=(1, 1))
    assert queue.snapshot(10)["queued"] == 512
    assert queue.counts["overflow"] == 88
    queue.reset((1, 2))
    assert not queue.take_due(now_ns=2_000_000_000, epoch=(1, 2))
    assert queue.put(0, b"old", now_ns=0, source_received_ns=0, epoch=(1, 1)) == 0
    for faults, counter in ((Faults(loss_percent=100), "loss"), (Faults(outages=(True, False, False)), "outage")):
        dropped = FaultQueue(faults, (2, 1))
        assert dropped.put(0, b"pose", now_ns=0, source_received_ns=0, epoch=(2, 1)) == 0
        assert dropped.counts[counter] == 1 and dropped.snapshot(0)["queued"] == 0


@pytest.mark.parametrize("settings", [{"seats": 4}, {"delay_ms": 1501}, {"jitter_ms": -1},
                                      {"loss_percent": True}, {"seed": -1}, {"outages": (False,)}])
def test_fault_settings_are_strict_and_bounded(settings):
    with pytest.raises(ValueError):
        Faults(**settings)


class Recorder:
    def __init__(self):
        self.pair, self.events = (None, None), []
        self.ghost_capture, self.endpoint = self, object()
        self.paused, self.attached = False, True

    def set_live_session(self, outgoing=None, incoming=None):
        self.pair = outgoing, incoming
        self.events.append(self.pair)

    def live_session_status(self):
        return {"paused": self.paused, "source_attached": self.attached}

    def native_live_endpoint(self, incoming):
        return self.endpoint if incoming is self.pair[1] else None


class Channel:
    def __init__(self, endpoint):
        self.endpoint, self.views, self.closed, self.close_failures = endpoint, [], False, 0
        self.disable_failures = 0

    def write(self, view):
        value = Update.from_buffer_copy(view.wire)
        if not value.enabled and self.disable_failures:
            self.disable_failures -= 1
            raise BlockingIOError("injected disabled-view wait")
        self.views.append(value)

    def close(self):
        if self.close_failures:
            self.close_failures -= 1
            raise BlockingIOError("injected cleanup wait")
        self.closed = True


@pytest.fixture
def lab():
    now, channels, recorder = [1_000_000_000], [], Recorder()

    def factory(endpoint):
        channel = Channel(endpoint); channels.append(channel); return channel

    value = module.LabService(recorder, channel_factory=factory, clock_ns=lambda: now[0], spawn_worker=False)
    yield value, recorder, now, channels
    for channel in channels:
        channel.close_failures = 0
        channel.disable_failures = 0
    value.close()


def publish(lab, *, sequence=1, owner=None):
    value, recorder, now, _channels = lab
    source = recorder.pair[0]
    owner = source.begin_capture() if owner is None else owner
    captured = producer(rom_layout=ROM_US, qpc_frequency=1_000_000_000)
    source.observe(owner, captured, (frame(sequence, qpc=now[0]),), received_ns=now[0])
    return owner


def test_adapter_is_dormant_and_normal_friends_remains_explicitly_unavailable(lab):
    service, recorder, _now, channels = lab
    assert not service.lab_snapshot()["active"] and not recorder.events and not channels
    assert service.snapshot()["available"] is False and "LOOPBACK" in service.snapshot()["unavailable_reason"]
    with pytest.raises(module.RoomError):
        service.identity()
    assert not recorder.events


def test_actual_captured_compact_frame_is_copied_to_three_distinct_lab_seats(lab):
    service, recorder, now, channels = lab
    service.start(Faults(seats=3, delay_ms=100))
    publish(lab)
    service.step()
    queued = service._queue._heap
    poses = [decode_pose(item.packet, expected_incarnation=service._peers[item.peer]) for item in queued]
    assert len(poses) == 3 and len({pose.participant_incarnation for pose in poses}) == 3
    assert all(pose.frame.sequence == 1 and pose.frame.qpc == now[0] for pose in poses)
    assert len({(pose.source_generation, pose.elapsed_ns, pose.continuity) for pose in poses}) == 1
    assert all(not peer.present for peer in channels[-1].views[-1].peers)
    now[0] += 100_000_000; service.step()
    assert service.lab_snapshot()["inbox_accepted"] == 3
    assert all(peer.present for peer in channels[-1].views[-1].peers)
    assert recorder.pair[0] is service._source  # only one source handoff, no alternate consumer


def test_capture_age_and_pump_receipt_age_have_independent_actual_stamps(lab):
    service, recorder, now, _channels = lab
    now[0] = 1_200_000_000
    results = []
    for receipt in (1_100_000_000, 1_150_000_000):
        service.start(Faults())
        source = recorder.pair[0]
        owner = source.begin_capture()
        source.observe(owner, producer(rom_layout=ROM_US, qpc_frequency=1_000_000_000),
                       (frame(1, qpc=1_000_000_000),), received_ns=receipt)
        service.step(); results.append(service.lab_snapshot())
    assert [state["capture_age_ms"] for state in results] == [200, 200]
    assert [state["pump_receipt_age_ms"] for state in results] == [100, 50]


@pytest.mark.parametrize("frequency,qpc", [(0, 1), (True, 1), (-1, 1), (1 << 63, 1), (1000, -1), (1000, 2000)])
def test_capture_age_is_unknown_for_invalid_frequency_or_future_local_stamp(frequency, qpc):
    pose = SimpleNamespace(frame=SimpleNamespace(qpc=qpc))
    publication = SimpleNamespace(source_frequency=frequency)
    assert module.LabService._capture_stamp(pose, publication, 1_000_000_000) is None


def test_source_gap_clears_inflight_before_any_new_pose_and_old_run_cannot_reappear(lab):
    service, recorder, now, channels = lab
    service.start(Faults(delay_ms=200))
    owner = publish(lab); service.step()
    source = recorder.pair[0]
    assert service.lab_snapshot()["queue"]["queued"] == 1
    source.source_unavailable(owner)
    now[0] += 200_000_000; service.step()
    assert service.lab_snapshot()["queue"]["queued"] == 0
    assert not channels[-1].views[-1].peers[0].present
    publish(lab, sequence=2, owner=owner); service.step()
    now[0] += 200_000_000; service.step()
    assert service.lab_snapshot()["inbox_accepted"] == 1
    assert decode_pose(bytes(channels[-1].views[-1].peers[0].packet), expected_incarnation=service._peers[0]).frame.sequence == 2


def test_gap_during_enqueue_cannot_tag_an_old_publication_as_the_new_epoch(lab, monkeypatch):
    service, recorder, _now, _channels = lab
    service.start(Faults(delay_ms=100))
    owner = publish(lab)
    encode = module.encode_pose

    def gap(*args, **kwargs):
        recorder.pair[0].source_unavailable(owner)
        return encode(*args, **kwargs)

    monkeypatch.setattr(module, "encode_pose", gap)
    service.step()
    assert service.lab_snapshot()["queue"]["queued"] == 0
    assert service.lab_snapshot()["inbox_accepted"] == 0


@pytest.mark.parametrize("reason", ["pause", "detach", "expiry"])
def test_pause_source_loss_and_expiry_clear_queued_display(lab, reason):
    service, recorder, now, channels = lab
    service.start(Faults(delay_ms=200))
    publish(lab); service.step()
    if reason == "pause":
        recorder.paused = True
    elif reason == "detach":
        recorder.attached = False
    else:
        now[0] += 300_000_000
    service.step()
    assert service.lab_snapshot()["queue"]["queued"] == 0
    assert not channels[-1].views[-1].peers[0].present


def test_duplicate_faults_reach_the_real_receiver_hwm_and_are_refused(lab):
    service, _recorder, now, _channels = lab
    service.start(Faults(duplicate_percent=100))
    publish(lab); service.step()
    now[0] += 1_000_000; service.step()
    assert service.lab_snapshot()["inbox_accepted"] == 1
    assert service.lab_snapshot()["inbox_rejected"] == 1


def test_reorder_hold_delivers_the_new_pose_first_and_receiver_rejects_the_old_one(lab):
    selected = None
    for seed in range(100):
        queue = FaultQueue(Faults(reorder_percent=50, seed=seed), (1, 1))
        queue.put(0, b"old", now_ns=0, source_received_ns=0, epoch=(1, 1))
        queue.put(0, b"new", now_ns=30_000_000, source_received_ns=30_000_000, epoch=(1, 1))
        if [item.packet for item in queue.take_due(now_ns=30_000_000, epoch=(1, 1))] == [b"new"]:
            selected = seed; break
    assert selected is not None, "no seed produced a held first packet"
    service, _recorder, now, channels = lab
    service.start(Faults(reorder_percent=50, seed=selected))
    owner = publish(lab); service.step()
    now[0] += 30_000_000; publish(lab, sequence=2, owner=owner); service.step()
    assert service.lab_snapshot()["inbox_accepted"] == 1
    now[0] += 70_000_000; service.step()
    assert service.lab_snapshot()["inbox_rejected"] == 1
    packet = bytes(channels[-1].views[-1].peers[0].packet)
    assert decode_pose(packet, expected_incarnation=service._peers[0]).frame.sequence == 2


def test_stop_revokes_before_failed_cleanup_and_blocks_replacement(lab):
    service, recorder, now, channels = lab
    service.start(Faults())
    publish(lab); source = recorder.pair[0]; service.step()
    publication = service._last_publication
    channel = channels[-1]; channel.close_failures = 2
    with pytest.raises(RuntimeError, match="cleanup"):
        service.stop()
    assert recorder.pair == (None, None) and not service.lab_snapshot()["active"]
    assert not channel.views[-1].enabled and not source.current(publication, now_ns=now[0])
    with pytest.raises(RuntimeError, match="cleanup"):
        service.start(Faults())
    assert len(channels) == 1 and service._channel is channel
    service.stop()
    service.start(Faults())
    assert service._run_id == 2


def test_newer_pose_does_not_cancel_an_older_simulated_inflight_arrival(lab):
    service, _recorder, now, channels = lab
    service.start(Faults(delay_ms=100))
    owner = publish(lab); service.step()
    now[0] += 30_000_000; publish(lab, sequence=2, owner=owner); service.step()
    now[0] += 70_000_000; service.step()
    assert service.lab_snapshot()["inbox_accepted"] == 1
    first = channels[-1].views[-1].peers[0]
    assert decode_pose(bytes(first.packet), expected_incarnation=service._peers[0]).frame.sequence == 1
    now[0] += 30_000_000; service.step()
    assert service.lab_snapshot()["inbox_accepted"] == 2


def test_failed_worker_start_withdraws_requested_capture_without_a_stranded_thread(lab, monkeypatch):
    service, recorder, _now, channels = lab
    service._spawn_worker = True

    class RefusedThread:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            raise RuntimeError("injected thread resource failure")

    monkeypatch.setattr(module.threading, "Thread", RefusedThread)
    with pytest.raises(RuntimeError, match="could not start"):
        service.start(Faults())
    assert recorder.pair == (None, None) and service._thread is None
    assert not service.lab_snapshot()["active"] and not channels


def test_failed_channel_constructor_keeps_its_cleanup_owner(lab):
    service, _recorder, _now, _channels = lab
    pending = Channel(None); pending.close_failures = 1
    attempts = []

    def factory(_endpoint):
        attempts.append(True)
        raise CleanupPending(pending, OSError("injected partial owner"))

    service._channel_factory = factory
    service.start(Faults()); publish(lab); service.step()
    assert service._channel is pending and service.lab_snapshot()["cleanup_pending"]
    with pytest.raises(RuntimeError):
        service.start(Faults())
    assert attempts == [True]
    service.stop()


def test_real_stop_retries_the_disabled_view_before_releasing_the_native_writer(lab):
    service, recorder, _now, channels = lab
    service.start(Faults()); publish(lab); service.step()
    channel = channels[-1]; channel.disable_failures = 1
    with pytest.raises(RuntimeError, match="cleanup"):
        service.stop()
    assert not channel.closed and recorder.pair == (None, None)
    service.stop()
    assert channel.closed and not channel.views[-1].enabled
    assert service.lab_snapshot()["error"] is None


def test_lab_composition_is_offline_and_restores_the_actual_main_factories():
    fake = SimpleNamespace()
    original_room, original_population = object(), object()
    fake._build_multiplayer, fake._community_population = original_room, original_population
    fake._local_ghost_population = lambda tracker: ("local", tracker)
    received = {}

    def create(**kwargs):
        received.update(kwargs); return FastAPI()

    fake.create_app = create

    def build():
        received["population"] = fake._community_population("tracker")
        service = fake._build_multiplayer(SimpleNamespace(recorder=Recorder()), appearance=lambda: None)
        return fake.create_app(multiplayer=service, refresh_library_on_start=True)

    fake.build = build
    app = module.build_lab_app(main_module=fake, local_port=8094)
    assert fake._build_multiplayer is original_room and fake._community_population is original_population
    assert fake.create_app is create
    assert received["population"] == (("local", "tracker"), None, None)
    assert received["refresh_library_on_start"] is False
    assert received["local_port"] == 8094
    assert not app.state.m3_lab.lab_snapshot()["active"]
    paths = {route.path for route in app.routes}
    assert {"/m3-lab", "/api/m3-lab", "/api/m3-lab/start", "/api/m3-lab/stop"} <= paths
    state = next(route for route in app.routes if route.path == "/api/m3-lab")
    assert state.endpoint(Response())["mode"] == "SIMULATED LOOPBACK"
    app.state.m3_lab.close()


def test_restarted_lab_uses_fresh_admission_namespaces_with_the_same_fault_seed():
    first = module.LabService(Recorder(), spawn_worker=False)
    second = module.LabService(Recorder(), spawn_worker=False)
    try:
        first.start(Faults(seed=42)); second.start(Faults(seed=42))
        assert first._peers != second._peers
        assert bytes(first._inbox.snapshot(now_ns=1).wire[40:56]) != bytes(second._inbox.snapshot(now_ns=1).wire[40:56])
        assert first._queue.faults.seed == second._queue.faults.seed == 42
    finally:
        first.close(); second.close()
