"""Lease ownership and stop behavior, independent of any media worker."""
from dataclasses import FrozenInstanceError, replace
import threading
import time

import pytest

from sm64_events.replay import capturecontrol as C
from sm64_events.replay import gpurequest as R
from sm64_events.replay import gpudemand as D
LIMITS = R.RequestLimits(8, 128 << 20, 8 << 20, 16 << 20, 8, 1 << 20,
                        4 << 20, 1 << 20, 256, 2000, 3000)
TABLE = (("counter", 0, 4),)


def eventual(call, predicate=bool, timeout=2):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            result = call()
            if predicate(result):
                return result
        except (FileNotFoundError, BlockingIOError):
            pass
        time.sleep(0.002)
    raise AssertionError("lease fixture did not reach the expected state")


class Fixture:
    def __init__(self, *, rom=True, available=True, unavailable_error=FileNotFoundError):
        self.status_value = C.CaptureStatus(77, 4, C.PASSIVE, 0, 0, 0, C.CAP_GPU,
                                             rom, 55, 1, "fixture")
        self.available = available
        self.unavailable_error = unavailable_error
        self.calls = []
        self.requests = self.renews = self.closed = 0
        self.renew_ok = True
        self.request = None
        self.before_acquire = None
        self.close_entered = threading.Event()
        self.close_release = None

    def log(self, name):
        self.calls.append((name, threading.get_ident()))

    def change(self, **values):
        self.status_value = replace(self.status_value, **values)

    def control(self, _):
        self.log("discover")
        if not self.available:
            raise self.unavailable_error("not discoverable yet")
        return self

    def status(self):
        self.log("status")
        return self.status_value

    def close(self):
        self.log("control_close")
        self.closed += 1

    def acquire(self, control, table, limits, *, validate):
        self.log("acquire")
        assert control is self and table == TABLE and limits == LIMITS
        if self.before_acquire:
            self.before_acquire()
        validate(self.status())
        self.requests += 1
        self.request = Request(self)
        return self.request

    def demand(self, **kwargs):
        kwargs.setdefault("publication_grace", 0.0)  # Pin this fixture's exact deadline; phase tests cover slack.
        return D.GpuDemand(77, TABLE, LIMITS, control_factory=self.control,
                           request_factory=self.acquire, poll_seconds=0.01,
                           renew_seconds=0.03, **kwargs)


class Request:
    def __init__(self, fixture):
        self.fixture = fixture
        s = fixture.status_value
        self.identity = R.RequestIdentity(s.producer_pid,
            s.producer_created_lo | (s.producer_created_hi << 32), s.generation,
            88, 99, 123, bytes(range(16)))

    def renew(self):
        self.fixture.log("renew")
        self.fixture.renews += 1
        return self.fixture.renew_ok

    def close(self):
        f = self.fixture
        f.log("request_close")
        f.close_entered.set()
        if f.close_release is not None:
            assert f.close_release.wait(2)


def test_startup_receipt_is_request_bound_and_survives_native_failure_cleanup():
    f = Fixture()
    d = f.demand().start()
    try:
        assert d.ready.wait(1)
        f.change(ack_token=123, state=C.ACTIVE, gpu_startup={"nonce": "wrong", "control_generation": 4})
        eventual(lambda: d.snapshot.state, lambda state: state == "active")
        assert d.snapshot.native_startup is None
        receipt = {"nonce": d.identity.nonce.hex(), "control_generation": 4,
                   "gl_renderer": "AMD fixture", "missing_entry_point": "glFixture"}
        f.change(gpu_startup=receipt, state=C.UNAVAILABLE, reason=10032)
        assert d.done.wait(1)
    finally:
        d.close()
    assert d.snapshot.native_startup == receipt
    assert d.snapshot.capture_build == "fixture" and d.snapshot.backend_reason == 10032


@pytest.mark.parametrize("unavailable_error", [FileNotFoundError, BlockingIOError])
def test_construction_and_status_are_passive_then_wait_for_producer_and_rom(unavailable_error):
    f = Fixture(rom=False, available=False, unavailable_error=unavailable_error)
    d = f.demand()
    assert d.snapshot.state == "new" and d.identity is None and not f.calls
    d.start()
    try:
        eventual(lambda: sum(name == "discover" for name, _ in f.calls) >= 2)
        assert f.requests == 0
        f.available = True
        eventual(lambda: d.snapshot.state, lambda s: s == "waiting_rom")
        assert f.requests == 0
        f.change(rom_open=True)
        assert d.ready.wait(1)
        assert f.requests == 1 and d.identity.token == 123
        with pytest.raises(FrozenInstanceError):
            d.identity.token = 124
    finally:
        d.close()
    assert len({tid for _, tid in f.calls}) == 1
    assert f.calls[-2][0] == "request_close" and f.calls[-1][0] == "control_close"


def test_blocked_media_does_not_stop_renewal_or_delay_explicit_stop():
    f = Fixture()
    d = f.demand().start()
    media_release = threading.Event()
    media = threading.Thread(target=lambda: media_release.wait(2))
    media.start()
    try:
        assert d.ready.wait(1)
        f.change(ack_token=123, state=C.ACTIVE)
        eventual(lambda: d.snapshot.state, lambda s: s == "active")
        eventual(lambda: f.renews, lambda n: n >= 3)
        assert media.is_alive()
        d.request_stop("media_failed")
        assert f.close_entered.wait(0.5) and media.is_alive()
        assert d.done.wait(0.5)
        assert d.snapshot.reason == "media_failed"
        with pytest.raises(RuntimeError, match="restarted"):
            d.start()
    finally:
        media_release.set()
        media.join(2)
        d.close()


