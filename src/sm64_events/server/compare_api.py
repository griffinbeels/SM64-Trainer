# src/sm64_events/server/compare_api.py
"""Compare REST surface. Same error taxonomy as api.py/replay_api.py:
LookupError->404, ValueError->409, RuntimeError->503. Import is a polled job
(download + re-encode is long); cache serving uses FileResponse (Range/206)."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel


def _http(e: Exception) -> HTTPException:
    if isinstance(e, LookupError):
        return HTTPException(404, str(e))
    if isinstance(e, ValueError):
        return HTTPException(409, str(e))
    return HTTPException(503, str(e))


class ImportBody(BaseModel):
    entity_key: str
    strat: str
    name: str
    source_kind: str            # 'youtube' | 'file'
    source_ref: str


class EditBody(BaseModel):
    name: str | None = None
    in_frame: int | None = None
    out_frame: int | None = None
    touch: bool | None = None


def create_compare_router(service) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/compare/view")
    def view(entity: str, strat: str | None = None):
        try:
            return service.view(entity, strat)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)

    @router.post("/compare/import")
    def start_import(body: ImportBody):
        try:
            job_id = service.start_import(
                entity_key=body.entity_key, strat=body.strat, name=body.name,
                source_kind=body.source_kind, source_ref=body.source_ref)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"job_id": job_id}

    @router.get("/compare/import/{job_id}")
    def import_status(job_id: str):
        try:
            return service.import_status(job_id)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)

    @router.post("/compare/upload")
    async def upload(entity_key: str, strat: str, name: str, filename: str,
                     request: Request):
        data = await request.body()
        try:
            job_id = service.start_upload(entity_key=entity_key, strat=strat,
                                          name=name, filename=filename, data=data)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"job_id": job_id}

    @router.put("/compare/videos/{comp_id}")
    async def edit(comp_id: int, body: EditBody):
        fields = {k: v for k, v in body.model_dump().items() if v is not None}
        try:
            return await service.update(comp_id, **fields)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)

    @router.delete("/compare/videos/{comp_id}")
    async def remove(comp_id: int):
        try:
            await service.delete(comp_id)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.get("/compare/cache/{name}")
    def cache(name: str):
        try:
            path = service.cache_path(name)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return FileResponse(path, media_type="video/mp4")  # native Range/206

    return router
