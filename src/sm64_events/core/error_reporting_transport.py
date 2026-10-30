"""Bounded Sentry event spool with asynchronous, acknowledged delivery.

Only scrubbed error envelopes and operational log batches are accepted. This is
deliberately separate from the SDK HTTP transport: that transport swallows
delivery errors and cannot tell a persistent spool whether an event was
accepted. SDK Envelope/Dsn APIs retain the maintained wire format; our sender
checks the actual HTTP response. Pending errors expire after 24 hours, including
while the app remains running; retry and delayed persistence preserve the
original enqueue age.

The first error of each distinct issue in an app session (at most
REPORTS_PER_SESSION) also carries the redacted Debug report. This worker builds
it, never the thread that captured the error, and only while reporting stays
enabled; delivery holds that event until its report is attached or refused.
Reports never fill the spool's last 512 KiB, so they cannot crowd out errors.
A reloaded envelope keeps a report item only with this transport's filename,
type, header and size bound: no earlier build persisted attachments, and SDK
scope attachments are never copied. Log batches are memory-only, scrubbed,
deduplicated and capped per day.
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
from sentry_sdk.envelope import Envelope, Item, PayloadRef
from sentry_sdk.transport import Transport
from sentry_sdk.utils import Dsn

from .diagnostic_retention import bounded_report
from .error_reporting_privacy import safe_event, safe_log_item

MAX_PENDING = 20
MAX_ENVELOPE_BYTES = 96 * 1024
MAX_REPORT_BYTES = 256 * 1024
MAX_STORED_BYTES = MAX_ENVELOPE_BYTES + MAX_REPORT_BYTES + 1024  # event, report and item headers
MAX_SPOOL_BYTES = 2 * 1024 * 1024
REPORT_SPOOL_BYTES = MAX_SPOOL_BYTES - 512 * 1024
REPORTS_PER_SESSION = 5
REPORT_FILENAME = "debug-report.md"
REPORT_TYPE = "text/plain"
REPORT_HEADER = b"# SM64 Trainer debug report"
MAX_AGE = 86400
DAILY_LIMIT = 10
MONTHLY_LIMIT = 50
SIGNATURE_COOLDOWN = 3600
MAX_LOG_BATCHES = 10
LOG_DAILY_LIMIT = 500
LOG_REPEAT_COOLDOWN = 60
LOG_CONTENT_TYPE = "application/vnd.sentry.items.log+json"


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


def _signature(event: dict, *, issue=False) -> str:
    """One repeated failure; `issue` ignores the exception text, as Sentry's grouping does."""
    values = event.get("exception", {}).get("values", [])
    if values:
        last = values[-1]
        frames = last.get("stacktrace", {}).get("frames", [])
        location = frames[-1] if frames else {}
        text = None if issue else last.get("value")
        identity = (last.get("type"), text, location.get("filename"), location.get("function"))
    else:
        identity = event.get("logger"), event.get("message"), event.get("logentry")
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def _event_envelope(event: dict, report: bytes | None = None) -> Envelope:
    """Keep SDK identity in the envelope header so retries retain one event ID.

    Relay assigns an identity when the header omits it, even if the JSON event
    contains its own ID. A scrubbed rebuild must preserve this wire contract.
    """
    envelope = Envelope(headers={"event_id": event["event_id"]} if event.get("event_id") else {})
    envelope.add_event(event)
    if report is not None:
        envelope.add_item(Item(payload=report, type="attachment", content_type=REPORT_TYPE, filename=REPORT_FILENAME))
    return envelope


def _stored_report(envelope: Envelope) -> bytes | None:
    for item in envelope.items:
        if (item.type == "attachment" and item.headers.get("filename") == REPORT_FILENAME
                and item.headers.get("content_type") == REPORT_TYPE):
            data = item.payload.get_bytes()
            if len(data) <= MAX_REPORT_BYTES and data.startswith(REPORT_HEADER):
                return data
    return None


