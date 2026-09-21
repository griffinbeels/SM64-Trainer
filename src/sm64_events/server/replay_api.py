# src/sm64_events/server/replay_api.py
"""Replay REST surface. Same error taxonomy as api.py: LookupError -> 404,
ValueError -> 409, RuntimeError -> 503. Anything ELSE escaping the service
(e.g. codec failure on a corrupt segment) is a genuine 500 — extract.py
guarantees no partial clip file survives those, so a retry is always safe.

Endpoints are sync `def` on purpose: extraction is CPU/GPU-bound and FastAPI
runs sync endpoints in its threadpool — the event loop (poller, websockets)
never blocks."""
from fastapi import APIRouter, Header, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.concurrency import contextmanager_in_threadpool
from pydantic import BaseModel, StrictInt

from sm64_events.replay.virtualmp4 import VirtualMp4
from sm64_events.server.replay_range import serve_media


class RevealBody(BaseModel):
    path: str


class SettingsBody(BaseModel):
    retention_attempts: StrictInt | None = None
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


class ReplayClipResponse(Response):
    """Release the disk lease on success, cancellation and failed sends."""

    def __init__(self, replay, name: str):
        super().__init__()
        self.replay = replay
        self.name = name

    async def __call__(self, scope, receive, send):
        try:
            async with contextmanager_in_threadpool(self.replay.read_clip(self.name)) as path:
                if isinstance(path, VirtualMp4):
                    await serve_media(path, scope, receive, send)
                else:
                    await FileResponse(path, media_type="video/mp4")(scope, receive, send)
        except (LookupError, ValueError, RuntimeError) as error:
            raise _http(error) from error


def _review_routes(router, replay):
    @router.get("/attempts/{attempt_id}/replay/review-state")
    def review_state(attempt_id: int, response: Response):
        try:
            state, token = replay.review_snapshot(attempt_id)
            response.headers["X-Replay-Review-Session"] = token
            return state
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e) from e

    @router.put("/attempts/{attempt_id}/replay/review-state")
    def put_review_state(attempt_id: int, body: dict,
                        x_replay_review_edit: str | None = Header(default=None)):
        try:
            return replay.update_review_state(attempt_id, body, edit=x_replay_review_edit)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e) from e



def _media_routes(router, replay):
    @router.api_route("/replay/clips/{name}", methods=["GET", "HEAD"])
    def clip(name: str):
        return ReplayClipResponse(replay, name)

    # HEAD serves the progress-graph click's existence probe (auto-open the
    # player only when a saved file exists) — FastAPI does NOT add HEAD to
    # GET routes by itself; FileResponse already sends headers-only for HEAD.
    @router.api_route("/replay/saved/{attempt_id}", methods=["GET", "HEAD"])
    def saved(attempt_id: int):
        # Saved clips outlive the buffer: this is how a PB stays watchable
        # in later sessions (view() falls back here when the ring is gone).
        try:
            path = replay.saved_clip_path(attempt_id)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return FileResponse(path, media_type="video/mp4")  # native Range/206

    # Which saved replays are being shrunk right now and how far along each
    # is: the close warning and the recording panel poll this. A read of the
    # worker's own table; it never starts, waits for or touches a job.
    @router.get("/replay/compression")
    def compression():
        return replay.compression_status()



def create_replay_router(replay) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/replay/status")
    def status():
        return replay.status()

    @router.get("/replay/settings")
    def get_settings():
        return replay.settings()

    @router.get("/replay/available")
    def available():
        # Attempt ids replayable right now (saved on disk OR covered by the live
        # ring). The Compare tab calls this on open to list only runs that will
        # actually extract — recomputed each time as the ring shifts.
        return {"available": replay.available_attempt_ids()}

    @router.put("/replay/settings")
    def put_settings(body: SettingsBody):
        try:
            options = ({"retention_attempts": body.retention_attempts}
                       if "retention_attempts" in body.model_fields_set else {})
            return replay.update_settings(body.retention_s,
                                          body.max_buffer_bytes,
                                          body.pre_pad_s, body.post_pad_s, **options)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)

    @router.post("/attempts/{attempt_id}/replay")
    def view(attempt_id: int):
        try:
            return replay.view(attempt_id)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)

    _review_routes(router, replay)

    _media_routes(router, replay)

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
