# src/sm64_events/server/update_api.py
"""Self-update REST surface. status() and skip() are cheap; apply() kicks the
download+verify+swap off-thread (UpdateService owns the worker) and, on success,
fires the same full-process restart the admin endpoint uses."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, StrictBool

from sm64_events.core.release_contract import version_key


class SkipBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str


class BetaBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    receive_beta_updates: StrictBool


def create_update_router(updater, restart, *, can_apply=None) -> APIRouter:
    router = APIRouter(prefix="/api/update")

    @router.get("/status")
    def status(force: bool = False, repair: bool = False):
        return updater.status(force=force, **({"repair": True} if repair else {}))

    @router.get("/preferences")
    def preferences():
        return updater.beta_preferences()

    @router.put("/preferences")
    def set_preferences(body: BetaBody):
        try:
            return updater.set_beta_updates(body.receive_beta_updates)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @router.post("/apply")
    def apply(repair: bool = False):
        if can_apply is not None and (reason := can_apply()):
            raise HTTPException(409, reason)
        options = {}
        if repair:
            options["repair"] = True
        if can_apply is not None:
            options["can_apply"] = can_apply
        return updater.begin_apply(on_success=restart, **options)

    @router.post("/skip")
    def skip(body: SkipBody):
        try:
            version_key(body.version)
        except ValueError as error:
            raise HTTPException(422, "Invalid release version.") from error
        updater.skip(body.version)
        return {"skipped": body.version}

    return router
