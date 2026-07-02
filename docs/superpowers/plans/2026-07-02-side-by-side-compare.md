# Side-by-Side Comparison Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Compare tab that plays the user's own gameplay next to one or more comparison videos (local files or YouTube), all driven by one frame-accurate transport, scrubbable in lockstep.

**Architecture:** Every comparison video is downloaded/copied and normalized to a local `.mp4` (yt-dlp + the bundled ffmpeg), so both sides are the project's existing frame-accurate `<video>` player. A new `comparisons` table (migration v10) keyed by `(entity_key, strat)` plus a content-addressed cache gives "load once / save it". A pure resolver picks the default comparison; a `CompareService` owns import jobs, CRUD, and serving; the Compare tab composes existing endpoints (session view + replay extract/serve) for the left side.

**Tech Stack:** Python 3.12 + uv, FastAPI, SQLite (WAL), pymem, pytest; Preact + htm (vendored) frontend; yt-dlp (new dep) + bundled ffmpeg for import.

## Global Constraints

- **uv only**, never pip: run tests with `uv run pytest -q`; add deps by editing `pyproject.toml` then `uv sync`.
- **Read-only to emulator memory**; this feature touches none.
- **Timestamps UTC**; store UTC, format on display. Use `_iso(_now())` helpers already in the codebase.
- **Error taxonomy** (all routers): `LookupError → 404`, `ValueError → 409`, `RuntimeError → 503`; anything else → 500.
- **Browser ↔ GUI parity** (domain rule #10): every user-facing feature is `ui/` + server only; the desktop shell adds no UI.
- **Runtime data paths** go through `core/paths.py` (cwd-relative from source, `%LOCALAPPDATA%\SM64Trainer` when frozen). Never hardcode paths.
- **yt-dlp is bundled in the release build** (`tools/build_exe.py`).
- **Frame math:** SM64 game logic is 30 fps; frame stepping is in GAME frames (`1/30 s`), independent of a clip's encoded fps. Reuse the exact-frame seek `(n + dir + 0.5) / 30` from `replay.js::step`.
- **Sync model is offset-only**: aligning start moments; no playback-rate warping.
- **Files that change together live together**; follow existing module patterns (mirror `routes`/`ranks`/`replay`).

---

## File Structure

**New (Python):**
- `src/sm64_events/tracking/comparisons.py` — pure: cache-name derivation, auto-select resolver, sync-frame math.
- `src/sm64_events/compare/__init__.py`
- `src/sm64_events/compare/importer.py` — `VideoImporter`: yt-dlp/copy → ffmpeg-normalize → content cache (injectable subprocess/downloader).
- `src/sm64_events/compare/service.py` — `CompareService`: import jobs, CRUD, view, serve; composes importer + db + ranks + broadcaster.
- `src/sm64_events/server/compare_api.py` — REST router.

**New (UI):**
- `src/sm64_events/ui/components/videosync.js` — `useSyncController` hook + `VideoStage` + `SyncTrack` (in/out handles).
- `src/sm64_events/ui/components/compare.js` — the Compare tab.
- `src/sm64_events/ui/frame.js` — shared game-frame step/jump helpers (extracted from `replay.js`).

**New (tests):**
- `tests/test_comparisons.py`, `tests/test_compare_importer.py`, `tests/test_compare_service.py`, `tests/test_compare_api.py`.

**Modified:**
- `src/sm64_events/core/paths.py` — add `compare_cache_dir()`.
- `src/sm64_events/storage/db.py` — `comparisons` table (v10) + CRUD.
- `src/sm64_events/tracking/views.py` — `build_compare_view()`.
- `src/sm64_events/main.py` — build importer + `CompareService`, pass to `create_app`.
- `src/sm64_events/server/app.py` — `create_app(..., compare=None)` + include router.
- `src/sm64_events/ui/components/replay.js` — use shared `frame.js`; add jump-to-beginning; add per-replay **Compare** button.
- `src/sm64_events/ui/app.js` — add "Compare" tab + `compareIntent` hand-off.
- `pyproject.toml` — add `yt-dlp`.
- `tools/build_exe.py` — bundle yt-dlp.
- `README.md`, `CLAUDE.md` (module map), `docs/architecture.md` (if domain knowledge gained).

**Waves (parallel fan-out):**
- **Wave 1 (foundation, parallel):** Task 1 (paths), Task 2 (pure logic), Task 3 (db table + CRUD).
- **Wave 2 (parallel, after Wave 1):** Task 4 (importer), Task 5 (`build_compare_view`).
- **Wave 3 (serialized on Wave 2):** Task 6 (`CompareService`), then Task 7 (router + wiring).
- **Wave 4 (UI, after Task 7 contract):** Task 8 (`frame.js` + replay.js), Task 9 (`videosync.js`), Task 10 (`compare.js`), Task 11 (app.js tab + deep-link).
- **Wave 5:** Task 12 (build + docs).

---

## Task 1: Content-cache path helper

**Files:**
- Modify: `src/sm64_events/core/paths.py` (add function near `replay_scratch_dir`, ~line 92)
- Test: `tests/test_paths.py` (create if absent; else append)

**Interfaces:**
- Produces: `compare_cache_dir() -> Path` returning `<data_root>/data/compare_cache`.

- [ ] **Step 1: Write the failing test**

Create/append `tests/test_paths.py`:

```python
from sm64_events.core.paths import compare_cache_dir, db_path


def test_compare_cache_dir_under_data_root():
    # Sits beside the db, under the same data root (frozen or source).
    assert compare_cache_dir() == db_path().parent / "compare_cache"
    assert compare_cache_dir().name == "compare_cache"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_paths.py::test_compare_cache_dir_under_data_root -v`
Expected: FAIL — `ImportError: cannot import name 'compare_cache_dir'`

- [ ] **Step 3: Add the helper**

In `src/sm64_events/core/paths.py`, after `replay_scratch_dir()`:

```python
def compare_cache_dir() -> Path:
    # Normalized comparison videos (yt-dlp'd / copied + ffmpeg-normalized).
    # Content-addressed by source; survives restarts (unlike the replay ring).
    return data_root() / "data" / "compare_cache"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_paths.py::test_compare_cache_dir_under_data_root -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/core/paths.py tests/test_paths.py
git commit -m "feat(paths): compare_cache_dir() for normalized comparison videos"
```

---

## Task 2: Pure comparison logic (cache name, auto-select, sync math)

**Files:**
- Create: `src/sm64_events/tracking/comparisons.py`
- Test: `tests/test_comparisons.py`

**Interfaces:**
- Consumes: nothing (pure).
- Produces:
  - `cache_name_for(source_ref: str) -> str` — deterministic `"<sha1-16>.mp4"`, the dedup identity.
  - `resolve_auto(saved: list[dict], suggestion: str | None, strat: str | None) -> dict` — returns `{"mode": "saved"|"suggestion"|"empty"|"no_strat", "comparison": dict|None}` per the four-branch priority.
  - `master_seek_time(in_frame: int | None, master_frame: int, game_fps: int = 30) -> float` — seconds to seek a stage to, `((in_frame or 0) + master_frame + 0.5) / game_fps`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_comparisons.py`:

```python
from sm64_events.tracking.comparisons import (cache_name_for, master_seek_time,
                                              resolve_auto)


def test_cache_name_is_deterministic_and_mp4():
    a = cache_name_for("https://youtu.be/abc")
    assert a == cache_name_for("https://youtu.be/abc")
    assert a.endswith(".mp4")
    assert a != cache_name_for("https://youtu.be/xyz")


def test_resolve_auto_prefers_most_recent_saved():
    saved = [{"id": 1, "last_used_utc": "2026-07-01T00:00:00Z"},
             {"id": 2, "last_used_utc": "2026-07-02T00:00:00Z"}]
    r = resolve_auto(saved, suggestion="https://youtu.be/std", strat="Ledgegrab")
    assert r["mode"] == "saved"
    assert r["comparison"]["id"] == 2


def test_resolve_auto_falls_back_to_suggestion():
    r = resolve_auto([], suggestion="https://youtu.be/std", strat="Ledgegrab")
    assert r["mode"] == "suggestion"
    assert r["comparison"] is None


def test_resolve_auto_empty_when_no_saved_no_suggestion():
    r = resolve_auto([], suggestion=None, strat="Ledgegrab")
    assert r["mode"] == "empty"


def test_resolve_auto_no_strat():
    r = resolve_auto([], suggestion=None, strat=None)
    assert r["mode"] == "no_strat"


def test_master_seek_time_offset_and_half_frame():
    # master frame 0, no in-point -> middle of frame 0 = 0.5/30
    assert master_seek_time(None, 0) == 0.5 / 30
    # in-point 90 (3 s), master frame 30 (1 s) -> (90+30+0.5)/30
    assert master_seek_time(90, 30) == (90 + 30 + 0.5) / 30
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_comparisons.py -v`
Expected: FAIL — `ModuleNotFoundError: sm64_events.tracking.comparisons`

- [ ] **Step 3: Write the implementation**

Create `src/sm64_events/tracking/comparisons.py`:

```python
"""Pure comparison logic — no I/O, no db, no ffmpeg.

Three concerns:
- cache_name_for: the dedup identity. Two comparison rows referencing the
  same source_ref share ONE normalized file ("load once"). Content-addressed
  by a hash of the source string, NOT the row id.
- resolve_auto: the four-branch auto-selection priority (spec feature #6):
  most-recent saved > rank-standard suggestion > empty > no active strat.
- master_seek_time: offset-only sync — each stage seeks to its own in-point
  plus the shared master game-frame, aimed at the MIDDLE of the frame so
  float rounding can't straddle a frame boundary (same math as replay.js).
"""
import hashlib


def cache_name_for(source_ref: str) -> str:
    digest = hashlib.sha1(source_ref.encode("utf-8")).hexdigest()[:16]
    return f"{digest}.mp4"


def resolve_auto(saved: list[dict], suggestion: str | None,
                 strat: str | None) -> dict:
    """Pick what the comparison slot shows by default. `saved` is the list of
    comparison rows for (entity, strat); `suggestion` is the rank-standard
    video URL (or None). Priority: recent saved > suggestion > empty; with no
    active strat there is nothing to key on."""
    if not strat:
        return {"mode": "no_strat", "comparison": None}
    if saved:
        newest = max(saved, key=lambda c: c["last_used_utc"])
        return {"mode": "saved", "comparison": newest}
    if suggestion:
        return {"mode": "suggestion", "comparison": None}
    return {"mode": "empty", "comparison": None}


def master_seek_time(in_frame: int | None, master_frame: int,
                     game_fps: int = 30) -> float:
    return ((in_frame or 0) + master_frame + 0.5) / game_fps
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_comparisons.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/tracking/comparisons.py tests/test_comparisons.py
git commit -m "feat(compare): pure cache-name, auto-select, sync-frame logic"
```

---

## Task 3: comparisons table (migration v10) + CRUD

**Files:**
- Modify: `src/sm64_events/storage/db.py` (append to `MIGRATIONS` after v9 ~line 168; add methods after `delete_route` ~line 429)
- Test: `tests/test_db_comparisons.py`

**Interfaces:**
- Produces (on `Database`):
  - `comparisons(entity_key: str | None = None, strat: str | None = None) -> list[dict]`
  - `insert_comparison(entity_key, strat, name, source_kind, source_ref, cache_name, created_utc, last_used_utc) -> int`
  - `update_comparison(comp_id: int, **fields) -> None` (fields ⊆ `name,in_frame,out_frame,last_used_utc`; `LookupError` if missing)
  - `delete_comparison(comp_id: int) -> None` (`LookupError` if missing)
  - `comparison_cache_refs(cache_name: str) -> int` (rows referencing a cache file — for safe unlink)
- Row dict keys: `id, entity_key, strat, name, source_kind, source_ref, cache_name, in_frame, out_frame, created_utc, last_used_utc`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_db_comparisons.py`:

```python
import pytest
from sm64_events.storage.db import Database


def _db(tmp_path):
    return Database(tmp_path / "t.db")


def test_insert_and_query_by_entity_strat(tmp_path):
    db = _db(tmp_path)
    cid = db.insert_comparison("star:7:0", "Ledgegrab", "XYZ run", "youtube",
                               "https://youtu.be/abc", "deadbeef.mp4",
                               "2026-07-02T00:00:00Z", "2026-07-02T00:00:00Z")
    assert isinstance(cid, int)
    rows = db.comparisons("star:7:0", "Ledgegrab")
    assert len(rows) == 1
    r = rows[0]
    assert r["name"] == "XYZ run" and r["source_kind"] == "youtube"
    assert r["cache_name"] == "deadbeef.mp4" and r["in_frame"] is None
    # not returned for a different pair
    assert db.comparisons("star:7:0", "Other") == []


def test_update_sync_points_and_touch(tmp_path):
    db = _db(tmp_path)
    cid = db.insert_comparison("segment:3", "Fast", "n", "file", "/v.mp4",
                               "c.mp4", "2026-07-02T00:00:00Z",
                               "2026-07-02T00:00:00Z")
    db.update_comparison(cid, in_frame=90, out_frame=300,
                         last_used_utc="2026-07-03T00:00:00Z")
    r = db.comparisons("segment:3", "Fast")[0]
    assert r["in_frame"] == 90 and r["out_frame"] == 300
    assert r["last_used_utc"] == "2026-07-03T00:00:00Z"


def test_update_unknown_raises(tmp_path):
    with pytest.raises(LookupError):
        _db(tmp_path).update_comparison(999, name="x")


def test_delete_and_cache_refcount(tmp_path):
    db = _db(tmp_path)
    a = db.insert_comparison("star:7:0", "L", "a", "youtube", "u1", "same.mp4",
                             "t", "t")
    db.insert_comparison("star:7:0", "L", "b", "youtube", "u2", "same.mp4",
                         "t", "t")
    assert db.comparison_cache_refs("same.mp4") == 2
    db.delete_comparison(a)
    assert db.comparison_cache_refs("same.mp4") == 1
    with pytest.raises(LookupError):
        db.delete_comparison(a)  # already gone
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_db_comparisons.py -v`
Expected: FAIL — `AttributeError: 'Database' object has no attribute 'insert_comparison'` (and the table won't exist)

- [ ] **Step 3: Add the migration**

In `src/sm64_events/storage/db.py`, append to the `MIGRATIONS` list (after the v9 `ALTER TABLE routes ...` entry, before the closing `]`):

```python
    # v10 — comparisons: saved side-by-side comparison videos (spec 2026-07-02).
    # Config (like routes), never journaled. Keyed by (entity_key, strat);
    # cache_name points into data/compare_cache (content-addressed dedup).
    # in/out_frame are non-destructive sync bounds in GAME frames (NULL = ends).
    """
    CREATE TABLE IF NOT EXISTS comparisons (
      id            INTEGER PRIMARY KEY AUTOINCREMENT,
      entity_key    TEXT NOT NULL,
      strat         TEXT NOT NULL,
      name          TEXT NOT NULL,
      source_kind   TEXT NOT NULL,
      source_ref    TEXT NOT NULL,
      cache_name    TEXT NOT NULL,
      in_frame      INTEGER,
      out_frame     INTEGER,
      created_utc   TEXT NOT NULL,
      last_used_utc TEXT NOT NULL
    );
    """,
```

- [ ] **Step 4: Add the CRUD methods**

In `src/sm64_events/storage/db.py`, after `delete_route` (~line 429), add:

```python
    # -- comparisons (config) ------------------------------------------------
    _COMP_COLS = ("id", "entity_key", "strat", "name", "source_kind",
                  "source_ref", "cache_name", "in_frame", "out_frame",
                  "created_utc", "last_used_utc")

    def comparisons(self, entity_key: str | None = None,
                    strat: str | None = None) -> list[dict]:
        q, params, where = "SELECT * FROM comparisons", [], []
        if entity_key is not None:
            where.append("entity_key=?"); params.append(entity_key)
        if strat is not None:
            where.append("strat=?"); params.append(strat)
        if where:
            q += " WHERE " + " AND ".join(where)
        q += " ORDER BY id"
        with self._lock:
            rows = self._conn.execute(q, params).fetchall()
        return [{k: r[k] for k in self._COMP_COLS} for r in rows]

    def insert_comparison(self, entity_key: str, strat: str, name: str,
                          source_kind: str, source_ref: str, cache_name: str,
                          created_utc: str, last_used_utc: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO comparisons (entity_key, strat, name, source_kind,"
                " source_ref, cache_name, created_utc, last_used_utc)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (entity_key, strat, name, source_kind, source_ref, cache_name,
                 created_utc, last_used_utc))
            self._conn.commit()
            return cur.lastrowid

    def update_comparison(self, comp_id: int, **fields) -> None:
        cols = ("name", "in_frame", "out_frame", "last_used_utc")
        unknown = set(fields) - set(cols)
        if unknown:
            raise ValueError(f"unknown fields {sorted(unknown)}")
        sets, vals = [], []
        for k in cols:
            if k in fields:
                sets.append(f"{k}=?"); vals.append(fields[k])
        if not sets:
            return
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE comparisons SET {','.join(sets)} WHERE id=?",
                (*vals, comp_id))
            self._conn.commit()
        if cur.rowcount == 0:
            raise LookupError(f"comparison {comp_id} not found")

    def delete_comparison(self, comp_id: int) -> None:
        with self._lock:
            cur = self._conn.execute("DELETE FROM comparisons WHERE id=?",
                                     (comp_id,))
            self._conn.commit()
        if cur.rowcount == 0:
            raise LookupError(f"comparison {comp_id} not found")

    def comparison_cache_refs(self, cache_name: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM comparisons WHERE cache_name=?",
                (cache_name,)).fetchone()
        return row["n"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_db_comparisons.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Run the full db suite (migration regression)**

Run: `uv run pytest tests/test_db.py -q` (and any other db-touching tests)
Expected: PASS — the v10 migration applies cleanly on a fresh db.

- [ ] **Step 7: Commit**

```bash
git add src/sm64_events/storage/db.py tests/test_db_comparisons.py
git commit -m "feat(db): comparisons table (v10) + CRUD"
```

---

## Task 4: VideoImporter (yt-dlp / copy → ffmpeg-normalize → cache)

**Files:**
- Create: `src/sm64_events/compare/__init__.py` (empty)
- Create: `src/sm64_events/compare/importer.py`
- Modify: `pyproject.toml` (add `yt-dlp`)
- Test: `tests/test_compare_importer.py`

**Interfaces:**
- Consumes: `cache_name_for` (Task 2), `compare_cache_dir` (Task 1).
- Produces: `class VideoImporter`:
  - `__init__(self, cache_dir: Path, ffmpeg: str, *, downloader=None, runner=None)` — `downloader(source_ref, dest_dir) -> Path` fetches a YouTube URL to a file (default: yt-dlp); `runner(cmd: list[str]) -> None` runs ffmpeg (default: subprocess). Injectable for tests.
  - `import_video(self, source_kind: str, source_ref: str, progress_cb=None) -> str` — returns `cache_name`. If already cached, returns immediately (dedup / "load once"). Else fetches (youtube) or copies (file) to a temp, normalizes to `<cache>/<cache_name>` (≤720p H.264 + faststart), returns the name. `progress_cb(fraction: float, message: str)` is called during the fetch.
  - Raises `LookupError` for a missing local file, `RuntimeError` for a download/normalize failure, `ValueError` for an unknown `source_kind`.

- [ ] **Step 1: Add the dependency**

Edit `pyproject.toml` `dependencies` list — add after `"pystray>=0.19",`:

```toml
    "yt-dlp>=2025.1.1",
```

Then run: `uv sync`
Expected: yt-dlp installed.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_compare_importer.py`:

```python
import pytest
from sm64_events.compare.importer import VideoImporter
from sm64_events.tracking.comparisons import cache_name_for


def _importer(tmp_path, downloader=None, runner=None):
    cache = tmp_path / "cache"; cache.mkdir()
    calls = []

    def default_runner(cmd):
        # fake ffmpeg: just create the output file (last arg)
        calls.append(cmd)
        open(cmd[-1], "wb").write(b"normalized")

    return VideoImporter(cache, ffmpeg="ffmpeg",
                         downloader=downloader,
                         runner=runner or default_runner), cache, calls


def test_import_file_copies_and_normalizes(tmp_path):
    src = tmp_path / "clip.mp4"; src.write_bytes(b"raw")
    imp, cache, calls = _importer(tmp_path)
    name = imp.import_video("file", str(src))
    assert name == cache_name_for(str(src))
    assert (cache / name).exists()
    assert len(calls) == 1  # ffmpeg ran once


def test_import_dedup_skips_second_time(tmp_path):
    src = tmp_path / "clip.mp4"; src.write_bytes(b"raw")
    imp, cache, calls = _importer(tmp_path)
    imp.import_video("file", str(src))
    imp.import_video("file", str(src))  # already cached
    assert len(calls) == 1  # ffmpeg did NOT run again


def test_import_missing_file_raises_lookup(tmp_path):
    imp, _, _ = _importer(tmp_path)
    with pytest.raises(LookupError):
        imp.import_video("file", str(tmp_path / "nope.mp4"))


def test_import_youtube_uses_downloader_and_progress(tmp_path):
    got = []

    def fake_dl(ref, dest_dir):
        p = dest_dir / "dl.mp4"; p.write_bytes(b"yt"); return p

    imp, cache, calls = _importer(tmp_path, downloader=fake_dl)
    name = imp.import_video("youtube", "https://youtu.be/abc",
                            progress_cb=lambda f, m: got.append((f, m)))
    assert (cache / name).exists()
    assert got and got[-1][0] == 1.0  # completed progress reported


def test_unknown_source_kind_raises_value(tmp_path):
    imp, _, _ = _importer(tmp_path)
    with pytest.raises(ValueError):
        imp.import_video("magnet", "x")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_compare_importer.py -v`
Expected: FAIL — `ModuleNotFoundError: sm64_events.compare.importer`

- [ ] **Step 4: Write the implementation**

Create `src/sm64_events/compare/__init__.py` (empty).

Create `src/sm64_events/compare/importer.py`:

```python
"""Comparison-video import: YouTube (yt-dlp) or local file -> ONE normalized
mp4 in the content cache.

Every comparison becomes a plain <video> the frame-stepping player can drive
exactly like a replay clip. Normalization (ffmpeg, the bundled binary) forces
H.264 <=720p + faststart so seeking / single-frame stepping is reliable
regardless of the source's codec, resolution, or container.

Dedup = "load once": the cache file is named by a hash of source_ref
(cache_name_for), so importing the same URL/path twice returns the existing
file without re-downloading or re-encoding. The downloader (yt-dlp) and the
ffmpeg runner are injected so tests never touch the network or a codec.
"""
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from sm64_events.tracking.comparisons import cache_name_for

log = logging.getLogger("sm64.compare")


def _default_downloader(source_ref: str, dest_dir: Path) -> Path:
    """Fetch a YouTube URL to dest_dir at <=720p; return the downloaded file."""
    import yt_dlp
    out_tmpl = str(dest_dir / "src.%(ext)s")
    opts = {"format": "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
            "outtmpl": out_tmpl, "quiet": True, "noprogress": True,
            "merge_output_format": "mp4"}
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([source_ref])
    files = list(dest_dir.glob("src.*"))
    if not files:
        raise RuntimeError("yt-dlp produced no file")
    return files[0]


def _default_runner(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


class VideoImporter:
    def __init__(self, cache_dir: Path, ffmpeg: str, *,
                 downloader=None, runner=None):
        self.cache_dir = Path(cache_dir)
        self.ffmpeg = ffmpeg
        self._download = downloader or _default_downloader
        self._run = runner or _default_runner

    def cache_path(self, cache_name: str) -> Path:
        return self.cache_dir / cache_name

    def import_video(self, source_kind: str, source_ref: str,
                     progress_cb=None) -> str:
        if source_kind not in ("youtube", "file"):
            raise ValueError(f"unknown source_kind {source_kind!r}")
        name = cache_name_for(source_ref)
        dest = self.cache_path(name)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if dest.exists():                       # dedup / load-once
            if progress_cb:
                progress_cb(1.0, "already loaded")
            return name

        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            if source_kind == "file":
                src = Path(source_ref)
                if not src.is_file():
                    raise LookupError(f"no such file: {source_ref}")
                if progress_cb:
                    progress_cb(0.3, "copying")
                raw = tdp / f"src{src.suffix or '.mp4'}"
                shutil.copy2(src, raw)
            else:
                if progress_cb:
                    progress_cb(0.1, "downloading")
                try:
                    raw = self._download(source_ref, tdp)
                except Exception as e:
                    raise RuntimeError(f"download failed: {e}") from e
            if progress_cb:
                progress_cb(0.7, "normalizing")
            tmp_out = tdp / name                # normalize beside the source
            cmd = [self.ffmpeg, "-y", "-i", str(raw),
                   "-vf", "scale=-2:min(720\\,ih)", "-c:v", "libx264",
                   "-preset", "veryfast", "-crf", "20",
                   "-movflags", "+faststart", "-c:a", "aac", str(tmp_out)]
            try:
                self._run(cmd)
            except Exception as e:
                raise RuntimeError(f"normalize failed: {e}") from e
            if not tmp_out.exists():
                raise RuntimeError("normalize produced no output")
            shutil.move(str(tmp_out), str(dest))  # atomic publish into cache
        if progress_cb:
            progress_cb(1.0, "done")
        return name
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_compare_importer.py -v`
Expected: PASS (5 passed)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/sm64_events/compare/__init__.py src/sm64_events/compare/importer.py tests/test_compare_importer.py
git commit -m "feat(compare): VideoImporter (yt-dlp/copy -> ffmpeg normalize -> dedup cache)"
```

---

## Task 5: build_compare_view (resolver + display payload)

**Files:**
- Modify: `src/sm64_events/tracking/views.py` (add function at end of file)
- Test: `tests/test_compare_view.py`

**Interfaces:**
- Consumes: `db.comparisons(entity, strat)` (Task 3), `resolve_auto` (Task 2), `ranks.video_for(entity, strat)`.
- Produces: `build_compare_view(db, ranks, entity: str, strat: str | None) -> dict` returning:
  ```
  {"entity": entity, "strat": strat,
   "saved": [comparison dict + "clip_url"], "auto": {mode, comparison|None},
   "suggestion": {"source_kind": "youtube", "source_ref": url, "name": str} | None}
  ```
  Each saved row gains `"clip_url": f"/api/compare/cache/{cache_name}"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_compare_view.py`:

```python
from sm64_events.tracking.views import build_compare_view


class _FakeRanks:
    def __init__(self, url): self._url = url
    def video_for(self, ek, strat): return self._url


class _FakeDB:
    def __init__(self, rows): self._rows = rows
    def comparisons(self, entity, strat):
        return [r for r in self._rows
                if r["entity_key"] == entity and r["strat"] == strat]


def _row(**kw):
    base = {"id": 1, "entity_key": "star:7:0", "strat": "Ledgegrab",
            "name": "n", "source_kind": "youtube", "source_ref": "u",
            "cache_name": "c.mp4", "in_frame": None, "out_frame": None,
            "created_utc": "t", "last_used_utc": "t"}
    base.update(kw); return base


def test_view_saved_gets_clip_url_and_auto_saved():
    db = _FakeDB([_row(id=5, cache_name="abc.mp4",
                       last_used_utc="2026-07-02T00:00:00Z")])
    v = build_compare_view(db, _FakeRanks("https://youtu.be/std"),
                           "star:7:0", "Ledgegrab")
    assert v["saved"][0]["clip_url"] == "/api/compare/cache/abc.mp4"
    assert v["auto"]["mode"] == "saved" and v["auto"]["comparison"]["id"] == 5


def test_view_suggestion_when_no_saved():
    v = build_compare_view(_FakeDB([]), _FakeRanks("https://youtu.be/std"),
                           "star:7:0", "Ledgegrab")
    assert v["suggestion"]["source_ref"] == "https://youtu.be/std"
    assert v["auto"]["mode"] == "suggestion"


def test_view_no_strat_is_empty():
    v = build_compare_view(_FakeDB([]), _FakeRanks(None), "star:7:0", None)
    assert v["saved"] == [] and v["auto"]["mode"] == "no_strat"
    assert v["suggestion"] is None


def test_view_none_ranks_no_suggestion():
    v = build_compare_view(_FakeDB([]), None, "star:7:0", "Ledgegrab")
    assert v["suggestion"] is None and v["auto"]["mode"] == "empty"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_compare_view.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_compare_view'`

- [ ] **Step 3: Write the implementation**

At the END of `src/sm64_events/tracking/views.py`, add (and add the import at the top with the other `tracking` imports):

```python
from sm64_events.tracking.comparisons import resolve_auto  # add near top imports
```

```python
def build_compare_view(db, ranks, entity: str, strat: str | None) -> dict:
    """Compare-tab payload for one (entity, strat): saved comparisons (each with
    a servable clip_url), the resolved auto-pick, and the rank-standard
    suggestion (one-click Load) when nothing is saved. Ranks may be None."""
    saved = []
    if strat:
        for c in db.comparisons(entity, strat):
            saved.append({**c, "clip_url": f"/api/compare/cache/{c['cache_name']}"})
    suggestion_url = ranks.video_for(entity, strat) if (ranks and strat) else None
    suggestion = ({"source_kind": "youtube", "source_ref": suggestion_url,
                   "name": f"{strat} — rank standard"} if suggestion_url else None)
    auto = resolve_auto(saved, suggestion_url, strat)
    return {"entity": entity, "strat": strat, "saved": saved,
            "auto": auto, "suggestion": suggestion}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_compare_view.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/tracking/views.py tests/test_compare_view.py
git commit -m "feat(compare): build_compare_view resolver + display payload"
```

---

## Task 6: CompareService (import jobs, CRUD, view, serve)

**Files:**
- Create: `src/sm64_events/compare/service.py`
- Test: `tests/test_compare_service.py`

**Interfaces:**
- Consumes: `VideoImporter` (Task 4), `build_compare_view` (Task 5), `db.*comparison*` (Task 3), `cache_name_for` (Task 2), the `Broadcaster` (`await broadcaster.publish(Event(...))`), a `tracker` exposing `.db` and `.ranks`.
- Produces: `class CompareService`:
  - `view(entity: str, strat: str | None) -> dict` — delegates to `build_compare_view`.
  - `start_import(entity_key, strat, name, source_kind, source_ref) -> str` — returns `job_id`; runs the import on a background thread, then inserts the comparison row.
  - `import_status(job_id) -> dict` — `{"state","progress","message","comparison"}`; raises `LookupError` for an unknown job.
  - `async update(comp_id, **fields) -> dict` — validates, updates, publishes `comparisons_changed`, returns the updated row.
  - `async delete(comp_id) -> None` — deletes the row, unlinks the cache file iff unreferenced, publishes `comparisons_changed`.
  - `cache_path(cache_name: str) -> Path` — validated servable path (`LookupError` if bad name / missing).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_compare_service.py`:

```python
import asyncio
import time

import pytest

from sm64_events.compare.importer import VideoImporter
from sm64_events.compare.service import CompareService
from sm64_events.storage.db import Database


class _Bc:
    def __init__(self): self.events = []
    async def publish(self, ev): self.events.append(ev)


class _Tracker:
    def __init__(self, db, ranks): self.db = db; self.ranks = ranks


class _Ranks:
    def video_for(self, ek, strat): return None


def _svc(tmp_path):
    cache = tmp_path / "cache"; cache.mkdir()

    def runner(cmd):  # fake ffmpeg
        open(cmd[-1], "wb").write(b"norm")

    imp = VideoImporter(cache, "ffmpeg", runner=runner)
    db = Database(tmp_path / "t.db")
    return CompareService(imp, _Tracker(db, _Ranks()), _Bc(), cache), db, cache


def test_import_job_completes_and_inserts_row(tmp_path):
    svc, db, cache = _svc(tmp_path)
    src = tmp_path / "v.mp4"; src.write_bytes(b"raw")
    job = svc.start_import("star:7:0", "Ledgegrab", "mine", "file", str(src))
    for _ in range(100):                        # poll to completion
        st = svc.import_status(job)
        if st["state"] in ("done", "error"):
            break
        time.sleep(0.02)
    assert st["state"] == "done", st
    assert st["comparison"]["name"] == "mine"
    assert db.comparisons("star:7:0", "Ledgegrab")[0]["name"] == "mine"


def test_import_error_reported(tmp_path):
    svc, db, cache = _svc(tmp_path)
    job = svc.start_import("star:7:0", "L", "x", "file",
                           str(tmp_path / "missing.mp4"))
    for _ in range(100):
        st = svc.import_status(job)
        if st["state"] in ("done", "error"):
            break
        time.sleep(0.02)
    assert st["state"] == "error"
    assert db.comparisons("star:7:0", "L") == []  # no row on failure


def test_delete_unlinks_unreferenced_cache(tmp_path):
    svc, db, cache = _svc(tmp_path)
    src = tmp_path / "v.mp4"; src.write_bytes(b"raw")
    job = svc.start_import("star:7:0", "L", "x", "file", str(src))
    for _ in range(100):
        if svc.import_status(job)["state"] == "done":
            break
        time.sleep(0.02)
    cid = db.comparisons("star:7:0", "L")[0]["id"]
    cname = db.comparisons("star:7:0", "L")[0]["cache_name"]
    assert (cache / cname).exists()
    asyncio.run(svc.delete(cid))
    assert not (cache / cname).exists()          # last ref gone -> file removed
    assert db.comparisons("star:7:0", "L") == []


def test_unknown_job_raises(tmp_path):
    svc, _, _ = _svc(tmp_path)
    with pytest.raises(LookupError):
        svc.import_status("nope")


def test_cache_path_rejects_bad_name(tmp_path):
    svc, _, _ = _svc(tmp_path)
    with pytest.raises(LookupError):
        svc.cache_path("../secret")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_compare_service.py -v`
Expected: FAIL — `ModuleNotFoundError: sm64_events.compare.service`

- [ ] **Step 3: Write the implementation**

Create `src/sm64_events/compare/service.py`:

```python
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
        return job

    # -- CRUD ----------------------------------------------------------------
    async def update(self, comp_id: int, **fields) -> dict:
        db = self.tracker.db
        if db is None:
            raise RuntimeError("database unavailable")
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
```

> Note: `db.comparisons()` with no args returns all rows (Task 3 supports optional filters). The `update` row-lookup uses that; keep the Task 3 signature (`entity_key=None, strat=None`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_compare_service.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/compare/service.py tests/test_compare_service.py
git commit -m "feat(compare): CompareService (import jobs, CRUD, view, serve)"
```

---

## Task 7: compare_api router + app/main wiring

**Files:**
- Create: `src/sm64_events/server/compare_api.py`
- Modify: `src/sm64_events/server/app.py` (`create_app` signature + include router)
- Modify: `src/sm64_events/main.py` (build importer + CompareService)
- Test: `tests/test_compare_api.py`

**Interfaces:**
- Consumes: `CompareService` (Task 6).
- Produces the REST surface:
  - `GET /api/compare/view?entity=&strat=` → `service.view(entity, strat)`
  - `POST /api/compare/import` `{entity_key,strat,name,source_kind,source_ref}` → `{job_id}`
  - `GET /api/compare/import/{job_id}` → job dict
  - `PUT /api/compare/videos/{id}` `{name?,in_frame?,out_frame?,touch?}` → updated row
  - `DELETE /api/compare/videos/{id}` → `{ok: true}`
  - `GET /api/compare/cache/{name}` → `FileResponse` (Range/206)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_compare_api.py`:

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sm64_events.server.compare_api import create_compare_router


class _FakeService:
    def __init__(self): self.deleted = []
    def view(self, entity, strat):
        return {"entity": entity, "strat": strat, "saved": [], "auto": None,
                "suggestion": None}
    def start_import(self, **kw): return "job123"
    def import_status(self, job_id):
        if job_id != "job123":
            raise LookupError("no such import job")
        return {"state": "done", "progress": 1.0, "message": "done",
                "comparison": {"id": 1}}
    async def update(self, comp_id, **fields):
        return {"id": comp_id, **fields}
    async def delete(self, comp_id):
        self.deleted.append(comp_id)


def _client(svc):
    app = FastAPI()
    app.include_router(create_compare_router(svc))
    return TestClient(app)


def test_view_endpoint():
    c = _client(_FakeService())
    r = c.get("/api/compare/view", params={"entity": "star:7:0",
                                           "strat": "Ledgegrab"})
    assert r.status_code == 200 and r.json()["strat"] == "Ledgegrab"


def test_import_returns_job_then_status():
    c = _client(_FakeService())
    r = c.post("/api/compare/import", json={"entity_key": "star:7:0",
        "strat": "L", "name": "n", "source_kind": "file", "source_ref": "/v"})
    assert r.json()["job_id"] == "job123"
    s = c.get("/api/compare/import/job123")
    assert s.json()["state"] == "done"
    assert c.get("/api/compare/import/nope").status_code == 404


def test_put_and_delete():
    svc = _FakeService()
    c = _client(svc)
    r = c.put("/api/compare/videos/5", json={"in_frame": 90, "touch": True})
    assert r.json()["in_frame"] == 90
    assert c.delete("/api/compare/videos/5").json()["ok"] is True
    assert svc.deleted == [5]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_compare_api.py -v`
Expected: FAIL — `ModuleNotFoundError: sm64_events.server.compare_api`

- [ ] **Step 3: Write the router**

Create `src/sm64_events/server/compare_api.py`:

```python
# src/sm64_events/server/compare_api.py
"""Compare REST surface. Same error taxonomy as api.py/replay_api.py:
LookupError->404, ValueError->409, RuntimeError->503. Import is a polled job
(download + re-encode is long); cache serving uses FileResponse (Range/206)."""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel


def _http(e: Exception) -> HTTPException:
    if isinstance(e, LookupError):
        return HTTPException(404, str(e))
    if isinstance(e, ValueError):
        return HTTPException(409, str(e))
    return HTTPException(503, str(e))


class ImportBody(BaseModel):
    entity_key: str
    strat: str
    name: str
    source_kind: str            # 'youtube' | 'file'
    source_ref: str


class EditBody(BaseModel):
    name: str | None = None
    in_frame: int | None = None
    out_frame: int | None = None
    touch: bool | None = None


def create_compare_router(service) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/compare/view")
    def view(entity: str, strat: str | None = None):
        try:
            return service.view(entity, strat)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)

    @router.post("/compare/import")
    def start_import(body: ImportBody):
        try:
            job_id = service.start_import(
                entity_key=body.entity_key, strat=body.strat, name=body.name,
                source_kind=body.source_kind, source_ref=body.source_ref)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"job_id": job_id}

    @router.get("/compare/import/{job_id}")
    def import_status(job_id: str):
        try:
            return service.import_status(job_id)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)

    @router.put("/compare/videos/{comp_id}")
    async def edit(comp_id: int, body: EditBody):
        fields = {k: v for k, v in body.model_dump().items() if v is not None}
        try:
            return await service.update(comp_id, **fields)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)

    @router.delete("/compare/videos/{comp_id}")
    async def remove(comp_id: int):
        try:
            await service.delete(comp_id)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.get("/compare/cache/{name}")
    def cache(name: str):
        try:
            path = service.cache_path(name)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return FileResponse(path, media_type="video/mp4")  # native Range/206

    return router
```

- [ ] **Step 4: Run the router tests**

Run: `uv run pytest tests/test_compare_api.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Wire into create_app**

In `src/sm64_events/server/app.py`, change the `create_app` signature (~line 214):

```python
def create_app(poller: Poller, broadcaster: Broadcaster,
               service=None, replay=None, updater=None, compare=None,
               debug_hooks: bool = False) -> FastAPI:
```

And after the replay router block (~line 323), add:

```python
    if compare is not None:
        from sm64_events.server.compare_api import create_compare_router
        app.include_router(create_compare_router(compare))
```

- [ ] **Step 6: Wire into main.py**

In `src/sm64_events/main.py`, add imports near the other compare-adjacent imports:

```python
from sm64_events.compare.importer import VideoImporter
from sm64_events.compare.service import CompareService
from sm64_events.core.paths import compare_cache_dir
```

After the `replay = ReplayService(...)` block and before the `detectors = [...]` list, add:

```python
    # Compare tab: import comparison videos (yt-dlp/copy -> ffmpeg normalize)
    # into the content cache, then serve them as plain clips. Only built when
    # ffmpeg is available (same binary the replay sink uses).
    compare = None
    _ffmpeg_bin = bundled_ffmpeg() or __import__("shutil").which("ffmpeg")
    if db is not None and _ffmpeg_bin:
        importer = VideoImporter(compare_cache_dir(), _ffmpeg_bin)
        compare = CompareService(importer, service, broadcaster,
                                 compare_cache_dir())
```

Change the final `return create_app(...)` to pass `compare`:

```python
    return create_app(poller, broadcaster, service=service, replay=replay,
                      updater=updater, compare=compare)
```

- [ ] **Step 7: Verify the app still builds and the full suite passes**

Run: `uv run pytest -q`
Expected: PASS (whole suite green, including the new compare tests).

- [ ] **Step 8: Commit**

```bash
git add src/sm64_events/server/compare_api.py src/sm64_events/server/app.py src/sm64_events/main.py tests/test_compare_api.py
git commit -m "feat(compare): REST router + app/main wiring"
```

---

## Task 8: Shared frame helper + jump-to-beginning in the replay player

**Files:**
- Create: `src/sm64_events/ui/frame.js`
- Modify: `src/sm64_events/ui/components/replay.js` (import helpers; add ⏮ button)

> UI tasks have no pytest gate — verification is the `superpowers:frontend-smoke-test` skill (Chrome DevTools MCP) + manual check. Each UI task ends with a smoke-test step.

**Interfaces:**
- Produces `ui/frame.js`:
  - `stepGameFrame(video, dir, gameFps = 30)` — pause, seek ±1 game frame to the frame middle.
  - `jumpToStart(video, startSeconds = 0)` — pause, seek to `startSeconds` (clip/sync start).
  - `gameFrameOf(video, gameFps = 30) -> number` — current game-frame index.

- [ ] **Step 1: Create the shared helper**

Create `src/sm64_events/ui/frame.js`:

```javascript
// src/sm64_events/ui/frame.js — shared game-frame video controls.
// SM64 logic is 30 fps; steps move in GAME frames regardless of encode rate.
// Seek to the MIDDLE of the target frame so float rounding never straddles a
// boundary (the fix from replay.js: stepping 1/encode-fps only changed the
// image every 2nd press). Used by the replay player and the compare sync layer.

export function gameFrameOf(video, gameFps = 30) {
  return Math.floor((video.currentTime || 0) * gameFps + 1e-4);
}

export function stepGameFrame(video, dir, gameFps = 30) {
  if (!video) return;
  if (!video.paused) video.pause();
  const n = gameFrameOf(video, gameFps);
  const t = (n + dir + 0.5) / gameFps;
  video.currentTime = Math.min(Math.max(t, 0), video.duration || 0);
}

export function jumpToStart(video, startSeconds = 0) {
  if (!video) return;
  if (!video.paused) video.pause();
  video.currentTime = Math.max(0, startSeconds);
}
```

- [ ] **Step 2: Use it in replay.js and add the ⏮ button**

In `src/sm64_events/ui/components/replay.js`, add the import after the existing imports (line 5 area):

```javascript
import { stepGameFrame, jumpToStart } from "../frame.js";
```

Replace the `step` function (lines ~81-89) body to delegate:

```javascript
  function step(dir) {
    stepGameFrame(videoEl.current, dir, state.game_fps || 30);
  }
  function toStart() {
    jumpToStart(videoEl.current, 0);
  }
```

In the controls row (the `<div style="display:flex;gap:.3rem...">` around line 126), add the jump button as the FIRST control:

```javascript
      <button onclick=${toStart} title="jump to the beginning">⏮ start</button>
```

- [ ] **Step 3: Smoke-test**

Start the server (`uv run python -m sm64_events.main`), open `http://127.0.0.1:8065`, go to Practice, expand a replay. Verify: ⏮ start seeks to 0 and pauses; ⏴/⏵ still step one game frame; play/pause unaffected. Use the `frontend-smoke-test` skill (check console clean).

- [ ] **Step 4: Commit**

```bash
git add src/sm64_events/ui/frame.js src/sm64_events/ui/components/replay.js
git commit -m "feat(ui): shared frame helper + jump-to-beginning in replay player"
```

---

## Task 9: videosync.js — sync controller, VideoStage, SyncTrack

**Files:**
- Create: `src/sm64_events/ui/components/videosync.js`

**Interfaces:**
- Consumes: `frame.js` helpers.
- Produces:
  - `useSyncController()` → `{ register(id, el, getInFrame), unregister(id), play(), pause(), step(dir), toStart(), playing }`. `getInFrame()` returns the stage's current in-point (game frames). On `step`/`toStart`/`pause`, every registered video is re-seeked to `master_seek_time`-style offset alignment: `(inFrame + masterFrame + 0.5)/30`. The master frame is read from the FIRST registered stage (`gameFrameOf - itsInFrame`).
  - `VideoStage({ src, inFrame, controller, id, gameFps })` — a `<video>` that registers with the controller and applies shared volume.
  - `SyncTrack({ video, inFrame, outFrame, onChange })` — two draggable range handles over the duration setting in/out (game frames); seeks the video to a handle while dragging for a live preview.

- [ ] **Step 1: Create the component**

Create `src/sm64_events/ui/components/videosync.js`:

```javascript
// src/sm64_events/ui/components/videosync.js — drive N videos in lockstep.
// Offset-only sync: each stage has an in-point (game frames); the transport
// keeps a shared master game-frame and, on every discrete action, re-seeks
// each video to (inFrame + masterFrame) aimed at the frame middle. Continuous
// play just runs every <video> at true rate (they start aligned); pause
// re-syncs to correct any drift.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { stepGameFrame, jumpToStart, gameFrameOf } from "../frame.js";

const html = htm.bind(h);
const VOLUME_KEY = "replay_volume";
function storedVolume() {
  let v = NaN;
  try { v = parseFloat(localStorage.getItem(VOLUME_KEY)); } catch {}
  return v >= 0 && v <= 1 ? v : 0.3;
}

export function useSyncController() {
  const stages = useRef(new Map());   // id -> { el, getInFrame }
  const [playing, setPlaying] = useState(false);

  const register = (id, el, getInFrame) => {
    if (el) stages.current.set(id, { el, getInFrame });
    else stages.current.delete(id);
  };
  const unregister = (id) => stages.current.delete(id);

  // master frame = the first stage's game frame minus its own in-point
  const masterFrame = () => {
    const first = stages.current.values().next().value;
    if (!first || !first.el) return 0;
    return Math.max(0, gameFrameOf(first.el) - (first.getInFrame() || 0));
  };

  const seekAll = (master) => {
    for (const { el, getInFrame } of stages.current.values()) {
      if (!el) continue;
      const t = ((getInFrame() || 0) + master + 0.5) / 30;
      el.currentTime = Math.min(Math.max(t, 0), el.duration || 0);
    }
  };

  const play = () => {
    for (const { el } of stages.current.values())
      if (el) el.play().catch(() => {});
    setPlaying(true);
  };
  const pause = () => {
    const m = masterFrame();
    for (const { el } of stages.current.values()) if (el) el.pause();
    seekAll(m);                        // re-sync on pause (corrects drift)
    setPlaying(false);
  };
  const step = (dir) => {
    const m = Math.max(0, masterFrame() + dir);
    for (const { el } of stages.current.values()) if (el && !el.paused) el.pause();
    seekAll(m);
    setPlaying(false);
  };
  const toStart = () => {
    for (const { el } of stages.current.values()) if (el) el.pause();
    seekAll(0);
    setPlaying(false);
  };

  return { register, unregister, play, pause, step, toStart, playing };
}

export function VideoStage({ src, inFrame, controller, id }) {
  const ref = useRef(null);
  const inRef = useRef(inFrame || 0);
  useEffect(() => { inRef.current = inFrame || 0; }, [inFrame]);
  useEffect(() => () => controller.unregister(id), [id]);
  return html`<video class="replay-player" style="width:100%" preload="auto"
      src=${src} playsinline
      ref=${(el) => {
        ref.current = el;
        controller.register(id, el, () => inRef.current);
        if (el && !el.dataset.vol) { el.dataset.vol = "1"; el.volume = storedVolume(); }
      }}></video>`;
}

// Dual-handle in/out selector over the video duration (game frames).
export function SyncTrack({ video, inFrame, outFrame, onChange }) {
  const [dur, setDur] = useState(0);
  useEffect(() => {
    const v = video && video.current;
    if (!v) return;
    const on = () => setDur(v.duration || 0);
    v.addEventListener("loadedmetadata", on);
    if (v.duration) setDur(v.duration);
    return () => v.removeEventListener("loadedmetadata", on);
  }, [video && video.current]);
  const maxF = Math.max(1, Math.floor(dur * 30));
  const preview = (f) => { const v = video && video.current;
    if (v) v.currentTime = f / 30; };
  return html`<div class="synctrack" style="margin:.3rem 0">
    <label class="meta">start
      <input type="range" min="0" max=${maxF} step="1" value=${inFrame || 0}
        oninput=${(e) => { const f = Number(e.target.value); preview(f);
          onChange({ in_frame: f, out_frame: outFrame }); }} />
    </label>
    <label class="meta">end
      <input type="range" min="0" max=${maxF} step="1"
        value=${outFrame == null ? maxF : outFrame}
        oninput=${(e) => { const f = Number(e.target.value); preview(f);
          onChange({ in_frame: inFrame || 0, out_frame: f }); }} />
    </label>
    <span class="meta">${((inFrame || 0) / 30).toFixed(2)}s –
      ${((outFrame == null ? maxF : outFrame) / 30).toFixed(2)}s</span>
  </div>`;
}
```

- [ ] **Step 2: Smoke-test deferred**

`videosync.js` is exercised by Task 10 (nothing imports it yet). No standalone check; verify it parses by loading the app (no console error from the module graph) after Task 10.

- [ ] **Step 3: Commit**

```bash
git add src/sm64_events/ui/components/videosync.js
git commit -m "feat(ui): videosync — sync controller, VideoStage, SyncTrack"
```

---

## Task 10: compare.js — the Compare tab

**Files:**
- Create: `src/sm64_events/ui/components/compare.js`

**Interfaces:**
- Consumes: `videosync.js` (`useSyncController`, `VideoStage`, `SyncTrack`), `api.js` (`getJSON`, `send`), the session view (`GET /api/session?scope=lifetime`), replay extract (`POST /api/attempts/{id}/replay`), compare endpoints (Task 7).
- Produces: `export function Compare({ t, intent, clearIntent })` — `intent` is `{attemptId, entity, strat}|null` from a deep link (Task 11).

- [ ] **Step 1: Create the component**

Create `src/sm64_events/ui/components/compare.js`:

```javascript
// src/sm64_events/ui/components/compare.js — side-by-side comparison tab.
// Left = my run (reuses the replay extract/serve pipeline by attempt_id).
// Right = comparison video(s) normalized to local mp4 (yt-dlp/file import).
// One centered transport (useSyncController) drives every <video> in lockstep.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { useSyncController, VideoStage, SyncTrack } from "./videosync.js";

const html = htm.bind(h);

// entity_key <-> section helpers
function entityOf(sec) {
  return sec.kind === "segment"
    ? `segment:${sec.segment_id}`
    : `star:${sec.course_id}:${sec.star_id}`;
}
function sectionLabel(sec) {
  return sec.kind === "segment" ? `⏱ ${sec.name}`
    : `${sec.course_name} · ${sec.star_name}`;
}

// ---- left: my run ----------------------------------------------------------
function MyRunPicker({ view, entity, attemptId, onPick }) {
  const sections = [...(view.stars || []), ...(view.segments || [])];
  const cur = sections.find((s) => entityOf(s) === entity) || sections[0];
  const runs = cur
    ? cur.attempts.filter((a) => !a.cleared && a.outcome === "success")
    : [];
  return html`<div class="compare-pick">
    <select value=${cur ? entityOf(cur) : ""}
        onchange=${(e) => onPick(e.target.value, null)}>
      ${sections.map((s) => html`<option value=${entityOf(s)}>${sectionLabel(s)}</option>`)}
    </select>
    <select value=${attemptId ?? ""}
        onchange=${(e) => onPick(entityOf(cur), Number(e.target.value))}>
      <option value="">— pick a run —</option>
      ${runs.map((a) => html`<option value=${a.id}>#${a.id} · ${a.igt || a.rta || "?"}
        ${a.strat_tag ? `· ${a.strat_tag}` : ""}</option>`)}
    </select>
  </div>`;
}

function MyRunStage({ attemptId, controller }) {
  const [st, setSt] = useState({ phase: "idle" });
  useEffect(() => {
    if (attemptId == null) { setSt({ phase: "idle" }); return; }
    let alive = true;
    setSt({ phase: "loading" });
    send("POST", `/api/attempts/${attemptId}/replay`)
      .then((r) => alive && setSt({ phase: "ready", ...r }))
      .catch((e) => alive && setSt({ phase: "error", message: String(e) }));
    return () => { alive = false; };
  }, [attemptId]);
  if (st.phase === "idle") return html`<div class="meta">Pick one of your runs on the left.</div>`;
  if (st.phase === "loading") return html`<div class="meta">extracting replay…</div>`;
  if (st.phase === "error")
    return html`<div class="badx">run footage unavailable</div>
      <div class="meta">${st.message}</div>`;
  return html`<${VideoStage} id="mine" src=${st.clip_url} inFrame=${0}
    controller=${controller} />`;
}

// ---- right: comparisons ----------------------------------------------------
function AddComparison({ entity, strat, suggestion, onAdded }) {
  const [job, setJob] = useState(null);
  const [url, setUrl] = useState("");
  const fileRef = useRef(null);

  async function startImport(source_kind, source_ref, name) {
    const r = await send("POST", "/api/compare/import",
      { entity_key: entity, strat, name, source_kind, source_ref });
    pollJob(r.job_id);
  }
  function pollJob(jobId) {
    setJob({ state: "running", progress: 0 });
    const id = setInterval(async () => {
      try {
        const s = await getJSON(`/api/compare/import/${jobId}`);
        setJob(s);
        if (s.state === "done") { clearInterval(id); setJob(null); onAdded(); }
        else if (s.state === "error") clearInterval(id);
      } catch { clearInterval(id); }
    }, 800);
  }
  function onDrop(e) {
    e.preventDefault();
    const f = e.dataTransfer.files[0];
    if (f) startImport("file", f.path || f.name, f.name);
  }

  if (strat == null)
    return html`<div class="meta">Select a strategy in Practice to enable comparisons.</div>`;
  return html`<div class="compare-add" ondragover=${(e) => e.preventDefault()}
      ondrop=${onDrop}>
    ${suggestion && html`<button onclick=${() =>
        startImport(suggestion.source_kind, suggestion.source_ref, suggestion.name)}>
      ▸ Load ${suggestion.name}</button>`}
    <input placeholder="paste a YouTube URL" value=${url}
      oninput=${(e) => setUrl(e.target.value)} />
    <button disabled=${!url} onclick=${() =>
      startImport("youtube", url, url)}>Add URL</button>
    <button onclick=${() => fileRef.current && fileRef.current.click()}>Choose file…</button>
    <input type="file" accept="video/*" style="display:none" ref=${fileRef}
      onchange=${(e) => { const f = e.target.files[0];
        if (f) startImport("file", f.path || f.name, f.name); }} />
    <span class="meta"> or drag a video here</span>
    ${job && job.state === "running" && html`<div class="meta">
      loading… ${Math.round((job.progress || 0) * 100)}% ${job.message || ""}</div>`}
    ${job && job.state === "error" && html`<div class="badx">import failed: ${job.message}</div>`}
  </div>`;
}

function ComparisonStage({ comp, controller, onEdit, onDelete }) {
  const vref = useRef(null);
  return html`<div>
    <div class="shead"><b>${comp.name}</b>
      <button class="meta" onclick=${() => onDelete(comp.id)} title="remove">×</button></div>
    <${VideoStage} id=${`cmp:${comp.id}`} src=${comp.clip_url}
      inFrame=${comp.in_frame || 0} controller=${controller} />
    <${SyncTrack} video=${vref} inFrame=${comp.in_frame} outFrame=${comp.out_frame}
      onChange=${(pts) => onEdit(comp.id, pts)} />
  </div>`;
}

// ---- transport -------------------------------------------------------------
function Transport({ controller }) {
  return html`<div class="compare-transport">
    <button onclick=${() => controller.toStart()} title="jump to beginning">⏮ start</button>
    <button onclick=${() => controller.step(-1)} title="back one frame">⏴ frame</button>
    <button onclick=${() => controller.playing ? controller.pause() : controller.play()}
      style="min-width:5.5rem">${controller.playing ? "❚❚ pause" : "▶ play"}</button>
    <button onclick=${() => controller.step(1)} title="forward one frame">frame ⏵</button>
    <div class="meta">1 frame = 1/30 s (game frame)</div>
  </div>`;
}

export function Compare({ t, intent, clearIntent }) {
  const controller = useSyncController();
  const [view, setView] = useState(null);          // lifetime session view
  const [entity, setEntity] = useState(null);
  const [strat, setStrat] = useState(null);
  const [attemptId, setAttemptId] = useState(null);
  const [cmp, setCmp] = useState({ saved: [], suggestion: null });

  // load the lifetime session view once (left-side picker source)
  useEffect(() => {
    getJSON("/api/session?scope=lifetime").then((v) => {
      setView(v);
      // default: the live target's section, else the first section
      const tgt = v.target || {};
      const def = tgt.kind === "segment" ? `segment:${tgt.segment_id}`
        : tgt.course_id != null ? `star:${tgt.course_id}:${tgt.star_id}` : null;
      if (!intent) setEntity(def);
    }).catch(() => {});
  }, []);

  // apply a deep-link intent (Compare button from Practice)
  useEffect(() => {
    if (!intent) return;
    setEntity(intent.entity);
    setStrat(intent.strat);
    setAttemptId(intent.attemptId);
    clearIntent();
  }, [intent]);

  // resolve the active strat for the chosen entity from the session view
  useEffect(() => {
    if (!view || !entity) return;
    const secs = [...(view.stars || []), ...(view.segments || [])];
    const sec = secs.find((s) => entityOf(s) === entity);
    if (sec && intent == null) setStrat(sec.last_strat || null);
  }, [entity, view]);

  // fetch comparisons + auto-pick whenever (entity, strat) changes
  const reloadCmp = () => {
    if (!entity) return;
    getJSON(`/api/compare/view?entity=${encodeURIComponent(entity)}`
      + (strat ? `&strat=${encodeURIComponent(strat)}` : ""))
      .then(setCmp).catch(() => {});
  };
  useEffect(reloadCmp, [entity, strat]);

  async function editCmp(id, pts) {
    await send("PUT", `/api/compare/videos/${id}`, pts);
    reloadCmp();
  }
  async function delCmp(id) {
    await send("DELETE", `/api/compare/videos/${id}`);
    reloadCmp();
  }
  function pickRun(ent, aid) {
    setEntity(ent);
    if (aid != null) setAttemptId(aid);
  }

  if (!view) return html`<p class="meta">loading…</p>`;
  // auto-selected saved comparison shows by default; others are addable
  const shown = cmp.saved;
  const suggestion = cmp.auto && cmp.auto.mode === "suggestion" ? cmp.suggestion : null;

  return html`<div class="compare">
    <div class="compare-grid">
      <div class="compare-col">
        <div class="meta listhead">My run</div>
        <${MyRunPicker} view=${view} entity=${entity} attemptId=${attemptId}
          onPick=${pickRun} />
        <${MyRunStage} attemptId=${attemptId} controller=${controller} />
      </div>
      <div class="compare-center">
        <${Transport} controller=${controller} />
      </div>
      <div class="compare-col">
        <div class="meta listhead">Comparison ${strat ? `· ${strat}` : ""}</div>
        ${shown.map((c) => html`<${ComparisonStage} key=${c.id} comp=${c}
          controller=${controller} onEdit=${editCmp} onDelete=${delCmp} />`)}
        <${AddComparison} entity=${entity} strat=${strat} suggestion=${suggestion}
          onAdded=${reloadCmp} />
      </div>
    </div>
  </div>`;
}
```

- [ ] **Step 2: Add minimal layout CSS**

In `src/sm64_events/ui/index.html`, add to the `<style>` block:

```css
.compare-grid { display:flex; gap:1rem; align-items:flex-start; }
.compare-col { flex:1; min-width:0; }
.compare-center { display:flex; flex-direction:column; align-items:center; padding-top:2rem; }
.compare-transport { display:flex; flex-direction:column; gap:.4rem; align-items:center; }
.compare-transport button { min-width:7rem; }
@media (max-width:900px){ .compare-grid{ flex-direction:column; } }
```

- [ ] **Step 3: Smoke-test (deferred to Task 11)**

The tab isn't reachable until Task 11 mounts it. Verify parse-clean load after Task 11.

- [ ] **Step 4: Commit**

```bash
git add src/sm64_events/ui/components/compare.js src/sm64_events/ui/index.html
git commit -m "feat(ui): Compare tab (my-run vs comparison, synced transport)"
```

---

## Task 11: Compare tab wiring + per-replay deep link

**Files:**
- Modify: `src/sm64_events/ui/app.js` (add tab + `compareIntent`)
- Modify: `src/sm64_events/ui/components/practice.js` (Compare button on each attempt)
- Modify: `src/sm64_events/ui/components/replay.js` (accept an `onCompare` prop; render the button)

**Interfaces:**
- Consumes: `Compare` (Task 10).
- Produces: a `compareIntent` state in `App`, an `openCompare(intent)` callback threaded to the attempt rows.

- [ ] **Step 1: Add the tab and intent in app.js**

In `src/sm64_events/ui/app.js`:

Add the import:
```javascript
import { Compare } from "./components/compare.js";
```
Change `TABS`:
```javascript
const TABS = ["Practice", "Segments", "Routes", "Run", "Compare", "Live feed"];
```
Inside `App()`, add state and an opener, and pass to Practice + Compare:
```javascript
  const [compareIntent, setCompareIntent] = useState(null);
  const openCompare = (intent) => { setCompareIntent(intent); setTab("Compare"); };
```
Update the pane dispatch:
```javascript
      ${tab === "Practice" ? html`<${Practice} t=${t} openCompare=${openCompare} />`
        : tab === "Segments" ? html`<${Segments} t=${t} />`
        : tab === "Routes" ? html`<${Routes} t=${t} />`
        : tab === "Run" ? html`<${Run} t=${t} />`
        : tab === "Compare" ? html`<${Compare} t=${t} intent=${compareIntent}
            clearIntent=${() => setCompareIntent(null)} />`
        : html`<${Feed} t=${t} />`}
```

- [ ] **Step 2: Thread openCompare to attempt rows in practice.js**

In `src/sm64_events/ui/components/practice.js`:

- `export function Practice({ t })` → `export function Practice({ t, openCompare })`.
- Thread `openCompare` down: `StarSection`/`SegmentSection` accept it and pass to `AttemptTable` → `AttemptRow`. Add `openCompare` to each component's props and to the `AttemptTable` mapping:
  ```javascript
  // in AttemptTable(...) props add openCompare, and in the row:
  <${AttemptRow} key=${a.id} a=${a} t=${t} idx=${idx}
    focus=${focus} clearFocus=${clearFocus}
    isNew=${freshIds ? freshIds.has(a.id) : false}
    openCompare=${openCompare} sec=${sec} />
  ```
  (Pass `sec=${sec}` from `StarSection`/`SegmentSection` so the row knows its entity/strat; add `sec` and `openCompare` to `AttemptTable`'s destructured props and forward them.)
- In `AttemptRow`, compute the entity + strat and pass an `onCompare` into the expanded `ReplayPlayer`:
  ```javascript
  const entity = a.segment_id != null ? `segment:${a.segment_id}`
    : `star:${a.course_id}:${a.star_id}`;
  const onCompare = openCompare
    ? () => openCompare({ attemptId: a.id, entity, strat: a.strat_tag || (sec && sec.last_strat) || null })
    : null;
  // expanded row:
  <${ReplayPlayer} attemptId=${a.id} onCompare=${onCompare} />
  ```

> Note: star attempts carry `course_id`/`star_id` on the section, not the attempt row (`_attempt_json` omits them for star rows). Derive entity from `sec` for stars and from `a.segment_id` for segments:
> ```javascript
> const entity = a.segment_id != null ? `segment:${a.segment_id}`
>   : (sec ? `star:${sec.course_id}:${sec.star_id}` : null);
> ```

- [ ] **Step 3: Render the Compare button in replay.js**

In `src/sm64_events/ui/components/replay.js`, change the signature:
```javascript
export function ReplayPlayer({ attemptId, onCompare }) {
```
In the bottom controls `<div>` (the Save Replay row, ~line 133), add after the Save button block:
```javascript
      ${onCompare && html` <button onclick=${onCompare}
          title="open this run in the Compare tab">⇆ Compare</button>`}
```

- [ ] **Step 4: Smoke-test the whole flow**

Start the server; open the app. Verify with the `frontend-smoke-test` skill:
1. **Compare tab** appears; opens on the most-recently-active star/segment; picking a run loads the left video; the transport plays/pauses/steps/jumps both videos in lockstep.
2. **Add flows**: paste a short YouTube URL → progress → it plays and is saved; "Choose file…" imports a local file; drag-drop imports.
3. **Auto-select**: with a strat that has a rank-standard video and no saved comparison, the "▸ Load …" suggestion appears; after loading, reopening the tab auto-shows it.
4. **Deep link**: Practice → expand a replay → **⇆ Compare** opens the tab with that run as MINE and its strat's comparison auto-selected.
5. **Sync points**: dragging the start/end handles re-aligns a comparison; console stays clean.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/ui/app.js src/sm64_events/ui/components/practice.js src/sm64_events/ui/components/replay.js
git commit -m "feat(ui): Compare tab wiring + per-replay deep-link button"
```

---

## Task 12: Bundle yt-dlp in the build + docs

**Files:**
- Modify: `tools/build_exe.py` (ensure yt-dlp is collected)
- Modify: `README.md` (API surface: compare endpoints + Compare tab), `CLAUDE.md` (module map rows), `docs/architecture.md` (if domain knowledge gained)

**Interfaces:** none (packaging + docs).

- [ ] **Step 1: Ensure yt-dlp is collected by PyInstaller**

`tools/build_exe.py` has a `COLLECT` list (line ~19) it feeds to `--collect-all` for each entry. yt-dlp imports its extractors lazily by name, so `--collect-all` is required or the frozen exe raises `No module named yt_dlp.extractor...` at download time. Add `"yt_dlp"` to the list:

```python
COLLECT = ["av", "windows_capture", "pyaudiowpatch", "pycaw", "comtypes",
           "pymem", "webview", "pystray", "numpy", "yt_dlp"]
```

- [ ] **Step 2: Build and verify yt-dlp is frozen (release-time / human-audit)**

Run (needs ffmpeg on PATH, per the build's existing requirement):
`uv run python tools/build_exe.py`
Expected: `Built: .../dist/SM64Trainer.exe`. Then run the exe, open the Compare tab, and import a short YouTube comparison to confirm yt-dlp works frozen. (This is the release-build gate; if not building an exe now, at minimum confirm `COLLECT` contains `yt_dlp` and defer the exe run to release.)

- [ ] **Step 3: Update the module map (CLAUDE.md)**

Add rows to the "Module map" table:

```
| Side-by-side compare (import jobs, CRUD, view, serve) | `compare/service.py` — composes `compare/importer.py` (yt-dlp/copy → ffmpeg-normalize → content-addressed cache in `core/paths.compare_cache_dir()`, dedup = "load once") + `storage/db.py` comparisons (v10). Pure bits (cache name, four-branch auto-select, offset-only sync math) in `tracking/comparisons.py`; payload in `tracking/views.py::build_compare_view` |
| Compare REST surface | `server/compare_api.py` — `/api/compare/view`, `import` (+ poll `import/{job}`), `videos/{id}` PUT/DELETE, `cache/{name}` (Range/206) |
| Compare tab UI | `ui/components/compare.js` (my-run vs comparison, one centered transport) + `ui/components/videosync.js` (`useSyncController` drives N `<video>` in lockstep, `SyncTrack` in/out handles) + `ui/frame.js` (shared game-frame step/jump, also used by replay.js) |
```

- [ ] **Step 4: Update the README API surface**

Add the compare endpoints to the REST/WS section of `README.md` (mirror how routes/ranks are documented): the six `/api/compare/*` routes, the `comparisons_changed` broadcast, and a one-line description of the Compare tab (left = your run by attempt, right = imported comparison videos, synced frame-accurate transport).

- [ ] **Step 5: Full verification**

Run: `uv run pytest -q`
Expected: whole suite green.

- [ ] **Step 6: Commit**

```bash
git add tools/build_exe.py CLAUDE.md README.md docs/architecture.md
git commit -m "build+docs: bundle yt-dlp; document compare surface"
```

---

## Final verification (before merge)

- [ ] `uv run pytest -q` — whole suite green.
- [ ] Compare tab manual pass (Task 11 Step 4 checklist), **human-audit** the frame-sync feel (I can't playtest): do the two videos stay aligned when scrubbing frame-by-frame after setting sync points?
- [ ] Built exe imports a YouTube comparison (yt-dlp frozen) — human-audit.
- [ ] Module map + README updated; `docs/architecture.md` gains any hard-won facts (e.g. yt-dlp `--collect-all` requirement, offset-only sync rationale).
- [ ] Delete-in-Explorer of a cache file → the comparison shows "broken" gracefully (no crash) — if not yet handled, file a follow-up (spec §9; the serving 404 already degrades, but the UI "broken" affordance is a nice-to-have that can land in a follow-up if descoped).

## Notes / deliberate scope calls

- **N-up UI deferred** (spec §12): the layer is a list (`cmp.saved.map(...)`, the controller registers by id), so rendering 2…N comparison stages is a UI-only extension. v1 shows the saved list already (usually one).
- **Import completion is not broadcast** (only update/delete publish `comparisons_changed`): the initiating client refetches on job-done; comparisons are a focused single-user surface. Revisit if cross-client live sync is wanted.
- **`f.path` on dropped/picked files**: Electron/pywebview expose a real filesystem path; a plain browser may not. In the desktop GUI (the primary surface) this works; if a browser-only file lacks a path, the import falls back to the file name and errors clearly (LookupError → visible message) rather than silently mis-importing. A true browser upload path (multipart) is a follow-up if needed.
