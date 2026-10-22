"""One passive snapshot serves local export and automatic error context.

Sources are independent and may be busy/stale. Collection is failure-driven;
there is no background telemetry sampler, gameplay stream or report-file spool.
"""
from __future__ import annotations

from datetime import datetime, timezone
import threading

from sm64_events.core.diagnostic_privacy import safe_snapshot
from sm64_events.core.native_diagnostics import NativeDiagnostics


class DiagnosticSnapshot:
    def __init__(self, *, capture=None, ghost=None, setup=None, pipeline=None, resources=None, native=None):
        self._sources = dict(capture=capture, ghost=ghost, setup=setup, pipeline=pipeline, resources=resources)
        self._native = native if native is not None else NativeDiagnostics()
        self._lock = threading.Lock()
        self._closed = False

    def collect(self, *, local=False):
        if not self._lock.acquire(blocking=False):
            return {"schema": 1, "error_type": "Busy"}
        try:
            if self._closed:
                return {"schema": 1, "error_type": "Closed"}
            stamp = datetime.now(timezone.utc).isoformat()
            raw = {"captured_utc": stamp, "sources": {}}
            for key, read in self._sources.items():
                try:
                    raw[key] = read() if read is not None else None
                    raw["sources"][key] = {"available": raw[key] is not None, "read_utc": stamp}
                except Exception as error:  # noqa: BLE001 - each unavailable source is evidence.
                    raw["sources"][key] = {"available": False, "error_type": type(error).__name__}
            try:
                raw["native"] = self._native.snapshot(raw.get("setup"))
                raw["sources"]["native"] = {"available": raw["native"].get("available", False), "read_utc": stamp}
            except Exception as error:  # noqa: BLE001 - export/reporting must survive native failures.
                raw["sources"]["native"] = {"available": False, "error_type": type(error).__name__}
            result = safe_snapshot(raw)
            if local:
                # Local tails still pass through the report's redactor; never
                # placed in the automatic schema, even by direct transport use.
                result["native_log_tails"] = [dict(source=row.get("source"), file=row.get("file"), tail=row["tail"])
                    for row in (raw.get("native") or {}).get("logs", []) if "tail" in row]
            return result
        finally:
            self._lock.release()

    def close(self):
        with self._lock:
            self._closed = True
            self._native.close()
