# src/sm64_events/server/update_api.py
"""Self-update REST surface. status() and skip() are cheap; apply() kicks the
download+verify+swap off-thread (UpdateService owns the worker) and, on success,
fires the same full-process restart the admin endpoint uses."""
from fastapi import APIRouter
from pydantic import BaseModel


class SkipBody(BaseModel):
    version: str


def create_update_router(updater, restart) -> APIRouter:
    router = APIRouter(prefix="/api/update")

    @router.get("/status")
    def status(force: bool = False):
        return updater.status(force=force)

    @router.post("/apply")
    def apply():
        return updater.begin_apply(on_success=restart)

    @router.post("/skip")
    def skip(body: SkipBody):
        updater.skip(body.version)
        return {"skipped": body.version}

    return router
