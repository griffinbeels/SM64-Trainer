"""Actual recorder demand/retirement union with inert native/media boundaries.

No native mapping, emulator, media encoder, sound, provider or network opens.
The real optional pose archive and SourceHandoff execute where observed.
"""
from dataclasses import replace
import sqlite3
import threading

import pytest

from ghost_native_fixture import frame, producer
from sm64_events.ghost.recording import PoseRecording
from sm64_events.multiplayer.protocol import decode_pose
from sm64_events.multiplayer.source_handoff import SourceHandoff
from sm64_events.replay.clock import CaptureClock
from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.recorder import ReplayRecorder
from test_replay_recorder import FakeAudioSource, FakeAvSink, FakeVideoSource, T0, WIN

INCARNATION = bytes(range(16))


class Lease:
    def __init__(self):
        self.closed = False
        self.closes = 0

    def close(self):
        self.closed = True
        self.closes += 1


class OpaqueInput:
    def snapshot(self):
        pytest.fail("recorder inspected the incoming snapshot")


class Native:
    def __init__(self, harness, profile, *, on_batch, producer_pid, live_source=None, live_input=None):
        assert harness.lease is not None and not harness.lease.closed
        assert not any(source.alive for source in harness.sources)
        self.harness, self.profile, self.on_batch, self.pid = harness, profile, on_batch, producer_pid
        self.session = live_source, live_input
        self.binding = None
        self.alive, self.stopping, self.joined = False, False, True
        self.requests, self.starts, self.stops = 0, 0, 0
        self.changes = []
        harness.sources.append(self)

    def _bind(self, outgoing):
        if isinstance(outgoing, SourceHandoff):
            self.binding = outgoing, outgoing.begin_capture()

    def start(self):
        self.starts += 1
        if not self.stopping:
            self.alive = True
            self._bind(self.session[0])

    def set_live_session(self, outgoing, incoming):
        if self.binding is not None and self.session[0] is not outgoing:
            handoff, owner = self.binding
            handoff.withdraw_capture(owner)
            handoff.end_capture(owner)
            self.binding = None
        changed = self.session[0] is not outgoing
        self.session = outgoing, incoming
        self.changes.append(self.session)
        if changed and self.alive and not self.stopping:
            self._bind(outgoing)

    def set_live_source(self, outgoing):
        self.set_live_session(outgoing, self.session[1])

    def request_stop(self):
        self.requests += 1
        self.stopping = True
        if self.binding is not None:
            self.binding[0].withdraw_capture(self.binding[1])

    def stop(self, timeout_s=3):
        assert timeout_s == 3
        self.stops += 1
        self.request_stop()
        if not self.joined:
            return False
        if self.binding is not None:
            self.binding[0].end_capture(self.binding[1])
            self.binding = None
        self.alive = False
        return True

    def emit(self, sequence):
        raw = frame(sequence)
        if self.binding is not None and not self.stopping:
            self.binding[0].observe(self.binding[1], producer(rom_layout=1), (raw,), received_ns=sequence)
        self.on_batch(producer(rom_layout=1), (raw,))


class Harness:
    def __init__(self, tmp_path, *, enabled=False, denied=False, local_archive=False):
        self.lease, self.acquisitions, self.sources = None, 0, []
        self.media_calls = []
        # Silent transport tests have no local archival consumer. The actual
        # app also registers one independently of its footage preference.
        self.local_factory = lambda **kw: Native(self, "local", **kw)
        cfg = ReplayConfig(scratch_dir=tmp_path / "buffer", recording_enabled=enabled, attach_poll_s=0.01)
        self.rec = ReplayRecorder(cfg, lambda _: WIN, self.video, self.audio,
            codec="libx264", video_sink_factory=self.sink, recorder_lock_factory=lambda: self.acquire(denied),
            clock_factory=lambda: CaptureClock(anchor_qpc_100ns=0, anchor_utc=T0),
            ghost_capture_factory=self.local_factory if local_archive else None,
            live_capture_factory=lambda **kw: Native(self, "live", **kw))

    def acquire(self, denied=False):
        self.acquisitions += 1
        if denied:
            return None
        self.lease = Lease()
        return self.lease

    def video(self, _win):
        self.media_calls.append("video")
        return FakeVideoSource()

    def audio(self, _pid):
        self.media_calls.append("audio")
        return FakeAudioSource()

    def sink(self, *_args):
        self.media_calls.append("sink")
        return FakeAvSink()


