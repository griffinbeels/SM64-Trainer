"""Production x86 control worker with a 64-bit client; no emulator or GPU."""
import importlib.util
import mmap
import os
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.replay import capturecontrol as C

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows native IPC")


@pytest.fixture(scope="module")
def native(tmp_path_factory):
    spec = importlib.util.spec_from_file_location("control_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vcvars = build.find_vcvars32()
    if vcvars is None:
        pytest.skip("MSVC unavailable")
    out = tmp_path_factory.mktemp("control-native")
    build.build_test_host(out, vcvars)
    # The shipped wrapper: against the fake (no SourceV2 export) the GPU
    # runtime refuses to configure and the control worker runs passive.
    dll = build.build_wrapper(out, vcvars)
    host = out / "control_host.exe"
    build._cl(vcvars, build.COMMON_FLAGS + [str(build.SOURCE / "control_host.c"),
              f"/Fe:{host}", f"/Fo{out}\\", "/link", "kernel32.lib"], out)
    return out, host, dll, build, vcvars


def eventually(get, predicate=lambda x: bool(x), timeout=3):
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        try:
            last = get()
            if predicate(last):
                return last
        except (FileNotFoundError, BlockingIOError):
            pass
        except RuntimeError as error:
            # The host maps the control page before it writes the header, so a
            # read in between reports "not ready". Under a loaded machine that
            # window was wide enough to fail a merge check (2026-09-21). Any
            # other RuntimeError is a real failure; an unsupported protocol
            # still fails, at the deadline, with the same message.
            if "not ready" not in str(error):
                raise
            last = error
        time.sleep(0.01)
    raise AssertionError(f"condition not reached: {last!r}")


def test_eventually_waits_through_the_page_that_is_mapped_but_not_written():
    """The race, reproduced without the host: map the page, leave the header
    blank for a moment, then write it. The real client reports "not ready"
    in between; the helper must wait for the header, not fail on it."""
    name = "sm64_control_test_" + uuid.uuid4().hex
    with mmap.mmap(-1, C.PAGE_BYTES, tagname=name + C.SUFFIX) as page:
        with pytest.raises(RuntimeError, match="not ready"):
            C.CaptureControl(name)
        writer = threading.Timer(0.2, C._STATUS.pack_into,
                                 (page, 0, C.MAGIC, C.VERSION, C.PAGE_BYTES, *[0] * 12))
        writer.start()
        try:
            control = eventually(lambda: C.CaptureControl(name))
            control.close()
        finally:
            writer.join()


@contextmanager
def running(native, *, terminated=False, name=None):
    out, host, dll, *_ = native
    name = name or "sm64_control_test_" + uuid.uuid4().hex
    (out / "sm64_trainer_gfx.ini").write_bytes(
        f"wrapped=fake_gfx.dll\nstream={name}\n".encode())
    process = subprocess.Popen([str(host), str(dll)], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, **quiet_spawn_kwargs())
    control = None
    try:
        assert process.stdout.readline().strip() == "ready"
        control = eventually(lambda: C.CaptureControl(name))
        yield process, control, name
    finally:
        if control:
            control.close()
        if process.poll() is None:
            process.communicate("quit\n", timeout=5)
        assert process.returncode == (1 if terminated else 0)


def command(process, text):
    process.stdin.write(text + "\n")
    process.stdin.flush()
    assert process.stdout.readline().strip() == "ok"


def test_discovery_is_small_and_passive_and_unavailable_is_truthful(native, monkeypatch):
    with pytest.raises(FileNotFoundError):
        C.CaptureControl("absent_" + uuid.uuid4().hex)
    with running(native) as (process, control, name):
        status = eventually(control.status, lambda s: s.rom_open)
        assert status.state == C.PASSIVE
        assert status.producer_pid == process.pid
        assert status.build_id.endswith("-gpu-runtime")
        assert control._wake is None and control._mutex is None
        opened, kernel = [], C._kernel

        class Recording:
            """The real kernel32 bindings, noting every mapping discovery opens."""
            def __init__(self):
                self._inner = kernel()

            def __getattr__(self, attribute):
                return getattr(self._inner, attribute)

            def OpenFileMappingW(self, access, inherit, mapping):
                opened.append(mapping)
                return self._inner.OpenFileMappingW(access, inherit, mapping)

        monkeypatch.setattr(C, "_kernel", Recording)
        with C.CaptureControl(name) as observer:
            assert observer.status().producer_pid == process.pid
        monkeypatch.undo()
        assert opened == [name + C.SUFFIX], "discovery opens the control page and nothing else"
        lease = control.acquire()
        status = eventually(control.status, lambda s: s.ack_token == lease.token)
        assert (status.state, status.reason) == (C.UNAVAILABLE, C.NO_BACKEND)
        lease.close()
        eventually(control.status, lambda s: s.state == C.PASSIVE)


def test_stale_cleanup_cannot_disable_new_owner(native):
    with running(native) as (_, control, _):
        stale = control.acquire()
        assert stale.renew()
        assert control._request()[7] == 1
        replacement = control.acquire()
        stale.close()
        assert control._request()[1] == replacement.token
        assert control._request()[7] == 1


def test_owner_lease_expires_and_can_renew_without_new_picture(native):
    with running(native) as (_, control, _):
        lease = control.acquire()
        eventually(control.status, lambda s: s.ack_token == lease.token)
        eventually(control.status, lambda s: s.reason == C.LEASE_EXPIRED,
                   timeout=C.LEASE_MS / 1000 + 1)
        assert lease.renew()
        eventually(control.status, lambda s: s.reason == C.NO_BACKEND)


def test_restart_new_generation_and_closed_producer_reject_stale_renewal(native):
    with running(native) as (process, control, _):
        lease = control.acquire()
        before = control.status()
        command(process, "restart")
        after = eventually(control.status, lambda s: s.generation != before.generation)
        assert not after.rom_open
        assert not lease.renew()
        replacement = control.acquire()
        lease.close()
        assert replacement.renew()
        command(process, "close")
        eventually(control.status, lambda s: s.state == C.CLOSED)
        assert not replacement.renew()


def test_repeated_immediate_restarts_keep_control_discoverable(native):
    with running(native) as (process, control, _):
        previous = control.status().generation
        for _ in range(10):
            command(process, "restart")
            status = eventually(control.status, lambda s, prior=previous: s.generation != prior)
            assert status.state == C.PASSIVE
            assert not status.rom_open
            previous = status.generation


def test_failed_reinitialization_retires_previous_control_session(native):
    with running(native) as (process, control, _):
        lease = control.acquire()
        eventually(control.status, lambda s: s.ack_token == lease.token)
        command(process, "fail-reinit")
        status = eventually(control.status, lambda s: s.state == C.CLOSED)
        assert not status.rom_open
        assert not lease.renew()


def test_client_contention_does_not_block_forwarding_or_report_corruption(native):
    with running(native) as (process, control, _):
        lease = control.acquire()
        eventually(control.status, lambda s: s.ack_token == lease.token)
        with control._write_lock():
            command(process, "frames")
            time.sleep(0.2)
            status = control.status()
            assert status.reason != C.PROTOCOL_ERROR
            assert status.ack_token == lease.token


def test_first_request_survives_abandoned_contender_without_another_signal(native):
    # Seed the first complete command while a different thread holds its mutex.
    # The worker must observe abandonment itself: no heartbeat/event follows it.
    name = "sm64_control_test_" + uuid.uuid4().hex
    kernel = C._kernel()
    mutex = kernel.CreateMutexW(None, False, name + C.SUFFIX + "_client")
    assert mutex
    held, abandon = threading.Event(), threading.Event()
    acquired = []

    def contender():
        acquired.append(kernel.WaitForSingleObject(mutex, 1000))
        held.set()
        abandon.wait(10)
        # Deliberately exit without ReleaseMutex, like a crashed writer thread.

    thread = threading.Thread(target=contender)
    with mmap.mmap(-1, C.PAGE_BYTES, tagname=name + C.SUFFIX) as page:
        created = C._process_creation(kernel, os.getpid())
        token = 12345
        C._REQUEST.pack_into(page, C.REQUEST_OFFSET, 2, token, os.getpid(),
                             *created, 1, 1, 1)
        thread.start()
        try:
            assert held.wait(2) and acquired == [0]
            with running(native, name=name) as (process, control, _):
                # seq>=4 proves the worker attempted the first read while busy.
                eventually(lambda: C._U32.unpack_from(page, 16)[0],
                           lambda seq: seq >= 4 and not seq & 1)
                assert control.status().ack_token == 0
                command(process, "frames")
                time.sleep(0.1)  # Drain the one RomOpen lifecycle event.
                abandon.set()
                thread.join(timeout=2)
                assert not thread.is_alive()
                status = eventually(control.status, lambda s: s.ack_token == token,
                                    timeout=1)
                assert (status.state, status.reason) == (C.UNAVAILABLE, C.NO_BACKEND)
        finally:
            abandon.set()
            thread.join(timeout=2)
            kernel.CloseHandle(mutex)


def test_abrupt_producer_exit_leaves_no_live_lease(native):
    with running(native, terminated=True) as (process, control, _):
        lease = control.acquire()
        # Only our CPU test child, never PJ64 or a live server.
        process.terminate()
        process.wait(timeout=5)
        assert control.status().state == C.CLOSED
        assert not lease.renew()


def test_close_retains_mapping_until_disable_is_proved(native, monkeypatch):
    with running(native) as (_, control, _):
        lease = control.acquire()
        close = lease.close
        def fail():
            raise TimeoutError("injected command lock failure")
        monkeypatch.setattr(lease, "close", fail)
        with pytest.raises(TimeoutError):
            control.close()
        assert control._view is not None and control._map is not None
        monkeypatch.setattr(lease, "close", close)
        control.close()
        assert control._view is None and control._map is None


def test_owner_process_death_revokes_demand_before_lease_expiry(native):
    with running(native) as (_, control, name):
        script = (
            "import sys; from sm64_events.replay.capturecontrol import CaptureControl; "
            "c=CaptureControl(sys.argv[1]); l=c.acquire(); print(l.token,flush=True); "
            "sys.stdin.readline()")
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
        child = subprocess.Popen([sys.executable, "-c", script, name],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, env=env,
                                 **quiet_spawn_kwargs())
        try:
            token = int(child.stdout.readline())
            eventually(control.status, lambda s: s.ack_token == token)
            with pytest.raises(RuntimeError, match="owned by another"):
                control.acquire()
            child.terminate()
            child.wait(timeout=5)
            eventually(control.status, lambda s: s.reason == C.OWNER_GONE, timeout=1)
        finally:
            if child.poll() is None:
                child.communicate("\n", timeout=5)


def test_a_vanilla_cartridge_is_reported_as_baseline_never_as_open(native):
    """The trainer reads `rom_open` to decide whether to ask for capture; a
    real run on vanilla SM64 must never look open, and must say why."""
    with running(native) as (process, control, _):
        eventually(control.status, lambda s: s.rom_open and not s.baseline_rom)
        command(process, "vanilla")
        status = eventually(control.status, lambda s: s.baseline_rom)
        assert not status.rom_open and status.state == C.PASSIVE
        command(process, "usamune")
        eventually(control.status, lambda s: s.rom_open and not s.baseline_rom)
