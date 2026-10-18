"""Project64 opening and closing around the server: normal ends are quiet.

His report, 2026-09-28: "when I closed PJ64 with the server open, I got this
error: [ERROR sm64.replay GPU recording stopped ... RuntimeError: producer
channel stopped: state=3 (ChannelStopped reason=536880912 cancelled)] -- we
should probably make sure to catch / handle this. There are some obvious
scenarios to handle: (1) server closed, PJ64 already open, (2) server open,
PJ64 closed (open to closed OR just closed), (3) server closed, PJ64 closed,
(4) server open, PJ64 opened (closed -> opened OR already opened by the time
the server is opened). Need to make sure we're handling all these cases
gracefully."

One test per cell of that matrix, named by its cell (A-E below), on the
existing stand-ins: the lease fixture of tests/test_gpudemand.py, fake
channels and readers. Nothing here starts Project64 or touches its folder.

  A  server starts, Project64 already open
  B  server open, Project64 closes (or closes its ROM)
  C  server starts, Project64 closed
  D  server open, Project64 opens
  E  server shuts down, Project64 open

INFO for a normal event; WARNING or ERROR only for a real fault. The plugin
refresh cells (A3, C2, D3) live in tests/test_capturelayer.py.
"""

import asyncio
import logging
import threading
import time
from types import SimpleNamespace as NS

import pytest

from sm64_events.ghost import capture as ghost_capture
from sm64_events.ghost.native_protocol import Status as GhostStatus
from sm64_events.memory.base import MemoryReadError
from sm64_events.memory.layout import US
from sm64_events.replay import capturecontrol as C
from sm64_events.replay import gpuchannel as GC
from sm64_events.replay import gpusettings
from sm64_events.replay.gpucapture import GpuCapture
from sm64_events.replay.gpucapture_session import CaptureSession
from sm64_events.replay.gpuchannel import ChannelStopped, LayerVersionMismatch
from sm64_events.server.poller import Poller
from test_gpu_backend_route import factory_and_first_capture
from test_gpudemand import Fixture, eventual
from test_poller import RecordingBroadcaster, ScriptedReader, StubMemory, snap

GD_CANCELLED = 0x20002710  # the channel reason in his traceback: 536880912


@pytest.fixture(autouse=True)
def fresh_memo():
    gpusettings.forget_adapter_codecs()
    yield
    gpusettings.forget_adapter_codecs()


def loud(caplog):
    return [f"{r.levelname} {r.name}: {r.getMessage()}" for r in caplog.records
            if r.levelno >= logging.WARNING]


def lines(caplog, text):
    return [r for r in caplog.records if text in r.getMessage()]


def gpu_run(fixture, media_step):
    """One GPU capture over the lease fixture. `media_step(session)` runs as
    the media operation once the lease is ACTIVE; whatever it raises is what
    the real channel would have raised at that moment."""

    class Session(CaptureSession):
        def _open_channel(self):
            assert self.demand.ready.wait(1)
            fixture.change(ack_token=123, state=C.ACTIVE)
            eventual(lambda: self.demand.snapshot.state, lambda s: s == "active")
            return media_step(self)

    capture = GpuCapture(77, US, nominal_rate=30, demand_factory=lambda *a: fixture.demand(),
                         session_factory=Session)
    capture.create_sink(NS(picture_feed=True), object(), object(), lambda *a: None)
    stopped = threading.Event()
    capture.start(None, stopped.set)
    assert stopped.wait(3)
    capture.finish()
    return capture


def closes(fixture, **published):
    """Project64 publishes `published`, then its channel reports cancelled."""
    def step(session):
        fixture.change(**published)
        raise ChannelStopped(C.CLOSED, GD_CANCELLED)
    return step


@pytest.fixture
def lease():
    return Fixture()


# --- A: server starts, Project64 already open ------------------------------------

def test_a1_open_with_a_rom_running_records_without_a_warning(lease, caplog):
    demand = lease.demand().start()
    with caplog.at_level(logging.DEBUG):
        try:
            assert demand.ready.wait(1)
            lease.change(ack_token=123, state=C.ACTIVE)
            eventual(lambda: demand.snapshot.state, lambda s: s == "active")
        finally:
            demand.close()
    assert loud(caplog) == []


class Channels:
    """A scripted native ghost channel: each entry is the outcome of one
    connect ("ok", or an exception to raise), and a connected channel's
    polls run `polls` outcomes ("ok" or an exception) before it ends."""

    def __init__(self, connects, polls=()):
        self.connects, self.polls = list(connects), list(polls)

    def __call__(self, pid, layout):
        outcome = self.connects.pop(0) if self.connects else FileNotFoundError(2, "unavailable")
        if isinstance(outcome, Exception):
            raise outcome
        channel = NS(close=lambda: None)
        channel.poll = lambda control: self._poll()
        return channel

    def _poll(self):
        outcome = self.polls.pop(0) if self.polls else ProcessLookupError("native ghost channel has ended")
        if isinstance(outcome, Exception):
            raise outcome
        return GhostStatus(), ()


