# src/sm64_events/server/api.py
"""REST command/query surface for the tracker UI (spec §7).

Error taxonomy (service exception types are part of the contract):
LookupError -> 404 (no such attempt), ValueError -> 409 (exists but not
saveable: bad mode, non-success, cleared, missing clock),
RuntimeError -> 503 (database unavailable / degraded mode)."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from sm64_events.links import star_links
from sm64_events.stats.registry import registry_meta
from sm64_events.tracking.views import build_session_view


class TargetBody(BaseModel):
    course_id: int
    star_id: int
    strat_tag: str | None = None


class ClearBody(BaseModel):
    reason: str | None = None


class PbBody(BaseModel):
    attempt_id: int
    timer_mode: str


class SessionBody(BaseModel):
    label: str | None = None


class StatSelection(BaseModel):
    key: str
    params: dict = {}


class StatMenuBody(BaseModel):
    selections: list[StatSelection]


def _http(e: Exception) -> HTTPException:
    if isinstance(e, LookupError):
        return HTTPException(404, str(e))
    if isinstance(e, ValueError):
        return HTTPException(409, str(e))
    return HTTPException(503, str(e))  # RuntimeError: degraded mode


def create_api_router(service) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/session")
    def session(clock: str = "igt"):
        if clock not in ("igt", "rta"):
            raise HTTPException(422, "clock must be igt or rta")
        if service.db is None:
            raise HTTPException(503, "database unavailable")
        return build_session_view(service.db, service, clock=clock)

    @router.post("/session/new")
    async def session_new(body: SessionBody):
        try:
            sid = await service.new_session(label=body.label)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"session_id": sid}

    @router.post("/target")
    async def target(body: TargetBody):
        try:
            await service.set_target(body.course_id, body.star_id, body.strat_tag)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.post("/attempts/{attempt_id}/clear")
    async def clear(attempt_id: int, body: ClearBody):
        try:
            await service.clear_attempt(attempt_id, reason=body.reason)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.post("/attempts/{attempt_id}/restore")
    async def restore(attempt_id: int):
        try:
            await service.restore_attempt(attempt_id)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.post("/pb")
    async def save_pb(body: PbBody):
        try:
            return await service.save_pb(body.attempt_id, body.timer_mode)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)

    @router.get("/stats/registry")
    def stats_registry():
        return registry_meta()

    @router.put("/statmenu")
    def put_statmenu(body: StatMenuBody):
        if service.db is None:
            raise HTTPException(503, "database unavailable")
        service.db.set_state("stat_menu", [s.model_dump() for s in body.selections])
        return {"ok": True}

    @router.get("/links/{course_id}/{star_id}")
    def links(course_id: int, star_id: int):
        return star_links(course_id, star_id)

    return router
