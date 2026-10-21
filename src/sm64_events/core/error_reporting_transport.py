"""Bounded Sentry event spool with asynchronous, acknowledged delivery.

Only scrubbed error envelopes are accepted. This is deliberately separate from
the SDK HTTP transport: that transport swallows delivery errors and cannot tell
a persistent spool whether an event was accepted. SDK Envelope/Dsn APIs retain
the maintained wire format; our sender checks the actual HTTP response. Pending
errors expire after 24 hours, including while the app remains running; retry and
delayed persistence preserve the original enqueue age.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from collections import deque
from pathlib import Path

import certifi
import urllib3
from sentry_sdk.consts import VERSION, EndpointType
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport
from sentry_sdk.utils import Dsn

from .error_reporting_privacy import safe_event

MAX_PENDING = 20
MAX_ENVELOPE_BYTES = 64 * 1024
MAX_SPOOL_BYTES = 1024 * 1024
MAX_AGE = 86400
DAILY_LIMIT = 10
MONTHLY_LIMIT = 50
SIGNATURE_COOLDOWN = 3600


class Delivery:
    """Synchronous bounded HTTP delivery; called only by the spool worker."""

    def __init__(self, dsn: str):
        self.auth = Dsn(dsn).to_auth(f"sentry.python/{VERSION}")
        self.pool = urllib3.PoolManager(
            timeout=urllib3.Timeout(total=3, connect=2, read=2), retries=False,
            cert_reqs="CERT_REQUIRED", ca_certs=certifi.where(),
        )

    def __call__(self, payload: bytes) -> tuple[bool, float]:
        response = self.pool.request(
            "POST", self.auth.get_api_url(EndpointType.ENVELOPE), body=payload,
            headers={"X-Sentry-Auth": self.auth.to_header(),
                     "Content-Type": "application/x-sentry-envelope"},
            redirect=False, preload_content=False,
        )
        try:
            retry = 0.0
            if response.status == 429:
                try:
                    retry = max(60.0, min(float(response.headers.get("Retry-After", "3600")), 86400))
                except ValueError:
                    retry = 3600.0
            limits = response.headers.get("X-Sentry-Rate-Limits", "")
            for limit in limits.split(","):
                parts = limit.strip().split(":")
                if len(parts) >= 2 and (not parts[1] or "error" in parts[1].split(";")):
                    try:
                        retry = max(retry, min(float(parts[0]), 86400))
                    except ValueError:
                        retry = max(retry, 3600)
            return 200 <= response.status < 300, retry
        finally:
            response.close()
            response.release_conn()


def _signature(event: dict) -> str:
    values = event.get("exception", {}).get("values", [])
    if values:
        last = values[-1]
        frames = last.get("stacktrace", {}).get("frames", [])
        location = frames[-1] if frames else {}
        identity = (last.get("type"), last.get("value"), location.get("filename"), location.get("function"))
    else:
        identity = event.get("logger"), event.get("message"), event.get("logentry")
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def _event_envelope(event: dict) -> Envelope:
    """Keep SDK identity in the envelope header so retries retain one event ID.

    Relay assigns an identity when the header omits it, even if the JSON event
    contains its own ID. A scrubbed rebuild must preserve this wire contract.
    """
    envelope = Envelope(headers={"event_id": event["event_id"]} if event.get("event_id") else {})
    envelope.add_event(event)
    return envelope


class PersistentTransport(Transport):
    """Never wait for network or disk on the calling practice thread."""

    def __init__(self, options: dict, directory: Path, *, enabled=True, delivery=None, clock=time.time):
        super().__init__(options)
        self.directory = directory
        self.delivery = delivery or Delivery(options["dsn"])
        self.clock = clock
        self.enabled = enabled
        self.lock = threading.RLock()
        self.persist_lock = threading.Lock()
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.incoming = deque()
        self.pending: dict[str, bytes] = {}
        self.created: dict[str, float] = {}
        self.signatures: dict[str, float] = {}
        self.budget = {"day": "", "month": "", "daily": 0, "monthly": 0}
        self.counters = {"accepted": 0, "sent": 0, "dropped": 0, "expired": 0,
                         "delivery_errors": 0, "storage_errors": 0}
        self.next_attempt = 0.0
        self.backoff = 5.0
        self.dirty = False
        self._next_temporary_cleanup = 0.0
        self._load()
        self.worker = threading.Thread(target=self._run, name="automatic-diagnostics", daemon=True)
        self.worker.start()

    def _load(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        state = self.directory / "budget.json"
        try:
            data = json.loads(state.read_text(encoding="utf-8")) if state.exists() else {}
            self.budget.update({key: data[key] for key in self.budget if key in data})
            self.signatures = {key: float(value) for key, value in data.get("signatures", {}).items()
                               if isinstance(key, str) and len(key) == 64 and float(value) > self.clock() - SIGNATURE_COOLDOWN}
        except (OSError, ValueError, TypeError):
            self.counters["storage_errors"] += 1
        total = 0
        self._prune_temporaries()
        for path in sorted(self.directory.glob("*.envelope")):
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                metadata = path.stat()
                if not self.enabled or metadata.st_size > MAX_ENVELOPE_BYTES or len(self.pending) >= MAX_PENDING or total + metadata.st_size > MAX_SPOOL_BYTES or metadata.st_mtime < self.clock() - MAX_AGE:
                    path.unlink(missing_ok=True)
                    continue
                payload = path.read_bytes()
                event = Envelope.deserialize(payload).get_event()
                if not event:
                    raise ValueError("Not an error event")
                envelope = _event_envelope(safe_event(event))
                self.pending[path.name] = envelope.serialize()
                self.created[path.name] = min(metadata.st_mtime, self.clock())
                if metadata.st_mtime > self.clock():
                    os.utime(path, (self.created[path.name], self.created[path.name]))
                total += len(payload)
            except (OSError, ValueError, TypeError, KeyError):
                self.counters["storage_errors"] += 1
                self._unlink(path)
        self._next_temporary_cleanup = 0.0
        self._prune_temporaries()

    def _unlink(self, path):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            self.counters["storage_errors"] += 1

    def _prune_temporaries(self):
        now = self.clock()
        if now < self._next_temporary_cleanup:
            return
        self._next_temporary_cleanup = now + 60
        with self.lock:
            active = {Path(name).with_suffix(".tmp").name for name in self.pending}
            total = sum(map(len, self.pending.values()))
            count = len(self.pending)
        candidates = []
        for temporary in self.directory.glob("*.tmp"):
            try:
                if (re.fullmatch(r"[0-9a-f]{32}\.tmp", temporary.name)
                        and temporary.name not in active and not temporary.is_symlink()
                        and temporary.is_file()):
                    metadata = temporary.stat()
                    candidates.append((metadata.st_mtime, metadata.st_size, temporary))
            except OSError:
                self.counters["storage_errors"] += 1
        for stamp, size, temporary in sorted(candidates, reverse=True):
            if (stamp <= now - MAX_AGE or size > MAX_ENVELOPE_BYTES
                    or count >= MAX_PENDING or total + size > MAX_SPOOL_BYTES):
                self._unlink(temporary)
            else:
                count += 1
                total += size

    def _admit(self, event: dict) -> bool:
        now = self.clock()
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        month = day[:7]
        if self.budget["day"] != day:
            self.budget.update(day=day, daily=0)
        if self.budget["month"] != month:
            self.budget.update(month=month, monthly=0)
        self.signatures = {key: stamp for key, stamp in self.signatures.items() if stamp > now - SIGNATURE_COOLDOWN}
        signature = _signature(event)
        if signature in self.signatures or self.budget["daily"] >= DAILY_LIMIT or self.budget["monthly"] >= MONTHLY_LIMIT:
            return False
        self.signatures[signature] = now
        self.budget["daily"] += 1
        self.budget["monthly"] += 1
        return True

    def capture_envelope(self, envelope: Envelope):
        try:
            event = envelope.get_event()
            if not event:
                return
            safe = safe_event(event)
            output = _event_envelope(safe)
            payload = output.serialize()
            with self.lock:
                if not self.enabled or self.stopping.is_set():
                    return
                total = sum(map(len, self.pending.values()))
                if len(payload) > MAX_ENVELOPE_BYTES or len(self.pending) >= MAX_PENDING or total + len(payload) > MAX_SPOOL_BYTES or not self._admit(safe):
                    self.counters["dropped"] += 1
                    return
                name = f"{uuid.uuid4().hex}.envelope"
                self.pending[name] = payload
                self.created[name] = self.clock()
                self.incoming.append((name, payload))
                self.counters["accepted"] += 1
                self.dirty = True
            self.wake.set()
        except Exception:  # noqa: BLE001 -- telemetry must never raise into practice
            self.counters["dropped"] += 1

    def _persist(self):
        with self.persist_lock:
            self._expire_pending()
            self._write_pending()

    def _expire_pending(self):
        """Expiry continues during long sessions and never resets on retry."""
        with self.lock:
            cutoff = self.clock() - MAX_AGE
            expired = [name for name, stamp in self.created.items() if stamp <= cutoff]
            for name in expired:
                self.pending.pop(name, None)
                self.created.pop(name, None)
            if expired:
                self.incoming = deque((name, payload) for name, payload in self.incoming
                                      if name in self.pending)
                self.counters["expired"] += len(expired)
        for name in expired:
            self._unlink(self.directory / name)
            self._unlink((self.directory / name).with_suffix(".tmp"))
        self._prune_temporaries()

    def _write_pending(self):
        with self.lock:
            incoming = list(self.incoming)
            self.incoming.clear()
            budget = json.dumps({**self.budget, "signatures": self.signatures}) if self.dirty else None
            self.dirty = False
        for name, payload in incoming:
            target = self.directory / name
            try:
                with self.lock:
                    if name not in self.pending:
                        continue
                    created = self.created[name]
                temporary = target.with_suffix(".tmp")
                temporary.write_bytes(payload)
                os.utime(temporary, (created, created))
                temporary.replace(target)
                with self.lock:
                    keep = name in self.pending
                if not keep:
                    target.unlink(missing_ok=True)
            except OSError:
                self.counters["storage_errors"] += 1
        if budget is not None:
            try:
                state = self.directory / "budget.tmp"
                state.write_text(budget, encoding="utf-8")
                state.replace(self.directory / "budget.json")
            except OSError:
                self.counters["storage_errors"] += 1

    def _deliver_one(self):
        self._expire_pending()
        with self.lock:
            if not self.enabled or not self.pending or self.clock() < self.next_attempt:
                return
            name, payload = next(iter(self.pending.items()))
        try:
            accepted, retry_after = self.delivery(payload)
        except Exception:  # noqa: BLE001 -- transport failures never recursively log/report
            accepted, retry_after = False, 0
        with self.lock:
            if name not in self.pending:
                return  # Expiry/opt-out during an in-flight send keeps it retired.
            if accepted:
                self.pending.pop(name, None)
                self.created.pop(name, None)
                self.counters["sent"] += 1
                self.backoff = 5.0
                self.next_attempt = self.clock() + retry_after
            else:
                self.counters["delivery_errors"] += 1
                self.next_attempt = self.clock() + max(self.backoff, retry_after)
                self.backoff = min(self.backoff * 2, 3600)
        if accepted:
            try:
                (self.directory / name).unlink(missing_ok=True)
            except OSError:
                self.counters["storage_errors"] += 1

    def _run(self):
        while not self.stopping.is_set():
            self._persist()
            self._deliver_one()
            self.wake.wait(0.25)
            self.wake.clear()
        self._persist()

    def set_enabled(self, enabled: bool):
        with self.lock:
            self.enabled = enabled
            if not enabled:
                self.incoming.clear()
                self.pending.clear()
                self.created.clear()
        if not enabled:
            for path in self.directory.glob("*.envelope"):
                path.unlink(missing_ok=True)
        self.wake.set()

    def status(self) -> dict:
        with self.lock:
            return {"pending": len(self.pending), **self.counters}

    def kill(self):
        if self.stopping.is_set():
            return
        self.stopping.set()
        self.wake.set()
        # An uncaught exception can enqueue immediately before process exit,
        # while this daemon is unscheduled or busy sending an older report.
        # Drain on the exiting thread; never depend on another worker turn.
        self._persist()
        self.worker.join(timeout=4)

    def flush(self, timeout, callback=None):
        # Shutdown persists reports without making the UI wait for the network.
        self.wake.set()
