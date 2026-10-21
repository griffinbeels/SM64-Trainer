"""Private transport process boundaries without an SDK, app or emulator."""
import json
import queue
import threading
import time

import pytest

from sm64_events.multiplayer.eos_process import EOSProcess, Runtime, TransportError


class Pipe:
    def __init__(self):
        self.lines = queue.Queue()
        self.writes = bytearray()
        self.closed = False

    def readline(self, _limit):
        return self.lines.get(timeout=5)

    def read(self, _limit):
        return self.lines.get(timeout=5)

    def write(self, value):
        # Exercise real partial-write handling instead of an all-or-nothing mock.
        count = min(3, len(value))
        self.writes.extend(value[:count])
        return count

    def flush(self):
        pass

    def close(self):
        self.closed = True


class Child:
    def __init__(self):
        self.stdin, self.stdout, self.stderr = Pipe(), Pipe(), Pipe()
        self.returncode = None

    def poll(self):
        return self.returncode


class Job:
    def __init__(self):
        self.child = Child()
        self.stopped = False
        self.command = None

    def spawn(self, command, cwd):
        self.command = command
        return self.child

    def stop(self):
        self.stopped = True
        self.child.returncode = 0
        self.child.stdout.lines.put(b"")
        self.child.stderr.lines.put(b"")

    def snapshot(self):
        return {"empty": self.stopped}


def wait(predicate):
    end = time.monotonic() + 3
    while not predicate():
        assert time.monotonic() < end
        time.sleep(.005)


@pytest.fixture
def process(tmp_path):
    for name in ("helper.exe", "EOSSDK-Win64-Shipping.dll", "player.cfg"):
        (tmp_path / name).write_bytes(b"inert-fixture")
    job, events = Job(), []
    value = EOSProcess(Runtime(tmp_path / "helper.exe", tmp_path / "player.cfg", tmp_path / "cache"),
        events.append, job_factory=lambda: job)
    yield value, job, events
    job.child.returncode = 0
    value.close()


def test_cached_construction_does_not_start_a_child(process):
    value, job, _events = process
    assert job.command is None and value.runtime.available()


def test_failed_spawn_still_retires_the_created_job_accounting_handle(process):
    value, job, _events = process
    def fail_spawn(*args, **kwargs):
        raise OSError("inert setup failure")
    job.spawn = fail_spawn
    with pytest.raises(OSError):
        value.start()
    value.close()
    assert job.stopped and job.snapshot()["empty"]


def test_immediate_heartbeat_partial_write_and_private_paths_only(process):
    value, job, _events = process
    value.start()
    wait(lambda: b"\n" in job.child.stdin.writes)
    assert json.loads(bytes(job.child.stdin.writes).splitlines()[0]) == {"v": 1, "op": "heartbeat"}
    assert "--config" in job.command and "--parent-pid" in job.command
    assert "inert-fixture" not in " ".join(job.command)


def test_reply_resolves_only_corresponding_request_and_does_not_publish_raw_reply(process):
    value, job, events = process
    value.start()
    future = value.submit("create_room")
    job.child.stdout.lines.put(b'{"v":1,"type":"reply","request_id":1,"ok":true}\n')
    assert future.result(timeout=2) is None and events == []


@pytest.mark.parametrize("wire", [b"x\n", b'{"v":2,"type":"ready"}\n', b"x" * 4097])
def test_malformed_helper_output_fails_pending_work_and_retires_owned_job(process, wire):
    value, job, events = process
    value.start()
    future = value.submit("identity")
    job.child.stdout.lines.put(wire)
    with pytest.raises(TransportError, match="transport_protocol_error"):
        future.result(timeout=2)
    wait(lambda: job.stopped)
    assert events == [{"v": 1, "type": "fatal", "code": "transport_protocol_error"}]


def test_latest_values_are_bounded_and_shutdown_completes_waiters(process):
    value, job, _events = process
    value.start()
    future = value.submit("identity")
    with value._lock:
        for peer in range(1, 4):
            assert value.latest(payload=b"newest", peer_ref=peer)
        assert not value.latest(payload=b"extra", peer_ref=4)
        assert value.latest(payload=b"replacement", peer_ref=1)
        assert len(value._latest) == 3
    job.child.returncode = 0
    value.close()
    with pytest.raises(TransportError, match="transport_stopped"):
        future.result()
    assert all(pipe.closed for pipe in (job.child.stdin, job.child.stdout, job.child.stderr))
    assert not any(t.name.startswith("eos-") for t in threading.enumerate())


def test_timed_out_command_retires_its_generation_even_with_healthy_stats(process):
    value, job, events = process
    now = [0.0]
    value._clock = lambda: now[0]
    value.start()
    future = value.submit("create_room")
    now[0] = 13
    value._last_output = 13  # Unrelated SDK stats cannot extend a command's outcome lease.
    with pytest.raises(TransportError, match="transport_timeout"):
        future.result(timeout=2)
    wait(lambda: job.stopped)
    assert value._failed and events[-1]["code"] == "transport_timeout"
    with pytest.raises(TransportError, match="transport_unavailable"):
        value.submit("create_room")
