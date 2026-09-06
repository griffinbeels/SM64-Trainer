"""Attempt recording associations, available without a capture device or encoder."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from sm64_events.core.recording_url import validate_recording_url


class RecordingBody(BaseModel):
    url: str | None = Field(max_length=4096)
    expected_revision: int | None = Field(default=None, ge=0)


def _error(error):
    status = 404 if isinstance(error, LookupError) else (
        409 if isinstance(error, ValueError) else 503)
    return HTTPException(status, str(error))


def create_recording_router(tracker):
    router = APIRouter(prefix="/api/attempts")

    @router.get("/{attempt_id}/recording")
    def get_link(attempt_id: int):
        try:
            return tracker.recording_link(attempt_id)
        except (LookupError, ValueError, RuntimeError) as error:
            raise _error(error) from error

    @router.put("/{attempt_id}/recording")
    async def put_link(attempt_id: int, body: RecordingBody):
        try:
            url = validate_recording_url(body.url)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        try:
            return await tracker.set_recording_link(
                attempt_id, url, expected_revision=body.expected_revision)
        except (LookupError, ValueError, RuntimeError) as error:
            raise _error(error) from error

    return router
