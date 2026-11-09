"""Course portraits over HTTP: the listing the UI asks for, and the pictures themselves.

`GET /api/icons/courses` answers `{courses: {stem: tail}, from_rom, region}`; the UI loads each portrait from
`/api/icons/course/<tail>` (ui/entities.js is the one place that builds that URL). A tail is either
`<rom key>/<stem>.png`, a picture cut from the player's own ROM (sm64_events/course_art.py), or
`standin/<stem>.svg`, our plain tile, until a ROM is known, or `shipped/<file>`, a picture the Trainer ships
itself (course_art.SHIPPED: Griffin's own screenshots of Rainbow Ride and Tick Tock Clock). The first listing starts the watcher; when the
pictures arrive it broadcasts `course_art_changed` and the page asks again.

`art=None` (tests, fixtures, a broadcast-only instance) serves the stand-ins and never looks for a ROM.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response

from sm64_events.core.events import Event
from sm64_events.course_art import (PORTRAITS, SHIPPED_KEY, STANDIN, CourseArt, shipped_file, shipped_listing,
                                    standin_svg)

# A ROM picture's URL names the ROM's hash, so it never changes under a page that cached it.
_IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}


def _standin_listing() -> dict:
    courses = {p.stem: f"{STANDIN}/{p.stem}.svg" for p in PORTRAITS} | shipped_listing()
    return {"courses": courses, "from_rom": False, "region": None}


def create_course_art_router(art: CourseArt | None, broadcaster=None) -> APIRouter:
    router = APIRouter(prefix="/api")

    def announce(loop: asyncio.AbstractEventLoop) -> None:
        if broadcaster is None:
            return
        event = Event(type="course_art_changed", frame=0, timestamp_utc=datetime.now(UTC),
                      payload=art.listing())
        asyncio.run_coroutine_threadsafe(broadcaster.publish(event), loop)

    @router.get("/icons/courses")
    async def course_icons():
        """Every course portrait's URL tail. Never waits for the ROM: the first ask starts the watcher, and a
        stand-in answers until the pictures are cut."""
        if art is None:
            return _standin_listing()
        loop = asyncio.get_running_loop()
        art.start(lambda: announce(loop))
        return art.listing()

    @router.get("/icons/course/{key}/{name}")
    def course_picture(key: str, name: str):
        if key == STANDIN:
            stem = name.removesuffix(".svg")
            svg = standin_svg(stem) if name.endswith(".svg") else None
            if svg is None:
                raise HTTPException(404, "no such course picture")
            return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "no-cache"})
        if key == SHIPPED_KEY:
            shipped = shipped_file(name)
            if shipped is None:
                raise HTTPException(404, "no such course picture")
            return FileResponse(shipped, media_type="image/webp", headers={"Cache-Control": "no-cache"})
        path = art.file(key, name) if art is not None else None
        if path is None:
            raise HTTPException(404, "no such course picture")
        return FileResponse(path, media_type="image/png", headers=_IMMUTABLE)

    return router
