# src/sm64_events/server/compilation_api.py
"""Failure-compilation REST surface. Same taxonomy as replay_api.py:
LookupError->404, ValueError->409, RuntimeError->503. Generation is a polled
job (dozens of ffmpeg cuts + a concat pass). Reveal reuses /api/replay/reveal
— the output lives under save_root, so no compilation-specific reveal exists.

Kind-dispatched body ({star:{...}} XOR {segment_id}) matches the app's other
star<->segment endpoints, so one path serves both and can't drift."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from sm64_events.tracking.compilation import EntityRef


def _http(e: Exception) -> HTTPException:
    if isinstance(e, LookupError):
        return HTTPException(404, str(e))
    if isinstance(e, ValueError):
        return HTTPException(409, str(e))
    return HTTPException(503, str(e))


class StarRef(BaseModel):
    course_id: int
    star_id: int


class CompileBody(BaseModel):
    star: StarRef | None = None
    segment_id: int | None = None
    x_before: float = 5.0
    y_after: float = 3.0


def create_compilation_router(service) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.post("/compilation")
    def start(body: CompileBody):
        if (body.star is None) == (body.segment_id is None):
            raise HTTPException(409, "provide exactly one of star or segment_id")
        identity = (EntityRef(segment_id=body.segment_id) if body.star is None
                    else EntityRef(course_id=body.star.course_id,
                                   star_id=body.star.star_id))
        try:
            job_id = service.start(identity, body.x_before, body.y_after)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"job_id": job_id}

    @router.get("/compilation/{job_id}")
    def status(job_id: str):
        try:
            return service.status(job_id)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)

    return router
