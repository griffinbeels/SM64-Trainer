# Route Builder + Route Practice + Full-Game Run Timer — Design

**Date:** 2026-06-14
**Status:** Approved design (pre-implementation)
**Scope:** One spec, three layers, built in five phases.

## 1. Summary

Add **routes** to the tracker: an ordered, shareable plan composed of the stars
and segments you already practice. A route powers three things:

1. **Route builder** — create/edit routes, reorder steps, define "K of N"
   groups, see a **cumulative success %** at each step, and **import/export**
   a route as a self-contained, copy-pastable JSON string.
2. **Route Practice mode** — a non-destructive focus layer on the existing
   Practice tab that shows only the route's members, in route order, with a
   suggested current/next step. Retry any step freely.
3. **Run mode** — run the whole route as a forgiving real-time speedrun with an
   app-maintained global timer, per-step splits, PB run + gold splits, a calming
   **Focus mode**, **click-to-hide** on any timer, and saved run history with a
   progression graph.

The infrastructure already largely exists. Stars and segments are a unified
**target** (`("star", c, s)` | `("segment", id)`), segments live in a DB table
with ids, per-item **success rate is already a stat** (`stats/registry.py`), the
session view already emits `stars[]`/`segments[]` sections, and `app.js` has a
disabled **"Routes" tab** stub. This design adds two new tables, two pure
server modules, view payloads, API endpoints, and the Routes-tab UI.

### Architecture (chosen: server-authoritative, projection-consistent)

Mirrors how segments + attempts already work: the journal is the source of
truth, the server computes, the UI renders. Route logic and a pure
`RunTracker` projector live server-side, so everything is replayable and
pytest-testable; the live clock ticks client-side from an authoritative start
time. (Alternatives B "run logic in JS" and C "everything in `ui_state` KV"
were rejected for being untestable / not queryable / breaking the
`segment_defs` precedent.)

## 2. Goals / Non-goals

**Goals**
- Define a route as an ordered list of steps; each step a single star/segment
  or a "complete K of N" group.
- Cumulative success % per step from lifetime history; no data ⇒ 0%.
- Non-destructive route-focused practice with free retries.
- Forgiving full-game RTA run timer with splits, PB/gold, history, graph.
- Self-contained import/export for sharing routes.

**Non-goals (this spec)**
- No new memory addresses or detectors (we reuse the existing `game_reset`).
- No multi-player / network sync; one active run at a time.
- No automatic route generation or optimization.

## 3. Settled decisions (from brainstorming)

- **One spec**, all three layers, five phases (§9).
- **Uniform step shape**: every step is `{label?, need:K, candidates:[...]}`;
  a single item is `need:1` with one candidate. One code path everywhere.
- **Cumulative success** = product of step rates; a group's rate = product of
  its **best-K** candidate rates; **no data on a step ⇒ 0%**, which zeroes
  everything downstream until attempts are logged.
- **Practice restriction = attention, not data**: a non-destructive focus
  filter. Background journaling continues; the route view hides non-route items
  and ignores non-route completions for current-step advancement.
- **Practice retries are unlimited**; auto-advance is a suggestion, never forced.
- **Run timer = forgiving continuous RTA**: the clock never stops for a
  step-reset; a step's split rolls up all its retries.
- **Run start**: arm via "Start run" (selects route); the clock zeroes and
  starts on the **next `game_reset` (F1)** at **+`start_offset` (default
  1360 ms, configurable)** — the SM64 emulator reset-timing convention. Every
  later `game_reset` **aborts** the in-progress run (saved with metadata) and
  **restarts** a fresh one.
- **Groups in a run**: mark off **K distinct** candidates, any order, **no
  duplicates**; backups in the set are valid substitutes.
- **Run history**: PB run + gold splits (sum-of-best); aborted runs **saved**
  with `status` + `reached_step`; finished runs carry `is_pb` (frozen at finish)
  for the progression graph.
