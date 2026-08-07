"""REST over the Ultimate Sheet library.

Reads are cheap and served straight from the loaded snapshot. The one
expensive route is the refresh, which downloads ~5.6 MB and re-derives 631
ladders, so it runs in a worker thread rather than blocking the event loop --
the poller shares this process and a blocked loop is a dropped star grab."""
from fastapi import APIRouter, Body, HTTPException
from fastapi.concurrency import run_in_threadpool

from sm64_events.library import adoptions as adoptions_store
from sm64_events.library.source import fetch


def create_library_router(store, overrides=None, adoptions=None) -> APIRouter:
    """`adoptions` is an `Adoptions` binding the user's assignments to the
    standards store; omit it and the adopt routes are simply not mounted, which
    is what a second broadcast-only instance wants."""
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
            result = await run_in_threadpool(store.refresh, fetch, overrides)
        except OSError as err:
            # A download failure is the network's fault, not a bad request --
            # and the caller needs the reason, since "refresh did nothing" and
            # "refresh could not reach Google" look identical otherwise.
            raise HTTPException(503, f"could not fetch the sheet: {err}") from err
        if adoptions is not None and result.get("applied"):
            # Adoptions._sync() merges ladders derived from `store.payload` AT
            # THE TIME of the last adopt/unadopt/load -- a refresh replaces
            # that payload in place without telling adoptions, so a strategy
            # the user assigned keeps grading against the PRE-refresh ladder
            # until the next adopt/unadopt or a restart. Re-loading closes it.
            adoptions.load()
        return result

    if adoptions is not None:
        @router.get("/adoptions")
        def library_adoptions():
            return {"rows": adoptions.rows(),
                    "ladders": adoptions.ladders()}

        @router.post("/adopt")
        def library_adopt(body: dict = Body(...)):
            """Assign one library row to a segment the user built.

            The sheet's movements are finer than our segments and its
            subsections have no segment at all, so the user builds the segment
            and then points a row at it -- we never invent 113 segments."""
            key, entity = body.get("row_key"), body.get("entity_key")
            if not key or not entity:
                raise HTTPException(400, "row_key and entity_key are required")
            try:
                return adoptions.adopt(key, entity)
            except adoptions_store.AdoptionError as err:
                # 409, not 400: the request is well formed and the refusal is
                # about the state of the world, which the caller must be told.
                raise HTTPException(409, str(err)) from err

        @router.post("/unadopt")
        def library_unadopt(body: dict = Body(...)):
            key = body.get("row_key")
            if not key:
                raise HTTPException(400, "row_key is required")
            return adoptions.unadopt(key)

    return router