def room():
    return SourceHandoff(INCARNATION, stale_after_ns=50), OpaqueInput()


def tick(rec, before=None):
    """One actual attach-loop iteration, without starting its thread or sleeping."""
    rec._stop_event.clear()
    rec._wait_attach = lambda _seconds: rec._stop_event.set()
    if before is not None:
        before()
    rec._run_attach_loop()
    rec._stop_event.clear()


def close(rec):
    rec.stop(cleanup=False)


def begin_capture(rec, errors):
    try:
        rec._begin_capture(WIN)
    except Exception as error:  # noqa: BLE001 - surface owned startup errors
        errors.append(error)


def test_no_room_defaults_preserve_disabled_discovery_and_capture(tmp_path):
    harness = Harness(tmp_path)
    rec = harness.rec
    rec._window_finder = lambda _: pytest.fail("no-demand recorder discovered a window")
    tick(rec)
    assert rec.live_session_status()["state"] == "off"
    assert not harness.sources and harness.acquisitions == 0
    assert not rec._cfg.scratch_dir.exists()
    close(rec)


@pytest.mark.parametrize("mode", ["outgoing", "incoming", "both"])
def test_live_only_uses_one_silent_source_without_any_media_storage_or_codec(tmp_path, monkeypatch, mode):
    harness = Harness(tmp_path)
    rec = harness.rec
    outgoing, incoming = room()
    pair = (None if mode == "incoming" else outgoing, None if mode == "outgoing" else incoming)
    def forbidden(*_args, **_kwargs):
        pytest.fail("live-only demand reached media/storage/codec resources")
    rec._codec = None
    rec._codec_factory = forbidden
    for name in ("_prepare_scratch", "_video_factory", "_audio_factory", "_video_sink_factory", "maintain_history"):
        monkeypatch.setattr(rec, name, forbidden)
    for owner, name in ((rec.ring, "maintain"), (rec.fragments, "maintain"), (rec.ghost_copies, "maintain"),
                        (rec.ledger, "open_archive"), (rec.ledger, "flush"), (rec._scratch, "prepare")):
        monkeypatch.setattr(owner, name, forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    rec.set_live_session(*pair)
    tick(rec)
    assert harness.acquisitions == 1 and len(harness.sources) == 1
    source = harness.sources[0]
    assert source.profile == "live" and source.session == pair and source.alive
    assert rec.pose_archive is None and rec._pose_owner.archive is None and rec._codec is None
    assert rec.status()["capture_state"] == "off" and not rec.status()["recording"]
    assert rec.live_session_status()["state"] == "attached" and not rec.can_collect()
    assert not rec._cfg.scratch_dir.exists() and not rec.recording_enabled
    tick(rec)  # lease ownership alone must not activate media maintenance
    rec.stop()
    assert harness.lease.closed and not rec._cfg.scratch_dir.exists()


def test_missing_silent_factory_never_falls_back_to_media(tmp_path):
    harness = Harness(tmp_path)
    rec = harness.rec
    rec.set_live_capture_factory(None)
    rec.set_live_session(*room())
    rec._window_finder = lambda _: pytest.fail("unavailable live source discovered a window")
    tick(rec)
    assert rec.live_session_status()["state"] == "unavailable"
    assert not harness.media_calls and not harness.sources and harness.acquisitions == 0
    close(rec)


@pytest.mark.parametrize("enabled", [False, True])
def test_denied_machine_lease_constructs_no_source_archive_or_media(tmp_path, enabled):
    harness = Harness(tmp_path, enabled=enabled, denied=True)
    harness.rec.set_live_session(*room())
    tick(harness.rec)
    assert harness.acquisitions == 1 and not harness.sources and not harness.media_calls
    assert harness.rec.ghost_capture is None and not harness.rec._cfg.scratch_dir.exists()
    close(harness.rec)


def test_media_and_live_share_one_native_owner_and_hotmount_without_media_restart(tmp_path):
    harness = Harness(tmp_path, enabled=True, local_archive=True)
    rec = harness.rec
    rec._begin_capture(WIN)
    source, archive = rec.ghost_capture, rec.pose_archive
    assert source.profile == "local" and archive is not None
    media_calls = list(harness.media_calls)
    pair = room()
    rec.set_live_session(*pair)
    assert rec.ghost_capture is source and source.session == pair
    source.emit(1)
    publication = pair[0].take_latest(now_ns=1)
    assert publication is not None and archive.status()["records"] == 1
    rec.set_live_session(None, None)
    assert not pair[0].current(publication, now_ns=2)
    assert rec.ghost_capture is source and rec._recording and harness.media_calls == media_calls
    assert harness.acquisitions == 1 and len(harness.sources) == 1
    close(rec)


def test_local_consumer_removal_hands_over_to_silent_room_under_same_lease(tmp_path):
    harness = Harness(tmp_path, enabled=True, local_archive=True)
    rec = harness.rec
    pair = room()
    rec.set_live_session(*pair)
    rec._begin_capture(WIN)
    old, archive, lease = rec.ghost_capture, rec.pose_archive, harness.lease
    old.emit(10)
    before = pair[0].take_latest(now_ns=10)
    rec.set_ghost_capture_factory(None)
    rec.set_recording_enabled(False)
    tick(rec)
    live = rec.ghost_capture
    assert live is not old and not old.alive and live.profile == "live"
    assert rec.pose_archive is archive and archive.status()["closed"] and rec._pose_owner.archive is None
    assert harness.lease is lease and not lease.closed and harness.acquisitions == 1
    live.emit(11)
    after = pair[0].take_latest(now_ns=11)
    old_pose = decode_pose(before.packet, expected_incarnation=INCARNATION)
    new_pose = decode_pose(after.packet, expected_incarnation=INCARNATION)
    assert new_pose.source_generation == old_pose.source_generation and new_pose.continuity > old_pose.continuity
    assert archive.status()["records"] == 1
    rec.set_ghost_capture_factory(harness.local_factory)
    rec.set_recording_enabled(True)
    tick(rec)
    assert rec.ghost_capture.profile == "local" and not live.alive
    assert rec.pose_archive is not archive and harness.lease is lease and harness.acquisitions == 1
    assert rec._recording and rec.recording_enabled
    close(rec)
    assert lease.closed and lease.closes == 1


def test_manual_pause_withdraws_outgoing_retains_incoming_and_room_then_resumes(tmp_path):
    harness = Harness(tmp_path)
    rec = harness.rec
    outgoing, incoming = room()
    rec.set_live_session(outgoing, incoming)
    tick(rec)
    source = rec.ghost_capture
    source.emit(1)
    publication = outgoing.take_latest(now_ns=1)
    rec.set_session_paused(True)
    assert source.session == (None, incoming) and not outgoing.current(publication, now_ns=2)
    assert rec.live_session_status()["incoming"] and not rec.live_session_status()["sharing"]
    rec.set_session_paused(False)
    assert rec.ghost_capture is source and source.session == (outgoing, incoming)
    source.emit(2)
    resumed = outgoing.take_latest(now_ns=2)
    assert decode_pose(resumed.packet, expected_incarnation=INCARNATION).continuity > 1
    assert harness.acquisitions == 1 and len(harness.sources) == 1 and not rec.recording_enabled
    close(rec)


def test_afk_does_not_suspend_live_and_live_only_session_reset_preserves_owner(tmp_path):
    harness = Harness(tmp_path)
    rec = harness.rec
    pair = room()
    rec.set_live_session(*pair)
    tick(rec)
    source, lease = rec.ghost_capture, harness.lease
    rec._last_player_active = 0
    rec._maybe_idle_pause()
    rec._set_idle(True)
    assert source.session == pair and not source.stopping
    assert not rec.reset_session_scratch()
    assert rec.ghost_capture is source and harness.lease is lease and not lease.closed
    assert not rec._cfg.scratch_dir.exists()
    close(rec)


def test_room_leave_retires_source_and_final_lease_without_changing_recording_preference(tmp_path):
    harness = Harness(tmp_path)
    rec = harness.rec
    rec.set_live_session(*room())
    tick(rec)
    source, lease = rec.ghost_capture, harness.lease
    rec.set_live_session()
    tick(rec)
    assert source.stopping and not source.alive and lease.closed
    assert rec.ghost_capture is None and rec.live_session_status()["state"] == "off"
    assert not rec.recording_enabled and rec.status()["capture_state"] == "off"
    close(rec)


def test_failed_live_close_retains_exact_owner_and_lease_and_reports_media_off(tmp_path):
    harness = Harness(tmp_path)
    rec = harness.rec
    rec.set_live_session(*room())
    tick(rec)
    source, pose_owner, lease = rec.ghost_capture, rec._pose_owner, harness.lease
    source.joined = False
    rec.set_live_session()
    tick(rec)
    assert rec._capture_retained["poses"] is pose_owner and not lease.closed
    assert rec.status()["capture_state"] == "off" and rec.live_session_status()["state"] == "stopping"
    rec.set_live_session(*room())
    tick(rec)
    assert len(harness.sources) == 1 and not lease.closed
    source.joined = True
    tick(rec)
    assert len(harness.sources) == 2 and not source.alive
    close(rec)


def test_failed_media_to_live_handover_retries_same_cleanup_then_reuses_lease(tmp_path):
    harness = Harness(tmp_path, enabled=True, local_archive=True)
    rec = harness.rec
    rec.set_live_session(*room())
    rec._begin_capture(WIN)
    source, lease = rec.ghost_capture, harness.lease
    source.joined = False
    rec.set_ghost_capture_factory(None)
    rec.set_recording_enabled(False)
    tick(rec)
    assert rec._capture_cleanup_failed and not lease.closed and len(harness.sources) == 1
    source.joined = True
    tick(rec)
    assert rec.ghost_capture.profile == "live" and harness.lease is lease and harness.acquisitions == 1
    close(rec)


@pytest.mark.parametrize("change", ["window", "rom", "factory"])
def test_live_retarget_joins_old_source_before_new_source(tmp_path, change):
    harness = Harness(tmp_path)
    rec = harness.rec
    rec.set_live_session(*room())
    tick(rec)
    old = rec.ghost_capture
    if change == "window":
        rec._window_finder = lambda _: replace(WIN, pid=43, hwnd=124)
    elif change == "rom":
        rec.retarget_capture()
    else:
        rec.set_live_capture_factory(lambda **kw: Native(harness, "replacement", **kw))
    tick(rec)
    assert not old.alive and rec.ghost_capture is not old and len(harness.sources) == 2
    close(rec)


def test_unsupported_rom_gate_never_starts_live_source(tmp_path):
    harness = Harness(tmp_path)
    rec = harness.rec
    rec.set_live_session(*room())
    rec.set_capture_gate(lambda: False)
    tick(rec)
    assert not harness.sources and harness.acquisitions == 0 and rec._capture_gated
    close(rec)


def test_lazy_codec_factory_runs_only_after_media_demand_and_lease(tmp_path):
    harness = Harness(tmp_path)
    rec = harness.rec
    calls = []
    def codec():
        assert harness.lease is not None and not harness.lease.closed
        calls.append("codec")
        return "libx264"
    rec._codec, rec._codec_factory = None, codec
    rec.set_live_session(*room())
    tick(rec)
    assert calls == []
    rec.set_recording_enabled(True)
    tick(rec)
    assert calls == ["codec"] and rec._codec == "libx264"
    close(rec)


def test_media_pressure_recovery_rechecks_owned_scratch_then_live_handover_stays_disk_silent(tmp_path, monkeypatch):
    harness = Harness(tmp_path, enabled=True)
    rec = harness.rec
    rec._begin_capture(WIN)
    lease = harness.lease
    rec._teardown_capture(keep_owner=True)
    assert not rec._recording and rec._scratch_ready and harness.lease is lease and not lease.closed
    checks = []
    monkeypatch.setattr(rec.ring, "maintain", lambda: checks.append("ring"))
    monkeypatch.setattr(rec.fragments, "maintain", lambda: checks.append("fragments"))
    monkeypatch.setattr(rec.ghost_copies, "maintain", lambda: checks.append("copies"))
    monkeypatch.setattr(rec.ledger, "flush", lambda: checks.append("ledger"))
    rec.maintain_history = lambda: checks.append("history")
    rec._next_storage_maintenance = 0
    rec._maintain_media_storage()
    assert checks == ["ring", "fragments", "copies", "history", "ledger"]
    rec.set_live_session(*room())
    rec.set_recording_enabled(False)
    tick(rec)
    assert rec._source_profile == "live" and harness.lease is lease and not lease.closed
    rec._next_storage_maintenance = 0
    rec._maintain_media_storage()
    assert checks == ["ring", "fragments", "copies", "history", "ledger"]
    close(rec)


def test_pose_owner_joint_constructor_and_compatibility_setters_preserve_other_endpoint():
    seen = []
    class Source:
        def __init__(self, *, on_batch, producer_pid, live_source, live_input):
            self.session = live_source, live_input
            seen.append((on_batch, producer_pid))
        def start(self): pass
        def set_live_session(self, outgoing, incoming):
            self.session = outgoing, incoming
        def stop(self, timeout_s=3): return True
    outgoing, incoming = object(), object()
    owner = PoseRecording(None, Source, producer_pid=123, allowed=lambda: True,
                          live_source=outgoing, live_input=incoming)
    owner.start()
    assert owner.source.session == (outgoing, incoming) and seen[0][1] == 123
    replacement = object()
    owner.set_live_source(replacement)
    assert owner.source.session == (replacement, incoming)
    owner.set_live_input(None)
    assert owner.source.session == (replacement, None)
    owner.stop()


def test_pose_owner_honestly_refuses_incoming_on_outgoing_only_legacy_source():
    class Legacy:
        def __init__(self, *, on_batch, producer_pid): pass
        def start(self): pass
        def set_live_source(self, _source): pass
        def stop(self, timeout_s=3): return True
    owner = PoseRecording(None, Legacy, producer_pid=123, allowed=lambda: True)
    owner.start()
    with pytest.raises(RuntimeError, match="does not support"):
        owner.set_live_session(None, OpaqueInput())
    owner.stop()


@pytest.mark.parametrize("action", ["leave", "replace", "media", "stop"])
def test_blocked_live_factory_cannot_resurrect_retired_demand(tmp_path, action):
    harness = Harness(tmp_path)
    rec, original_pair = harness.rec, room()
    rec.set_live_session(*original_pair)
    entered, release, errors = threading.Event(), threading.Event(), []
    def delayed_factory(**kwargs):
        entered.set()
        assert release.wait(3)
        return Native(harness, "delayed", **kwargs)
    rec.set_live_capture_factory(delayed_factory)
    worker = threading.Thread(target=begin_capture, args=(rec, errors), name="owned-demand-start", daemon=True)
    stopper = None
    worker.start()
    try:
        assert entered.wait(1)
        if action == "leave":
            rec.set_live_session()
        elif action == "replace":
            rec.set_live_session(*room())
        elif action == "media":
            rec.set_ghost_capture_factory(harness.local_factory)
            rec.set_recording_enabled(True)
        else:
            requested = threading.Event()
            original_stop = rec._pose_owner.request_stop
            def request_stop():
                original_stop()
                requested.set()
            rec._pose_owner.request_stop = request_stop
            stopper = threading.Thread(target=lambda: close(rec), name="owned-demand-stop", daemon=True)
            stopper.start()
            assert requested.wait(1), "app stop waited behind source factory"
    finally:
        release.set()
        worker.join(3)
        if stopper is not None:
            stopper.join(3)
    assert not worker.is_alive() and (stopper is None or not stopper.is_alive()) and not errors
    assert not harness.sources[0].alive and original_pair[0].take_latest(now_ns=0) is None
    if action in {"leave", "stop"}:
        assert harness.sources[0].starts == 0 and harness.lease.closed
    else:
        assert not harness.lease.closed
        tick(rec)
        assert rec.ghost_capture is harness.sources[-1] and len(harness.sources) == 2
        assert harness.acquisitions == 1
    close(rec)


def test_rapid_live_leave_rejoin_retires_old_writer_before_replacement(tmp_path):
    harness = Harness(tmp_path)
    rec = harness.rec
    pair = room()
    rec.set_live_session(*pair)
    tick(rec)
    old, lease = rec.ghost_capture, harness.lease
    old.emit(1)
    publication = pair[0].take_latest(now_ns=1)
    rec.set_live_session()
    rec.set_live_session(*pair)
    assert old.stopping and not pair[0].current(publication, now_ns=2)
    tick(rec)
    assert rec.ghost_capture is not old and not old.alive
    assert harness.lease is lease and harness.acquisitions == 1
    rec.ghost_capture.emit(2)
    current = pair[0].take_latest(now_ns=2)
    assert decode_pose(current.packet, expected_incarnation=INCARNATION).continuity > 1
    close(rec)


def test_media_none_same_burst_forwards_both_mounts_and_invalidates_taken_value(tmp_path):
    harness = Harness(tmp_path, enabled=True, local_archive=True)
    rec = harness.rec
    pair = room()
    rec.set_live_session(*pair)
    rec._begin_capture(WIN)
    source = rec.ghost_capture
    source.emit(1)
    publication = pair[0].take_latest(now_ns=1)
    rec.set_live_session()
    rec.set_live_session(*pair)
    assert source.changes[-2:] == [(None, None), pair]
    assert not pair[0].current(publication, now_ns=2) and rec.ghost_capture is source
    close(rec)


def test_live_gate_loss_withdraws_then_recovers_under_same_machine_lease(tmp_path):
    harness = Harness(tmp_path)
    rec = harness.rec
    pair, gate = room(), [True]
    rec.set_live_session(*pair)
    rec.set_capture_gate(lambda: gate[0])
    tick(rec)
    old, lease = rec.ghost_capture, harness.lease
    old.emit(1)
    publication = pair[0].take_latest(now_ns=1)
    gate[0] = False
    tick(rec)
    assert not old.alive and not pair[0].current(publication, now_ns=2)
    assert rec.ghost_capture is None and not lease.closed
    gate[0] = True
    tick(rec)
    assert rec.ghost_capture is not old and harness.lease is lease and harness.acquisitions == 1
    close(rec)


def test_session_rotation_after_media_handover_preserves_live_source_and_lease(tmp_path):
    harness = Harness(tmp_path, enabled=True)
    rec = harness.rec
    rec.set_live_session(*room())
    rec._begin_capture(WIN)
    rec.set_recording_enabled(False)
    tick(rec)
    source, lease = rec.ghost_capture, harness.lease
    assert rec.reset_session_scratch()
    assert rec.ghost_capture is source and source.alive and harness.lease is lease and not lease.closed
    assert rec._pose_owner.archive is None and rec.pose_archive is None
    close(rec)


@pytest.mark.parametrize("remove_local", [False, True])
def test_media_disabled_during_cold_start_preserves_remaining_consumers_and_lease(tmp_path, remove_local):
    harness = Harness(tmp_path, enabled=True, local_archive=True)
    rec = harness.rec
    rec.set_live_session(*room())
    entered, release, errors = threading.Event(), threading.Event(), []
    class DelayedAudio(FakeAudioSource):
        def start(self, on_pcm):
            entered.set()
            assert release.wait(3)
            super().start(on_pcm)
    rec._audio_factory = lambda _pid: DelayedAudio()
    worker = threading.Thread(target=begin_capture, args=(rec, errors), name="owned-cold-media-handover", daemon=True)
    worker.start()
    try:
        assert entered.wait(1)
        lease = harness.lease
        source, archive = rec.ghost_capture, rec.pose_archive
        if remove_local:
            rec.set_ghost_capture_factory(None)
        rec.set_recording_enabled(False)
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive() and not errors and not lease.closed
    assert not rec._recording
    tick(rec)
    if remove_local:
        assert not source.alive and archive.status()["closed"] and rec.ghost_capture.profile == "live"
    else:
        assert rec.ghost_capture is source and rec.pose_archive is archive and source.alive
        source.emit(1)
        assert archive.status()["records"] == 1
        rec.set_recording_enabled(True)
        tick(rec)
        assert rec._recording and rec.ghost_capture is source and rec.pose_archive is archive
    assert harness.lease is lease and harness.acquisitions == 1
    close(rec)


def test_local_archive_and_room_keep_one_source_through_media_failure_off_on_and_leave(tmp_path):
    harness = Harness(tmp_path, local_archive=True)
    rec, pair = harness.rec, room()
    rec._codec = None
    rec._codec_factory = lambda: pytest.fail("pose-only source probed codec")
    rec.set_live_capture_factory(None)  # the existing local owner supplies both endpoints
    rec.set_live_session(*pair)
    tick(rec)
    source, archive, lease = rec.ghost_capture, rec.pose_archive, harness.lease
    assert source.profile == "local" and rec.live_session_status()["state"] == "attached"
    source.emit(1)
    assert archive.status()["records"] == 1 and pair[0].take_latest(now_ns=1) is not None
    assert not harness.media_calls and rec.status()["capture_state"] == "off"

    rec._codec_factory = lambda: "libx264"
    rec.set_recording_enabled(True)
    tick(rec)
    video = rec._video_source
    source.emit(2)
    rec._source_stopped(video)
    tick(rec)
    assert rec._recording and rec._video_source is not video
    rec.set_recording_enabled(False)
    tick(rec)
    source.emit(3)
    assert not rec._recording and rec.ghost_capture is source and rec.pose_archive is archive
    assert harness.lease is lease and not lease.closed and harness.acquisitions == 1

    rec.set_session_paused(True)
    source.emit(4)
    assert archive.status()["records"] == 3 and source.session == (None, pair[1])
    rec.set_session_paused(False)
    source.emit(5)
    publication = pair[0].take_latest(now_ns=5)
    rec.set_live_session()
    rec.set_live_session(*pair)
    assert not pair[0].current(publication, now_ns=6) and rec.ghost_capture is source
    rec.set_live_session()
    tick(rec)
    source.emit(6)
    assert archive.status()["records"] == 5 and rec.ghost_capture is source and not lease.closed
    rec.set_recording_enabled(True)
    tick(rec)
    assert rec._recording and rec.ghost_capture is source and len(harness.sources) == 1
    close(rec)
    assert archive.status()["closed"] and lease.closes == 1


@pytest.mark.parametrize("change", ["rom", "window", "gate"])
def test_local_and_live_retarget_retain_failed_owner_before_replacing_both(tmp_path, change):
    harness = Harness(tmp_path, local_archive=True)
    rec, pair = harness.rec, room()
    rec.set_live_session(*pair)
    tick(rec)
    source, archive, lease = rec.ghost_capture, rec.pose_archive, harness.lease
    source.emit(1)
    publication = pair[0].take_latest(now_ns=1)
    source.joined = False
    if change == "rom":
        rec.retarget_capture()
    elif change == "window":
        rec._window_finder = lambda _: replace(WIN, pid=43, hwnd=124)
    else:
        rec.set_capture_gate(lambda: False)
    tick(rec)
    assert rec._capture_cleanup_failed and not archive.status()["closed"]
    assert not pair[0].current(publication, now_ns=2) and not lease.closed
    tick(rec)
    assert len(harness.sources) == 1 and not lease.closed
    source.joined = True
    if change == "gate":
        rec.set_capture_gate(lambda: True)
    tick(rec)
    assert archive.status()["closed"] and not source.alive
    assert len(harness.sources) == 2 and rec.pose_archive is not archive
    close(rec)


def test_qualified_gpu_fallback_keeps_live_endpoints_archive_and_sharing(tmp_path, monkeypatch):
    """The real startup-failure route cannot trade ghost demand for footage."""
    from sm64_events.replay.sourcefactory import DesktopAfterGpuStartup
    from test_gpu_startup_fallback import setup_factory, run_failed_start

    factory, _ = setup_factory(monkeypatch)
    failed, _ = run_failed_start(factory)
    assert failed.error and failed.cleanup_error is None
    factory.desktop_factory = lambda *_args, **_kwargs: FakeVideoSource()
    harness = Harness(tmp_path, enabled=True, local_archive=True)
    rec, pair = harness.rec, room()
    rec._video_factory = factory.create
    rec.set_live_session(*pair)
    try:
        rec._begin_capture(WIN)
        assert isinstance(rec._video_source, DesktopAfterGpuStartup)
        assert rec.status()["recording"] and rec.status()["frame_source"] == "desktop"
        source, archive, lease = rec.ghost_capture, rec.pose_archive, harness.lease
        assert source.session == pair and rec.live_session_status()["incoming"]
        source.emit(1)
        assert pair[0].take_latest(now_ns=1) is not None and archive.status()["records"] == 1

        # An unavailable video source and the user's recording switch are both
        # media-only changes. They must not retire or replace the room source.
        rec._source_stopped(rec._video_source)
        tick(rec)
        rec.set_recording_enabled(False)
        tick(rec)
        source.emit(2)
        assert not rec.status()["recording"] and source.session == pair
        rec.set_recording_enabled(True)
        tick(rec)
        source.emit(3)
        assert rec.status()["recording"] and rec.ghost_capture is source
        assert rec.pose_archive is archive and archive.status()["records"] == 3
        assert pair[0].take_latest(now_ns=3) is not None
        assert harness.lease is lease and not lease.closed and harness.acquisitions == 1
        assert len(harness.sources) == 1 and source.session == pair
    finally:
        close(rec)
