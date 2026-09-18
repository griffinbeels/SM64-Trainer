"""Cross-bitness immutable configuration with the real control enable transaction."""

import ctypes
import importlib.util
import os
from pathlib import Path
import struct
import subprocess
import sys
import time
import uuid

import pytest
from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "plugin/gfxwrap"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


C = load("request_control", ROOT / "src/sm64_events/replay/capturecontrol.py")
R = load("request_owner", ROOT / "src/sm64_events/replay/gpurequest.py")
LIMITS = R.RequestLimits(
    8, 128 << 20, 8 << 20, 16 << 20, 8, 1 << 20, 4 << 20, 1 << 20, 256, 2000, 3000
)
TABLE = [("timer", 0x1234, 4), ("controller", 0x2340, 16)]


@pytest.fixture(scope="module")
def exe(tmp_path_factory):
    work = tmp_path_factory.mktemp("gpu_request")
    build = load("request_build", ROOT / "tools/build_plugin.py")
    vcvars = build.find_vcvars32()
    assert vcvars
    cpp = [flag for flag in build.COMMON_FLAGS if not flag.startswith("/std:")]
    build._cl(
        vcvars,
        cpp
        + [
            "/std:c++17",
            "/EHsc",
            "/c",
            f"/I{NATIVE}",
            str(NATIVE / "gpu_request.cpp"),
            str(NATIVE / "gpu_request_host.cpp"),
            f"/Fo{work}\\",
        ],
        work,
    )
    target = work / "runtime_control_host.exe"
    build._cl(
        vcvars,
        build.COMMON_FLAGS
        + [
            f"/I{NATIVE}",
            str(NATIVE / "runtime_control_host.c"),
            str(work / "gpu_request.obj"),
            str(work / "gpu_request_host.obj"),
            f"/Fo{work}\\",
            f"/Fe:{target}",
            "/link",
            *build.LIBS,
        ],
        work,
    )
    return target


def eventual(call, predicate=lambda value: bool(value)):
    until = time.monotonic() + 4
    while time.monotonic() < until:
        try:
            value = call()
            if predicate(value):
                return value
        except (FileNotFoundError, BlockingIOError):
            pass
        time.sleep(0.01)
    raise AssertionError("native control response timed out")


@pytest.fixture
def session(exe):
    name = "sm64_gpu_request_test_" + uuid.uuid4().hex
    child = subprocess.Popen(
        [str(exe), name],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **quiet_spawn_kwargs(),
    )
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


def check(child, control, token, *, generation=None):
    status = control.status()
    birth = C._process_creation(control._k, os.getpid())
    child.stdin.write(
        f"check {os.getpid()} {birth[0] | (birth[1] << 32)} {token} "
        f"{status.generation if generation is None else generation}\n"
    )
    child.stdin.flush()
    return child.stdout.readline().strip() == "1"


def test_request_exists_before_enable_and_dies_with_its_generation_and_owner(
    session, monkeypatch
):
    child, control = session
    publish = control._publish
    observed = []

    def wrapped(request):
        if request[7] and request[1] != control._request()[1]:
            observed.append(check(child, control, request[1]))
        publish(request)

    monkeypatch.setattr(control, "_publish", wrapped)
    with R.GpuRequest.acquire(control, TABLE, LIMITS) as request:
        assert observed == [True]
        assert request.renew() and check(child, control, request.lease.token)
        assert not check(
            child,
            control,
            request.lease.token,
            generation=control.status().generation + 1,
        )
        token = request.lease.token
    assert not check(child, control, token)


def test_prepare_failure_preserves_previous_lease(session):
    _, control = session
    lease = control.acquire()
    before = control._request()

    def fail(*_):
        raise ValueError("injected request setup failure")

    with pytest.raises(ValueError, match="injected"):
        control.acquire(prepare=fail)
    assert control._request() == before and lease.renew()


@pytest.mark.parametrize(
    "offset,value",
    [
        (16, 1),
        (12, 511),
        (20, 0),
        (48, 0),
        (72, 600 << 20),
        (80, 9),
        (84, (8 << 20) + 1),
        (88, 17),
        (128 + 4, 132),
        (256, 1),
    ],
)
def test_native_refuses_mutated_identity_budget_or_table(session, offset, value):
    child, control = session
    with R.GpuRequest.acquire(control, TABLE, LIMITS) as request:
        assert check(child, control, request.lease.token)
        original = ctypes.string_at(request._view + offset, 4)
        ctypes.memmove(request._view + offset, struct.pack("<I", value), 4)
        assert not check(child, control, request.lease.token)
        ctypes.memmove(request._view + offset, original, 4)
        assert check(child, control, request.lease.token)


def test_config_collision_does_not_overwrite_or_disable_previous_owner(
    session, monkeypatch
):
    child, control = session
    with R.GpuRequest.acquire(control, TABLE, LIMITS) as first:
        before = control._request()
        monkeypatch.setattr(C.secrets, "randbelow", lambda _: first.lease.token - 1)
        with pytest.raises(RuntimeError, match="already owned"):
            R.GpuRequest.acquire(control, TABLE, LIMITS)
        assert control._request() == before and first.renew()
        assert check(child, control, first.lease.token)


def test_invalid_table_never_changes_control_and_failed_disable_retains_page_until_retry(
    session, monkeypatch
):
    child, control = session
    before = control._request()
    with pytest.raises(ValueError, match="RDRAM"):
        R.GpuRequest.acquire(control, [("bad", 0, 129)], LIMITS)
    assert control._request() == before
    request = R.GpuRequest.acquire(control, TABLE, LIMITS)
    token = request.lease.token

    def fail():
        raise TimeoutError("injected disable failure")

    with monkeypatch.context() as patch:
        patch.setattr(request.lease, "close", fail)
        with pytest.raises(TimeoutError, match="injected"):
            request.close()
    # Retained, not leaked: the page and lease stay owned for the retry the
    # demand supervisor makes (test_gpu_owner_recovery.py), and that retry
    # releases everything.
    assert request._view is not None and request.lease is not None
    request.close()
    assert request._view is None and request._map is None and request.lease is None
    assert not check(child, control, token)