class PersistentTransport(Transport):
    """Never wait for network, disk or report building on the calling practice thread."""

    def __init__(self, options: dict, directory: Path, *, enabled=True, delivery=None, clock=time.time, report=None):
        super().__init__(options)
        self.directory = directory
        self.delivery = delivery or Delivery(options["dsn"])
        self.clock = clock
        self.enabled = enabled
        self.enabled_since = clock()
        self.report = report
        self.lock = threading.RLock()
        self.persist_lock = threading.Lock()
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.incoming = deque()
        self.pending: dict[str, bytes] = {}
        self.created: dict[str, float] = {}
        self.awaiting: dict[str, dict] = {}
        self.reported: set[str] = set()
        self.logs: deque[bytes] = deque()
        self.log_seen: dict[tuple, float] = {}
        self.run_trace = uuid.uuid4().hex  # for a log row that arrives without the SDK's trace
        self.signatures: dict[str, float] = {}
        self.budget = {"day": "", "month": "", "daily": 0, "monthly": 0, "logs": 0}
        self.counters = {"accepted": 0, "sent": 0, "dropped": 0, "expired": 0,
                         "delivery_errors": 0, "storage_errors": 0, "reports": 0,
                         "logs_sent": 0, "logs_dropped": 0}
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
                if not self.enabled or metadata.st_size > MAX_STORED_BYTES or len(self.pending) >= MAX_PENDING or total + metadata.st_size > MAX_SPOOL_BYTES or metadata.st_mtime < self.clock() - MAX_AGE:
                    path.unlink(missing_ok=True)
                    continue
                payload = path.read_bytes()
                stored = Envelope.deserialize(payload)
                event = stored.get_event()
                if not event:
                    raise ValueError("Not an error event")
                envelope = _event_envelope(safe_event(event), _stored_report(stored))
                cleaned = envelope.serialize()
                self.pending[path.name] = cleaned
                self.created[path.name] = min(metadata.st_mtime, self.clock())
                if cleaned != payload:
                    self.incoming.append((path.name, cleaned))
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
            if (stamp <= now - MAX_AGE or size > MAX_STORED_BYTES
                    or count >= MAX_PENDING or total + size > MAX_SPOOL_BYTES):
                self._unlink(temporary)
            else:
                count += 1
                total += size

    def _roll_budget(self, now: float):
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        if self.budget["day"] != day:
            self.budget.update(day=day, daily=0, logs=0)
        if self.budget["month"] != day[:7]:
            self.budget.update(month=day[:7], monthly=0)

    def _admit(self, event: dict) -> bool:
        now = self.clock()
        self._roll_budget(now)
        self.signatures = {key: stamp for key, stamp in self.signatures.items() if stamp > now - SIGNATURE_COOLDOWN}
        signature = _signature(event)
        if signature in self.signatures or self.budget["daily"] >= DAILY_LIMIT or self.budget["monthly"] >= MONTHLY_LIMIT:
            return False
        self.signatures[signature] = now
        self.budget["daily"] += 1
        self.budget["monthly"] += 1
        return True

    def _wants_report(self, event: dict) -> bool:
        if self.report is None or len(self.reported) >= REPORTS_PER_SESSION:
            return False
        issue = _signature(event, issue=True)
        if issue in self.reported:
            return False
        self.reported.add(issue)
        return True

    def capture_envelope(self, envelope: Envelope):
        try:
            event = envelope.get_event()
            if not event:
                self._capture_logs(envelope)
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
                if self._wants_report(safe):
                    self.awaiting[name] = safe
                self.counters["accepted"] += 1
                self.dirty = True
            self.wake.set()
        except Exception:  # noqa: BLE001 -- telemetry must never raise into practice
            self.counters["dropped"] += 1

    def _capture_logs(self, envelope: Envelope):
        """Admit warning-and-above operational lines; everything else in a non-event envelope drops."""
        rows = []
        for item in envelope.items:
            if item.type == "log":
                data = item.payload.json if item.payload.json is not None else json.loads(item.payload.get_bytes())
                if isinstance(data, dict) and isinstance(data.get("items"), list):
                    rows.extend(data["items"][:100])
        projected = [row for row in (safe_log_item(item, self.run_trace) for item in rows[:100]) if row is not None]
        if not projected:
            return
        with self.lock:
            if not self.enabled or self.stopping.is_set():
                return
            now = self.clock()
            accepted = [row for row in projected if row["timestamp"] >= self.enabled_since and self._admit_log(row, now)]
            if not accepted:
                return
            output = Envelope()
            output.add_item(Item(type="log", content_type=LOG_CONTENT_TYPE, headers={"item_count": len(accepted)},
                                 payload=PayloadRef(json={"version": 2, "items": accepted})))
            payload = output.serialize()
            if len(payload) > MAX_ENVELOPE_BYTES or len(self.logs) >= MAX_LOG_BATCHES:
                self.counters["logs_dropped"] += len(accepted)
                return
            self.logs.append(payload)
        self.wake.set()

    def _admit_log(self, row: dict, now: float) -> bool:
        self._roll_budget(now)
        key = (row["attributes"]["logger.name"]["value"], row["body"])
        if self.log_seen.get(key, float("-inf")) > now - LOG_REPEAT_COOLDOWN or self.budget["logs"] >= LOG_DAILY_LIMIT:
            self.counters["logs_dropped"] += 1
            return False
        if len(self.log_seen) >= 512:
            self.log_seen = {seen: stamp for seen, stamp in self.log_seen.items() if stamp > now - LOG_REPEAT_COOLDOWN}
        self.log_seen[key] = now
        self.budget["logs"] += 1
        self.dirty = True
        return True

    def _persist(self):
        with self.persist_lock:
            self._expire_pending()
            self._attach_reports()
            self._write_pending()

    def _attach_reports(self):
        """Build each held event's report here, off the capturing thread, then release it."""
        with self.lock:
            waiting = list(self.awaiting.items())
        for name, event in waiting:
            report = None
            try:
                with self.lock:
                    wanted = self.enabled and name in self.pending
                markdown = self.report() if wanted else None
                if isinstance(markdown, str) and markdown:
                    report = _event_envelope(event, bounded_report(markdown, MAX_REPORT_BYTES)).serialize()
            except Exception:  # noqa: BLE001 -- a failed report never costs the error itself
                report = None
            with self.lock:
                self.awaiting.pop(name, None)
                if report is None or not self.enabled or name not in self.pending:
                    continue
                total = sum(map(len, self.pending.values())) - len(self.pending[name]) + len(report)
                if total > REPORT_SPOOL_BYTES:
                    continue
                self.pending[name] = report
                self.incoming.append((name, report))
                self.counters["reports"] += 1

    def _expire_pending(self):
        """Expiry continues during long sessions and never resets on retry."""
        with self.lock:
            cutoff = self.clock() - MAX_AGE
            expired = [name for name, stamp in self.created.items() if stamp <= cutoff]
            for name in expired:
                self.pending.pop(name, None)
                self.created.pop(name, None)
                self.awaiting.pop(name, None)
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
            incoming = list(dict.fromkeys(name for name, _ in self.incoming))
            self.incoming.clear()
            budget = json.dumps({**self.budget, "signatures": self.signatures}) if self.dirty else None
            self.dirty = False
        for name in incoming:
            target = self.directory / name
            try:
                with self.lock:
                    # The latest payload wins: a report may have replaced the bare event.
                    payload = self.pending.get(name)
                    if payload is None:
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
            if not self.enabled or self.clock() < self.next_attempt:
                return
            # An event still waiting for its report is held, never sent bare.
            name = next((key for key in self.pending if key not in self.awaiting), None)
            if name is not None:
                payload = self.pending[name]
            elif self.logs:
                payload = self.logs.popleft()
            else:
                return
        try:
            accepted, retry_after = self.delivery(payload)
        except Exception:  # noqa: BLE001 -- transport failures never recursively log/report
            accepted, retry_after = False, 0
        with self.lock:
            if name is None:
                self.counters["logs_sent" if accepted else "delivery_errors"] += 1
                if not accepted and self.enabled and len(self.logs) < MAX_LOG_BATCHES:
                    self.logs.appendleft(payload)
            elif name not in self.pending:
                return  # Expiry/opt-out during an in-flight send keeps it retired.
            elif accepted:
                self.pending.pop(name, None)
                self.created.pop(name, None)
                self.counters["sent"] += 1
            else:
                self.counters["delivery_errors"] += 1
            if accepted:
                self.backoff = 5.0
                self.next_attempt = self.clock() + retry_after
            else:
                self.next_attempt = self.clock() + max(self.backoff, retry_after)
                self.backoff = min(self.backoff * 2, 3600)
        if accepted and name is not None:
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
            if enabled != self.enabled:
                self.enabled_since = self.clock()
            self.enabled = enabled
            if not enabled:
                self.incoming.clear()
                self.pending.clear()
                self.created.clear()
                self.awaiting.clear()
                self.logs.clear()
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