- **Focus mode** (monochrome, no ± deltas, no gold coloring) + **click-to-hide**
  any timer (`----` toggle); both pure UI state in `localStorage`.
- **Import/export**: self-contained JSON; import reuses exact-match segments
  (name+triggers+guards), creates the rest, with a dry-run preview.
- **Pause**: manual/AFK pause subtracts from run time (confirm in plan; default
  on).

## 4. Data model

### 4.1 `routes` table (config — mirrors `segment_defs`)

```
id           INTEGER PRIMARY KEY AUTOINCREMENT
name         TEXT NOT NULL
steps        TEXT NOT NULL        -- JSON, see below
created_utc  TEXT NOT NULL
updated_utc  TEXT NOT NULL
```

**Step JSON (uniform shape):**
```jsonc
steps = [ step, ... ]
step  = { "label": "Whomp's",        // optional display label
          "need": 3,                  // 1..len(candidates)
          "candidates": [ item, ... ] }
item  = { "type": "star", "course": 2, "star": 0 }
      | { "type": "segment", "segment_id": 1 }
// single step: { "need": 1, "candidates": [ {…} ] }
```
Steps reference segments by **local** `segment_id` (the route lives in this DB).
Portability is handled at export/import (§7).

### 4.2 `runs` table (history)

```
id             INTEGER PRIMARY KEY AUTOINCREMENT
route_id       INTEGER              -- nullable if the route is later deleted
route_snapshot TEXT NOT NULL        -- JSON {name, steps} frozen at run time
mode           TEXT NOT NULL        -- "forgiving" (only mode for now)
status         TEXT NOT NULL        -- "finished" | "aborted"
reached_step   INTEGER NOT NULL     -- index reached (for aborted; = len for finished)
total_ms       INTEGER              -- final time (null/ partial for aborted)
started_utc    TEXT NOT NULL
ended_utc      TEXT NOT NULL
is_pb          INTEGER NOT NULL DEFAULT 0  -- was this a PB total when it finished
splits         TEXT NOT NULL        -- JSON, see below
```

**Splits JSON:**
```jsonc
splits = [ { "step_index": 0,
             "label": "LBLJ",
             "signature": "...",          // step identity for gold matching
             "completed_item": { "type": "segment", "segment_id": 1 },
             "time_ms": 108000,           // wall-clock for this step (all retries)
             "attempts": 10, "fails": 9 }, ... ]
```