@pytest.mark.parametrize("changes, reason", [
    ({"producer_pid": 78}, "producer_pid_changed"),
    ({"capabilities": 0}, "gpu_capture_unavailable"),
    ({"rom_open": False}, "rom_closed"),
    ({"generation": 5}, "producer_identity_changed"),
    ({"producer_created_lo": 56}, "producer_identity_changed"),
    ({"state": C.CLOSED, "reason": C.OWNER_GONE}, "producer_closed"),
    ({"state": C.UNAVAILABLE, "reason": 6}, "native_capture_ended:3:6"),
    ({"state": C.PASSIVE}, "native_capture_ended:1:0"),
    ({"ack_token": 999}, "native_request_replaced"),
])
def test_native_loss_revokes_once_without_automatic_reactivation(changes, reason):
    f = Fixture()
    d = f.demand().start()
    assert d.ready.wait(1)
    f.change(ack_token=123, state=C.ACTIVE)
    eventual(lambda: d.snapshot.state, lambda s: s == "active")
    f.change(**changes)
    assert d.done.wait(1)
    assert d.snapshot.state == "fault" and d.snapshot.reason == reason
    assert f.close_entered.is_set() and f.requests == 1 and d.stop_event.is_set()
    d.close()


def test_preparing_is_valid_but_has_independent_setup_deadline():
    f = Fixture()
    d = f.demand(setup_timeout=0.15).start()
    assert d.ready.wait(1)
    f.change(ack_token=123, state=C.PREPARING)
    eventual(lambda: d.snapshot.state, lambda s: s == "preparing")
    assert d.done.wait(1)
    assert d.snapshot.reason == "capture_setup_deadline" and f.renews >= 1
    d.close()


@pytest.mark.parametrize(("case", "changes", "expected"), [
    ("active", {"state": C.ACTIVE}, None),
    ("typed_refusal", {"state": C.UNAVAILABLE, "reason": 10031}, "native_capture_ended:3:10031"),
    ("changed_process", {"producer_pid": 78}, "producer_pid_changed"),
    ("changed_generation", {"generation": 5}, "producer_identity_changed"),
    ("closed_rom", {"rom_open": False}, "rom_closed"),
    ("lost_capability", {"capabilities": 0}, "gpu_capture_unavailable"),
    ("preparing", {"state": C.PREPARING}, "capture_setup_deadline"),
    ("stale_ack", {"state": C.ACTIVE, "ack_token": 999}, "capture_setup_deadline"),
    ("contended", {}, "capture_setup_deadline"),
])
def test_final_startup_observation_wins_over_an_elapsed_deadline(monkeypatch, case, changes, expected):
    """Read the actual last control observation; don't lengthen the time budget."""
    f = Fixture()
    request = Request(f)
    f.change(ack_token=request.identity.token, **{k: v for k, v in changes.items() if k != "ack_token"})
    if "ack_token" in changes:
        f.change(ack_token=changes["ack_token"])
    clocks = iter((0.0, 5.0))
    d = f.demand(clock=lambda: next(clocks, 5.0))
    status = f.status
    def observe():
        if case == "contended":
            f.log("status")
            raise BlockingIOError("control publication in progress")
        result = status()
        if case == "active":
            d.stop_event.set()  # End this synchronous fixture after the success observation.
        return result
    monkeypatch.setattr(f, "status", observe)
    if expected is None:
        d._monitor(f, request)
        assert d.snapshot.state == "active"
    else:
        with pytest.raises(D.DemandFailure, match=expected):
            d._monitor(f, request)
        assert not f.renews
    assert [name for name, _ in f.calls].count("status") == 1


def test_admission_rechecks_status_inside_request_prepare():
    f = Fixture()
    f.before_acquire = lambda: f.change(rom_open=False)
    d = f.demand().start()
    assert d.done.wait(1)
    assert f.requests == 0 and d.identity is None and d.snapshot.reason == "rom_closed"
    assert f.closed == 1
    d.close()


def test_stop_before_start_and_while_discovering_create_no_request():
    f = Fixture(available=False)
    d = f.demand()
    d.request_stop("cancelled")
    d.start()
    assert d.done.is_set() and not f.calls
    d.close()
    waiting = f.demand().start()
    eventual(lambda: f.calls)
    waiting.stop_event.set()
    assert waiting.done.wait(0.5) and f.requests == 0
    waiting.close()


def test_close_timeout_retains_supervisor_ownership_until_cleanup_finishes():
    f = Fixture()
    f.close_release = threading.Event()
    d = f.demand().start()
    assert d.ready.wait(1)
    d.request_stop()
    assert f.close_entered.wait(1)
    with pytest.raises(TimeoutError, match="still owns cleanup"):
        d.close(timeout=0.01)
    assert not d.done.is_set()
    f.close_release.set()
    d.close()
    assert d.done.is_set()


def test_failed_renewal_ends_request_without_reacquiring():
    f = Fixture()
    f.renew_ok = False
    d = f.demand().start()
    assert d.done.wait(1)
    assert d.snapshot.reason == "capture_lease_lost" and f.requests == 1
    d.close()


def test_a_real_run_on_another_rom_never_requests_capture():
    """Vanilla SM64 on the same plugin: the wrapper reports `baseline_rom` and
    never `rom_open`, so the demand waits idle and says why (2026-09-16)."""
    f = Fixture(rom=False)
    f.change(baseline_rom=True)
    d = f.demand()
    d.start()
    try:
        eventual(lambda: d.snapshot.state, lambda s: s == "baseline_rom")
        time.sleep(0.05)
        assert f.requests == 0 and d.identity is None
    finally:
        d.close()
