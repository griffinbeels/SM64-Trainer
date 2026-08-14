"""REST over the Ultimate Sheet library.

Reads are cheap and served straight from the loaded snapshot. The one
expensive route is the refresh, which downloads ~5.6 MB and re-derives 631
ladders, so it runs in a worker thread rather than blocking the event loop --
the poller shares this process and a blocked loop is a dropped star grab."""
from fastapi import APIRouter, Body, HTTPException
from fastapi.concurrency import run_in_threadpool

from sm64_events.library import adoptions as adoptions_store
from sm64_events.library.audit import row_key
from sm64_events.library.source import fetch


def create_library_router(store, overrides=None, adoptions=None,
                          segment_names=None) -> APIRouter:
    """`adoptions` is an `Adoptions` binding the user's assignments to the
    standards store; omit it and the adopt routes are simply not mounted, which
    is what a second broadcast-only instance wants.

    `segment_names` is a zero-arg callable yielding (id, name) pairs for the
    user's LIVE segment definitions -- what `auto_match` pairs an entity-less
    target against per request (round 6: "we should autoassign any segments
    that exist already"). Omit it and no target claims a match."""
    router = APIRouter(prefix="/api/library", tags=["library"])

    @router.get("")
    def library_index():
        return store.index()

    @router.get("/status")
    def library_status():
        return store.status()

    def decorated(target: dict) -> dict:
        # The link button needs three facts per row: its stable key (the
        # adopt endpoints speak row_key, never indices), whether it is
        # already assigned, and whether this instance can adopt at all --
        # a broadcast-only second instance mounts no adoptions store, and a
        # button that 404s is worse than no button. Both full-target doors
        # (numeric index and entity key) serve the same decorated shape, so
        # the page never has to branch on which one it came through.
        # Decorate COPIES: the store's payload is shared across requests and
        # a baked-in `adopted` goes stale on the next assignment.
        assigned = adoptions.rows() if adoptions is not None else {}

        def rows(items):
            return [{**item,
                     "row_key": (key := row_key(target, item["name"],
                                                item["ids"])),
                     "adopted": assigned.get(key)}
                    for item in items]

        matched = None
        if segment_names is not None and not target.get("entity_key"):
            try:
                matched = adoptions_store.auto_match(target["label"],
                                                     segment_names())
            except Exception:          # a degraded db must not sink the page
                matched = None

        return {**target,
                "approaches": rows(target["approaches"]),
                "subsections": rows(target["subsections"]),
                "adoptable": adoptions is not None,
                "matched_segment": matched}

    @router.get("/target/{index}")
    def library_target(index: int):
        target = store.target(index)
        if target is None:
            raise HTTPException(404, "no such target")
        return {"index": index, **decorated(target)}

    @router.get("/entity/{entity_key:path}")
    def library_for_entity(entity_key: str):
        """Every target mapped to one entity -- what the objective card's book
        mark jumps to. An entity with none is a 200 carrying an empty list,
        not a 404: "the community has not timed this" is an answer.

        A LINK counts as a mapping (his rule, 2026-08-14: "once there's a
        connection to a library page, we should be brought there
        automatically"): an entity the sheet never mapped -- every linked
        segment -- resolves to the target(s) whose rows are ADOPTED onto it,
        else to the target it name-matches. `focus_row_key` names the piece
        the link points at (subsection assignments only), so the page can
        open that piece rather than the target's first strategy; a
        whole-target link has no single row to focus."""
        targets = [decorated(target) for target in store.for_entity(entity_key)]
        focus = None
        if not targets and adoptions is not None:
            assigned = adoptions.rows()
            for position, target in enumerate(store.payload["targets"]):
                focus_here, linked_here = None, False
                for collection, kind in (("approaches", "approach"),
                                         ("subsections", "subsection")):
                    for item in target[collection]:
                        key = row_key(target, item["name"], item["ids"])
                        if assigned.get(key) == entity_key:
                            linked_here = True
                            if kind == "subsection" and focus_here is None:
                                focus_here = key
                if linked_here:
                    targets.append(decorated({"index": position, **target}))
                    if focus is None:
                        focus = focus_here
        if (not targets and segment_names is not None
                and entity_key.startswith("segment:")):
            try:
                pairs = list(segment_names())
                for position, target in enumerate(store.payload["targets"]):
                    if target.get("entity_key"):
                        continue
                    hit = adoptions_store.auto_match(target["label"], pairs)
                    if hit and hit["entity"] == entity_key:
                        targets.append(decorated({"index": position, **target}))
            except Exception:      # a degraded db must not sink the page
                pass
        return {"entity_key": entity_key, "targets": targets,
                "focus_row_key": focus}

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
            # Round 8: `by_entity` is the segment editor's reverse view --
            # which library target points at which segment. `matched_by_name`
            # is the same view for the AUTOMATIC association, so the editor
            # can say "already matched by name" instead of a "Not linked"
            # that contradicts the Library page's own chip.
            matched = {}
            if segment_names is not None:
                try:
                    pairs = list(segment_names())
                    for position, target in enumerate(store.payload["targets"]):
                        if target.get("entity_key"):
                            continue
                        hit = adoptions_store.auto_match(target["label"], pairs)
                        if hit:
                            matched.setdefault(hit["entity"],
                                               {"index": position,
                                                "label": target["label"]})
                except Exception:
                    matched = {}
            return {"rows": adoptions.rows(),
                    "ladders": adoptions.ladders(),
                    "by_entity": adoptions.linked_targets(),
                    "matched_by_name": matched}

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

        @router.post("/adopt_target")
        def library_adopt_target(body: dict = Body(...)):
            """Round 7: link a WHOLE target -- every laddered approach
            becomes a strategy on the segment in one request, so a
            half-linked target is unreachable by a failed batch."""
            index, entity = body.get("target_index"), body.get("entity_key")
            if not isinstance(index, int) or not entity:
                raise HTTPException(400,
                                    "target_index and entity_key are required")
            try:
                return adoptions.adopt_target(index, entity)
            except adoptions_store.AdoptionError as err:
                raise HTTPException(409, str(err)) from err

        @router.post("/unadopt_target")
        def library_unadopt_target(body: dict = Body(...)):
            index = body.get("target_index")
            if not isinstance(index, int):
                raise HTTPException(400, "target_index is required")
            try:
                return adoptions.unadopt_target(index)
            except adoptions_store.AdoptionError as err:
                raise HTTPException(409, str(err)) from err

    return router