def run_ghost_capture(monkeypatch, channels, seconds):
    monkeypatch.setattr(ghost_capture, "NativeChannel", channels)
    capture = ghost_capture.NativeCapture(US, lambda *_: None, producer_pid=77)
    monkeypatch.setattr(capture._stop, "wait", lambda timeout=None: capture._stop.is_set()
                        or time.sleep(0.001))
    capture.start()
    time.sleep(seconds)
    assert capture.stop()
    return capture


def test_a2_open_without_a_rom_waits_quietly(lease, monkeypatch, caplog):
    """The lease waits for a ROM, and the ghost channel, which only exists
    while a ROM renders, is ABSENT: one INFO line, not a WARNING a minute
    ("native ghost channel unavailable; reconnecting: FileNotFoundError")."""
    lease.change(rom_open=False)
    demand = lease.demand().start()
    with caplog.at_level(logging.INFO):
        try:
            eventual(lambda: demand.snapshot.state, lambda s: s == "waiting_rom")
            assert not demand.done.is_set()
        finally:
            demand.close()
        run_ghost_capture(monkeypatch, Channels([FileNotFoundError(2, "unavailable")] * 5), 0.05)
    assert loud(caplog) == []
    assert len(lines(caplog, "native ghost channel absent")) == 1


def test_a4_a_capture_layer_from_another_build_keeps_the_desktop_with_one_warning(monkeypatch, caplog):
    """Project64 started before the trainer could refresh its layer, so it
    speaks an older GPU channel. Until it restarts no retry can succeed: one
    WARNING, the desktop grab with the reason, no retry cooldown, no ERROR
    traceback every 10-60 s."""
    def mismatched(**_):
        raise LayerVersionMismatch(1)

    with caplog.at_level(logging.INFO):
        factory, win, gpu, desktops = factory_and_first_capture(
            monkeypatch, lambda owner, demand: CaptureSession(
                owner, demand, channel_factory=mismatched))
        camera = factory.create(win)
    assert gpu.error is None and factory.retry_gate._failures == 0
    assert camera.frame_source == "desktop" and "different builds" in camera.gpu_unsupported
    assert [r.levelname for r in caplog.records if r.levelno >= logging.WARNING] == ["WARNING"]


# --- B: server open, Project64 closes ----------------------------------------------

def test_b1_closing_project64_mid_recording_is_one_info_line(lease, caplog):
    """His report. Project64 closing its ROM revokes GPU capture and closes
    the ROM in one publication (gfxwrap.c RomClosed); the lease read the
    capability first and called it a fault."""
    with caplog.at_level(logging.INFO):
        capture = gpu_run(lease, closes(lease, capabilities=0, rom_open=False, state=C.PASSIVE))
    assert capture.error is None
    assert loud(caplog) == []
    assert len(lines(caplog, "Project64 closed the ROM; recording stopped")) == 1
    assert lines(caplog, "failure before cleanup") == []


def test_b2_capability_revoked_a_moment_before_the_rom_closes_is_still_normal(lease, caplog):
    """The same close read half-published: capability gone, ROM still open."""
    def step(session):
        lease.change(capabilities=0)
        threading.Timer(0.04, lambda: lease.change(rom_open=False, state=C.PASSIVE)).start()
        raise ChannelStopped(C.CLOSED, GD_CANCELLED)

    with caplog.at_level(logging.INFO):
        capture = gpu_run(lease, step)
    assert capture.error is None and loud(caplog) == []
    assert len(lines(caplog, "Project64 closed the ROM; recording stopped")) == 1


def test_b3_the_project64_process_ending_says_so(lease, caplog):
    with caplog.at_level(logging.INFO):
        capture = gpu_run(lease, closes(lease, state=C.CLOSED, reason=C.OWNER_GONE))
    assert capture.error is None and loud(caplog) == []
    assert len(lines(caplog, "Project64 closed; recording stopped")) == 1


def test_b4_a_real_backend_loss_with_the_rom_open_is_still_an_error(lease, caplog):
    with caplog.at_level(logging.INFO):
        capture = gpu_run(lease, closes(lease, capabilities=0))
    assert capture.error and "gpu_capture_unavailable" in capture.error
    assert any(r.levelno == logging.ERROR and "GPU recording stopped" in r.getMessage()
               for r in caplog.records)


