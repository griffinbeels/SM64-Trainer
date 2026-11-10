"""Supervised private IPC to the original MIT EOS helper.

No raw helper event, stderr, user identifier, invitation, nonce, configuration
value or packet is logged. Only the dedicated reader/writer touch pipes. A
separate watchdog can retire a hung helper without blocking capture/rendering.
"""
from __future__ import annotations

import base64
from concurrent.futures import Future
from dataclasses import dataclass
import io
import json
import os
from pathlib import Path
import queue
import threading
import time


class TransportError(RuntimeError):
    pass


@dataclass(frozen=True)
class Runtime:
    executable: Path
    config: Path
    cache: Path

    def available(self) -> bool:
        return self.executable.is_file() and self.config.is_file() and (
            self.executable.parent / "EOSSDK-Win64-Shipping.dll").is_file()


class EOSProcess:
    def __init__(self, runtime: Runtime, on_event, *, job_factory=None, clock=time.monotonic):
        self.runtime, self._event, self._clock = runtime, on_event, clock
        self._job_factory = job_factory
        self._job = self._child = None
        self._stdout = None
        self._lock = threading.RLock()
        self._cleanup_lock = threading.Lock()
        self._control = queue.Queue(32)
        self._latest, self._pending = {}, {}
        self._next = 0
        self._stop = threading.Event()
        self._threads = []
        self._last_output = self._clock()
        self._failed = False
        self._closing = False

    def start(self):
        if not self.runtime.available():
            raise TransportError("transport_unavailable")
        with self._lock:
            if self._job is not None or self._stop.is_set():
                raise TransportError("transport_already_started")
            if self._job_factory is None:
                from sm64_events.replay.gpuprocess.process_job import OwnedJob
                self._job_factory = OwnedJob
            self._job = self._job_factory()
            self._child = self._job.spawn([str(self.runtime.executable), "--config", str(self.runtime.config),
                "--parent-pid", str(os.getpid()), "--cache-dir", str(self.runtime.cache)],
                cwd=self.runtime.executable.parent)
            # OwnedJob deliberately exposes raw pipes. FileIO.readline would
            # issue a read per byte; only the reader uses this bounded buffer.
            self._stdout = io.BufferedReader(self._child.stdout, buffer_size=65536)
            self._last_output = self._clock()
            for name, work in (("eos-events", self._read), ("eos-commands", self._write),
                               ("eos-errors", self._drain_errors), ("eos-watchdog", self._watch)):
                thread = threading.Thread(target=work, name=name, daemon=True)
                self._threads.append(thread); thread.start()

    def submit(self, op: str, **fields) -> Future:
        with self._lock:
            if self._stop.is_set() or self._failed or self._child is None:
                raise TransportError("transport_unavailable")
            if len(self._pending) >= 32 or self._next >= (1 << 53) - 1:
                raise TransportError("transport_busy")
            self._next += 1
            request_id = self._next
            wire = self._wire({"v":1, "op":op, "request_id":request_id, **fields})
            future = Future()
            try:
                self._control.put_nowait(wire)
            except queue.Full as error:
                raise TransportError("transport_busy") from error
            self._pending[request_id] = future, self._clock()
            return future

    @staticmethod
    def _wire(value):
        raw = json.dumps(value, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8") + b"\n"
        if len(raw) > 4096:
            raise TransportError("transport_command_too_large")
        return raw

    def latest(self, *, payload: bytes, **fields):
        if type(payload) is not bytes or not 0 < len(payload) <= 1024:
            raise TransportError("transport_payload_invalid")
        key = fields.get("peer_ref")
        if type(key) is not int or key <= 0:
            raise TransportError("transport_peer_invalid")
        wire = self._wire({"v":1, "op":"send_latest", **fields,
                           "payload_b64":base64.b64encode(payload).decode("ascii")})
        with self._lock:
            if self._stop.is_set() or self._failed:
                return False
            if key not in self._latest and len(self._latest) >= 3:
                return False
            self._latest[key] = wire
            return True

    def control(self, payload: bytes, **fields) -> Future:
        if type(payload) is not bytes or not 0 < len(payload) <= 512:
            raise TransportError("transport_control_invalid")
        return self.submit("send_control", **fields, payload_b64=base64.b64encode(payload).decode("ascii"))

    def _notify(self, value):
        try:
            self._event(value)
        except Exception:  # noqa: BLE001 - external state handler cannot strand owned pipes
            self._fail("transport_consumer_failed")

    def _fail(self, reason):
        with self._lock:
            if self._failed:
                return
            self._failed = True
            self._latest.clear()
            for future, _ in self._pending.values():
                if not future.done():
                    future.set_exception(TransportError(reason))
            self._pending.clear()
        try:
            self._event({"v":1, "type":"fatal", "code":reason})
        except Exception:  # noqa: BLE001, S110 - observer errors may contain private IPC
            pass
        if self._job is not None:
            self._job.stop()

    def _read(self):
        try:
            while not self._stop.is_set():
                raw = self._stdout.readline(4097)
                if not raw:
                    if not self._stop.is_set() and not self._closing:
                        self._fail("transport_stopped")
                    return
                if len(raw) > 4096 or not raw.endswith(b"\n"):
                    self._fail("transport_protocol_error"); return
                value = json.loads(raw)
                if type(value) is not dict or value.get("v") != 1 or type(value.get("type")) is not str:
                    self._fail("transport_protocol_error"); return
                with self._lock:
                    self._last_output = self._clock()
                    entry = self._pending.pop(value.get("request_id"), None) if value["type"] == "reply" else None
                if entry is not None:
                    future = entry[0]
                    if not future.done():
                        if value.get("ok") is True:
                            future.set_result(None)
                        else:
                            code = value.get("error")
                            code = code if type(code) is str and len(code) <= 64 and code.replace("_", "").isalnum() else "transport_refused"
                            future.set_exception(TransportError(code))
                if value["type"] != "reply":
                    self._notify(value)
        except (OSError, ValueError, RecursionError):
            if not self._stop.is_set() and not self._closing:
                self._fail("transport_protocol_error")

    def _write(self):
        heartbeat = float("-inf")
        try:
            while not self._stop.is_set() and not self._failed:
                now = self._clock()
                if now - heartbeat >= 2:
                    self._write_line(self._wire({"v":1, "op":"heartbeat"})); heartbeat = now
                try:
                    wire = self._control.get(timeout=.01)
                except queue.Empty:
                    with self._lock:
                        key = next(iter(self._latest), None)
                        wire = self._latest.pop(key) if key is not None else None
                if wire is not None:
                    self._write_line(wire)
        except (OSError, ValueError):
            if not self._stop.is_set() and not self._closing:
                self._fail("transport_write_failed")

    def _write_line(self, wire):
        remaining = memoryview(wire)
        while remaining:
            count = self._child.stdin.write(remaining)
            if not count:
                raise OSError("private pipe closed")
            remaining = remaining[count:]
        self._child.stdin.flush()

    def _drain_errors(self):
        try:
            while not self._stop.is_set() and self._child.stderr.read(4096):
                pass  # No raw vendor stderr can enter application logs.
        except (OSError, ValueError):
            pass

    def _watch(self):
        while not self._stop.wait(.25) and not self._failed:
            now = self._clock()
            with self._lock:
                expired = any(now - when > 12 for _, when in self._pending.values())
                stalled = now - self._last_output > 15
            # Unknown-outcome commands cannot remain queued after their caller
            # times out: retire the whole process generation before completing
            # waiters, even if unrelated stats are keeping stdout healthy.
            if expired or stalled:
                self._fail("transport_timeout"); return

    def close(self, *, timeout=2):
        # Concurrent callers must not close a pipe while another cleanup owns
        # it. A refused/unfinished disposal keeps every owner for the next call.
        if not self._cleanup_lock.acquire(timeout=timeout):
            raise TransportError("transport_cleanup_pending")
        try:
            self._close(timeout)
        finally:
            self._cleanup_lock.release()

    def _close(self, timeout):
        if self._job is None:
            return
        self._closing = True
        if self._child is not None and not self._failed and self._child.poll() is None:
            try:
                self.submit("shutdown").result(timeout=1)
            except (TransportError, TimeoutError):
                pass
            grace = time.monotonic() + 2
            while self._child.poll() is None and not self._failed and time.monotonic() < grace:
                time.sleep(.01)
        self._stop.set()
        with self._lock:
            pending, self._pending = self._pending, {}
            self._latest.clear()
        for future, _ in pending.values():
            if not future.done():
                future.set_exception(TransportError("transport_stopped"))
        self._job.stop()
        deadline = time.monotonic() + timeout
        for thread in self._threads:
            if thread is not threading.current_thread():
                thread.join(max(0, deadline - time.monotonic()))
        while True:
            disposal = self._job.snapshot()
            disposed = disposal["empty"] and disposal["handle_closed"]
            if disposed or time.monotonic() >= deadline:
                break
            time.sleep(.01)
        if not disposed or any(t.is_alive() for t in self._threads):
            raise TransportError("transport_cleanup_pending")
        pipes = (self._child.stdin, self._stdout or self._child.stdout, self._child.stderr) if self._child is not None else ()
        try:
            for pipe in pipes:
                pipe.close()
        except (OSError, ValueError) as error:
            raise TransportError("transport_cleanup_pending") from error
        self._stdout = self._child = self._job = None
        self._threads.clear()
