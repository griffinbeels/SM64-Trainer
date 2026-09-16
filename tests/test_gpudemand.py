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
    def __init__(self, *, rom=True, available=True):
        self.status_value = C.CaptureStatus(77, 4, C.PASSIVE, 0, 0, 0, C.CAP_GPU,
                                             rom, 55, 1, "fixture")
        self.available = available
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
            raise FileNotFoundError("not installed yet")
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


def test_construction_and_status_are_passive_then_wait_for_producer_and_rom():
    f = Fixture(rom=False, available=False)
    d = f.demand()
    assert d.snapshot.state == "new" and d.identity is None and not f.calls
    d.start()
    try:
        eventual(lambda: f.calls)
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
