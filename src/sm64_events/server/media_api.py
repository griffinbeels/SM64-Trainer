"""Shared recording playback preparation; reads never initiate a download."""
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field


class MediaBody(BaseModel):
    url: str = Field(min_length=1, max_length=4096)
    retry: bool = False


def create_media_router(media):
    router = APIRouter(prefix="/api/media")

    @router.get("/preview")
    def preview(url: str = Query(min_length=1, max_length=4096)):
        try:
            return media.preview(url)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.get("/cache/{name}")
    def cached(name: str):
        try:
            return FileResponse(media.cache_path(name), media_type="video/mp4")
        except LookupError as error:
            raise HTTPException(404, str(error)) from error

    @router.get("")
    def status(url: str = Query(min_length=1, max_length=4096)):
        try:
            return media.status(url)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.post("")
    def prepare(body: MediaBody):
        try:
            return media.start(body.url, retry=body.retry)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    return router