`route_snapshot` makes a finished run's splits meaningful even after the route
is edited or deleted. **Current PB** and **gold splits** are computed on the
fly (min over a route's finished runs), not stored as separate rows.

### 4.3 Run settings (`ui_state` KV)

`run_settings = { "start_offset_ms": 1360 }` — bounded/validated; corrupt or
out-of-range values fall back to the default so the server always starts
(mirrors the replay-settings discipline). `start_offset_ms` is the SM64
emulator reset-timing offset; global default 1360 ms.

## 5. Server modules

### 5.1 `tracking/routes.py` (new, pure)

- Route + step model and `validate_route(d)` (raises `ValueError` listing the
  first problem — same contract as `segments.validate_definition`).
- `route_stats(route, attempts) -> [step_stat, ...]` — per step: resolved
  display name(s), per-step success rate, cumulative rate. Reuses the
  `success_rate` logic from `stats/registry.py` (failures =
  reset/hard_reset/death). Group rate = product of best-K candidate rates;
  no-data candidate = 0. **Pure → fully unit-tested.**
- `export_route(route, segment_defs) -> dict` — resolves each `segment_id` to
  its full definition, emits `{kind:"sm64-route", version:1, name, steps}`.
- `resolve_import(payload, existing_segment_defs) -> (steps, summary)` — pure
  reconciliation: for each embedded segment, match an exact existing def
  (name+triggers+guards) → reuse id; else mark for creation. Returns rewritten
  steps (with placeholders for to-create) + a `{reused, create}` summary. The
  service performs the actual creates and final id rewrite.

### 5.2 `tracking/runs.py` (new, pure projector)

`RunTracker` consumes the event stream alongside `SegmentEngine`, given the
active route snapshot:

- **State**: active run (route snapshot, `started_utc`, `start_offset_ms`,
  `current_step`, per-step progress: marked-off candidates, attempts, fails,
  step `started_utc`).
- **`run_started`** (journaled) begins a run.
- **Step completion**: `star_collected` matching a current-step candidate, or a
  segment success (`attempt_completed{kind:"segment", outcome:"success"}`)
  matching a candidate `segment_id`, marks it off. When `need` is met → record
  the split (`time_ms` from step start to now), advance `current_step`. Final
  step → run **finished**.
- **Forgiving failures**: a `practice_reset`/`state_loaded`/`death` during the
  current step increments its `fails`/`attempts`; the clock does not stop.
- **`game_reset`**: **abort** the in-progress run (persist `status="aborted"`,
  `reached_step`) and **restart** a fresh run of the same route at 0.
- **Groups**: mark off K distinct candidates, any order, ignore duplicates.
- **PB/gold** (pure helpers over `runs` rows): PB = min `total_ms` among
  finished runs of `route_id`; gold per step = min `time_ms` among finished
  runs matching that step's `signature`; sum-of-best = Σ golds.
- Exposes live state (current step, marks, step-start time) like
  `armed_segment_ids`; emits broadcast-only notices the service forwards.

Wired into `tracking/projection.py`'s `replay()` so runs re-derive from the
journal; finished/aborted runs are written to the `runs` table (like the
attempts cache is rebuilt). The live ticking clock is **not** server-driven:
the UI computes `elapsed = now - started_utc + start_offset - paused` from
authoritative anchors broadcast by the server.

### 5.3 `tracking/views.py` (extend)

- **Route view**: `{id, name, steps:[{label, need, candidates:[{display, kind,
  rate}], step_rate, cumulative, broken?}]}`. A step referencing a deleted
  segment marks `broken` (no cascade-delete).
- **Run view**: current run — `started_utc`, `start_offset_ms`, paused flag,
  `current_step`, marks, splits so far, PB/gold/sum-of-best for live ±.
- **Run history**: list of runs (filterable to finished-only) + the progression
  series (finished totals over time, `is_pb` flagged) for the graph.

### 5.4 `tracking/service.py` (extend)

Commands appended through the existing journaled pipeline:
- Route CRUD: `create_route` / `update_route` / `delete_route` (validate before
  insert/patch like segments; `routes_changed` broadcast + re-load).
- `export_route(id)`; `import_route(payload, dry_run)` (creates missing
  segments via existing `create_segment` path, then inserts the route).
- Run lifecycle: `start_run(route_id)` (snapshot route, journal `run_started`),
  `end_run()` (manual abort), `run_settings()` / `update_run_settings()`.

### 5.5 `server/api.py` (extend) — error taxonomy unchanged

```
GET    /api/routes                 list
POST   /api/routes                 create
GET    /api/routes/{id}            route view (resolved + cumulative)
PUT    /api/routes/{id}            update
DELETE /api/routes/{id}            delete
GET    /api/routes/{id}/export     self-contained JSON
POST   /api/routes/import          ?dry_run=true → preview; else create

POST   /api/run/start              {route_id} → arm + journal run_started
POST   /api/run/end                manual abort of the active run
GET    /api/run                    current run state (reconnect/refresh)
GET    /api/run/history            ?route_id=&finished_only=
GET    /api/run/settings           run settings
PUT    /api/run/settings           update (bounded)
```
`ValueError → 409`, `LookupError → 404`, `RuntimeError → 503` (degraded mode).
Declare `/api/routes/{id}/export` and `/api/routes/import` literal paths
*before* `/api/routes/{id}` (declaration-order rule — `fastapi-patterns`).

### 5.6 `storage/db.py` (extend)

- Migration v7: `routes` table + CRUD (`routes()`, `insert_route`,
  `update_route`, `delete_route`).
- Migration v8: `runs` table + `insert_run`, `runs(route_id?, finished_only?)`,
  and a `replace_runs`/rebuild path used by `replay()`.
- Run settings via existing `get_state`/`set_state`.

## 6. UI (Routes tab — the stub becomes real)

`app.js`: un-stub `"Routes"` and route it to a new `Routes` component.

- **`components/routes.js`** — builder: route picker + `New`/`Delete`,
  drag-to-reorder steps, `+ Star` (course/star catalog from the session view),
  `+ Segment` (segment defs list), `+ Group` (set `need`, drop candidates), two
  live % columns (step + cumulative), and **Import/Export** (Export → JSON box
  + Copy; Import → paste → dry-run preview "reuse X / create Y" → confirm).
- **Practice focus** (in `components/practice.js` + `store.js`): a route picker
  that filters/reorders the existing sections to route members; soft
  current-step pointer (`▶ CURRENT` / `NEXT`) that auto-advances as a
  suggestion and is overridable by clicking a step (sets target); ignores
  non-route arming/target changes for the route pointer. Groups render as
  freely-practiceable candidate clusters.
- **`components/runview.js`** — splits panel: big ticking clock, per-step
  splits with live ± vs PB and gold highlight, current step running live, group
  mark-offs, PB/sum-of-best footer; **Focus** button (monochrome, no
  deltas/gold) and **click-to-hide** on any timer (`localStorage`).
- **`components/runhistory.js`** — saved-runs list (finished-only filter) +
  progression graph reusing the `progress.js` pattern (gold dots = `is_pb`).
- `store.js`: active route (`localStorage`), run WS events
  (`run_started`/`run_progress`/`run_finished`/`run_aborted`), local clock tick
  anchored to server time; `api.js`: route/run client calls.

## 7. Import / Export format

```jsonc
{ "kind": "sm64-route", "version": 1,
  "name": "Standard Route (LBLJ)",
  "steps": [
    { "need": 1, "candidates": [
        { "type": "segment",
          "segment": { "name": "LBLJ", "start_triggers": [...],
                       "end_triggers": [...], "guards": [] } } ] },
    { "need": 3, "label": "Whomp's", "candidates": [
        { "type": "star", "course": 2, "star": 0 },
        { "type": "star", "course": 2, "star": 1 },
        { "type": "star", "course": 2, "star": 2 } ] },
    ...
  ] }
```
Stars are portable (`course`/`star`). Segments embed their **full definition**.
On import: validate `kind`/`version`; reuse an exact-match existing segment
(name+triggers+guards) else create; rewrite to local ids; insert the route.

## 8. Validation, errors, edge cases

- Route validation: `need` in `1..len(candidates)`; each candidate well-formed;
  referenced `segment_id` exists; `course`/`star` in range.
- Deleted segment referenced by a route → **broken** step in the view, no
  cascade-delete of routes (matches deleted-segment sections today).
- Arming a run requires a selected route; **one active run** at a time.
- `game_reset` mid-run = save-aborted-then-restart; runs snapshot the route.
- Gold splits matched by step **signature**, so reordering steps is safe.
- Reconnect/refresh re-fetches authoritative run state (`GET /api/run`).
- Shared contract touched: `tracking/projection.py` (wire `RunTracker` into
  `replay()`) — a "never edit in two branches at once" file; keep it on a
  focused branch and merge cleanly. **`main.py` is NOT needed** by this feature
  (the service loads routes from the db like `segment_defs`, the `RunTracker`
  lives in the projector driven by journaled `run_started` events, and
  endpoints go in `server/api.py`'s already-mounted router). See §12.

## 9. Build phases

- **A · Data + route core** — `routes`/`runs` tables + CRUD; `tracking/routes.py`
  (model, validate, cumulative, export/import resolve); API; `test_routes.py`,
  `test_db.py` additions.
- **B · Builder UI** — Routes tab: reorder, add star/segment/group,
  import/export with preview.
- **C · Practice focus** — route picker, filtered/ordered list, current/next,
  cumulative in context (mostly client-side over the route + session views).
- **D · Run mode** — `RunTracker` + replay wiring, forgiving clock + 1.36
  offset, splits, PB/gold, run view, Focus mode, click-to-hide; `test_runs.py`.
- **E · Run history** — saved runs, finished/aborted filter, progression graph.

## 10. Testing & live gate

- Pure server logic → pytest: `test_routes.py` (validate, cumulative math,
  export/import resolution), `test_runs.py` (lifecycle, forgiving timing,
  groups, abort/restart, PB/gold, the 1.36 offset), plus `test_db.py`,
  `test_views.py`, `test_api.py` additions. `uv run pytest -q` must pass.
- **Live gate (with the human + PJ64):** confirm **F1 in PJ64 1.6 fires the
  existing `game_reset` event** (the only behavior we rely on but haven't
  exercised this way). No new memory addresses.

## 11. Compatibility with the `desktop-gui-packaging` worktree

A second worktree (`.claude/worktrees/desktop-gui-packaging`, branch
`desktop-gui-packaging`) is building the desktop GUI + portable exe. Diffed
against master — the two efforts are **file-disjoint**; no code-file collisions.

- **No shared code files.** The GUI touches `main.py`, `server/app.py`,
  `replay/config.py`, `ui/components/header.js`, `pyproject.toml`, `uv.lock`,
  and adds `core/paths.py`, `core/relaunch.py`, `desktop/*`. This feature
  touches `storage/db.py`, `tracking/{routes,runs,views,service,projection}.py`,
  `server/api.py`, adds `ui/{routes,runview,runhistory}.js`, and edits
  `ui/{app,store,api}.js`. No overlap.
- **`main.py` is NOT needed here** (confirmed by reading the composition root):
  so the GUI's sensitive lazy-`app`/paths refactor of `main.py` and this work
  never meet.
- **`projection.py`** (a "never edit in two branches" contract) is touched here
  but NOT by the GUI — safe, absent a third concurrent editor.
- **Migrations:** the GUI adds **no** DB migration, so `routes`=v7 / `runs`=v8
  are unclaimed. If another branch adds a migration before this lands, re-number
  and re-check (guard seed/repair per the db.py migration discipline).
- **Synergies:** the GUI's `core/paths.py` (`db_path()`) will place the new
  tables under `%LOCALAPPDATA%` when frozen — nothing extra to do. The GUI's
  **parity rule** (UI features live in `ui/` + server, never forked into the
  desktop shell) is satisfied for free — the Routes tab is plain `ui/` served by
  the same server, so it appears in both the browser and the pywebview window.
- **Doc merge points (low risk):** both update `CLAUDE.md` (module-map rows) and
  the API reference. The GUI **moves the API reference to `docs/api.md`**; if the
  GUI lands first, document the new `/api/routes*` + `/api/run*` surface there,
  not in the old README tables.
- **Suggested ordering:** let `desktop-gui-packaging` merge to master first (it
  does the structural `paths.py` + lazy-`app` refactor); branch this work from
  the updated master so the new tables inherit `db_path()`. Parallel is also
  safe given the file-disjointness — only merge-time care on the docs.

## 12. Definition of done

- All phases merged; `uv run pytest -q` green; new behavior tested.
- F1 → `game_reset` confirmed on the live gate.
- `CLAUDE.md` module map updated (new modules/tables/endpoints); `README`
  updated for the new API + WS surface; `docs/architecture.md` updated with the
  run-timing model (forgiving RTA, F1 + offset) and its rationale.
- Commit messages explain WHY (existing `git log` style).
