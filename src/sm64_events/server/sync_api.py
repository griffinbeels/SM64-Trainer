# src/sm64_events/server/sync_api.py
"""Version sync REST surface: the gate registry plus both ROM versions'
coverage reports, and the one write path a human's own verdict goes through.

`GET /api/sync` reads `sync.registry` rather than `sync.gates` directly, so a
gate module another track has not filled in yet (address_gates.py,
calibration_gates.py, feature_gates.py all ship as empty stubs until their own
tasks land) still shows up the moment it registers -- `registry.py` is the
only door that imports all three, and importing it here is what makes the
registry COMPLETE rather than whatever happened to be imported already.

`PUT /api/sync/verdict` is how `tools/sync_version.py` (and this page's own
future "mark it by hand" affordance) records what Griffin just confirmed. It
never goes through `tracking/service.py` -- a sync verdict is not a game event,
it has no `frame` in the game's clock, and the projector has no business
replaying it -- so the write lands directly in the report file
(`sync/report.py`) and the broadcast is a bare `broadcaster.publish`, exactly
the pattern `/api/uilog` and `/api/segments/origin` already use for a fact
that changes on-screen state but never belongs in the journal.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from sm64_events.core.events import Event
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.sync import registry
from sm64_events.sync.gates import Verdict
from sm64_events.sync.report import Report, report_path

VERSIONS = ("us", "jp")


class VerdictBody(BaseModel):
    status: str
    value: int | None = None
    measured: dict | None = None
    evidence: str = ""
    frames: int | None = None


class SyncVerdictBody(BaseModel):
    version: str
    gate_id: str
    verdict: VerdictBody
    at: str
    # False from tools/sync_version.py, which already wrote its own report
    # file: the server then only BROADCASTS, so a server in another checkout
    # (8066 is whatever run-test-server.bat launched) never writes a second
    # copy beside its own data dir. True (the default) for a manual PUT.
    persist: bool = True


def create_sync_router(broadcaster: Broadcaster, reports_root=None) -> APIRouter:
    router = APIRouter()

    @router.get("/api/sync")
    def get_sync() -> dict:
        return {
            "features": list(registry.FEATURES),
            "gates": registry.as_json(),
            "reports": {
                version: Report(report_path(version, reports_root)).load().as_json()
                for version in VERSIONS
            },
        }

    @router.put("/api/sync/verdict")
    async def put_sync_verdict(body: SyncVerdictBody) -> dict:
        if body.version not in VERSIONS:
            raise HTTPException(
                400, f"unknown version {body.version!r} -- know: {VERSIONS}")
        try:
            registry.gate(body.gate_id)
        except KeyError:
            raise HTTPException(404, f"no gate called {body.gate_id!r}") from None
        try:
            verdict = Verdict(status=body.verdict.status, value=body.verdict.value,
                              measured=body.verdict.measured,
                              evidence=body.verdict.evidence,
                              frames=body.verdict.frames)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

        if body.persist:
            report = Report(report_path(body.version, reports_root))
            report.load().record(body.gate_id, verdict, body.at)

        await broadcaster.publish(Event(
            type="sync_verdict", frame=0,
            timestamp_utc=datetime.now(timezone.utc),
            payload={"version": body.version, "gate_id": body.gate_id,
                     "verdict": verdict.as_json(), "at": body.at}))
        return {"ok": True}

    return router
