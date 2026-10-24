"""Compose cached app facts without calling user-facing routes or acquiring capture."""
from __future__ import annotations

from contextlib import asynccontextmanager
import logging

from sm64_events.core import error_reporting
from sm64_events.core.diagnostic_snapshot import DiagnosticSnapshot
from sm64_events.core.silent_failures import SilentFailureMonitor

log = logging.getLogger("sm64.diagnostics")


def make_snapshot(*, poller, service, monitor, replay, capture_layer, ghost, setup_observer, polling_state):
    recorder = getattr(replay, "recorder", None)
    def setup():
        read = getattr(capture_layer, "diagnostic_status", None)
        layer = read() if read else None
        runtime = getattr(setup_observer, "diagnostic_status", None)
        return {**(layer or {}), "runtime": runtime() if runtime else {}} if layer is not None or runtime else None
    def ghost_status():
        read = getattr(ghost, "diagnostic_status", None)
        result = read() if read else {}
        capture = getattr(recorder, "ghost_capture", None)
        native = capture.snapshot_status(blocking=False) if capture is not None else None
        return {**result, "native": native} if read or capture is not None else None
    def pipeline():
        sampler = getattr(poller, "input_sampler", None)
        latest = getattr(poller, "latest", None)
        return {"emulator_attached": bool(getattr(poller.memory, "attached", False)),
                "paused": bool(getattr(poller, "paused", False)),
                "frame": latest.global_timer if latest else None, "polling": polling_state(),
                "inputs": sampler.health() if sampler else None,
                "tracking": service.tracking_health() if hasattr(service, "tracking_health") else None,
                "input_storage": poller.input_storage_health() if hasattr(poller, "input_storage_health") else None}
    def progress():
        latest = getattr(poller, "latest", None)
        read = getattr(recorder, "silent_failure_status", None)
        capture = read() if read else {}
        read = getattr(ghost, "silent_failure_status", None)
        ghost_progress = read() if read else {}
        read = getattr(capture_layer, "diagnostic_status", None)
        layer = read() if read else {}
        read = getattr(setup_observer, "silent_failure_status", None)
        setup_progress = read(layer or {}) if read else {}
        return {"capture": capture, "ghost": ghost_progress, "setup": setup_progress,
                "game": {"attached": bool(getattr(poller.memory, "attached", False)),
                    "paused": bool(getattr(poller, "paused", False)),
                    "held": bool(getattr(poller, "hold_reason", None)),
                    "frame": latest.global_timer if latest else None,
                    "scope": getattr(latest, "session_id", None)}}
    return DiagnosticSnapshot(capture=getattr(recorder, "diagnostic_status", None),
        setup=setup, ghost=ghost_status, pipeline=pipeline, resources=lambda: monitor.latest,
        progress=progress if recorder is not None else None)


def with_diagnostics(lifespan, snapshot, *, report=None):
    @asynccontextmanager
    async def combined(app):
        unbind_report = error_reporting.set_report_source(report) if report is not None else (lambda: None)
        unbind_snapshot = error_reporting.set_diagnostic_source(snapshot.collect)
        def unbind():
            unbind_snapshot()
            unbind_report()
        watcher = SilentFailureMonitor(snapshot.progress) if snapshot.has_progress else None
        stopped = True
        try:
            async with lifespan(app):
                try:
                    if watcher is not None:
                        watcher.start()
                    yield
                finally:
                    if watcher is not None:
                        stopped = watcher.stop()
        finally:
            unbind()
            if stopped:
                _close_snapshot(snapshot)
            else:
                watcher.close_after_stop(lambda: _close_snapshot(snapshot))
    return combined


def _close_snapshot(snapshot):
    try:
        snapshot.close()
    except (OSError, RuntimeError):
        log.warning("Passive diagnostic handle cleanup remains pending.")
