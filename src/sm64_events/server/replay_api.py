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
from pydantic import BaseModel


class RevealBody(BaseModel):
    path: str


class SettingsBody(BaseModel):
    retention_s: float | None = None   # null/omitted = keep the whole session
    max_buffer_bytes: int
    pre_pad_s: float | None = None     # omitted = keep current
    post_pad_s: float | None = None    # omitted = keep current


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

    @router.get("/replay/settings")
    def get_settings():
        return replay.settings()

    @router.put("/replay/settings")
    def put_settings(body: SettingsBody):
        try:
            return replay.update_settings(body.retention_s,
                                          body.max_buffer_bytes,
                                          body.pre_pad_s, body.post_pad_s)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)

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

    @router.get("/replay/saved/{attempt_id}")
    def saved(attempt_id: int):
        # Saved clips outlive the buffer: this is how a PB stays watchable
        # in later sessions (view() falls back here when the ring is gone).
        try:
            path = replay.saved_clip_path(attempt_id)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return FileResponse(path, media_type="video/mp4")  # native Range/206

    @router.post("/attempts/{attempt_id}/replay/save")
    def save(attempt_id: int):
        try:
            return replay.save(attempt_id)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)

    @router.post("/replay/reveal")
    def reveal(body: RevealBody):
        try:
            replay.reveal(body.path)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    return router
