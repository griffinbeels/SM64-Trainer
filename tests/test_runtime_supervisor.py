"""Real independent watchdog against a threaded blocked delivery facade, no GPU."""
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import pytest
from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.replay import capturecontrol as C
from sm64_events.replay import gpurequest as R

ROOT = Path(__file__).resolve().parents[1]
LIMITS = R.RequestLimits(8, 128 << 20, 8 << 20, 16 << 20, 8, 1 << 20,
                        4 << 20, 1 << 20, 256, 2000, 3000)
TABLE = [("timer", 0x1234, 4), ("controller", 0x2340, 16)]
ACTIVE = 4


def eventual(call, predicate=lambda value: bool(value), timeout=4):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        try:
            value = call()
            if predicate(value):
                return value
        except (FileNotFoundError, BlockingIOError):
            pass
        time.sleep(0.01)
    raise AssertionError("supervisor response timed out")


def command(child, text):
    child.stdin.write(text + "\n")
    child.stdin.flush()
    return child.stdout.readline().strip()


def stats(child):
    return tuple(map(int, command(child, "stats").split()))


@pytest.fixture
def session(runtime_supervisor_exe):
    name = "sm64_supervisor_test_" + uuid.uuid4().hex
    child = subprocess.Popen([str(runtime_supervisor_exe), name], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, **quiet_spawn_kwargs())
    control = None
    try:
        assert child.stdout.readline().strip() == "ready"
        control = eventual(lambda: C.CaptureControl(name))
        yield child, control
    finally:
        if control:
            control.close()
        stdout, stderr = child.communicate("quit\n", timeout=5)
        assert child.returncode == 0, stdout + stderr


def test_passive_discovery_then_hot_enable_and_new_token_after_disable(session):
    child, control = session
    assert control.status().capabilities & 2
    assert stats(child)[0:2] == (0, 0)
    first = R.GpuRequest.acquire(control, TABLE, LIMITS)
    eventual(control.status, lambda value: value.state == ACTIVE)
    assert stats(child)[0] == 1 and first.renew()
    first.close()
    eventual(control.status, lambda value: value.state == C.PASSIVE)
    assert stats(child)[1] == 0
    with R.GpuRequest.acquire(control, TABLE, LIMITS):
        eventual(control.status, lambda value: value.state == ACTIVE)
        assert stats(child)[0] == 2


def test_lease_expiry_revokes_even_when_gpu_worker_is_blocked(session):
    child, control = session
    assert command(child, "block") == "ok"
    with R.GpuRequest.acquire(control, TABLE, LIMITS) as request:
        eventual(lambda: stats(child), lambda value: value[3] == 1)
        eventual(control.status, lambda value: value.reason == C.LEASE_EXPIRED,
                   timeout=C.LEASE_MS / 1000 + 1)
        count, gate, _, inside = stats(child)
        assert count == 1 and gate == 0 and inside == 1
        assert request.renew()
        eventual(control.status, lambda value: value.state == C.UNAVAILABLE)
        assert stats(child)[0:2] == (1, 0)
        assert command(child, "unblock") == "ok"


def test_rom_close_disarms_immediately_without_join_and_old_token_does_not_restart(session):
    child, control = session
    assert command(child, "block") == "ok"
    with R.GpuRequest.acquire(control, TABLE, LIMITS):
        eventual(lambda: stats(child), lambda value: value[3] == 1)
        assert command(child, "romclose") == "ok"
        assert stats(child)[1] == 0
        assert command(child, "romopen") == "ok"
        eventual(control.status, lambda value: value.state == C.UNAVAILABLE)
        assert stats(child)[0:2] == (1, 0)
        assert command(child, "unblock") == "ok"


def test_backend_fault_event_revokes_without_waiting_for_next_heartbeat(session):
    child, control = session
    with R.GpuRequest.acquire(control, TABLE, LIMITS):
        eventual(control.status, lambda value: value.state == ACTIVE)
        assert command(child, "fault") == "ok"
        eventual(control.status, lambda value: value.state == C.UNAVAILABLE, timeout=1)
        assert stats(child)[1] == 0


def test_missing_configuration_is_explicit_and_never_requests_worker(session):
    child, control = session
    lease = control.acquire()
    eventual(control.status, lambda value: value.ack_token == lease.token)
    assert control.status().state == C.UNAVAILABLE
    assert control.status().reason == 5 and stats(child)[0] == 0


def test_busy_status_read_does_not_fault_active_capture(session):
    child, control = session
    with R.GpuRequest.acquire(control, TABLE, LIMITS):
        eventual(control.status, lambda value: value.state == ACTIVE)
        before = int(command(child, "reads"))
        assert command(child, "busy-status") == "ok"
        eventual(lambda: int(command(child, "reads")), lambda value: value > before)
        assert control.status().state == ACTIVE and stats(child)[1] != 0


def test_lifecycle_cancellation_during_request_cannot_leave_late_active_gate(session):
    child, control = session
    assert command(child, "pause-request") == "ok"
    with R.GpuRequest.acquire(control, TABLE, LIMITS):
        eventual(lambda: command(child, "inside-request"), lambda value: value == "1")
        assert command(child, "romclose") == "ok"
        assert stats(child)[1] == 0
        assert command(child, "romopen") == "ok"
        assert command(child, "resume-request") == "ok"
        eventual(control.status, lambda value: value.state == C.UNAVAILABLE)
        assert stats(child)[1] == 0


def test_owner_death_revokes_blocked_worker_before_lease_timeout(session):
    child, control = session
    assert command(child, "block") == "ok"
    script = (
        "import sys; from sm64_events.replay.capturecontrol import CaptureControl; "
        "from sm64_events.replay.gpurequest import GpuRequest,RequestLimits; "
        "c=CaptureControl(sys.argv[1]); q=GpuRequest.acquire(c,[('timer',4660,4)],"
        "RequestLimits(8,134217728,8388608,16777216,8,1048576,4194304,1048576,256,2000,3000)); "
        "print(q.lease.token,flush=True);sys.stdin.readline()")
    owner = subprocess.Popen([sys.executable, "-c", script, control.name.removesuffix(C.SUFFIX)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, **quiet_spawn_kwargs())
    try:
        token = int(owner.stdout.readline())
        eventual(control.status, lambda value: value.ack_token == token)
        eventual(lambda: stats(child), lambda value: value[3] == 1)
        owner.terminate()
        owner.wait(timeout=5)
        eventual(control.status, lambda value: value.reason == C.OWNER_GONE, timeout=1)
        assert stats(child)[1] == 0
    finally:
        if owner.poll() is None:
            owner.communicate("\n", timeout=5)
        assert command(child, "unblock") == "ok"
