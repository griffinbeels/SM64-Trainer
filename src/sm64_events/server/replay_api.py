# src/sm64_events/server/replay_api.py
"""Replay REST surface. Same error taxonomy as api.py: LookupError -> 404,
ValueError -> 409, RuntimeError -> 503. Anything ELSE escaping the service
(e.g. codec failure on a corrupt segment) is a genuine 500 — extract.py
guarantees no partial clip file survives those, so a retry is always safe.

Endpoints are sync `def` on purpose: extraction is CPU/GPU-bound and FastAPI
runs sync endpoints in its threadpool — the event loop (poller, websockets)
never blocks."""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse


def _http(e: Exception) -> HTTPException:
    if isinstance(e, LookupError):
        return HTTPException(404, str(e))
    if isinstance(e, ValueError):
        return HTTPException(409, str(e))
    return HTTPException(503, str(e))


def create_replay_router(replay) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/replay/status")
    def status():
        return replay.status()

    @router.post("/attempts/{attempt_id}/replay")
    def view(attempt_id: int):
        try:
            return replay.view(attempt_id)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)

    @router.get("/replay/clips/{name}")
    def clip(name: str):
        try:
            path = replay.clip_path(name)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return FileResponse(path, media_type="video/mp4")  # native Range/206

    @router.post("/attempts/{attempt_id}/replay/save")
    def save(attempt_id: int):
        try:
            return replay.save(attempt_id)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)

    return router
