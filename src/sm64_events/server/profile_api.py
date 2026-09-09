"""Explicit, time-limited diagnostics; independent of recorder lifecycle."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from sm64_events.core.profiling import profile


class StartProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    duration_s: float = Field(default=60, ge=1, le=300, allow_inf_nan=False)


class StopProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=32, max_length=32)


def create_profile_router(collector=profile):
    router = APIRouter(prefix="/api/diagnostics/profile", tags=["diagnostics"])

    @router.get("")
    def snapshot():
        return collector.snapshot()

    @router.post("/start", status_code=201)
    def start(body: StartProfile):
        try:
            return collector.start(body.duration_s)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/stop")
    def stop(body: StopProfile):
        try:
            return collector.stop(body.session_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    return router
