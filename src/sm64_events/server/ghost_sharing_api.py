"""Bulk native ghost sharing: explicit upload/export jobs, one file per collection."""
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from sm64_events.ghost.portable import MAX_PACKAGE_BYTES, checked_author
from sm64_events.ghost.sharing_catalog import checked_selection
from sm64_events.server.jobs import JobBoard


def _provider(session_of):
    try:
        provider = session_of()._sharing()
        if provider is None:
            raise RuntimeError("Ghost sharing is available after the trainer starts recording.")
        return provider
    except RuntimeError as error:
        raise HTTPException(503, str(error)) from error


def _mount_import(router, sharing, jobs):
    @router.post("/ghost/library/import")
    async def import_collection(request: Request):
        provider = sharing()
        folder = provider.root / "uploads"
        await run_in_threadpool(folder.mkdir, parents=True, exist_ok=True)
        source = folder / f"{uuid4().hex}.upload"
        size = 0
        try:
            with source.open("wb") as stream:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_PACKAGE_BYTES:
                        raise HTTPException(413, "This ghost collection is too large; split it into smaller collections.")
                    await run_in_threadpool(stream.write, chunk)
            if not size:
                raise HTTPException(422, "Choose a ghost collection to import.")
        except Exception:
            source.unlink(missing_ok=True)
            raise

        def work(step):
            try:
                result = provider.import_file(source, step)
                return result, f"Imported {result['imported']} ghosts; {result['duplicates']} were already in your library."
            finally:
                source.unlink(missing_ok=True)

        return {"job_id": jobs.start("ghost-import", work)}


def _mount_race(router, session_of, sharing):
    @router.post("/ghost/library/prepare-race")
    async def prepare_race(body: dict):
        session = session_of()
        if (set(body) != {"ghosts", "target"} or not isinstance(body["target"], dict)
                or body["target"] != session._read_target()):
            raise HTTPException(422, "The practice target changed; choose ghosts for the current target.")
        try:
            choice = await run_in_threadpool(sharing().prepare_race, body["ghosts"], body["target"])
        except (LookupError, ValueError, OSError) as error:
            raise HTTPException(422, str(error)) from error
        return {"ghosts": choice, "target": body["target"]}


def mount_collection_routes(router, session_of):
    jobs = JobBoard()

    def sharing():
        return _provider(session_of)

    @router.get("/ghost/library")
    def library():
        return sharing().catalog()

    @router.post("/ghost/library/export")
    def export(body: dict):
        provider = sharing()
        try:
            if set(body) != {"selected", "author"}:
                raise ValueError("Choose ghosts and enter the name to credit on your export.")
            checked_selection(body["selected"])
            checked_author(body["author"])
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

        def work(step):
            result = provider.export(body["selected"], body["author"], step)
            return result, f"Exported {result['exported']} ghosts in one collection."

        return {"job_id": jobs.start("ghost-export", work)}

    _mount_import(router, sharing, jobs)
    _mount_race(router, session_of, sharing)

    def job_of(number):
        result = jobs.status(number)
        if result is None:
            raise HTTPException(404, "This sharing job is no longer available.")
        return result

    @router.get("/ghost/library/jobs/{number}")
    def job(number: str):
        status = job_of(number)
        result = status["result"]
        if isinstance(result, dict) and "path" in result:
            status["result"] = {key: value for key, value in result.items() if key != "path"}
            status["result"]["download_url"] = f"/api/ghost/library/download/{number}"
        return status

    @router.get("/ghost/library/download/{number}")
    def download(number: str):
        status = job_of(number)
        result = status.get("result")
        if status["state"] != "done" or not isinstance(result, dict) or not result.get("path"):
            raise HTTPException(409, "This ghost export is not ready to download.")
        path = Path(result["path"])
        if not path.resolve().is_relative_to(sharing().root / "exports") or not path.is_file():
            raise HTTPException(404, "This export has expired; export your selected ghosts again.")
        return FileResponse(path, media_type="application/zip", filename="sm64-ghost-collection.sm64ghosts")
