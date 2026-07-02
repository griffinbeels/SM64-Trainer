# Side-by-Side Comparison — Design Spec

**Date:** 2026-07-02
**Status:** Approved (design), pending implementation plan
**Author:** Claude (brainstorming session)

## 1. Summary

A new **Compare** tab that plays the user's own gameplay next to one or more
comparison videos, all driven by a single frame-accurate transport (play,
pause, frame ±1, jump-to-beginning) so the user can scrub two runs frame by
frame in lockstep. The comparison side accepts local files and YouTube links;
by default it auto-loads the rank-standard video for the star/segment's active
strategy. The whole feature composes existing pieces — after every video is
normalized to a local `.mp4`, both sides are the project's existing
frame-accurate `<video>` player.

The intent: from Practice, click **Compare** and immediately be looking at
"how did my Ledgegrab differ from the rank-standard Ledgegrab," then click back
to Practice. Practice → Compare → Practice must feel seamless.

## 2. Goals & non-goals

**Goals**
- Reuse the existing replay player and its game-frame stepping.
- Two (eventually N) videos on screen, one centered control bar driving all in sync.
- Per-video draggable start/end (sync) points so runs that begin at different
  offsets can be aligned manually.
- Left = my gameplay: pick any star/segment → run history → a specific run,
  loaded through the existing extract/serve pipeline.
- Right = comparison: drag-drop file, file-picker, or YouTube URL.
- Comparisons are saved (loaded only once) and associated with a specific
  `(star/segment, strategy)` combination; multiple comparisons per strategy.
- Auto-load the right comparison from the active strategy, by a priority order.
- New player control **jump-to-beginning**, on both Practice and Compare.
- Robust to files deleted in Explorer or in-tool.

**Non-goals (v1)**
- The N-up multi-grid **UI** (data/sync/control layer is built for N; only the
  2-up layout ships — see §12).
- Frame-perfect *rate* alignment beyond offset (see §5).
- Editing/trimming that re-encodes the source (sync points are non-destructive
  playback bounds, not cuts).

## 3. Key decisions

1. **Download & normalize everything to local files.** On first load, YouTube
   is fetched via **yt-dlp** + the bundled **ffmpeg**, and dropped/picked files
   are copied, into a content-addressed cache. Every video — mine and
   comparison — is then a plain `<video>`, giving true frame-accurate stepping
   everywhere, a single uniform player, offline playback, and a natural fit for
   "load once, save it." yt-dlp is a new dependency and **is bundled in the
   release build**.
2. **One-click Load for the first download.** The tab auto-*selects* the right
   video (fills the slot with its source + name) but does **not** silently
   fetch a possibly-large VOD; the user clicks **Load** to download+normalize
   (with a progress bar). Every load after the first is instant from cache.
   *(Away-default; user may revisit.)*
3. **Ship polished 1-vs-1; build the layer for N.** The storage, sync, and
   control layer treat comparisons as a **list**, so adding the N-up grid later
   is a UI-only change with no schema/plumbing rework. *(Away-default; user may
   revisit.)*
4. **Offset-only sync.** Sync points align the *start moment* of each video;
   playback rate is assumed equal (true for two recordings of SM64's 30 fps
   game logic). No rate warping.
5. **Download at ≤720p**, normalized to a `faststart` mp4 so seeking and
   frame-stepping are reliable. SM64 content needs no more resolution.

## 4. Data model & storage

### 4.1 `comparisons` table (migration v10)

Config (like `routes`/`segment_defs`), not history — never rebuilt from the
journal.

```
id            INTEGER PRIMARY KEY AUTOINCREMENT
entity_key    TEXT NOT NULL     -- "star:<course>:<star>" | "segment:<id>" (ranks.entity_key)
strat         TEXT NOT NULL     -- strategy name; the (entity_key, strat) pair is the association
name          TEXT NOT NULL     -- user-facing label
source_kind   TEXT NOT NULL     -- 'youtube' | 'file'
source_ref    TEXT NOT NULL     -- original URL or original picked path (dedup identity)
cache_name    TEXT NOT NULL     -- normalized mp4 filename in the content cache
in_frame      INTEGER           -- sync start (game frames); NULL = clip start
out_frame     INTEGER           -- sync end   (game frames); NULL = clip end
created_utc   TEXT NOT NULL
last_used_utc TEXT NOT NULL     -- drives "most recently selected"
```

CRUD in `storage/db.py` mirrors the `routes` helpers
(`comparisons()`, `insert_comparison()`, `update_comparison()`,
`delete_comparison()`).

