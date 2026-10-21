"""Actual NativeCapture pump with a scripted channel and the real live handoff."""
import threading
import time

from ghost_native_fixture import frame
from sm64_events.ghost import capture
from sm64_events.ghost.clip import ClipError
from sm64_events.ghost.native_protocol import Status
from sm64_events.memory.layout import US
from sm64_events.multiplayer.protocol import decode_pose
from sm64_events.multiplayer.source_handoff import SourceHandoff

INCARNATION = bytes(range(16))


def status(*, epoch=5, dropped=0):
    value = Status()
    value.producer_pid, value.producer_created_lo = 123, 456
    value.generation, value.rom_generation, value.configuration = 2, 3, 4
    value.epoch, value.qpc_frequency, value.dropped = epoch, 30, dropped
    return value


class Script:
    def __init__(self, rows):
        self.rows, self.constructions, self.closed = list(rows), 0, 0
        self.finished = threading.Event()
        self.capture = None

    def channel(self, *_):
        self.constructions += 1
        return self

    def poll(self, _controls):
        if not self.rows:
            self.capture._stop.set()
            self.finished.set()
            return status(), ()
        item = self.rows.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item()
        return item

    def close(self):
        self.closed += 1


def make_capture(monkeypatch, script, archive, *, live=None):
    monkeypatch.setattr(capture, "NativeChannel", script.channel)
    source = capture.NativeCapture(US, archive, producer_pid=123, live_source=live)
    script.capture = source
    return source


def finish(source, script):
    try:
        assert script.finished.wait(2), "scripted source did not drain"
    finally:
        assert source.stop(3)


def handoff():
    return SourceHandoff(INCARNATION, stale_after_ns=10_000_000_000)


def latest(live):
    value = live.take_latest(now_ns=time.monotonic_ns())
    assert value is not None
    return value


def pose(value):
    return decode_pose(value.packet, expected_incarnation=INCARNATION)


def test_optional_tap_observes_every_hidden_boundary_before_archive_and_coalescing(monkeypatch):
    hidden = bytearray(frame(2)); hidden[152] = 1
    rows = (frame(1), bytes(hidden), frame(3))
    script, live, received = Script([(status(), rows)]), handoff(), []

    def archive(producer, frames):
        publication = latest(live)
        received.append((producer, frames, pose(publication), publication))
        assert live.current(publication, now_ns=time.monotonic_ns())

    source = make_capture(monkeypatch, script, archive, live=live)
    source.start(); finish(source, script)
    assert script.constructions == script.closed == 1
    assert received[0][1] == rows
    assert received[0][2].frame.sequence == 3 and received[0][2].continuity == 3
    assert not live.current(received[0][3], now_ns=time.monotonic_ns())


def test_room_tap_can_attach_and_detach_on_the_same_running_archive_source(monkeypatch):
    script, live, seen, values = Script([(status(), (frame(i),)) for i in range(1, 5)]), handoff(), [], []

    def archive(_producer, frames):
        seen.append(frames)
        if len(seen) == 1:
            source.set_live_source(live)
        elif len(seen) == 2:
            values.append(latest(live))
            source.set_live_source(None)
        else:
            assert live.take_latest(now_ns=time.monotonic_ns()) is None
            assert not live.current(values[0], now_ns=time.monotonic_ns())

    source = make_capture(monkeypatch, script, archive)
    source.start(); finish(source, script)
    assert len(seen) == 4 and pose(values[0]).frame.sequence == 2
    assert script.constructions == script.closed == 1


def test_stop_withdraws_a_taken_packet_before_a_stalled_archive_callback_joins(monkeypatch):
    script, live, taken = Script([(status(), (frame(1),))]), handoff(), []
    entered, release = threading.Event(), threading.Event()

    def archive(*_):
        taken.append(latest(live)); entered.set()
        assert release.wait(3)

    source = make_capture(monkeypatch, script, archive, live=live)
    source.start()
    try:
        assert entered.wait(2) and live.current(taken[0], now_ns=time.monotonic_ns())
        assert not source.stop(0)
        assert not live.current(taken[0], now_ns=time.monotonic_ns())
        assert script.closed == 0 and source._thread.is_alive()
    finally:
        release.set()
        assert source.stop(3)
    assert script.closed == 1


