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


def _youtube_title(url: str) -> str | None:
    """Resolve a YouTube video's TITLE from its URL via the public oEmbed
    endpoint (no API key, no download). Best-effort — returns None on any
    failure so the import falls back to the URL as the name."""
    import json
    import urllib.parse
    import urllib.request
    try:
        api = "https://www.youtube.com/oembed?" + urllib.parse.urlencode(
            {"url": url, "format": "json"})
        req = urllib.request.Request(api, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read().decode()).get("title") or None
    except Exception:
        return None


class CompareService:
    def __init__(self, importer, tracker, broadcaster, cache_dir,
                 title_probe=_youtube_title):
        self.importer = importer
        self.tracker = tracker              # exposes .db and .ranks
        self.broadcaster = broadcaster
        self.cache_dir = cache_dir
        self._title_probe = title_probe     # url -> title | None (injectable)
        self._jobs: dict[str, dict] = {}

    # -- queries -------------------------------------------------------------
    def view(self, entity: str, strat: str | None) -> dict:
        if self.tracker.db is None:
            raise RuntimeError("database unavailable")
        self._backfill_titles(entity)
        return build_compare_view(self.tracker.db, self.tracker.ranks,
                                  entity, strat)

    def _backfill_titles(self, entity: str) -> None:
        """Give URL-named YouTube comparisons their real title. Older rows (and
        any whose title-probe failed at import) stored the URL as the name;
        resolve + persist it once so the raw URL is never shown. Probes each
        row at most once — after backfill the name no longer starts with http."""
        db = self.tracker.db
        for c in db.comparisons(entity):
            name = c["name"] or ""
            if c["source_kind"] == "youtube" and name.startswith("http"):
                title = self._title_probe(c["source_ref"])
                if title and title != name:
                    try:
                        db.update_comparison(c["id"], name=title)
                    except Exception:
                        log.exception("title backfill failed for comparison %s", c["id"])

    def adopt(self, source_id: int, strat: str) -> dict:
        """'Load existing': copy an existing comparison into (its entity, strat)
        — reuses the same source + cache (no re-download). Dedup'd, so adopting
        one already present under that strat just returns it."""
        db = self.tracker.db
        if db is None:
            raise RuntimeError("database unavailable")
        src = next((c for c in db.comparisons() if c["id"] == source_id), None)
        if src is None:
            raise LookupError(f"comparison {source_id} not found")
        entity = src["entity_key"]
        row = next((c for c in db.comparisons(entity, strat)
                    if c["source_ref"] == src["source_ref"]), None)
        if row is None:
            now = _iso_now()
            cid = db.insert_comparison(entity, strat, src["name"], src["source_kind"],
                                       src["source_ref"], src["cache_name"], now, now)
            row = next(c for c in db.comparisons(entity, strat) if c["id"] == cid)
        return {**row, "clip_url": f"/api/compare/cache/{row['cache_name']}"}

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
            target=self._run_job, name="compare-import", daemon=True,
            args=(job_id, entity_key, strat, name, source_kind, source_ref,
                  lambda progress: self.importer.import_video(
                      source_kind, source_ref, progress_cb=progress)),
        ).start()
        return job_id

    def start_upload(self, entity_key: str, strat: str, name: str,
                     filename: str, data: bytes) -> str:
        if self.tracker.db is None:
            raise RuntimeError("database unavailable")
        job_id = uuid.uuid4().hex
        self._jobs[job_id] = {"state": "running", "progress": 0.0,
                              "message": "starting", "comparison": None}
        threading.Thread(
            target=self._run_job, name="compare-upload", daemon=True,
            args=(job_id, entity_key, strat, name, "file", f"upload:{filename}",
                  lambda progress: self.importer.import_bytes(
                      data, progress_cb=progress)),
        ).start()
        return job_id

    def _run_job(self, job_id, entity_key, strat, name, source_kind,
                source_ref, produce) -> None:
        job = self._jobs[job_id]

        def progress(frac, msg):
            job["progress"] = frac; job["message"] = msg

        try:
            db = self.tracker.db
            # dedup: if this exact video is already a comparison for
            # (entity, strat), REUSE it — keeps its saved in/out (trim) instead
            # of inserting a duplicate. This is what makes re-adding a YouTube
            # URL you've configured before come back with its saved start/end.
            row = next((c for c in db.comparisons(entity_key, strat)
                        if c["source_ref"] == source_ref), None)
            if row is not None:
                progress(1.0, "already added")
            else:
                if source_kind == "youtube":         # nicer name than the URL
                    title = self._title_probe(source_ref)
                    if title:
                        name = title
                cache_name = produce(progress)
                now = _iso_now()
                cid = db.insert_comparison(entity_key, strat, name, source_kind,
                                           source_ref, cache_name, now, now)
                row = next(c for c in db.comparisons(entity_key, strat)
                           if c["id"] == cid)
            job["comparison"] = {**row,
                                 "clip_url": f"/api/compare/cache/{row['cache_name']}"}
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