def test_b5_project64_closing_while_capture_starts_ends_quietly(lease, caplog):
    """Closed before the first picture: the lease is still PREPARING."""
    class Session(CaptureSession):
        def _open_channel(self):
            assert self.demand.ready.wait(1)
            lease.change(ack_token=123, state=C.PREPARING)
            eventual(lambda: self.demand.snapshot.state, lambda s: s == "preparing")
            lease.change(capabilities=0, rom_open=False, state=C.PASSIVE)
            raise ChannelStopped(C.CLOSED, GD_CANCELLED)

    capture = GpuCapture(77, US, nominal_rate=30, demand_factory=lambda *a: lease.demand(),
                         session_factory=Session)
    capture.create_sink(NS(picture_feed=True), object(), object(), lambda *a: None)
    stopped = threading.Event()
    with caplog.at_level(logging.INFO):
        capture.start(None, stopped.set)
        assert stopped.wait(3)
        capture.finish()
    assert capture.error is None and loud(caplog) == []


def test_b6_the_ghost_channel_ending_with_project64_is_one_info_line(monkeypatch, caplog):
    with caplog.at_level(logging.DEBUG):
        run_ghost_capture(monkeypatch, Channels(["ok"], polls=["ok", "ok"]), 0.05)
    assert loud(caplog) == []
    assert len(lines(caplog, "native ghost channel absent")) == 1


@pytest.mark.parametrize("retirement", [
    {"rom_open": False, "capabilities": 0, "state": C.PASSIVE},
    {"state": C.CLOSED, "reason": C.OWNER_GONE},
    {"generation": 5},
    {"state": C.UNAVAILABLE, "reason": C.FRESH_REQUEST},
], ids=["rom-close", "project64-exit", "plugin-reinit", "rapid-rom-swap"])
def test_image_disappears_before_lifecycle_publication_retires_quietly(lease, caplog, retirement):
    """Real report: source-image-missing wins the race to the control page.

    The producer's close may follow that terminal read. A terminal snapshot
    must not stop the supervisor before it can observe that explicit evidence.
    """
    workers = []

    def step(session):
        lease.change(state=C.UNAVAILABLE, reason=10016)
        original = lease.status
        observed = threading.Event()

        def status():
            value = original()
            if value.reason == 10016:
                observed.set()
            return value

        lease.status = status

        def retire():
            assert observed.wait(1)
            time.sleep(0.65)  # close follows the fault by ~0.66 s in his log
            lease.change(**retirement)

        worker = threading.Thread(target=retire)
        workers.append(worker)
        worker.start()
        raise ChannelStopped(GC.CLOSED, 0x20000000 | 10016)

    with caplog.at_level(logging.INFO):
        try:
            capture = gpu_run(lease, step)
        finally:
            for worker in workers:
                worker.join(2)
    assert capture.error is None and loud(caplog) == []
    assert lease.requests == 1 and lease.closed == 1
    assert lines(caplog, "failure before cleanup") == []
    # A reopened/successor producer admits a fresh lease after the old owners
    # close. No native request is resubmitted into the retired epoch.
    lease.change(rom_open=True, capabilities=C.CAP_GPU, state=C.PASSIVE,
                 ack_token=0, reason=0)
    next_demand = lease.demand().start()
    try:
        assert next_demand.ready.wait(1)
        lease.change(ack_token=123, state=C.ACTIVE)
        eventual(lambda: next_demand.snapshot.state, lambda s: s == "active")
        assert lease.requests == 2
    finally:
        next_demand.close()


def test_missing_image_with_a_live_rom_keeps_the_original_fault(lease, caplog):
    def step(session):
        lease.change(state=C.UNAVAILABLE, reason=10016)
        raise ChannelStopped(GC.CLOSED, 0x20000000 | 10016)

    with caplog.at_level(logging.INFO):
        capture = gpu_run(lease, step)
    assert "native_capture_ended:3:10016" in capture.error
    assert any(r.levelno == logging.ERROR for r in caplog.records)


def test_stopping_during_image_loss_cannot_erase_the_observed_fault(lease):
    demand = lease.demand().start()
    observed = threading.Event()
    original = lease.status

    def status():
        value = original()
        if value.reason == 10016:
            observed.set()
        return value

    try:
        assert demand.ready.wait(1)
        lease.change(ack_token=123, state=C.ACTIVE)
        eventual(lambda: demand.snapshot.state, lambda s: s == "active")
        lease.status = status
        lease.change(state=C.UNAVAILABLE, reason=10016)
        assert observed.wait(1)
        demand.request_stop("user_stopped", expected=True)
        assert demand.done.wait(1)
        assert demand.snapshot.reason == "native_capture_ended:3:10016"
        assert demand.snapshot.state == "fault"
        assert not demand.snapshot.lifecycle and not demand.snapshot.cancelled
    finally:
        demand.close()


