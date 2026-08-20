"""REST for bringing in times the trainer never watched.

Its own router rather than another block in `server/api.py`, for the reason
`ranks_api` is its own: this needs BOTH the tracker service and the library
snapshot, and mounting it beside them is cheaper than threading the sheet into
the general API router.

Two doors, one back room. `POST /import/manual` lands a single time he typed;
`POST /import/sheet` lands a whole runner column. Both build
`tracking/importing.ImportCandidate`s and go through `TrackerService.
import_times`, so the improvement rule, the provenance and the version
attribution are decided in exactly one place.

The runner LIST is not here: `GET /api/library/runners` already serves it from
the bundled snapshot, which is what lets the picker fill with no network wait
while the import itself reads a fresh fetch.
"""
import logging

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from sm64_events.library.import_runner import candidates_for
from sm64_events.library.source import fetch
from sm64_events.server.ranks_api import absorb_after_regrade
from sm64_events.tracking.importing import ImportCandidate

_log = logging.getLogger("sm64.import")

MANUAL_SOURCE = "manual"


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


def create_import_router(service, library=None, overrides=None) -> APIRouter:
    """`library` is the `LibraryStore`; omit it and the sheet door is simply
    not mounted, the same way the library router drops its adopt routes on a
    broadcast-only instance."""
    router = APIRouter(prefix="/api/import", tags=["import"])

    async def land(source: str, candidates: list) -> dict:
        try:
            summary = await service.import_times(source, candidates)
        except ValueError as err:
            raise HTTPException(422, str(err)) from err
        except RuntimeError as err:
            raise HTTPException(503, str(err)) from err
        if summary.get("imported"):
            # Every rank on every scope just moved for a reason that is not a
            # run -- the same shape as the game-version flip, and handled the
            # same way (`server/mode_api.py`). Without this the next rank fetch
            # reads the climb as earned and fires a full-screen celebration for
            # something he did not just do, which he reads as a bug outright
            # (his ruling, 2026-08-01).
            absorb_after_regrade(service)
        return summary

    @router.post("/manual")
    async def import_manual(body: ManualImportBody):
        return await land(MANUAL_SOURCE, [ImportCandidate(
            entity_key=body.entity_key.strip(), strat_tag=body.strat_tag.strip(),
            time_cs=body.time_cs, game_version=body.game_version)])

    @router.delete("/{source:path}")
    async def remove_import(source: str):
        """Erase every time one source brought.

        Erased, not marked: "marking them as 'removed' is still worthless.
        Just completely erase them" (2026-08-02). Latest-row-wins means each
        deletion restores whatever that row superseded, exactly as undoing a
        single PB save does."""
        try:
            return {"removed": service.remove_imported(source)}
        except RuntimeError as err:
            raise HTTPException(503, str(err)) from err

    if library is not None:
        @router.post("/sheet")
        async def import_sheet(body: SheetImportBody):
            if body.refresh:
                try:
                    # ~5.6 MB and a full re-derive, so off the event loop: the
                    # poller shares this process and a blocked loop is a
                    # dropped star grab (`server/library_api.py` says the same).
                    await run_in_threadpool(library.refresh, fetch, overrides)
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
                    raise HTTPException(
                        503, f"could not read the sheet: {err}") from err
            candidates, rejected = candidates_for(library.payload, body.runner)
            summary = await land(f"sheet:{body.runner}", candidates)
            return {**summary, "rejected": rejected,
                    "sheet_revision": library.revision}

    return router
