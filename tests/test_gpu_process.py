from pathlib import Path
import io, sys, time
import pytest

ROOT = Path(__file__).resolve().parent / "gpu_process_helpers"
from sm64_events.replay.gpuprocess.process_controller import Controller, Limits, Refused
from sm64_events.replay.gpuprocess.process_protocol import (
    frame,
    read_frame,
    HEADER,
    MAGIC,
    REQUEST,
)

MEASUREMENTS = []
PROCESSES = []


def limits(**change):
    values = dict(
        max_count=3,
        max_bytes=1024 * 1024,
        max_json=32768,
        max_packet=1024,
        max_age=3,
        call_timeout=0.7,
        startup_timeout=3,
        watchdog_interval=0.02,
        stderr_bytes=512,
        shutdown_timeout=1,
    )
    values.update(change)
    return Limits(**values)


def controller(helper="fixture_helper.py", **change):
    return Controller(
        executable=Path(sys.executable), helper=ROOT / helper, limits=limits(**change)
    )


def wait(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(0.005)


def reply(c):
    wait(lambda: c.status()["completed_undrained"] > 0)
    return c.take_result()


def finish(c):
    if not c.status()["done"]:
        c.stop("fixture cleanup")
    wait(lambda: c.status()["done"])
    state = c.status()
    assert state["pid"] is None or state["process_exit"] is not None
    assert (
        state["disposal"]["empty"]
        and state["disposal"]["handle_closed"]
        and state["disposal"]["active_processes"] == 0
    )
    PROCESSES.append(
        {n: state[n] for n in ["pid", "process_exit", "done", "fault", "disposal"]}
    )


def test_framing_bounds_strict_json_and_truncation():
    good = frame(REQUEST, {"a": 1}, b"", max_json=100, max_packet=0)
    assert read_frame(io.BytesIO(good), REQUEST, max_json=100, max_packet=0) == (
        {"a": 1},
        b"",
    )
    for data in [
        good[:-1],
        HEADER.pack(MAGIC, 1, REQUEST, 101, 0),
        HEADER.pack(MAGIC, 1, REQUEST, 2, 1),
        HEADER.pack(b"BAD!", 1, REQUEST, 2, 0),
    ]:
        with pytest.raises((ValueError, EOFError)):
            read_frame(io.BytesIO(data), REQUEST, max_json=100, max_packet=0)
    raw = b'{"a":1,"a":2}'
    with pytest.raises(ValueError):
        read_frame(
            io.BytesIO(HEADER.pack(MAGIC, 1, REQUEST, len(raw), 0) + raw),
            REQUEST,
            max_json=100,
            max_packet=0,
        )


def test_completed_undrained_credits_and_immutable_command():
    c = controller(max_count=2)
    try:
        command = {"op": "Open", "adapter_luid": [0, 84637], "fixture": "echo"}
        one = c.enqueue(command)
        command["fixture"] = "hang"
        wait(lambda: c.status()["completed_undrained"] == 1)
        before = c.status()["reserved_bytes"]
        assert before > 0
        two = c.enqueue({"op": "Poll"})
        wait(lambda: c.status()["completed_undrained"] == 2)
        assert (
            isinstance(c.enqueue({"op": "Poll"}), Refused)
            and c.status()["pending_count"] == 2
            and c.status()["reserved_bytes"] > before
        )
        assert (
            c.take_result().request_id == one
            and c.status()["reserved_bytes"] < before + 70000
        )
        assert c.take_result().request_id == two and c.status()["reserved_bytes"] == 0
        close = c.enqueue({"op": "Close"})
        assert isinstance(c.enqueue({"op": "Poll"}), Refused)
        assert reply(c).request_id == close
        wait(lambda: c.status()["done"])
        assert c.status()["process_exit"] == 0
    finally:
        finish(c)


def test_open_uses_setup_budget_but_later_native_calls_keep_short_deadline():
    c = controller(call_timeout=0.1)
    try:
        c.enqueue({"op": "Open", "adapter_luid": [0, 84637], "fixture": "slow-open"})
        assert reply(c).metadata["result"] == 0
        assert c.status()["fault"] is None
        c.enqueue({"op": "Poll", "fixture": "hang"})
        wait(lambda: c.status()["done"])
        assert "request deadline" in c.status()["fault"]
    finally:
        finish(c)


def test_packet_and_exact_once_custody_receipt():
    c = controller()
    try:
        c.enqueue({"op": "Open", "adapter_luid": [0, 84637], "fixture": "echo"})
        reply(c)
        c.enqueue(
            {
                "op": "Submit",
                "slot": 0,
                "bridge_token": 77,
                "serial": 10,
                "pts": 1,
                "encoder_duration": 3,
                "force_idr": False,
            }
        )
        packet = reply(c)
        assert (
            packet.payload == b"compressed-fixture"
            and packet.metadata["key_returns"] == []
        )
        c.enqueue({"op": "Poll"})
        assert reply(c).metadata["key_returns"] == [
            {"slot": 0, "bridge_token": 77, "serial": 10}
        ]
        c.enqueue({"op": "Poll"})
        assert reply(c).metadata["key_returns"] == []
    finally:
        finish(c)


@pytest.mark.parametrize("mode", ["wrong-nonce", "duplicate"])
def test_bad_response_identity_retires_epoch(mode):
    c = controller()
    try:
        c.enqueue({"op": "Open", "adapter_luid": [0, 84637], "fixture": mode})
        reply(c)
        if mode == "duplicate":
            c.enqueue({"op": "Poll"})
            reply(c)
        wait(lambda: c.status()["fault"] is not None)
        assert isinstance(c.enqueue({"op": "Poll"}), Refused)
    finally:
        finish(c)


@pytest.mark.parametrize("write_stall", [False, True])
def test_watchdog_kills_owned_blocked_pipe_and_keeps_public_calls_short(write_stall):
    c = controller(
        "fixture_no_read.py" if write_stall else "fixture_helper.py",
        max_json=262144,
        max_bytes=2 * 1024 * 1024,
        call_timeout=0.4,
    )
    started = time.monotonic()
    try:
        request = {"op": "Open", "adapter_luid": [0, 84637], "fixture": "hang"}
        if write_stall:
            request["padding"] = "P" * 240000
        before = time.monotonic()
        request_id = c.enqueue(request)
        enqueue_ms = (time.monotonic() - before) * 1000
        assert isinstance(request_id, int) and enqueue_ms < 150
        result = reply(c)
        assert (
            result.metadata["worker_disposal_required"]
            and result.payload == b""
            and result.metadata["key_returns"] == []
        )
        wait(lambda: c.status()["done"])
        state = c.status()
        assert state["process_exit"] is not None and "deadline" in state["fault"]
        if not write_stall:
            assert state["stderr_total"] == 50000 and len(state["stderr_tail"]) == 512
        elapsed = time.monotonic() - started
        assert elapsed < 4
        MEASUREMENTS.append(
            {
                "write_stall": write_stall,
                "elapsed_s": elapsed,
                "enqueue_ms": enqueue_ms,
                "pid": state["pid"],
                "process_exit": state["process_exit"],
                "stderr_total": state["stderr_total"],
                "stderr_retained": len(state["stderr_tail"]),
                "reason": state["fault"],
                "job": state["disposal"],
            }
        )
    finally:
        finish(c)


def test_completed_reply_age_expires_even_after_clean_child_exit():
    c = controller(max_age=0.5)
    try:
        c.enqueue({"op": "Open", "adapter_luid": [0, 84637], "fixture": "echo"})
        reply(c)
        c.enqueue({"op": "Close"})
        wait(lambda: c.status()["done"])
        assert (
            c.status()["completed_undrained"] == 1 and c.status()["reserved_bytes"] > 0
        )
        wait(lambda: c.status()["fault"] is not None)
        assert "age deadline" in c.status()["fault"]
        expired = c.take_result()
        assert expired.metadata["worker_disposal_required"] and expired.payload == b""
    finally:
        finish(c)


def test_byte_reservation_refuses_too_large_reply_before_enqueue():
    c = controller(max_bytes=1000)
    try:
        assert (
            isinstance(
                c.enqueue(
                    {"op": "Open", "adapter_luid": [0, 84637], "fixture": "echo"}
                ),
                Refused,
            )
            and c.status()["pending_count"] == 0
        )
    finally:
        finish(c)


@pytest.mark.parametrize(
    "mode", ["close-missing-return", "receipt-bool-mask", "receipt-short-status"]
)
def test_incomplete_custody_proof_retires_worker(mode):
    c = controller()
    try:
        c.enqueue({"op": "Open", "adapter_luid": [0, 84637], "fixture": mode})
        reply(c)
        c.enqueue(
            {
                "op": "Submit",
                "slot": 0,
                "bridge_token": 77,
                "serial": 10,
                "pts": 1,
                "encoder_duration": 3,
                "force_idr": False,
            }
        )
        reply(c)
        c.enqueue({"op": "Close" if mode == "close-missing-return" else "Poll"})
        result = reply(c)
        assert (
            result.metadata["worker_disposal_required"]
            and result.metadata["key_returns"] == []
        )
        assert c.status()["fault"]
    finally:
        finish(c)


def test_open_adapter_identity_must_match_request():
    c = controller()
    try:
        c.enqueue({"op": "Open", "adapter_luid": [0, 999], "fixture": "echo"})
        result = reply(c)
        assert (
            result.metadata["worker_disposal_required"]
            and "adapter identity" in c.status()["fault"]
        )
    finally:
        finish(c)


def test_stderr_thread_start_failure_still_disposes_child_and_pipes(monkeypatch):
    import threading

    original = threading.Thread.start

    def start(thread):
        if thread.name == "encoder-stderr":
            raise RuntimeError("fixture cannot start stderr thread")
        return original(thread)

    monkeypatch.setattr(threading.Thread, "start", start)
    c = controller()
    try:
        c.enqueue({"op": "Open", "adapter_luid": [0, 84637], "fixture": "echo"})
        wait(lambda: c.status()["done"])
        assert c.status()["fault"] and c.status()["process_exit"] is not None
        assert c._proc.stdin.closed and c._proc.stdout.closed and c._proc.stderr.closed
    finally:
        finish(c)


def test_controller_reports_unproved_job_handle_disposal(monkeypatch):
    from sm64_events.replay.gpuprocess.process_job import OwnedJob

    original = OwnedJob.snapshot

    def snapshot(job):
        state = original(job)
        if state["empty"]:
            state.update(handle_closed=False, error="fixture CloseHandle refused")
        return state

    monkeypatch.setattr(OwnedJob, "snapshot", snapshot)
    c = controller(shutdown_timeout=0.1)
    try:
        c.enqueue({"op": "Open", "adapter_luid": [0, 84637], "fixture": "echo"})
        reply(c)
        c.enqueue({"op": "Close"})
        reply(c)
        wait(lambda: c.status()["done"])
        assert "job exit could not be proved" in c.status()["fault"]
        assert not c.status()["disposal"]["handle_closed"]
    finally:
        monkeypatch.setattr(OwnedJob, "snapshot", original)
        finish(c)