def test_b6b_a_ghost_channel_that_misbehaves_still_warns(monkeypatch, caplog):
    with caplog.at_level(logging.INFO):
        run_ghost_capture(monkeypatch, Channels([RuntimeError("native ghost lease was replaced")]), 0.02)
    assert any("lease was replaced" in message for message in loud(caplog))


class ClosedMemory(StubMemory):
    def __init__(self, alive):
        super().__init__()
        self._alive = alive

    def alive(self):
        return self._alive


def test_b7_tracking_detaches_with_an_info_line_when_project64_closed(caplog):
    memory = ClosedMemory(alive=False)
    poller = Poller(memory, [], RecordingBroadcaster(),
                    reader=ScriptedReader([snap(1), MemoryReadError("gone")]))
    with caplog.at_level(logging.INFO):
        asyncio.run(poller.tick())
        asyncio.run(poller.tick())
    assert memory.detached and loud(caplog) == []
    assert len(lines(caplog, "Project64 closed; tracking detached")) == 1


def test_b8_a_read_failure_from_a_live_project64_still_warns(caplog):
    memory = ClosedMemory(alive=True)
    poller = Poller(memory, [], RecordingBroadcaster(),
                    reader=ScriptedReader([snap(1), MemoryReadError("unreadable")]))
    with caplog.at_level(logging.INFO):
        asyncio.run(poller.tick())
        asyncio.run(poller.tick())
    assert memory.detached and any("lost emulator" in message for message in loud(caplog))


# --- C: server starts, Project64 closed ------------------------------------------

def test_c1_no_project64_at_start_waits_without_a_warning(tmp_path, caplog):
    from test_replay_recorder import FakeAudioSource, FakeVideoSource, make_recorder

    recorder = make_recorder(tmp_path, FakeVideoSource(), FakeAudioSource(), found=None)
    with caplog.at_level(logging.INFO):
        recorder.start()
        try:
            time.sleep(0.1)
            assert recorder.status()["recording"] is False
        finally:
            recorder.stop()
    assert loud(caplog) == []


# --- D: server open, Project64 opens ---------------------------------------------

def test_d1_the_ghost_channel_logs_once_per_absence_not_once_per_minute(monkeypatch, caplog):
    """Project64 starts without a ROM (absent), a ROM renders (present), it
    closes (absent again): two absences, two INFO lines."""
    with caplog.at_level(logging.DEBUG):
        run_ghost_capture(monkeypatch, Channels(
            [FileNotFoundError(2, "unavailable")] * 3 + ["ok"], polls=["ok"]), 0.05)
    assert loud(caplog) == []
    assert len(lines(caplog, "native ghost channel absent")) == 2


def test_d4_a_project64_idle_after_a_rom_closed_waits_for_the_next_rom(lease):
    """Closing a ROM leaves GPU capture revoked until the next ROM opens. A
    request made meanwhile waited for a ROM only while capability remained;
    without it the lease faulted and armed the retry cooldown."""
    lease.change(rom_open=False, capabilities=0)
    demand = lease.demand().start()
    try:
        eventual(lambda: demand.snapshot.state, lambda s: s == "waiting_rom")
        assert not demand.done.is_set()
        lease.change(rom_open=True, capabilities=C.CAP_GPU)
        assert demand.ready.wait(1)
    finally:
        demand.close()


# --- E: server shuts down, Project64 open ----------------------------------------

def test_e1_stopping_the_server_mid_recording_is_quiet(lease, caplog):
    started = threading.Event()

    class Session(CaptureSession):
        def _open_channel(self):
            assert self.demand.ready.wait(1)
            lease.change(ack_token=123, state=C.ACTIVE)
            eventual(lambda: self.demand.snapshot.state, lambda s: s == "active")
            return True

        def _open_encoder(self):
            return ("Open", 1, NS(metadata={}))

        def _first_offers(self):
            return (NS(),)

        def _create_media(self, *_):
            started.set()

        def tick(self):
            pass

    capture = GpuCapture(77, US, nominal_rate=30, demand_factory=lambda *a: lease.demand(),
                         session_factory=Session)
    capture.create_sink(NS(picture_feed=True), object(), object(), lambda *a: None)
    with caplog.at_level(logging.INFO):
        capture.start(None, lambda: None)
        assert started.wait(2)
        capture.finish()       # the recorder's stop at server shutdown
    assert capture.error is None and loud(caplog) == []
