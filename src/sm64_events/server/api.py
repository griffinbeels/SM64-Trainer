# src/sm64_events/server/api.py
"""REST command/query surface for the tracker UI (spec §7).

Error taxonomy (service exception types are part of the contract):
LookupError -> 404 (no such attempt), ValueError -> 409 (exists but not
saveable: bad mode, non-success, cleared, missing clock, or — for pb/undo —
not the current PB), RuntimeError -> 503 (database unavailable / degraded
mode)."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from sm64_events.links import star_links
from sm64_events.stats.registry import (registry_meta, selection_id,
                                        selection_order)
from sm64_events.tracking.segments import vocab
from sm64_events.tracking.views import (build_route_view, build_run_history,
                                        build_run_view, build_session_view)


class TargetBody(BaseModel):
    kind: str = "star"
    course_id: int | None = None
    star_id: int | None = None
    segment_id: int | None = None
    strat_tag: str | None = None


class ClearBody(BaseModel):
    reason: str | None = None


class PbBody(BaseModel):
    attempt_id: int
    timer_mode: str


class WipeBody(BaseModel):
    kind: str                      # "star" | "segment" | "all"
    course_id: int | None = None
    star_id: int | None = None
    segment_id: int | None = None
    scope: str = "session"         # "session" (active) | "lifetime"


class SessionBody(BaseModel):
    label: str | None = None


class ContinueBody(BaseModel):
    session_id: int


class StratBody(BaseModel):
    course_id: int = Field(ge=0)
    star_id: int = Field(ge=0)
    strat_tag: str | None = None


class StatSelection(BaseModel):
    key: str
    params: dict = {}


class StatMenuBody(BaseModel):
    selections: list[StatSelection]


class Marker(BaseModel):
    frames: int = Field(ge=0)
    label: str

    @field_validator("label")
    @classmethod
    def _trim_label(cls, v: str) -> str:
        v = v.strip()
        if not 1 <= len(v) <= 60:
            raise ValueError("label must be 1-60 chars after trimming")
        return v


class MarkersBody(BaseModel):
    segment_id: int | None = None
    course_id: int | None = Field(default=None, ge=0)
    star_id: int | None = Field(default=None, ge=0)
    strat_tag: str | None = None
    markers: list[Marker] = Field(max_length=30)


class SegmentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    start_triggers: list[dict]
    end_triggers: list[dict]
    guards: list[dict] = []
    enabled: bool = True


class SegmentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    start_triggers: list[dict] | None = None
    end_triggers: list[dict] | None = None
    guards: list[dict] | None = None
    enabled: bool | None = None


class RouteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    steps: list[dict]


class RoutePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    steps: list[dict] | None = None


class ImportBody(BaseModel):
    payload: dict


class RunStartBody(BaseModel):
    route_id: int

class RunSettingsBody(BaseModel):
    start_offset_ms: int


def _http(e: Exception) -> HTTPException:
    if isinstance(e, LookupError):
        return HTTPException(404, str(e))
    if isinstance(e, ValueError):
        return HTTPException(409, str(e))
    return HTTPException(503, str(e))  # RuntimeError: degraded mode


def create_api_router(service) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/session")
    def session(clock: str = "igt", scope: str = "session"):
        if clock not in ("igt", "rta"):
            raise HTTPException(422, "clock must be igt or rta")
        if scope not in ("session", "lifetime"):
            raise HTTPException(422, "scope must be session or lifetime")
        if service.db is None:
            raise HTTPException(503, "database unavailable")
        return build_session_view(service.db, service, clock=clock, scope=scope)

    @router.post("/session/new")
    async def session_new(body: SessionBody):
        try:
            sid = await service.new_session(label=body.label)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"session_id": sid}

    @router.post("/session/continue")
    async def session_continue(body: ContinueBody):
        try:
            sid = await service.continue_session(body.session_id)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"session_id": sid}

    @router.delete("/session/{session_id}")
    async def session_delete(session_id: int):
        try:
            await service.delete_session(session_id)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.get("/segments")
    def segments_list():
        """List all segment definitions; 503 in degraded mode."""
        if service.db is None:
            raise HTTPException(503, "database unavailable")
        return service.db.segment_defs()

    @router.get("/segments/vocab")
    def segments_vocab():
        """Return trigger/guard/level vocabulary for the builder GUI.

        Route is declared BEFORE /segments/{segment_id} so FastAPI matches
        the literal 'vocab' path before treating it as an id (declaration
        order wins — fastapi-patterns)."""
        return vocab()

    @router.post("/segments")
    async def create_segment(body: SegmentBody):
        try:
            sid = await service.create_segment(body.model_dump())
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True, "id": sid}

    @router.put("/segments/{segment_id}")
    async def update_segment(segment_id: int, body: SegmentPatch):
        try:
            # exclude None (unset fields), but keep False/[] (explicit sets)
            patch = {k: v for k, v in body.model_dump().items()
                     if v is not None}
            await service.update_segment(segment_id, patch)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.delete("/segments/{segment_id}")
    async def delete_segment(segment_id: int):
        try:
            await service.delete_segment(segment_id)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    # routes — literal '/routes/import' declared before '/routes/{route_id}'
    # so the path segment is never parsed as an id (declaration order wins —
    # fastapi-patterns; mirrors /segments/vocab).
    @router.get("/routes")
    def routes_list():
        if service.db is None:
            raise HTTPException(503, "database unavailable")
        return service.db.routes()

    @router.post("/routes")
    async def create_route(body: RouteBody):
        try:
            rid = await service.create_route(body.model_dump())
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True, "id": rid}

    @router.post("/routes/import")
    async def import_route(body: ImportBody, dry_run: bool = False):
        try:
            return await service.import_route(body.payload, dry_run=dry_run)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)

    @router.get("/routes/{route_id}")
    def route_view(route_id: int):
        if service.db is None:
            raise HTTPException(503, "database unavailable")
        try:
            return build_route_view(service.db, route_id)
        except (LookupError, ValueError) as e:
            raise _http(e)

    @router.get("/routes/{route_id}/export")
    def export_route(route_id: int):
        if service.db is None:
            raise HTTPException(503, "database unavailable")
        try:
            return service.export_route(route_id)
        except (LookupError, ValueError) as e:
            raise _http(e)

    @router.put("/routes/{route_id}")
    async def update_route(route_id: int, body: RoutePatch):
        try:
            patch = {k: v for k, v in body.model_dump().items() if v is not None}
            await service.update_route(route_id, patch)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.delete("/routes/{route_id}")
    async def delete_route(route_id: int):
        try:
            await service.delete_route(route_id)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.post("/run/start")
    async def run_start(body: RunStartBody):
        try:
            await service.start_run(body.route_id)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.post("/run/end")
    async def run_end():
        try:
            await service.end_run()
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.get("/run/history")
    def run_history(route_id: int | None = None):
        if service.db is None:
            raise HTTPException(503, "database unavailable")
        return build_run_history(service.db, route_id=route_id)

    @router.get("/run/settings")
    def run_settings_get():
        if service.db is None:
            raise HTTPException(503, "database unavailable")
        return service.run_settings()

    @router.put("/run/settings")
    async def run_settings_put(body: RunSettingsBody):
        try:
            return await service.update_run_settings(body.model_dump())
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)

    @router.get("/run")
    def run_state():
        if service.db is None:
            raise HTTPException(503, "database unavailable")
        return build_run_view(service.db, service)

    @router.post("/target")
    async def target(body: TargetBody):
        """Set the active practice target.

        kind="segment": requires segment_id; targeting a DISABLED definition
        is allowed — disabling pauses detection without forfeiting the target;
        the section simply accrues no attempts.
        kind="star" (default): requires course_id and star_id.
        """
        try:
            if body.kind == "segment":
                if body.segment_id is None:
                    raise ValueError("segment target needs segment_id")
                await service.set_target_segment(body.segment_id, body.strat_tag)
            else:
                if body.course_id is None or body.star_id is None:
                    raise ValueError("star target needs course_id and star_id")
                await service.set_target(body.course_id, body.star_id,
                                         body.strat_tag)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.post("/strat")
    async def strat(body: StratBody):
        try:
            await service.set_strat(body.course_id, body.star_id, body.strat_tag)
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

    @router.post("/pb/undo")
    async def undo_pb(body: PbBody):
        try:
            return await service.undo_pb(body.attempt_id, body.timer_mode)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)

    @router.post("/wipe")
    async def wipe(body: WipeBody):
        try:
            return await service.wipe_data(
                body.kind, course_id=body.course_id, star_id=body.star_id,
                segment_id=body.segment_id, scope=body.scope)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)

    @router.get("/stats/registry")
    def stats_registry():
        return registry_meta()

    @router.put("/statmenu")
    def put_statmenu(body: StatMenuBody):
        if service.db is None:
            raise HTTPException(503, "database unavailable")
        seen: set[str] = set()
        deduped = []
        for s in body.selections:
            sid = selection_id(s.key, s.params)
            if sid not in seen:
                seen.add(sid)
                deduped.append(s.model_dump())
        deduped.sort(key=lambda s: selection_order(s["key"], s.get("params")))
        service.db.set_state("stat_menu", deduped)
        return {"ok": True}

    @router.put("/markers")
    async def put_markers(body: MarkersBody):
        """Replace the marker list for one identity+strategy (spec §3).

        Identity is either segment_id XOR (course_id + star_id) — providing
        both or neither raises 409.  Key format: seg:{id}:{strat} for segment
        markers, {course}:{star}:{strat} for star markers.

        async + no awaits: the read-modify-write on the timeline_markers
        dict is atomic on the event loop (same pattern as set_target's
        strategies RMW in tracking/service.py)."""
        if service.db is None:
            raise HTTPException(503, "database unavailable")
        has_seg = body.segment_id is not None
        has_star = body.course_id is not None and body.star_id is not None
        if has_seg and has_star:
            raise HTTPException(409, "provide segment_id OR course_id+star_id, not both")
        if not has_seg and not has_star:
            raise HTTPException(409, "provide segment_id OR course_id+star_id")
        if has_seg:
            key = f"seg:{body.segment_id}:{body.strat_tag or ''}"
        else:
            key = f"{body.course_id}:{body.star_id}:{body.strat_tag or ''}"
        state = service.db.get_state("timeline_markers", {})
        state[key] = sorted(
            ({"frames": m.frames, "label": m.label} for m in body.markers),
            key=lambda m: m["frames"])
        service.db.set_state("timeline_markers", state)
        return {"ok": True}

    @router.get("/links/{course_id}/{star_id}")
    def links(course_id: int, star_id: int):
        return star_links(course_id, star_id)

    return router
