# Default Routes — Spec #1: Foundation (sequence segments, route-scoped arming, categories, seed/reconcile)

Date: 2026-07-23
Status: design — awaiting user review before writing-plans

## 1. Overview

Ship the standard Usamune route corpus (16 / 70 / 120 / 0-1 Star and per-course
Stage RTA) as editable default routes, plus the castle-movement segments they
depend on. This is delivered as **two specs**:

- **Spec #1 (this document) — foundation.** The engine and storage capabilities
  the corpus needs, provable end-to-end with data we already have (the 10
  existing segments, migrated into the new seed path). Lands on `main` first
  because it touches the shared contracts (`core/events.py`, `MatchContext`,
  `tracking/segments.py`, `tracking/projection.py`).
- **Spec #2 — the corpus.** ~13 main-category route variants, ~15–25 Stage RTA
  routes, and the ~45 shared movement segments with their waypoints and guards.
  **Pure data**, authored as a seed JSON against the frozen spec-#1 contracts —
  never as inline SQL and never requiring the builder UI to render.

Non-goals for spec #1: authoring any route/segment content; rendering the new
capabilities in the builder UI (see §9 — that is the concurrent UI redesign's or
a post-redesign slice's job); a list-valued "through levels" param; per-waypoint
castle-subarea deferral.

## 2. The four decisions (user, 2026-07-23)

1. **Default ownership:** defaults are ordinary **editable** routes/segments; a
   seed bump refreshes only rows the user never touched; each seeded row has a
   **Reset to default** action. (Not fork-on-edit, not insert-once.)
2. **Grouping:** one `category` field shared by **both** routes and segments
   (honours the star↔segment parity rule). Free-text; seeded values like
   "Main Categories", "Stage RTA", "Castle Movement", "Tricks", "Bowser Fights".
3. **Movement identity:** **one shared segment per stage pair** — `CCM → BitDW`
   exists once; every route referencing that move pools its attempts/PBs/ranks.
   Genuinely different paths for the same pair become explicitly named variants.
4. **Arm scoping:** the **active route gates arming**; guards only refine
   (disambiguate repeats within one route). Active route becomes journaled
   server state — fixing the current localStorage-only parity violation.
5. **Cancel semantics (this session):** a multi-step segment cancelled by an
   off-sequence major action is a **silent abandon** (no attempt row) —
   consistent with the engine's existing silent-disarm on a foreign level change
   and the AFK/no-op discards.

## 3. Prior art

- **Route content:** Ukikipedia RTA guides (`/wiki/RTA_Guide/{16,70,120}_Star`,
  `/wiki/RTA_Guide/0/1_Star`) enumerate the named variants; the per-course pages
  (`/wiki/RTA_Guide/<Course>`) carry explicit ordered `## 70 Route` / `## 120
  Route` star lists that supply both the Stage RTA content and the expansion of
  the full-game guides' "all stars" shorthand. Corpus authoring (spec #2) is
  transcription, not invention.
- **Sequence/waypoint matcher:** an **ordered-event automaton** — the same shape
  as gesture recognizers and log-sequence matchers: advance a pointer on the
  expected next token, cancel on an unexpected significant token, ignore noise.
  We already run a two-state instance of exactly this (`SegmentEngine`); spec #1
  generalizes it from length-2 to length-N. Not novel.
- **Seed + reconcile:** the repo already ships one instance —
  `ranks/standards.py` over `rank_standards.seed.json` with a `SEED_VERSION` and
  a reconcile that keeps community rows fresh while preserving user-created ones.
  Spec #1 reuses that exact pattern for routes/segments rather than the inline-SQL
  path in `db.py::MIGRATIONS`, which the module map itself warns is
  unmaintainable and has already forced two hand-written repair migrations
  (v5 LBLJ, v6 Bowser 3).

## 4. Section 1 — segments are ordered sequences (`waypoints`)

### 4.1 Model

A segment is an **ordered sequence of steps**. `start_triggers` is step 0 (arms),
`end_triggers` is the final step (completes), and a new field carries the ordered
middle steps:

- `waypoints: list[list[dict]]` — each waypoint is an **any-of** set of trigger
  clauses, symmetric with `start_triggers` / `end_triggers`.
- **Empty `waypoints` ⇒ byte-for-byte current behaviour.** Every existing def
  (LBLJ, MIPS Clip, Lakitu Skip, the Bit* pipes, the Bowser fights) keeps empty
  waypoints and is completely untouched — including all the echo/relocation/
  deferred-subarea machinery keyed on `start_triggers`/`end_triggers`. Only the
  seeded re-entry movements opt in. This is what makes it safe to change the
  shared matcher contract.

Re-entries are expressed as explicit ordered waypoints using **existing** trigger
types — no new trigger needed. Example `SL → HMC (via re-entry)`:

```
start:     [ level_exit  from=10 ]              # exit Snowman's Land
waypoints: [ [ level_enter to=10 ],             # re-enter SL
             [ level_exit  from=10 ] ]          # exit SL again (pause-exit)
end:       [ level_enter to=7 ]                 # enter Hazy Maze Cave
```

(`level_visit` — a single clause matching `from==X or to==X` — is a possible
future convenience to collapse a round-trip, but YAGNI for spec #1; explicit
waypoints match the user's "define a full sequence" framing.)

### 4.2 Engine (`tracking/segments.py`)

`_Arm` gains `progress: int` — the index of the next waypoint to match (0 = none
consumed; `== len(waypoints)` = all consumed, now awaiting the end). The clock
runs from the original arm throughout; advancing a waypoint records **no row and
does not re-arm** (the progress pointer is precisely what stops a re-matched
*start* clause from re-arming and resetting the clock).

Per-event precedence while a **waypoint-bearing** def is armed:

1. `progress == len(waypoints)` **and** an end clause matches → **success**
   (the end cannot fire until every waypoint is consumed).
2. `death` → hard fail (row). `game_reset` → hard fail (row). *(unchanged)*
3. relocation anchor elsewhere, or a retry `practice_reset`/`state_loaded` at the
   arm position → reset `progress = 0` and re-arm in place (the existing
   continuation loop, extended to rewind the sequence).
4. echo anchor → invisible. *(unchanged — `anchor_is_echo` already covers this;
   waypoints match real edges/grabs, never synthetic anchors.)*
5. `waypoints[progress]` matches → **advance** `progress`, no row, no re-arm.
6. a **major action** matching none of the above → **cancel** (silent disarm,
   **no row**; per decision 5).
7. anything else → transparent (ignored).

**Major action = `{star_collected, key_grabbed, level_changed (real edge)}`.**
A `level_changed` to the wrong stage = misroute → cancel; a star/key grab
mid-sequence = task switch → cancel. Minor events — `area_changed` (walking
lobby→basement is normal movement), `warp_entered`, `spawned` — stay transparent,
so a movement that crosses castle areas never trips over itself.

**Scope:** the strict advance/cancel classification applies **only** to
waypoint-bearing defs. A def with empty waypoints keeps today's lenient close
chain (a foreign `level_changed` already silent-disarms it at the current
`segments.py:875`, so 2-step segments are unaffected and all existing tests hold).

**Start-refire suppression:** while a waypoint-bearing def is armed, a re-match of
its own start clause must **not** re-arm (the sequence owns progression). The
progress pointer handles this: the second `exit SL` advances `waypoints[progress]`
rather than re-arming. The arm phase (`segments.py:904`) is gated to skip
re-arming an already-armed waypoint-bearing def.

### 4.3 Storage & validation

- Migration: `ALTER TABLE segment_defs ADD COLUMN waypoints TEXT NOT NULL
  DEFAULT '[]'`.
- `SegmentDef` gains `waypoints`; `db.segment_defs()` / insert / update carry it.
- `validate_definition` validates each waypoint clause with the existing
  `_check_clause` against `TRIGGERS` (free reuse). A waypoint list may be empty.
- Export/import (`tracking/routes.py::export_route`/`resolve_import` and
  `db`-side segment embed) include `waypoints` in the embedded segment def, and
  `_segment_matches` compares it (so an imported re-entry segment reuses an exact
  local match rather than duplicating).

### 4.4 Tests (`tests/test_segments.py`)

- `exit SL → enter SL → exit SL → enter HMC` → exactly **one** success attempt
  whose `rta_frames` spans the whole sequence; **zero** other rows.
- Mid-sequence `star_collected` → **silent abandon**, no row, def disarmed.
- Mid-sequence `level_changed` to a wrong stage → cancel, no row.
- `death` inside the sequence → one `death` row (still fatal).
- Retry `practice_reset` at the arm position after one waypoint → `progress`
  rewinds to 0, one continuation, no spurious row.
- A def with empty waypoints reproduces an existing two-step test verbatim
  (regression guard on the "unaffected" claim).

## 5. Section 2 — route-scoped arming

### 5.1 Journaled active route

- New event **`route_selected {route_id, segment_ids}`**, published like
  `target_set` (frame=0, zero-duration). It **snapshots the member segment ids**
  in the payload — the same self-containment trick `_arm_run` uses to snapshot
  route steps into `run_started` — so replay reconstructs "route X active during
  frames A–B" without ever consulting the mutable `routes` table.
- Journaled (not `ui_state`) for **replay determinism**: arming of a historical
  event depends on which route was active *at that event's time*; only a
  timeline position gives replay the right answer. `route_id` may be null / the
  set empty to mean "no active route" (only standalone targets arm).
- `update_route` on the active route **re-emits** `route_selected` with fresh
  membership (mirrors the existing `void_active` re-arm on edit).
- Active route survives `game_reset` (it is a persistent practice choice, not run
  state).

### 5.2 MatchContext + guard

- `MatchContext` gains `route_segments: frozenset[int] | None` (from the latest
  `route_selected`; None = no active route) and `target_segment: int | None`
  (derived from `self.target` when it is a segment target — the standalone
  "practise this one movement" path). Both threaded in `projection.py` alongside
  the existing `num_stars` / `last_star_*` fields at the `MatchContext(...)` build
  site (~`projection.py:364`).
- New guard **`in_active_route`** (no params), `phase="arm"`, with a stub
  `check` — read declaratively by the engine's arm gate exactly like
  `min_time`/`max_time` are read by projection. Arm gate: a def carrying this
  guard arms only when `d.id in (ctx.route_segments or ())` **or**
  `d.id == ctx.target_segment`.
- **Opt-in.** The guard is absent from all 10 existing defs → their behaviour is
  unchanged. Only the seeded movement corpus (spec #2) carries it. Guards are
  ANDed (existing), so `in_active_route` composes with `last_star_grabbed` etc.
  to disambiguate repeats inside one route (120★ visits BoB twice).

### 5.3 Graceful degradation

Because the `target_segment` half satisfies the guard, "practise this single
movement" (setting a segment target from the stage banner / header) arms it even
with **no** route selected — so the corpus is usable before the UI wires a route
selector. Route selection is the bulk-arm convenience on top.

### 5.4 Tests (`tests/test_segments.py`, `tests/test_projection.py`)

- A def with `in_active_route` does **not** arm when `route_segments` lacks it.
- Same def arms when `route_selected` includes it; disarms cleanly on a later
  `route_selected` that drops it.
- Same def arms when it is the `target_segment` even with no route selected.
- A def **without** the guard ignores route state entirely (regression).
- Replay: feeding `route_selected` then reprojecting reproduces the same armed
  set (determinism).

## 6. Section 3 — categories

- Migration: `ALTER TABLE routes ADD COLUMN category TEXT` and
  `ALTER TABLE segment_defs ADD COLUMN category TEXT` (nullable → "Uncategorized"
  at the view layer).
- `GET /api/routes` and `/api/segments` already return whole rows, so `category`
  rides along with no endpoint change; `create`/`update` accept it (validated as
  an optional string). `RouteBody`/`RoutePatch` and the segment body gain the
  optional field.
- Both builders can render grouped sections off `category` (the redesign owns the
  markup — §9). Free-text on either kind; users type their own.
- **Star↔segment parity:** category is one mechanism serving both kinds; no
  second grouping concept to keep in sync.

## 7. Section 4 — seed + reconcile (delivery mechanism)

Populated by spec #2; the mechanism itself is spec #1 and must be provable with
the 10 existing segments.

- One combined `src/sm64_events/data/defaults.seed.json` with a `SEED_VERSION`
  and two ordered blocks — **`segments` first, then `routes`** — because seeded
  routes reference seeded segments. Each row carries a stable **`seed_key`** slug
  (e.g. `seg:ccm->bitdw`, `route:16-star-lblj`) — the identity reconcile matches
  on, independent of the autoincrement id.
- **Route→segment references in the seed use `seed_key`, not `segment_id`.** A
  route step's segment candidate is stored on disk (`routes` table) as
  `{type:"segment", segment_id:<int>}`, but the seed cannot know the local
  autoincrement id, so seeded route candidates carry
  `{type:"segment", seed_key:"seg:..."}`. Reconcile inserts/refreshes the
  `segments` block first, then resolves each route candidate's `seed_key` to the
  now-known local `segment_id` before writing the `routes` row. A `seed_key` that
  resolves to no segment marks the route step broken (same `broken` flag the view
  already computes for a deleted segment) rather than failing the whole reconcile.
- A `seed_key TEXT` + `seed_dirty INTEGER NOT NULL DEFAULT 0` column on both
  `routes` and `segment_defs`. `seed_key` is non-null only for seeded rows;
  `seed_dirty` flips to 1 on the first user write to a seeded row.
- **Reconcile (on startup, mirroring `ranks/standards._reconcile`):**
  - seeded row present, `seed_dirty=0` → overwrite from seed (ship fixes).
  - seeded row present, `seed_dirty=1` → leave alone (user owns it).
  - seed row absent from db → insert.
  - user-created row (no `seed_key`) → never touched.
  - a `seed_key` removed from a newer seed → leave the db row (never delete user
    data); it simply stops being reconciled.
- **Reset to default:** `POST /api/routes/{id}/reset` and
  `/api/segments/{id}/reset` re-copy the row from the seed by its `seed_key` and
  clear `seed_dirty` (LookupError → 404 if the row has no `seed_key`, i.e. it was
  user-created). Broadcast `routes_changed` / segment reload as the existing
  mutations do.
- **Migrate the 10 existing segments into the seed.** The v4 SQL seed (with the
  v5 LBLJ / v6 Bowser 3 repairs folded into their corrected values) becomes seed
  rows carrying `seed_key`s. One seed path, not two. The v4–v6 migrations stay in
  place (they already ran on live dbs); a new migration backfills `seed_key` onto
  the existing rows by name so reconcile can adopt them without duplicating.

### 7.1 Tests (`tests/test_db.py`, new `tests/test_seed_reconcile.py`)

- Untouched seeded row refreshes on a `SEED_VERSION` bump; `seed_dirty=1` row
  does not.
- User-created row (no `seed_key`) survives a bump untouched.
- Reset restores a dirtied seeded row and clears `seed_dirty`; Reset on a
  user-created row → LookupError/404.
- The 10 migrated segments adopt their `seed_key`s without creating duplicates on
  an existing db.

## 8. Data contracts (the coordination surface with the redesign)

Additive only. The redesign is told to preserve backend contracts, so these
fields extend payloads without breaking existing consumers:

- `GET /api/segments/vocab` gains the `in_active_route` guard automatically
  (registry-driven `vocab()`); the def shape gains `waypoints`.
- `GET /api/routes` / `/api/segments` rows gain `category`, `seed_key`,
  `seed_dirty`.
- The **session view** (`tracking/views.py`) gains an `active_route`
  descriptor (id + name + member ids) so the UI can show/scope without reading
  localStorage, and each route/segment descriptor carries `category` and a
  `seeded` boolean (has `seed_key`) so the UI can show the Reset affordance.
- New endpoints: `route_selected` write (e.g. `POST /api/route/select
  {route_id}`), `POST /api/routes/{id}/reset`, `POST /api/segments/{id}/reset`.

## 9. UI compatibility & sequencing (concurrent redesign)

A separate agent (GPT-5.6-Sol) is redesigning the UI under
`src/sm64_events/ui/` following the `.agents/skills/sm64-uiux` skill, which
**preserves every backend contract, API call, WebSocket behavior, localStorage
key, and browser/desktop parity** and owns the `ui/components/*.js` markup. Spec
#1 is therefore scoped to avoid touching the redesign's files:

- **Spec #1 edits no `ui/components/*.js` markup.** All engine behaviour is
  tested at the projection/service layer, not through the DOM. Collision surface
  with the redesign is ≈ zero.
- The new capabilities reach the UI purely as **data** (§8): grouped rendering
  off `category`, the Reset affordance off `seeded`, waypoints/`in_active_route`
  off the vocab. Because `segments.js` is "100% vocab-driven", a vocab-faithful
  redesign renders the new guard automatically; the **waypoints editor** (a third
  ordered clause list) is the one genuinely new builder surface and is
  **deferred to a slice that lands on top of the redesigned `segments.js`** — not
  authored twice. The corpus (spec #2) is authored via seed JSON + API, so it
  does not need the waypoints editor to exist.
- **Active route:** becomes server-authoritative (§5). The `sm64.activeRoute`
  localStorage key is **retained as an optimistic mirror** (the redesign's
  "preserve localStorage keys" holds); the server value in the session view is
  the source of truth. Wiring the route selector to the new endpoint is a
  coordination point with the redesign / spec #2, with the standalone
  `target_segment` path as the fallback until then.
- **Layout stability:** the redesign reserves fixed layout slots and forbids new
  banners. Spec #1 adds no banner; armed/route state is exposed as data for the
  redesign to place inside its reserved active-objective slot.
- **Parity:** `tests/test_ui_section_parity.py` stays green; category/reset are
  added to both cards or recorded in `ONLY_IN_*` with a reason.

**Merge order:** spec #1's contract changes (`core/events.py`, `MatchContext`,
`segments.py`, `projection.py`, `main.py`) land on `main` first — they are on
CLAUDE.md's "never edit in two branches at once" list. The redesign rebases onto
them; the waypoints editor slice lands after the redesign.

## 10. Touched files (spec #1)

Backend/contract: `core/events.py` (new event type), `tracking/segments.py`
(waypoints engine, `level`-free `in_active_route` guard, vocab), `MatchContext`
(in `segments.py`), `tracking/projection.py` (thread route/target-segment,
`route_selected` handling), `tracking/service.py` (`select_route`,
`reset_route`/`reset_segment`, category on create/update, re-emit on edit),
`tracking/views.py` (`active_route`, `category`, `seeded` in payloads),
`tracking/routes.py` (waypoints in export/import), `storage/db.py` (migrations:
`waypoints`, `category`, `seed_key`, `seed_dirty`, `seed_key` backfill; reconcile
loader), `server/api.py` (select + reset endpoints, category in bodies),
`data/*.seed.json`, `main.py` (run reconcile at startup, like rank standards).

Tests: `test_segments.py`, `test_projection.py`, `test_db.py`,
`test_seed_reconcile.py` (new), `test_routes.py` (waypoints export/import),
`test_views.py` (active_route/category/seeded), `test_api.py` (select/reset),
`test_ui_section_parity.py` (unchanged/extended).

## 11. Definition of done (spec #1)

- `uv run pytest -q` green; every new behaviour has a test (§4.4, §5.4, §7.1).
- New memory reads: none (no new addresses; `waypoints` reuses existing triggers).
- CLAUDE.md module map updated (segments.py waypoints; route_selected; seed
  path; reset endpoints); README updated for the new endpoints/payload fields;
  `docs/architecture.md` gains the sequence-matcher + seed-reconcile rationale.
- Contract changes merged to `main` before spec #2 / the waypoints-editor slice.
