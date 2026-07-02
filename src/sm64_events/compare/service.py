"""CompareService — import jobs + CRUD + view + serve for the Compare tab.

Import is long (download + re-encode), so it runs on a background thread and
is polled: start_import returns a job id immediately; import_status reports
progress; the worker inserts the comparison row on success. CRUD is async so
it can publish comparisons_changed (broadcast-only config, like routes).

The initiating client refetches its list when the job finishes (and after its
own edits/deletes) — comparisons are a focused single-user surface, so import
completion is surfaced via the poll rather than a cross-client broadcast.
"""
import logging
import re
import threading
import uuid
from datetime import datetime, timezone

from sm64_events.core.events import Event
from sm64_events.tracking.views import build_compare_view

log = logging.getLogger("sm64.compare")

_CACHE_RE = re.compile(r"[0-9a-f]{16}\.mp4")  # cache_name_for output shape


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class CompareService:
    def __init__(self, importer, tracker, broadcaster, cache_dir):
        self.importer = importer
        self.tracker = tracker              # exposes .db and .ranks
        self.broadcaster = broadcaster
        self.cache_dir = cache_dir
        self._jobs: dict[str, dict] = {}

    # -- queries -------------------------------------------------------------
    def view(self, entity: str, strat: str | None) -> dict:
        if self.tracker.db is None:
            raise RuntimeError("database unavailable")
        return build_compare_view(self.tracker.db, self.tracker.ranks,
                                  entity, strat)

    def cache_path(self, cache_name: str):
        if not _CACHE_RE.fullmatch(cache_name):
            raise LookupError("no such comparison video")
        p = self.cache_dir / cache_name
        if not p.exists():
            raise LookupError("no such comparison video")
        return p

    # -- import (job) --------------------------------------------------------
    def start_import(self, entity_key: str, strat: str, name: str,
                     source_kind: str, source_ref: str) -> str:
        if self.tracker.db is None:
            raise RuntimeError("database unavailable")
        job_id = uuid.uuid4().hex
        self._jobs[job_id] = {"state": "running", "progress": 0.0,
                              "message": "starting", "comparison": None}
        threading.Thread(
            target=self._run_import, name="compare-import", daemon=True,
            args=(job_id, entity_key, strat, name, source_kind, source_ref),
        ).start()
        return job_id

    def _run_import(self, job_id, entity_key, strat, name, source_kind,
                    source_ref) -> None:
        job = self._jobs[job_id]

        def progress(frac, msg):
            job["progress"] = frac; job["message"] = msg

        try:
            cache_name = self.importer.import_video(source_kind, source_ref,
                                                    progress_cb=progress)
            now = _iso_now()
            cid = self.tracker.db.insert_comparison(
                entity_key, strat, name, source_kind, source_ref, cache_name,
                now, now)
            row = next(c for c in self.tracker.db.comparisons(entity_key, strat)
                       if c["id"] == cid)
            job["comparison"] = {**row,
                                 "clip_url": f"/api/compare/cache/{cache_name}"}
            job["progress"] = 1.0; job["message"] = "done"
            job["state"] = "done"
        except Exception as e:
            log.exception("comparison import failed")
            job["state"] = "error"; job["message"] = str(e)

    def import_status(self, job_id: str) -> dict:
        job = self._jobs.get(job_id)
        if job is None:
            raise LookupError("no such import job")
        return dict(job)  # shallow copy: callers never mutate live job state

    # -- CRUD ----------------------------------------------------------------
    async def update(self, comp_id: int, **fields) -> dict:
        db = self.tracker.db
        if db is None:
            raise RuntimeError("database unavailable")
        # Pre-check existence: db.update_comparison only raises LookupError when
        # it runs an UPDATE, so an empty/no-op fields set on a missing id would
        # otherwise fall through to a None row (TypeError) AND fire a spurious
        # comparisons_changed broadcast. Check BEFORE update AND publish.
        if not any(c["id"] == comp_id for c in db.comparisons()):
            raise LookupError(f"comparison {comp_id} not found")
        if "touch" in fields:
            fields.pop("touch")
            fields["last_used_utc"] = _iso_now()
        db.update_comparison(comp_id, **fields)          # LookupError if absent
        await self._changed()
        row = next((c for c in db.comparisons() if c["id"] == comp_id), None)
        return {**row, "clip_url": f"/api/compare/cache/{row['cache_name']}"}

    async def delete(self, comp_id: int) -> None:
        db = self.tracker.db
        if db is None:
            raise RuntimeError("database unavailable")
        row = next((c for c in db.comparisons() if c["id"] == comp_id), None)
        if row is None:
            raise LookupError(f"comparison {comp_id} not found")
        db.delete_comparison(comp_id)
        if db.comparison_cache_refs(row["cache_name"]) == 0:  # last reference
            (self.cache_dir / row["cache_name"]).unlink(missing_ok=True)
        await self._changed()

    async def _changed(self) -> None:
        await self.broadcaster.publish(Event(type="comparisons_changed",
                                             frame=0, timestamp_utc=datetime.now(
                                                 timezone.utc), payload={}))
