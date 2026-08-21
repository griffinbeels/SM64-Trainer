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
import urllib.request
import xml.etree.ElementTree as ElementTree
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from sm64_events.library import sheet_link
from sm64_events.library.build import build
from sm64_events.library.import_runner import candidates_for
from sm64_events.library.source import FETCH_TIMEOUT_S, fetch
from sm64_events.server.ranks_api import absorb_after_regrade
from sm64_events.tracking import import_names, livesplit
from sm64_events.tracking.importing import ImportCandidate

_log = logging.getLogger("sm64.import")

MANUAL_SOURCE = "manual"
PASTE_SOURCE = "paste"
LIVESPLIT_SOURCE = "livesplit"
LINK_SOURCE = "link"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _fetch_bytes(url: str) -> bytes:
    """One GET, with the same timeout the Ultimate Sheet's own fetch uses.

    A separate function so the route can hand it to a threadpool: a sheet is
    megabytes, and the poller shares this process — a blocked event loop is a
    dropped star grab."""
    with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT_S) as reply:
        return reply.read()


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


class LinkImportBody(BaseModel):
    url: str
    # Only needed when the link turns out to be a copy of the Ultimate Sheet:
    # that shape has a column per runner and no way to guess which is yours.
    runner: str = ""
    dry_run: bool = False


class PasteImportBody(BaseModel):
    text: str
    # Read the block and report what it would do WITHOUT writing anything.
    # A block of a few hundred lines is exactly where a silent misread is
    # expensive, so the preview is the default the UI uses.
    dry_run: bool = False


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

    def build_catalog():
        """Every name this instance will answer to, newest-specific LAST.

        Order is precedence (`Catalog.add_target`, first writer wins): the
        game's own star names cannot be redirected by a sheet label or by a
        segment somebody named after a star."""
        catalog = import_names.star_catalog()
        if library is not None:
            import_names.sheet_catalog(library.payload, catalog)
        database = getattr(service, "db", None)
        if database is not None:
            import_names.segment_catalog(database.segment_defs(), catalog)
        return catalog

    def timer_mode_for(entity_key: str) -> str:
        """Segments are RTA-only and stars follow the IGT clock. Read off the
        standards store where there is one, so a per-entity clock override is
        honoured rather than second-guessed."""
        ranks = getattr(service, "ranks", None)
        if ranks is not None:
            return ranks.clock_for(entity_key)
        return "rta" if entity_key.startswith("segment:") else "igt"

    @router.post("/paste")
    async def import_paste(body: PasteImportBody):
        """A block of times, in whatever the player already has them written
        in. `dry_run` reports what WOULD land, which is what the UI shows
        before anything is written."""
        candidates, unresolved = import_names.parse_block(
            body.text, build_catalog(), timer_mode_for=timer_mode_for)
        rejected = [{"line": item.line, "text": item.text,
                     "reason": item.reason} for item in unresolved]
        if body.dry_run:
            # The SAME planner the real run uses (`_plan_import`), so the
            # preview answers the question the button then performs — a second
            # implementation of the arithmetic is exactly what would make it
            # not worth trusting.
            try:
                summary = service.preview_import(candidates)
            except ValueError as err:
                raise HTTPException(422, str(err)) from err
            except RuntimeError as err:
                raise HTTPException(503, str(err)) from err
            return {"source": PASTE_SOURCE, **summary,
                    "rejected": rejected, "dry_run": True}
        summary = await land(PASTE_SOURCE, candidates)
        return {**summary, "rejected": rejected, "dry_run": False}

    @router.post("/link")
    async def import_link(body: LinkImportBody):
        """Import from a link to somebody's own spreadsheet.

        The workbook says which shape it is: a copy of the Ultimate Sheet is
        read by the real reader and a named runner's column extracted; any
        other grid becomes lines and goes through the paste parser, so a
        personal `star | time | strat` sheet needs no format of its own.

        Only Google Sheets links are fetched — the SERVER does the fetching,
        so "any URL" would mean "any URL reachable from this machine"."""
        try:
            url = sheet_link.export_url(sheet_link.sheet_id_from(body.url))
        except ValueError as err:
            raise HTTPException(422, str(err)) from err
        try:
            data = await run_in_threadpool(_fetch_bytes, url)
        except Exception as err:                       # noqa: BLE001
            _log.warning("sheet link fetch failed: %r", err)
            raise HTTPException(
                503, f"could not read that sheet: {err}. Is it shared with "
                     "'anyone with the link'?") from err

        shape = "grid"
        if sheet_link.is_ultimate_shaped(data):
            shape = "ultimate"
            if not body.runner:
                raise HTTPException(
                    422, "that is a copy of the Ultimate Sheet — say which "
                         "runner's column to take")
            payload = build(data, _now_iso(), overrides)
            candidates, dropped = candidates_for(payload, body.runner)
            # The drop TALLY rather than a row apiece: this path counts by
            # kind (`library/import_runner.py`), so inventing one line per
            # dropped row would be reporting detail we do not have.
            rejected = [{"line": 0, "text": f"{count} {kind}", "reason": kind}
                        for kind, count in sorted(dropped.items()) if count]
        else:
            candidates, unresolved = sheet_link.candidates_from_grid(
                data, build_catalog(), timer_mode_for=timer_mode_for)
            rejected = [{"line": item.line, "text": item.text,
                         "reason": item.reason} for item in unresolved]

        if body.dry_run:
            try:
                summary = service.preview_import(candidates)
            except ValueError as err:
                raise HTTPException(422, str(err)) from err
            except RuntimeError as err:
                raise HTTPException(503, str(err)) from err
            return {"source": LINK_SOURCE, **summary, "shape": shape,
                    "rejected": rejected, "dry_run": True}
        summary = await land(LINK_SOURCE, candidates)
        return {**summary, "shape": shape, "rejected": rejected,
                "dry_run": False}

    @router.post("/livesplit")
    async def import_livesplit(request: Request,
                               dry_run: bool = False,
                               strategy: str = ""):
        """A LiveSplit `.lss`, posted as the RAW request body.

        Raw bytes rather than a multipart form for the reason
        `server/compare_api.py`'s upload already gives: multipart would add
        `python-multipart` to what the frozen exe ships, for one field.

        Its golds are REAL-TIME stretches of the run, so they land on
        segments the player built here, matched by name — never on a star,
        whose bests are Usamune IGT."""
        data = await request.body()
        if not data:
            raise HTTPException(422, "no splits file in the request body")
        try:
            candidates, unresolved = livesplit.candidates_for(
                data, build_catalog(), strategy=strategy or None)
        except ElementTree.ParseError as err:
            # "Nothing landed" and "that was not a splits file" look identical
            # from the outside, and only one is worth acting on.
            raise HTTPException(
                422, f"that does not read as a LiveSplit splits file: {err}"
            ) from err
        rejected = [{"line": item.line, "text": item.text,
                     "reason": item.reason} for item in unresolved]
        if dry_run:
            try:
                summary = service.preview_import(candidates)
            except ValueError as err:
                raise HTTPException(422, str(err)) from err
            except RuntimeError as err:
                raise HTTPException(503, str(err)) from err
            return {"source": LIVESPLIT_SOURCE, **summary,
                    "rejected": rejected, "dry_run": True}
        summary = await land(LIVESPLIT_SOURCE, candidates)
        return {**summary, "rejected": rejected, "dry_run": False}

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
