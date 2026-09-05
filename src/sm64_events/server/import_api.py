"""REST for bringing in times the trainer never watched.

Its own router rather than another block in `server/api.py`, for the reason
`ranks_api` is its own: this needs BOTH the tracker service and the library
snapshot, and mounting it beside them is cheaper than threading the sheet into
the general API router.

TWO DOORS, ONE BACK ROOM -- the card's own box and a runner's Ultimate Sheet
column. Five until round 2 (2026-08-22: the paste and LiveSplit doors, commit
d70721b9 last carries them) and three until round 3 (2026-08-23: the link to
your own sheet -- "too much for us to handle, we need to just get the
Ultimate Sheet parsing as good as possible"; the commit before this one
last carries it). All backlog task 0103. Every door is the same three steps:

  1. READ its source into `ImportCandidate`s, plus the rows it could not use;
  2. LAND them through `TrackerService.import_times`;
  3. ANSWER in one shape -- the service's summary plus `rejected`.

`finish` is steps 2 and 3. A door is step 1 and nothing else, which is what a
third door should cost: a reader that produces candidates, and one `finish`.

The runner LIST is not here: `GET /api/library/runners` already serves it from
the bundled snapshot, which is what lets the picker fill with no network wait
while the import itself reads a fresh fetch.
"""
import asyncio
import logging
from contextlib import contextmanager

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from sm64_events.library import adoptions as adoptions_store
from sm64_events.library.audit import row_key
from sm64_events.library.import_runner import TIMER_MODE, candidates_for
from sm64_events.library.mapping import segment_seed_key
from sm64_events.library.source import fetch
from sm64_events.server.jobs import JobBoard
from sm64_events.server.ranks_api import absorb_after_regrade
from sm64_events.tracking.importing import ImportCandidate

_log = logging.getLogger("sm64.import")

MANUAL_SOURCE = "manual"


@contextmanager
def _service_refusals():
    """The service's two refusals as HTTP answers: a candidate it cannot file
    is the caller's fault (422); no live session is the server's (503)."""
    try:
        yield
    except ValueError as err:
        raise HTTPException(422, str(err)) from err
    except RuntimeError as err:
        raise HTTPException(503, str(err)) from err


class SheetUnreadable(RuntimeError):
    """The live sheet could not be downloaded or read -- the one failure both
    sheet doors phrase the same way, so "could not reach the sheet" and "this
    runner has no times" never look alike to the person waiting."""


class ManualImportBody(BaseModel):
    entity_key: str                     # "star:<course>:<star>"
    strat_tag: str
    time_cs: int
    game_version: str | None = None     # None = grade on the running version


class SheetImportBody(BaseModel):
    runner: str
    # Fetch the LIVE sheet first, so what lands is his most recent entry
    # rather than whatever we last bundled (his instruction, 2026-08-20).
    refresh: bool = True


def _timer_mode_for(service, entity_key: str) -> str:
    """Segments are RTA-only and stars follow the IGT clock. Read off the
    standards store where there is one, so a per-entity clock override is
    honoured rather than second-guessed."""
    ranks = getattr(service, "ranks", None)
    if ranks is not None:
        return ranks.clock_for(entity_key)
    return "rta" if entity_key.startswith("segment:") else "igt"


def sheet_row_placer(service, adoptions):
    """Where a sheet row that is not a star row lands HERE, if anywhere.

    The same three facts the Library tab shows for a row, in the same
    order of authority, so what the import does and what the page says
    cannot disagree:
      1. the row's explicit link to a segment he built (`adoptions`) --
         a subsection's or a movement's, under the strategy the link
         names (a piece's community timing is its Standard);
      2. the name-match an entity-less target gets unasked (round 6:
         "we should autoassign any segments that exist already");
      3. the seed_key behind a sheet Bowser id -- `segment:6` is the
         BitFS pipe entry, the stage's No Reds card, on the machine that
         scraped it; HERE it is whichever `segment_defs` row carries
         `seg:bitfs-pipe`.
    Every answer is a LOCAL id on that row's own clock; a row none of the
    three can place stays in the could-not-use list. Built per request --
    the links and the ids are read at the moment of the call, so a caller
    keeps calling this once per request rather than caching the result.

    Module-level (not a router closure) because `server/scorecard_api.py`'s
    column export vouches for its own non-star rows with these SAME facts,
    in this SAME order of authority, so a row the export prints a time for
    is always a row this import would land too -- one door, not two honest
    copies of the same derivation."""
    database = getattr(service, "db", None)
    if database is None:
        return None
    definitions = database.segment_defs()
    local_ids = {definition["seed_key"]: definition["id"]
                 for definition in definitions if definition.get("seed_key")}
    names = [(definition["id"], definition["name"])
             for definition in definitions]
    linked = adoptions.rows() if adoptions is not None else {}

    def place(target, item, kind):
        entity = linked.get(row_key(target, item["name"], item["ids"]))
        if entity:
            return (entity, _timer_mode_for(service, entity),
                    adoptions_store.strategy_name(
                        target["label"], item["name"], kind=kind))
        if kind != "approach":
            return None
        target_key = target.get("entity_key") or ""
        if not target_key:
            hit = adoptions_store.auto_match(target["label"], names)
            if hit:
                return (hit["entity"], _timer_mode_for(service, hit["entity"]),
                        adoptions_store.strategy_name(
                            target["label"], item["name"]))
            return None
        local = local_ids.get(segment_seed_key(target_key))
        if local is None:
            return None
        local_key = f"segment:{local}"
        return local_key, _timer_mode_for(service, local_key), None
    return place


