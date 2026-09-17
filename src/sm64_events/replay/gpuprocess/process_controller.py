"""Nonblocking command admission and bounded replies from one owned helper.

Only the I/O thread touches pipes. An independent watchdog retires an uncertain
worker epoch and terminates its private Windows job. Public methods do not join,
wait on pipes/native work, or retry a possibly admitted request.
"""

from collections import deque
from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import math
import threading
import time
import uuid
import logging
import subprocess

log = logging.getLogger(__name__)
from .process_job import OwnedJob
from ..gpuencoder_abi import Status as NativeStatus, Retained as NativeRetained
from .process_protocol import (
    exact,
    uint,
    metadata_bytes,
    canonical_command,
    frame,
    read_frame,
    write_all,
    REQUEST,
    REPLY,
)


@dataclass(frozen=True)
class Limits:
    max_count: int
    max_bytes: int
    max_json: int
    max_packet: int
    max_age: float
    call_timeout: float
    startup_timeout: float
    watchdog_interval: float
    stderr_bytes: int
    shutdown_timeout: float

    def __post_init__(self):
        for n in ["max_count", "max_bytes", "max_json", "max_packet", "stderr_bytes"]:
            if type(getattr(self, n)) is not int or getattr(self, n) <= 0:
                raise ValueError("positive integer " + n + " required")
        for n in [
            "max_age",
            "call_timeout",
            "startup_timeout",
            "watchdog_interval",
            "shutdown_timeout",
        ]:
            if not math.isfinite(getattr(self, n)) or getattr(self, n) <= 0:
                raise ValueError("positive finite " + n + " required")
        if (
            self.max_count > 64
            or self.max_json > 262144
            or self.max_packet > 16 * 1024 * 1024
            or self.stderr_bytes > 1024 * 1024
        ):
            raise ValueError("controller hard limit exceeded")
        if self.watchdog_interval > min(
            self.max_age, self.call_timeout, self.startup_timeout
        ):
            raise ValueError("watchdog interval exceeds deadline")


@dataclass(frozen=True)
class Refused:
    reason: str


@dataclass(frozen=True)
class Reply:
    request_id: int
    metadata: dict
    payload: bytes
    command_json: bytes = b""


@dataclass
class Entry:
    request_id: int
    command: dict
    wire: bytes
    credit: int
    created: float
    started: float | None = None
    reply: Reply | None = None