### 4.2 Content cache (`data/compare_cache/`)

Normalized mp4s named by a hash of `source_ref`. **"Load once" = dedup here:**
importing a `source_ref` already present reuses the file; the new row just
points at the same `cache_name`. Path resolves via `core/paths.py` (cwd-relative
from source, `%LOCALAPPDATA%` when frozen), like every other runtime data
location.

### 4.3 Auto-selection resolver (feature #6)

Computed server-side (`build_compare_view`), given `(entity_key, strat)`:

1. Most recent saved comparison for `(entity_key, strat)` by `last_used_utc`.
2. Else the rank-standard video: `ranks.video_for(entity_key, strat)` (a YouTube
   URL) → surfaced as a one-click **Load** suggestion (not yet a saved row).
3. Else empty, with an "add a comparison" affordance.
4. No active strat → empty (nothing to key on).

## 5. Sync / transport layer

`ui/components/videosync.js`:

- **`VideoStage`** — a thin `<video>` wrapper (shared-volume via the existing
  `replay.js` mechanism) exposing an imperative handle: `play()`, `pause()`,
  `seekToGameFrame(n)`, `currentGameFrame()`.
- **`SyncController`** — owns the master transport `{ playing, masterFrame }`
  and a list of registered stages, each with an `in_frame` offset. Fan-out:
  - **play/pause** → every stage `play()`/`pause()`. Stages stay aligned because
    they start aligned and run at true rate; a light **re-sync on pause**
    (re-seek each to `in_frame + masterFrame`) corrects long-play drift.
  - **frame ±1 / jump-to-beginning** → compute `masterFrame`, seek each stage to
    `(in_frame + masterFrame + 0.5) / 30` (the exact-frame math already in
    `replay.js::step`, which avoids straddling frame boundaries).
  - **sync in/out** → draggable per-video handles set `in_frame`/`out_frame`;
    changing `in_frame` changes that stage's offset only. Non-destructive.