def held_row_lander(service, library, adoptions):
    """`land(row_keys) -> {landed, released}`: land every HELD TIME on the
    rows just linked, through the ordinary import rule.

    The other half of holding a cell (`import_runner.py`): a row the
    import could not place is kept, and the moment a link gives it a home
    -- the Library's link door, the recorder saving a piece, a whole-target
    link -- the cell goes through `sheet_row_placer` exactly as a fresh
    import would, and lands under the same improvement rule (a cell slower
    than what he already holds there lands nothing). Either way the hold
    is RELEASED: its question has been answered, and a hold that outlived
    its answer would print a stale number in the column. Wired into the
    library router's adopt doors as `on_adopt` (`server/app.py`)."""
    async def land(row_keys) -> dict:
        database = getattr(service, "db", None)
        if database is None or library is None:
            return {"landed": 0, "released": 0}
        wanted = set(row_keys)
        held = [cell for cell in database.held_times()
                if cell["row_key"] in wanted]
        if not held:
            return {"landed": 0, "released": 0}
        place = sheet_row_placer(service, adoptions)
        by_source, placed_keys = {}, set()
        for cell in held:
            target, item, kind = adoptions_store.find_row(
                library.payload, cell["row_key"])
            if target is None:
                continue                 # the sheet no longer has the row
            placed = place(target, item, kind)
            if not placed:
                continue                 # linked elsewhere, or not at all
            entity_key, timer_mode, strategy = placed
            by_source.setdefault(cell["source"], []).append(ImportCandidate(
                entity_key=entity_key,
                strat_tag=strategy or adoptions_store.sheet_strategy(
                    target, item, kind),
                time_cs=int(cell["time_cs"]),
                game_version=cell.get("game_version"),
                platform=cell.get("platform"),
                video=cell.get("video"), row_key=cell["row_key"],
                timer_mode=timer_mode or TIMER_MODE))
            placed_keys.add(cell["row_key"])
        landed = 0
        for source, candidates in by_source.items():
            with _service_refusals():
                summary = await service.import_times(source, candidates)
            landed += summary.get("imported", 0)
        if landed:
            absorb_after_regrade(service)
        released = database.delete_held_times(row_keys=sorted(placed_keys))
        return {"landed": landed, "released": released}
    return land