class Controller:
    def __init__(self, *, executable, helper, limits):
        self.limits = limits
        self.nonce = uuid.uuid4().hex
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._pending = {}
        self._work = deque()
        self._ready = deque()
        self._bytes = 0
        self._next = 1
        self._fault = None
        self._job = None
        self._proc = None
        self._inflight = None
        self._closing = False
        self._draining = False
        self._close_pending = None
        self._done = False
        self._stderr = bytearray()
        self._stderr_total = 0
        self._slot_owners = {}
        self._native_identity = None
        self._started = time.monotonic()
        self._command = []
        for p in (Path(executable), Path(helper)):
            if not p.is_absolute() or not p.is_file():
                raise ValueError("explicit absolute executable/helper files required")
            self._command.append(str(p.resolve()))
        self.identity = {
            "executable": self._command[0],
            "helper": self._command[1],
            "helper_sha256": hashlib.sha256(Path(helper).read_bytes()).hexdigest(),
        }
        self._command.extend([self.nonce, str(limits.max_json), str(limits.max_packet)])
        self._io = threading.Thread(target=self._run, name="encoder-pipe", daemon=True)
        self._watcher = threading.Thread(
            target=self._watch, name="encoder-watchdog", daemon=True
        )
        try:
            self._watcher.start()
            self._io.start()
        except BaseException:
            self.stop("controller thread startup failed")
            raise

    def enqueue(self, command):
        # JSON serialization snapshots nested caller values before admission.
        try:
            raw = metadata_bytes(command, self.limits.max_json)
            copied = json.loads(raw)
            if type(copied) is not dict or copied.get("op") not in (
                "Open",
                "Submit",
                "Repeat",
                "Poll",
                "Close",
            ):
                return Refused("unsupported command")
        except (ValueError, TypeError, RecursionError):
            return Refused("invalid command metadata")
        packet_credit = (
            self.limits.max_packet if copied["op"] in ("Submit", "Repeat") else 0
        )
        credit = len(raw) + 3 * self.limits.max_json + packet_credit + 32
        with self._lock:
            if self._fault or self._closing or self._done:
                return Refused(self._fault or "worker closing")
            if self._close_pending is not None:
                return Refused("close reply pending")
            if self._draining and copied["op"] not in ("Poll", "Close"):
                return Refused("worker draining")
            if copied["op"] == "Close" and any(
                e.reply is None for e in self._pending.values()
            ):
                return Refused("earlier command pending")
            if (
                len(self._pending) >= self.limits.max_count
                or self._bytes + credit > self.limits.max_bytes
            ):
                return Refused("bounded command/reply capacity")
            if self._next >= (1 << 64):
                return Refused("request sequence exhausted")
            request_id = self._next
            request = {"nonce": self.nonce, "request_id": request_id, "command": copied}
            try:
                wire = frame(
                    REQUEST, request, b"", max_json=self.limits.max_json, max_packet=0
                )
            except ValueError:
                return Refused("framed metadata bound exceeded")
            self._next += 1
            entry = Entry(request_id, copied, wire, credit, time.monotonic())
            self._pending[request_id] = entry
            self._work.append(request_id)
            self._bytes += credit
            if copied["op"] == "Close":
                self._draining = True
                self._close_pending = request_id
            self._wake.set()
            return request_id

    def take_result(self):
        with self._lock:
            if not self._ready:
                return None
            entry = self._pending.pop(self._ready.popleft())
            self._bytes -= entry.credit
            return entry.reply

    def status(self):
        job = self._job
        disposal = None if job is None else job.snapshot()
        with self._lock:
            proc = self._proc
            return {
                "nonce": self.nonce,
                "fault": self._fault,
                "pending_count": len(self._pending),
                "next_request_id": self._next,
                "reserved_bytes": self._bytes,
                "completed_undrained": len(self._ready),
                "inflight": self._inflight,
                "pid": None if proc is None else proc.pid,
                "process_exit": None if proc is None else proc.poll(),
                "done": self._done,
                "disposal": disposal,
                "stderr_tail": bytes(self._stderr),
                "stderr_total": self._stderr_total,
                "identity": dict(self.identity),
                "resume": None if self._job is None else self._job.resume_evidence,
            }

    def _fail_locked(self, reason):
        if self._fault:
            return self._job
        self._fault = reason[:512]
        self._stop.set()
        self._wake.set()
        for entry in self._pending.values():
            was_ready = entry.reply is not None
            expired = time.monotonic() - entry.created > self.limits.max_age
            if not was_ready or expired:
                metadata = {
                    "nonce": self.nonce,
                    "request_id": entry.request_id,
                    "result": 13,
                    "error": self._fault,
                    "worker_disposal_required": True,
                    "status": None,
                    "retained": None,
                    "key_returns": [],
                    "packet": None,
                    "identity": None,
                }
                entry.reply = Reply(
                    entry.request_id, metadata, b"", canonical_command(entry.command)
                )
                if not was_ready:
                    self._ready.append(entry.request_id)
        self._work.clear()
        return self._job

    def stop(self, reason):
        if not isinstance(reason, str) or not reason:
            raise ValueError("explicit stop reason required")
        with self._lock:
            job = self._fail_locked(reason)
        if job:
            job.stop()

    def _watch(self):
        while not self._stop.wait(self.limits.watchdog_interval):
            job = None
            with self._lock:
                if self._done and not self._pending:
                    return
                now = time.monotonic()
                reason = None
                if (
                    self._proc is None
                    and now - self._started > self.limits.startup_timeout
                ):
                    reason = "helper startup deadline"
                for entry in self._pending.values():
                    if now - entry.created > self.limits.max_age:
                        reason = "command/reply age deadline"
                        break
                    if (
                        entry.reply is None
                        and entry.started is not None
                        and now - entry.started
                        > (
                            self.limits.startup_timeout
                            if entry.command["op"] == "Open"
                            else self.limits.call_timeout
                        )
                    ):
                        reason = "native/pipe request deadline"
                        break
                if (
                    self._proc is not None
                    and self._inflight is None
                    and not self._closing
                    and self._proc.poll() is not None
                ):
                    reason = "helper exited unexpectedly"
                if reason:
                    job = self._fail_locked(reason)
            # Never hold controller/I/O locks while terminating the private job.
            if job:
                job.stop()
                return

    def _stderr_reader(self, pipe):
        try:
            while True:
                data = pipe.read(4096)
                if not data:
                    return
                with self._lock:
                    self._stderr_total += len(data)
                    self._stderr.extend(data)
                    if len(self._stderr) > self.limits.stderr_bytes:
                        del self._stderr[: -self.limits.stderr_bytes]
        except (OSError, ValueError):
            return
        finally:
            pipe.close()

    def _status_snapshot(self, status):
        exact(status, [name for name, _ in NativeStatus._fields_])
        counters = {
            "submitted",
            "completed",
            "delivered",
            "acquired",
            "released",
            "timeouts",
        }
        for name, value in status.items():
            if name == "adapter_high":
                if type(value) is not int or not -(1 << 31) <= value < (1 << 31):
                    raise ValueError("signed adapter high required")
            elif type(value) is not int or not 0 <= value < (
                1 << (64 if name in counters else 32)
            ):
                raise ValueError("invalid native status integer")
        if (
            status["struct_size"] != 104
            or status["version"] != 1
            or status["reserved"]
            or status["state"] not in (1, 2, 3, 4, 5)
            or status["encoder_state"] not in range(6)
        ):
            raise ValueError("native status ABI/state mismatch")
        held, pending = status["held_mask"], status["pending_mask"]
        if not status["owner_thread"]:
            raise ValueError("missing native owner thread")
        if held > 3 or pending > 3 or pending & ~held:
            raise ValueError("native custody mask mismatch")
        if (
            not status["delivered"] <= status["completed"] <= status["submitted"]
            or status["released"] > status["acquired"]
            or status["acquired"] - status["released"] != held.bit_count()
        ):
            raise ValueError("native custody/completion counters mismatch")

    def _validate_status(self, entry, metadata):
        op, code, status = entry.command["op"], metadata["result"], metadata["status"]
        if status is not None:
            self._status_snapshot(status)
            identity = tuple(
                status[n] for n in ["owner_thread", "adapter_high", "adapter_low"]
            )
            if op == "Open" and code == 0:
                requested = entry.command.get("adapter_luid")
                if (
                    type(requested) is not list
                    or len(requested) != 2
                    or any(type(v) is not int for v in requested)
                    or tuple(requested) != identity[1:]
                ):
                    raise ValueError("opened adapter identity mismatch")
                self._native_identity = identity
            elif (
                self._native_identity is not None and identity != self._native_identity
            ):
                raise ValueError("native owner/adapter identity changed")
        elif code in (0, 9, 10) and not metadata["error"]:
            raise ValueError("missing operational native status")

    def _validate_packet(self, entry, metadata, payload):
        op, code, packet = entry.command["op"], metadata["result"], metadata["packet"]
        if op in ("Submit", "Repeat") and code == 0 and not metadata["error"]:
            exact(packet, ["serial", "pts", "encoder_duration", "keyframe", "bytes"])
            if (
                any(
                    packet[n] != entry.command[n] or type(packet[n]) is not int
                    for n in ["serial", "pts", "encoder_duration"]
                )
                or type(packet["keyframe"]) is not bool
                or type(packet["bytes"]) is not int
                or packet["bytes"] != len(payload)
                or not payload
            ):
                raise ValueError("compressed packet echo mismatch")
        elif packet is not None or payload:
            raise ValueError("unexpected compressed packet")

    def _validate(self, entry, metadata, payload):
        exact(
            metadata,
            [
                "nonce",
                "request_id",
                "result",
                "error",
                "worker_disposal_required",
                "status",
                "retained",
                "key_returns",
                "packet",
                "identity",
            ],
        )
        if (
            metadata["nonce"] != self.nonce
            or uint(metadata["request_id"], "reply id", True) != entry.request_id
        ):
            raise ValueError("reply identity mismatch")
        code = metadata["result"]
        if (
            type(code) is not int
            or not 0 <= code <= 15
            or type(metadata["worker_disposal_required"]) is not bool
        ):
            raise ValueError("reply result shape")
        if metadata["error"] is not None and (
            type(metadata["error"]) is not str or len(metadata["error"]) > 512
        ):
            raise ValueError("reply error bound")
        op = entry.command["op"]
        status = metadata["status"]
        self._validate_status(entry, metadata)
        retained = metadata["retained"]
        if retained is not None:
            exact(retained, [name for name, _ in NativeRetained._fields_])
            for name, value in retained.items():
                uint(value, "retained " + name)
            if (
                retained["struct_size"] != 56
                or retained["version"] != 1
                or retained["reserved"]
                or retained["valid"] not in (0, 1)
            ):
                raise ValueError("retained snapshot ABI mismatch")
        self._validate_packet(entry, metadata, payload)
        if op == "Submit" and code == 0:
            slot = entry.command["slot"]
            if slot in self._slot_owners:
                raise ValueError("slot admitted before prior custody receipt")
            self._slot_owners[slot] = {
                "slot": slot,
                "bridge_token": entry.command["bridge_token"],
                "serial": entry.command["serial"],
            }
        returns = metadata["key_returns"]
        if type(returns) is not list or len(returns) > 2:
            raise ValueError("key return bound")
        for receipt in returns:
            exact(receipt, ["slot", "bridge_token", "serial"])
            slot = receipt["slot"]
            uint(receipt["bridge_token"], "returned bridge token", True)
            uint(receipt["serial"], "returned encoding serial")
            if (
                type(slot) is not int
                or self._slot_owners.get(slot) != receipt
                or not isinstance(status, dict)
                or (status["held_mask"] | status["pending_mask"]) & (1 << slot)
            ):
                raise ValueError("unproven key custody receipt")
            del self._slot_owners[slot]
        if op == "Close" and code == 0:
            if (
                status is None
                or status["state"] != 4
                or status["encoder_state"] != 4
                or status["held_mask"]
                or status["pending_mask"]
                or self._slot_owners
            ):
                raise ValueError("Close did not prove complete custody teardown")
        return metadata

    def _exchange(self, proc):
        while not self._stop.is_set():
            with self._lock:
                entry = self._pending[self._work.popleft()] if self._work else None
                if entry is not None:
                    entry.started = time.monotonic()
                    self._inflight = entry.request_id
                else:
                    self._wake.clear()
            if entry is None:
                self._wake.wait()
                continue
            write_all(proc.stdin, entry.wire)
            metadata, payload = read_frame(
                proc.stdout,
                REPLY,
                max_json=self.limits.max_json,
                max_packet=self.limits.max_packet,
            )
            self._validate(entry, metadata, payload)
            terminal = (
                metadata["error"] is not None or metadata["worker_disposal_required"]
            )
            closed = entry.command["op"] == "Close" and metadata["result"] == 0
            with self._lock:
                if self._fault:
                    break  # Watchdog won: never publish a late packet.
                entry.reply = Reply(
                    entry.request_id,
                    metadata,
                    payload,
                    canonical_command(entry.command),
                )
                self._ready.append(entry.request_id)
                self._inflight = None
                if entry.command["op"] == "Close":
                    self._close_pending = None
                if closed:
                    self._closing = True
            if terminal:
                self.stop(metadata["error"] or "native worker quarantined")
                break
            if closed:
                proc.wait(timeout=self.limits.shutdown_timeout)
                break

    def _run(self):
        job = None
        stderr_thread = None
        try:
            job = OwnedJob()
            with self._lock:
                self._job = job
                stopped = self._fault is not None
            if stopped:
                job.stop()
                return
            proc = job.spawn(self._command, str(Path(self._command[1]).parent))
            with self._lock:
                self._proc = proc
                stopped = self._fault is not None
            if stopped:
                job.stop()
                return
            reader = threading.Thread(
                target=self._stderr_reader,
                args=(proc.stderr,),
                name="encoder-stderr",
                daemon=True,
            )
            reader.start()
            stderr_thread = reader
            self._exchange(proc)
        except BaseException as exc:
            log.exception("GPU helper transport failed")
            self.stop(
                "helper transport failed: " + type(exc).__name__ + ": " + str(exc)[:400]
            )
        finally:
            self._dispose(job, stderr_thread)

    def _dispose(self, job, stderr_thread):
        if job:
            job.stop()
        # Bounded waits occur only in the I/O thread, never admission/watchdog.
        proc = None if job is None else job.child
        if proc is not None:
            with self._lock:
                if self._proc is None:
                    self._proc = proc
            try:
                proc.wait(timeout=self.limits.shutdown_timeout)
            except (subprocess.TimeoutExpired, OSError):
                log.exception("GPU helper process exit wait failed; checking owned job")
        if job:
            deadline = time.monotonic() + self.limits.shutdown_timeout
            while time.monotonic() < deadline:
                disposal = job.snapshot()
                if disposal["empty"] and disposal["handle_closed"]:
                    break
                time.sleep(0.005)
            disposal = job.snapshot()
            if not (disposal["empty"] and disposal["handle_closed"]):
                self.stop(
                    "owned helper job exit could not be proved; worker epoch remains retired"
                )
        if proc is not None and proc.poll() is not None and job.snapshot()["empty"]:
            for pipe in (proc.stdin, proc.stdout):
                if pipe:
                    pipe.close()
            if stderr_thread:
                stderr_thread.join(timeout=self.limits.shutdown_timeout)
            elif proc.stderr:
                proc.stderr.close()
        with self._lock:
            self._done = True
            self._inflight = None
        self._wake.set()
