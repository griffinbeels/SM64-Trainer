"""Compose cached app facts without calling user-facing routes or acquiring capture."""
from __future__ import annotations

from contextlib import asynccontextmanager
import logging

from sm64_events.core import error_reporting
from sm64_events.core.diagnostic_snapshot import DiagnosticSnapshot

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
    return DiagnosticSnapshot(capture=getattr(recorder, "diagnostic_status", None),
        setup=setup, ghost=ghost_status, pipeline=pipeline, resources=lambda: monitor.latest)


def with_diagnostics(lifespan, snapshot):
    @asynccontextmanager
    async def combined(app):
        unbind = error_reporting.set_diagnostic_source(snapshot.collect)
        try:
            async with lifespan(app):
                yield
        finally:
            unbind()
            try:
                snapshot.close()
            except (OSError, RuntimeError):
                log.warning("Passive diagnostic handle cleanup remains pending.")
    return combined
