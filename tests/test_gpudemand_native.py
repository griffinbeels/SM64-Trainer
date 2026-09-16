"""Independent Python renew/revoke thread against actual native control IPC."""
import importlib.util
from pathlib import Path
import subprocess
import time
import uuid

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.replay import capturecontrol as C
from test_gpudemand import D, R, LIMITS, TABLE, eventual

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def native(tmp_path_factory):
    work = tmp_path_factory.mktemp("demand_native")
    spec = importlib.util.spec_from_file_location("demand_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vcvars = build.find_vcvars32()
    assert vcvars
    native = ROOT / "plugin/gfxwrap"
    flags = [flag for flag in build.COMMON_FLAGS if not flag.startswith("/std:")]
    names = ["gpu_request", "runtime_control", "runtime_delivery", "runtime_supervisor_fake"]
    build._cl(vcvars, flags + ["/std:c++17", "/EHsc", "/c", f"/I{native}",
        *(str(native / f"{name}.cpp") for name in names), f"/Fo{work}\\"], work)
    target = work / "demand_native.exe"
    build._cl(vcvars, build.COMMON_FLAGS + [f"/I{native}",
        str(native / "runtime_supervisor_host.c"), *(str(work / f"{name}.obj") for name in names),
        f"/Fo{work}\\", f"/Fe:{target}", "/link", *build.LIBS], work)
    return target


def command(child, value):
    child.stdin.write(value + "\n")
    child.stdin.flush()
    return child.stdout.readline().strip()


@pytest.fixture
def session(native):
    name = "sm64_demand_test_" + uuid.uuid4().hex
    child = subprocess.Popen([str(native), name], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **quiet_spawn_kwargs())
    control = demand = None
    try:
        assert child.stdout.readline().strip() == "ready"
        control = eventual(lambda: C.CaptureControl(name))
        demand = D.GpuDemand(child.pid, TABLE, LIMITS, control_name=name)
        yield child, control, demand
    finally:
        try:
            if demand:
                demand.close()
        finally:
            if control:
                control.close()
            if child.poll() is None:
                stdout, stderr = child.communicate("quit\n", timeout=5)
                assert child.returncode == 0, stdout + stderr


def test_real_lease_renews_while_delivery_is_blocked_and_stops_before_it_unblocks(session):
    child, control, demand = session
    assert command(child, "block") == "ok"
    demand.start()
    try:
        assert demand.ready.wait(2)
        eventual(lambda: demand.snapshot.state, lambda s: s == "preparing")
        # Cross the real 3-second native lease deadline. Driver stand-in stays blocked.
        time.sleep(3.2)
        assert demand.snapshot.renewals >= 5 and not demand.done.is_set()
        stats = tuple(map(int, command(child, "stats").split()))
        assert stats[0] == 1 and stats[1] != 0 and stats[3] == 1
        assert control.status().state == C.PREPARING
        demand.request_stop("blocked_media_stop")
        assert demand.done.wait(1.5)
        eventual(lambda: command(child, "stats").split()[1], lambda s: s == "0")
        assert command(child, "stats").split()[3] == "1"
    finally:
        assert command(child, "unblock") == "ok"


def test_rom_opens_later_and_published_identity_matches_native_ack(session):
    child, control, demand = session
    assert command(child, "romclose") == "ok"
    eventual(control.status, lambda s: not s.rom_open)
    demand.start()
    eventual(lambda: demand.snapshot.state, lambda s: s == "waiting_rom")
    assert command(child, "stats").split()[0] == "0" and demand.identity is None
    assert command(child, "romopen") == "ok"
    assert demand.ready.wait(2)
    eventual(lambda: demand.snapshot.state, lambda s: s == "active")
    identity = demand.identity
    status = control.status()
    assert identity.producer_pid == child.pid
    assert identity.producer_birth == status.producer_created_lo | (status.producer_created_hi << 32)
    assert (identity.control_generation, identity.token) == (status.generation, status.ack_token)
    assert len(identity.nonce) == 16 and identity.owner_birth > 0


def test_real_producer_exit_ends_demand_without_request_reuse(session):
    child, _, demand = session
    demand.start()
    assert demand.ready.wait(2)
    eventual(lambda: demand.snapshot.state, lambda s: s == "active")
    stdout, stderr = child.communicate("quit\n", timeout=5)
    assert child.returncode == 0, stdout + stderr
    assert demand.done.wait(1.5)
    assert demand.snapshot.state == "fault" and demand.snapshot.reason == "producer_closed"


def test_validate_failure_in_preparation_preserves_previous_control_request(session):
    _, control, _ = session
    first = R.GpuRequest.acquire(control, TABLE, LIMITS)
    try:
        eventual(control.status, lambda s: s.ack_token == first.identity.token)
        def refuse(_):
            raise ValueError("test admission changed")
        with pytest.raises(ValueError, match="admission changed"):
            R.GpuRequest.acquire(control, TABLE, LIMITS, validate=refuse)
        assert first.renew()
        assert control.status().ack_token == first.identity.token
    finally:
        first.close()
