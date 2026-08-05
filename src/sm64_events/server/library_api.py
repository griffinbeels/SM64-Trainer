"""REST over the Ultimate Sheet library.

Reads are cheap and served straight from the loaded snapshot. The one
expensive route is the refresh, which downloads ~5.6 MB and re-derives 631
ladders, so it runs in a worker thread rather than blocking the event loop --
the poller shares this process and a blocked loop is a dropped star grab."""
from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from sm64_events.library.source import fetch


def create_library_router(store, overrides=None) -> APIRouter:
    router = APIRouter(prefix="/api/library", tags=["library"])

    @router.get("")
    def library_index():
        return store.index()

    @router.get("/status")
    def library_status():
        return store.status()

    @router.get("/target/{index}")
    def library_target(index: int):
        target = store.target(index)
        if target is None:
            raise HTTPException(404, "no such target")
        return {"index": index, **target}

    @router.get("/entity/{entity_key:path}")
    def library_for_entity(entity_key: str):
        """Every target mapped to one entity -- what the objective card's book
        mark jumps to. An entity with none is a 200 carrying an empty list,
        not a 404: "the community has not timed this" is an answer."""
        return {"entity_key": entity_key, "targets": store.for_entity(entity_key)}

    @router.get("/runners")
    def library_runners():
        return {"runners": store.runners()}

    @router.get("/runner/{name:path}")
    def library_runner(name: str):
        return store.runner(name)

    @router.post("/refresh")
    async def library_refresh():
        try:
            return await run_in_threadpool(store.refresh, fetch, overrides)
        except OSError as err:
            # A download failure is the network's fault, not a bad request --
            # and the caller needs the reason, since "refresh did nothing" and
            # "refresh could not reach Google" look identical otherwise.
            raise HTTPException(503, f"could not fetch the sheet: {err}") from err

    return router