def create_import_router(service, library=None, overrides=None,
                         adoptions=None) -> APIRouter:
    """`library` is the `LibraryStore`; omit it and the sheet door is simply
    not mounted, the same way the library router drops its adopt routes on a
    broadcast-only instance. `adoptions` is the SAME `Adoptions` the library
    router holds -- the user's row->segment links -- so an import lands a
    linked row exactly where the Library tab says it is linked."""
    router = APIRouter(prefix="/api/import", tags=["import"])

    async def finish(source: str, candidates: list, rejected: list,
                     held=(), **extra) -> dict:
        """Land, hold what could not land, and answer in the one shape
        every door shares -- `rejected` for rows nothing can keep, `held`
        for rows kept aside until a link gives them a home."""
        with _service_refusals():
            summary = await service.import_times(source, candidates,
                                                 held=held)
        if summary.get("imported"):
            # Every rank on every scope just moved for a reason that is not a
            # run -- the same shape as the game-version flip, and handled the
            # same way (`server/mode_api.py`). Without this the next rank fetch
            # reads the climb as earned and fires a full-screen celebration for
            # something he did not just do, which he reads as a bug outright
            # (his ruling, 2026-08-01).
            absorb_after_regrade(service)
        return {"source": source, **summary, "rejected": rejected,
                "held": [{"text": cell["text"], "reason": cell["reason"],
                          "row_key": cell["row_key"]} for cell in held],
                **extra}

    @router.post("/manual")
    async def import_manual(body: ManualImportBody):
        """One time he typed on a star's card."""
        candidate = ImportCandidate(
            entity_key=body.entity_key.strip(), strat_tag=body.strat_tag.strip(),
            time_cs=body.time_cs, game_version=body.game_version)
        return await finish(MANUAL_SOURCE, [candidate], [])

    @router.delete("/{source:path}")
    async def remove_import(source: str):
        """Erase every time one source brought.

        Erased, not marked: "marking them as 'removed' is still worthless.
        Just completely erase them" (2026-08-02). Latest-row-wins means each
        deletion restores whatever that row superseded, exactly as undoing a
        single PB save does."""
        with _service_refusals():
            return {"removed": await service.remove_imported(source)}

    if library is not None:
        _sheet_jobs = JobBoard()

        def _read_sheet(body: SheetImportBody, step=None):
            """Steps 1 of the sheet door, off the event loop: refresh the
            library if asked (~5.6 MB and a full re-derive -- the poller
            shares this process and a blocked loop is a dropped star grab,
            `server/library_api.py` says the same), then read the runner's
            column into candidates and held cells. `step` narrates the real
            boundaries when a job is watching. Returns `(candidates, held,
            refreshed)` -- `refreshed` says the library changed, so the
            caller re-derives the sheet-fitted rank standards ON THE LOOP
            (round 33: "any time we pull in the spreadsheet, we should
            probably do a quick rank standards update")."""
            refreshed = False
            if body.refresh:
                try:
                    refreshed = bool(library.refresh(fetch, overrides, step=step).get("applied"))
                except Exception as err:
                    # BROADER than OSError on purpose, and this was found by
                    # driving the real drawer: a download that SUCCEEDS and
                    # cannot be read raises LookupError ("no sheet named
                    # 'Log'"), and a captive portal or an error page raises
                    # BadZipFile. Neither is an OSError, so the request 500'd
                    # and the panel sat on "Downloading the current sheet…"
                    # with no way out. The sheet is a remote document nobody
                    # here controls, so every way it can fail to be READ is
                    # the same answer to the person waiting — and it must be
                    # an answer, because "could not reach the sheet" and "this
                    # runner has no times" look identical from the outside.
                    _log.warning("sheet refresh failed: %r", err)
                    raise SheetUnreadable(f"could not read the sheet: {err}") from err
            if step:
                step(0.85, f"Matching {body.runner}'s rows to your trainer…")
            candidates, held = candidates_for(library.payload, body.runner,
                                              place=sheet_row_placer(service, adoptions))
            return candidates, held, refreshed

        def _resync_standards():
            """The sheet changed under the store: re-derive its sheet-fitted
            ladders from the new payload (`Adoptions.load`), then absorb the
            re-grade so no celebration fires for a rank he did not run for.
            Called on the event loop, where the standards are read."""
            if adoptions is not None:
                adoptions.load()
            absorb_after_regrade(service)

        @router.post("/sheet")
        async def import_sheet(body: SheetImportBody):
            """A whole runner's Ultimate Sheet column, in one request."""
            try:
                candidates, held, refreshed = await run_in_threadpool(_read_sheet, body)
            except SheetUnreadable as err:
                raise HTTPException(503, str(err)) from err
            if refreshed:
                _resync_standards()
            return await finish(f"sheet:{body.runner}", candidates, [],
                                held=held, sheet_revision=library.revision)

        @router.post("/sheet/job")
        async def start_sheet_import(body: SheetImportBody):
            """The same import, run on a background thread and REPORTED as it
            goes -- round 29, his words: "we should have a similar progress
            bar, like the one we made for the copy sheet column button. I
            want to see my progress as it's happening, otherwise it feels
            laggy and unresponsive." The reading (download, build, fit,
            match) runs on the job's thread with `_read_sheet`'s own steps;
            the landing is `finish`, which must run ON the event loop (the
            service is async and the journal is its), so the thread hands it
            back and waits. The result is byte-identical to the one-request
            door's body."""
            loop = asyncio.get_running_loop()

            async def land(candidates, held, refreshed):
                if refreshed:
                    _resync_standards()
                return await finish(f"sheet:{body.runner}", candidates, [],
                                    held=held, sheet_revision=library.revision)

            def work(step):
                candidates, held, refreshed = _read_sheet(body, step=step)
                step(0.92, f"Landing {len(candidates)} times…")
                landing = asyncio.run_coroutine_threadsafe(
                    land(candidates, held, refreshed), loop)
                try:
                    summary = landing.result()
                except HTTPException as err:
                    raise RuntimeError(err.detail) from err
                return summary, "Done"

            return {"job_id": _sheet_jobs.start("sheet-import", work)}

        @router.get("/sheet/job/{job_id}")
        async def sheet_import_status(job_id: str):
            job = _sheet_jobs.status(job_id)
            if job is None:
                raise HTTPException(404, "no such sheet import job")
            return job

    return router