def test_optional_live_failure_does_not_reconnect_or_drop_archive_records(monkeypatch, caplog):
    class Broken(SourceHandoff):
        def observe(self, *args, **kwargs):
            raise RuntimeError("injected live observer fault")

    live = Broken(INCARNATION, stale_after_ns=50)
    script, seen = Script([(status(), (frame(i),)) for i in range(1, 4)]), []
    source = make_capture(monkeypatch, script, lambda *args: seen.append(args), live=live)
    source.start(); finish(source, script)
    assert len(seen) == 3 and script.constructions == script.closed == 1
    assert sum("injected live observer fault" in row.getMessage() for row in caplog.records) == 1
    assert live.take_latest(now_ns=time.monotonic_ns()) is None


def test_archive_refusal_does_not_suppress_the_independent_live_packet(monkeypatch):
    script, live, seen = Script([(status(), (frame(1),))]), handoff(), []

    def archive(*_):
        seen.append(pose(latest(live)))
        raise ClipError("injected archive refusal")

    source = make_capture(monkeypatch, script, archive, live=live)
    source.start(); finish(source, script)
    assert seen[0].frame.sequence == 1 and script.constructions == 1
    assert source.snapshot_status()["reason"] == "stopped"


def test_source_absence_invalidates_delivery_before_reconnect_and_keeps_clock_fences(monkeypatch):
    live, taken, seen = handoff(), [], []

    def check_reconnected():
        assert not live.current(taken[0], now_ns=time.monotonic_ns())
        return status(), (frame(2),)

    script = Script([(status(), (frame(1),)), ProcessLookupError("source closed"), check_reconnected])

    def archive(*_):
        value = latest(live); taken.append(value); seen.append(pose(value))

    source = make_capture(monkeypatch, script, archive, live=live)
    wait = source._stop.wait
    monkeypatch.setattr(source._stop, "wait", lambda timeout=None: wait(min(timeout or 0, .001)))
    source.start(); finish(source, script)
    assert script.constructions == script.closed == 2
    assert [p.source_generation for p in seen] == [1, 1]
    assert [p.continuity for p in seen] == [1, 2]
    assert seen[1].elapsed_ns > seen[0].elapsed_ns


def test_empty_changed_source_status_and_known_queue_loss_invalidate_taken_packets(monkeypatch):
    live, taken, seen = handoff(), [], []

    def empty_after_gap():
        # This runs before the poll returns; the old value was current until
        # the pump actually receives the changed status and loss certificate.
        assert live.current(taken[-1], now_ns=time.monotonic_ns())
        return status(epoch=6, dropped=1), ()

    def after_gap():
        assert not live.current(taken[-1], now_ns=time.monotonic_ns())
        return status(epoch=6, dropped=1), (frame(1, epoch=6),)

    script = Script([(status(), (frame(1),)), empty_after_gap, after_gap])

    def archive(*_):
        value = latest(live); taken.append(value); seen.append(pose(value))

    source = make_capture(monkeypatch, script, archive, live=live)
    source.start(); finish(source, script)
    assert [p.source_generation for p in seen] == [1, 2]
    assert script.constructions == script.closed == 1


def test_no_live_tap_keeps_the_existing_archive_callback_path(monkeypatch):
    rows, seen = (frame(1), frame(2)), []
    script = Script([(status(), rows)])
    source = make_capture(monkeypatch, script, lambda *args: seen.append(args))
    source.start(); finish(source, script)
    assert seen[0][1] == rows and script.constructions == script.closed == 1


def test_pause_resume_coalescing_rebinds_source_and_immediately_withdraws_old_packet(monkeypatch):
    script, live, taken, continuities = Script([(status(), (frame(i),)) for i in range(1, 4)]), handoff(), [], []
    incoming = object()

    def archive(*_):
        value = latest(live); taken.append(value); continuities.append(pose(value).continuity)
        if len(taken) == 1:
            source.set_live_session(None, incoming)
            assert not live.current(value, now_ns=time.monotonic_ns())
            source.set_live_session(live, incoming)

    source = make_capture(monkeypatch, script, archive, live=live)
    source.set_live_input(incoming)
    source.start(); finish(source, script)
    assert continuities == [1, 2, 2]
    assert script.constructions == script.closed == 1
