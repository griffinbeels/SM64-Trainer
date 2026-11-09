"""The Ghost Lab's ROM store over HTTP, for the one-time pick (ui/components/rompick.js).

    GET  /api/scene-lab/roms                  -> {regions: {us|jp: {found, label, version, source, known_build, reason}}}
    POST /api/scene-lab/roms/pick[?region=us]  body: the chosen file's bytes -> {stored: "us"|"jp", regions: ...}

GET first copies the cartridge Project64 is running, when it is a Usamune ROM (`RomStore.copy_from_game`: once
per process and cartridge, so a page that polls costs one header read per ask). POST keeps a chosen file and
answers 400 with one plain sentence when it is neither Usamune ROM. The file reaches only this loopback
server and the private copy; nothing is sent anywhere else. The lab mount includes this router
(`app.include_router(create_rom_router())`); `store` is for tests.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from sm64_events.scenelab.roms import CARTRIDGES, ROM_BYTES, RomRefused, RomStore, default_store

MAX_BODY = 2 * ROM_BYTES      # the largest body read before the file is refused as too large


def create_rom_router(store: RomStore | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/scene-lab")

    def current() -> RomStore:
        return store if store is not None else default_store()

    @router.get("/roms")
    def roms():
        rom_store = current()
        rom_store.copy_from_game()
        return rom_store.status()

    @router.post("/roms/pick")
    async def pick(request: Request, region: str | None = None):
        if region is not None and region not in CARTRIDGES:
            return JSONResponse(status_code=400, content={"detail": f"unknown region {region!r}"})
        data = bytearray()
        async for chunk in request.stream():
            data += chunk
            if len(data) > MAX_BODY:
                return JSONResponse(status_code=413, content={
                    "detail": "This file is too large to be a ROM, so choose the ROM file you play in Project64."})
        rom_store = current()
        try:
            stored = await run_in_threadpool(rom_store.adopt, bytes(data), region=region)
        except RomRefused as refused:
            return JSONResponse(status_code=400, content={"detail": str(refused)})
        return {"stored": stored, **await run_in_threadpool(rom_store.status)}

    return router