The stepping/jump logic currently inline in `replay.js` is extracted into a
shared helper so Practice (single video) and Compare (N stages) share one
implementation; both gain jump-to-beginning (feature #5).

Steps move in **game frames (30 fps)** regardless of each source's encoded fps —
1 game frame = 1/30 s of true-speed playback on every clip, so heterogeneous
encode rates still step by the same game moment.

## 6. Server surface (`server/compare_api.py`)

Same error taxonomy as `api.py`/`replay_api.py` (LookupError→404, ValueError→409,
RuntimeError→503). Import is CPU/GPU/network-bound → sync `def` in the FastAPI
threadpool, tracked as a job for progress.

- `GET /api/compare/view?entity=&strat=` → `{ saved: [...], auto: {...}|null,
  suggestion: {source_kind, source_ref, name}|null }` — saved comparisons for
  the pair + the resolved auto-pick + the rank-standard suggestion.
- `POST /api/compare/import` `{entity_key, strat, name?, source_kind, source_ref}`
  → starts an import job; returns `{job_id}`.
- `GET /api/compare/import/{job_id}` → `{state: pending|running|done|error,
  progress: 0..1, message?, comparison?}` (poll, mirroring the update-apply
  poll already in `store.js`).
- `PUT /api/compare/videos/{id}` `{name?, in_frame?, out_frame?, touch?}` → edit
  sync points / name; `touch` bumps `last_used_utc`.
- `DELETE /api/compare/videos/{id}` → remove row (+ cache file if unreferenced).
- `GET /api/compare/cache/{name}` → `FileResponse` with native Range/206 (same
  as clip serving).

Left side + run history reuse **existing** endpoints untouched:
`GET /api/session?scope=lifetime` (picker + run history),
`POST /api/attempts/{id}/replay` (extract/cache/serve),
`GET /api/replay/clips|saved/...` (serve).

The import service (`compare/importer.py`) wraps yt-dlp/ffmpeg/file-copy behind
a boundary — external tools are **never** invoked from a route (coding
standard). Injectable so tests fake it. Broadcasts `comparisons_changed` on
create/delete (like `routes_changed`).

## 7. UI (`ui/components/compare.js`)

- **New "Compare" tab** in `ui/app.js` `TABS`.
- Layout: **[ MINE ] · [ centered controls ] · [ COMPARISON(s) ]**. Control bar:
  `⏮ start · ⏴ frame · ▶/❚❚ play · frame ⏵`, one bar driving all stages.
- **Left panel (mine):** star/segment picker from the lifetime session view →
  run-history list (that section's `attempts`) → pick a run → its `attempt_id`
  drives the existing replay pipeline:
  - cached clip exists → load it;
  - extractable from the ring → extract (same as Practice);
  - gone from cache and ring → error ("this run's footage is no longer
    available").
- **Right panel (comparison):** the resolved comparison + a picker of saved
  comparisons for the active strat. **Add** via drag-drop, file picker, or paste
  YouTube URL → import job → progress bar → plays + saved. Per-video sync
  in/out handles.
- **Two entry points (from the screenshots):**
  1. The **Compare tab** opens on the most-recently-active star/segment, with a
     run selector.
  2. A **Compare button under each replay** in the Practice attempt player:
     opens the tab with that attempt preloaded as MINE and that attempt's strat's
     comparison auto-selected. Hand-off via a small `compareIntent` state lifted
     to `app.js`.
- **Browser ↔ GUI parity** (domain rule #10): everything is `ui/` + server, so it
  appears in both the browser tab and the desktop window.

## 8. New / changed files

**New**
- `tracking/comparisons.py` — pure: dedup key, auto-select priority, sync-frame
  math, row shape.
- `compare/importer.py` — yt-dlp download + ffmpeg normalize + file copy;
  content-addressed cache; progress hook; injectable.
- `server/compare_api.py` — REST surface.
- `ui/components/compare.js` — the Compare tab.
- `ui/components/videosync.js` — `SyncController` + `VideoStage`.
- shared frame/jump helper (extracted from `replay.js`).
- Tests: `tests/test_comparisons.py`, `tests/test_compare_api.py`,
  `tests/test_compare_importer.py`.

**Changed**
- `storage/db.py` — `comparisons` table (v10) + CRUD.
- `tracking/service.py` — import/delete/touch commands + `comparisons_changed`
  broadcast.
- `tracking/views.py` — `build_compare_view` (resolver + display).
- `main.py` — wire the compare router + importer service.
- `ui/app.js` — Compare tab + `compareIntent`.
- `ui/components/practice.js` / `replay.js` — Compare button + jump-to-beginning.
- `ui/store.js` — refetch on `comparisons_changed` where relevant.
- `tools/build_exe.py` — bundle yt-dlp (hidden imports as needed).
- `pyproject.toml` — add `yt-dlp`.
- `README.md` (API surface) + `CLAUDE.md` module map + `docs/architecture.md` if
  domain knowledge is gained.

## 9. Error handling & robustness

- **Cache file deleted on disk** (Explorer) → comparison marked **broken** in the
  view; offers re-download (source_ref retained) or removal. Self-healing: the
  next view sees the missing file.
- **Failed download** (age/geo-gated, network) → job ends `error` with yt-dlp's
  message; no row created.
- **Delete-in-tool** → removes the row and the cache file **iff** no other row
  references that `cache_name`.
- **Left-side footage gone** → the existing replay error surfaces ("run no
  longer available"), exactly as Practice.

## 10. Testing

- **Pure** (`test_comparisons.py`): dedup identity, auto-select priority order
  (all four branches), sync-frame math.
- **Importer** (`test_compare_importer.py`): yt-dlp/ffmpeg mocked at the
  boundary; asserts dedup reuse, cache naming, progress reporting, error
  propagation.
- **API** (`test_compare_api.py`): import job lifecycle with a faked importer,
  CRUD, sync-point PUT, cache serving, `comparisons_changed`.
- **Human-audit**: actual multi-video visual sync / game-feel of scrubbing (I
  can't playtest feel) — a checklist item at merge.

## 11. Definition of done

- `uv run pytest -q` passes; new behavior has tests.
- Module map (`CLAUDE.md`) and README API surface updated; architecture.md if
  domain knowledge gained.
- yt-dlp bundled and verified in the built exe.
- Human-audit checklist for the Compare tab (sync feel, all three add paths,
  deep-link from Practice, jump-to-beginning on both tabs).

## 12. Future (explicitly deferred)

- **N-up grid UI**: render 2…N comparison stages in a responsive grid, all in
  lockstep. Storage/sync/control already support the list; this is a UI-only
  add with a sensible cap (perf: simultaneous `<video>` decodes).
- **Section-only download** (yt-dlp `--download-sections`) if full-VOD size
  becomes a problem — deferred; ≤720p full download is the v1 baseline.

## 13. Open items for user review

- Confirm the two away-defaults (§3.2 one-click Load, §3.3 1-vs-1 first).
- Confirm offset-only sync (§5) is sufficient (no rate alignment).
