"""Private transport process boundaries without an SDK, app or emulator."""
import io
import json
import math
import queue
import threading
import time

import pytest

from sm64_events.multiplayer.eos_process import EOSProcess, Runtime, TransportError


class Pipe(io.RawIOBase):
    def __init__(self):
        super().__init__()
        self.lines = queue.Queue()
        self.writes = bytearray()
        self.unread = bytearray()
        self.eof = False
        self.raw_reads = 0

    def readable(self):
        return True

    def writable(self):
        return True

    def readinto(self, buffer):
        self.raw_reads += 1
        if not self.unread and not self.eof:
            self.unread.extend(self.lines.get(timeout=5))
            self.eof = not self.unread
        count = min(len(buffer), len(self.unread))
        buffer[:count] = self.unread[:count]
        del self.unread[:count]
        return count

    def write(self, value):
        # Exercise real partial-write handling instead of an all-or-nothing mock.
        count = min(3, len(value))
        self.writes.extend(value[:count])
        return count

    def flush(self):
        pass


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
        self.handle_closable = True

    def spawn(self, command, cwd):
        self.command = command
        return self.child

    def stop(self):
        self.stopped = True
        self.child.returncode = 0
        self.child.stdout.lines.put(b"")
        self.child.stderr.lines.put(b"")

    def snapshot(self):
        return {"empty": self.stopped, "handle_closed": self.stopped and self.handle_closable}


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


def test_raw_pipe_fake_obeys_read_size_and_exposes_bytewise_readline():
    pipe = Pipe()
    pipe.lines.put(b"abcdef\n")
    assert pipe.readline(4) == b"abcd"
    assert pipe.raw_reads == 4 and pipe.read(2) == b"ef"


def test_helper_reader_amortizes_raw_reads_for_many_live_pose_events(process):
    value, job, events = process
    lines = [json.dumps({"v": 1, "type": "data_received", "sequence": at,
                         "payload_b64": "A" * 768}).encode() + b"\n" for at in range(120)]
    raw = b"".join(lines)
    job.child.stdout.lines.put(raw)
    job.child.stdout.lines.put(b"")
    value.start()
    wait(lambda: len(events) >= len(lines))
    assert sum(event["type"] == "data_received" for event in events) == len(lines)
    assert job.child.stdout.raw_reads <= math.ceil(len(raw) / 65536) + 1


def test_close_retains_all_owners_until_empty_job_handle_is_closed(process):
    value, job, _events = process
    value.start()
    job.child.returncode = 0
    job.handle_closable = False
    with pytest.raises(TransportError, match="transport_cleanup_pending"):
        value.close()
    assert value._job is job and value._child is job.child
    assert value._threads and all(not thread.is_alive() for thread in value._threads)
    assert all(not pipe.closed for pipe in (job.child.stdin, job.child.stdout, job.child.stderr))
    job.handle_closable = True
    value.close(timeout=.2)
    assert all(pipe.closed for pipe in (job.child.stdin, job.child.stdout, job.child.stderr))
    assert value._job is None and value._child is None and value._threads == []


def test_close_retains_buffer_and_pipes_until_blocked_reader_returns(process):
    value, job, _events = process
    entered, release = threading.Event(), threading.Event()

    def consumer(_event):
        entered.set()
        release.wait(3)

    value._event = consumer
    value.start()
    job.child.stdout.lines.put(b'{"v":1,"type":"ready"}\n')
    assert entered.wait(1)
    buffer = value._stdout
    job.child.returncode = 0
    try:
        with pytest.raises(TransportError, match="transport_cleanup_pending"):
            value.close(timeout=.02)
        assert value._stdout is buffer and value._job is job
        assert value._threads and any(thread.is_alive() for thread in value._threads)
        assert all(not pipe.closed for pipe in (job.child.stdin, job.child.stdout, job.child.stderr))
    finally:
        release.set()
        value.close(timeout=.5)
    assert buffer.closed and job.child.stdout.closed


def test_close_keeps_owners_when_pipe_close_refuses_and_retries(process):
    value, job, _events = process
    value.start()
    job.child.returncode = 0
    close = job.child.stderr.close

    def refuse():
        raise OSError("inert close refusal")

    job.child.stderr.close = refuse
    with pytest.raises(TransportError, match="transport_cleanup_pending"):
        value.close(timeout=.2)
    assert value._job is job and value._child is job.child and value._stdout is not None
    job.child.stderr.close = close
    value.close(timeout=.2)
    assert job.child.stderr.closed and value._job is None


def test_buffered_reader_preserves_maximum_accepted_line_size(process):
    value, job, events = process
    prefix, suffix = b'{"v":1,"type":"ready","padding":"', b'"}\n'
    wire = prefix + b"x" * (4096 - len(prefix) - len(suffix)) + suffix
    value.start()
    job.child.stdout.lines.put(wire)
    wait(lambda: len(events) == 1)
    assert len(wire) == 4096 and events[0]["type"] == "ready" and not value._failed


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
