# STRATEGY MODAL + AUTO RANK-TABLE COLUMNS — Design Spec

**Date:** 2026-07-23
**Status:** Approved design, pending implementation plan

## 1. Goal

Two related UI improvements around custom strategies:

1. **Auto-columns.** The rank standards table shows a column for EVERY
   strategy the star/segment knows — registered via a strat dropdown, tagged
   on any past attempt, or defined in the standards store — not just the
   store-defined ones. Cells for a strat with no standards data render "—";
   no rank is awarded until the user enters times (already the behavior of
   `classify.rank_for`, which returns `None` on an empty ladder). Fixes the
   current gap where a custom strat like "logless" shows "no rank standards
   for this strategy" with no column to fill in.

2. **Strategy-creation modal.** All strategy creation goes through one proper
   modal (replacing two `window.prompt` call sites and the header's inline
   input): strategy-name text entry + the full rank ladder (Mario→Bronze,
   colored rank cells) with a blank time input and a blank example-video URL
   input per rank, plus Save/Cancel. Built on a reusable modal shell so
   future modals are cheap to add.

## 2. Prior art

- **In-repo (strongest):** the update popup (`ui/components/update.js`) is
  the codebase's one existing modal, with `.modal-backdrop`/`.modal` CSS
  already defined in `ui/index.html`. The shell extracts THAT pattern rather
  than introducing a second one; update.js migrates onto the shared shell.
- **Industry:** the overlay-dialog pattern (react-modal, Radix Dialog) —
  fixed backdrop + centered panel, Esc and backdrop-click to dismiss,
  clicks inside stop propagation. The native `<dialog>`/`showModal()`
  element (Baseline 2022, supported in WebView2) was considered and
  rejected: it would run a second modal system beside the existing CSS
  pattern, or force a restyle of the update popup onto `::backdrop`.

## 3. Decisions (user-confirmed 2026-07-23)

| Question | Decision |
|---|---|
| Modal entry points | **All three** — practice-tab strat dropdown, standards-table "+ Strategy", header target picker |
| × on a column header | **Clear data only** — wipes the strat's times/videos from the store; the column persists while the strat is still registered or observed on attempts; a store-only strat genuinely disappears |
| Column derivation | **Client-side union** — `StandardsPanel` receives the section's strategy list (`sec.strategies`, already the registered+observed+standards union from views.py) as a prop; zero server changes |
| Modal shell | **Extract the existing update-popup pattern** into a generic `Modal` component reusing the existing CSS |

## 4. Modal shell — `ui/components/modal.js` (new)

`Modal({ title, onClose, footer, children })`:

- Renders the existing `.modal-backdrop` / `.modal` markup and CSS.
- Backdrop click → `onClose`; Esc keydown (document listener, attached while
  mounted) → `onClose`; clicks inside the panel stop propagation.
- `footer` renders inside `.modal-actions`; `children` is the body.
- ~40 lines, stateless. `update.js` migrates to it with no visual or
  behavioral change (its own state machine stays in update.js).

## 5. Strategy modal — `ui/components/stratmodal.js` (new)

Props: `entity` (`star:{c}:{s}` or `segment:{id}`), `existing` (known strat
names for duplicate rejection), `onSaved(name)`, `onClose`.

- **Body:** strategy-name text input, then one row per rank — `RANK_NAMES`
  minus Iron (the implicit floor, as everywhere), rank cell colored via
  `rankColor` — each with a blank time input (seconds, same format as the
  standards table) and a blank video-URL input.
- **Save:** trim the name; reject empty or duplicate (exact match against
  `existing`) with an inline error. Then:
  1. `POST /api/ranks/standards/{entity}` `{strategy}` — creates the empty
     ladder (store no-ops if it exists, so retry is safe);
  2. `PUT /api/ranks/standards/{entity}/{strat}/{rank}` `{seconds}` for each
     filled, parseable time (blank/NaN skipped — matches the table);
  3. `PUT /api/ranks/standards/{entity}/{strat}/{rank}/video` `{url}` for
     each non-blank URL;
  4. `onSaved(name)`.
  A failed request shows an inline error line INSIDE the modal (no
  `window.alert`) and keeps it open; PUTs overwrite, so re-saving is
  idempotent.
- **Cancel / Esc / backdrop:** `onClose`, zero writes.

No new server surface: every write rides an existing ranks endpoint, and
`rank_standards_changed` broadcasts already refresh other views.

## 6. Auto-columns — `standards.js` (+ the two practice.js call sites)

- `StandardsPanel` gains a `strategies` prop; both call sites in practice.js
  pass `sec.strategies` (stars AND segments — views.py computes the union
  for both).
- Column order: store strategies in store order (community defaults first),
  then remaining known strats appended in `sec.strategies` order. The
  existing "logless" gets its empty column retroactively — no backfill.
- Cell lookup guards a missing store entry (`data.strategies[s] || {}`);
  absent thresholds already render "—".
- The × button keeps calling the existing DELETE; its tooltip changes to
  "clear this strategy's standards". Union-derived columns persist after ×;
  store-only columns disappear.
- "+ Strategy" (edit mode, unchanged trigger) opens the strategy modal
  instead of `window.prompt`.

## 7. Entry-point wiring

- **practice.js StarSection:** `__new` opens the modal (replacing
  `window.prompt`); `onSaved(name)` → existing `POST /api/strat` →
  `t.refresh()`; cancel → the existing `stratNonce` snap-back. The dropped-
  write alert path for `/api/strat` is unchanged.
- **standards.js:** `+ Strategy` → modal; `onSaved` → `load()` +
  `onChanged`.
- **header.js target picker:** the `+ new strategy…` option opens the modal,
  replacing the `adding` inline-input state entirely; `onSaved(name)`
  selects the name in the picker; "Set target" applies it (which registers
  the strat via `set_target`, as today). NOTE: the picker's option list is
  the REGISTERED-strategies KV (view top-level `strategies`), which won't
  contain the new name until Set target registers it — the select must
  render its current value as an option when it's absent from the list, or
  the picker would display blank after save.
- **Segments:** SegmentSection deliberately has no strat dropdown
  (`/api/strat` is star-shaped — v1 note in practice.js). Segments reach the
  modal via their standards table's "+ Strategy"; auto-columns work
  identically. Adding a segment strat dropdown stays OUT OF SCOPE.

## 8. Error handling

- Modal-internal validation errors (empty/duplicate name) and request
  failures render as an inline error line in the modal; the modal never
  closes on failure.
- Partial-save recovery: if a threshold/video PUT fails after the create
  succeeded, the strat exists with partial data; the user retries Save
  (idempotent) or cancels — the auto-column union still shows the strat, so
  nothing is stranded invisibly.
- Existing error taxonomy untouched (LookupError→404, ValueError→409,
  RuntimeError→503 via `send()`).

## 9. Out of scope

- Migrating other `window.prompt`/`window.confirm` sites (route name/rename,
  example-video URL editing, wipe confirmations) — the shell makes these
  cheap later, but they are not part of this feature.
- A segment strat dropdown / segment-shaped `/api/strat`.
- Ladder monotonicity validation (times increasing down the ladder) — the
  existing table doesn't validate; the modal matches it.
- Any Python/server change.

## 10. Verification

- `uv run pytest -q` — must stay green (no server changes expected; this is
  the regression gate).
- Frontend smoke test (Chrome DevTools MCP, mandatory): create a strat from
  each of the three entry points; confirm the new column appears empty, no
  rank/banner change until a time is entered, × clears an in-use strat's
  data but keeps its column, Cancel writes nothing, and the update popup
  still renders after its migration to the shell.
- Human audit (frontend feel change): user verifies the modal flow live.

## Addendum (2026-07-23, user-approved): full delete for custom strategies

### Goal

A way to DELETE a custom strategy entirely: its rank standards are cleared
AND it disappears from every dropdown. Community-seeded ("default") strats
cannot be deleted — only custom ones.

### Why a tombstone

Dropdown lists are a three-source union: standards store + registered KV +
strategies OBSERVED on past attempts. The first two can be truly removed.
The third cannot: attempts live in the append-only journal and re-derive on
reprojection, so stripping `strat_tag` from history would corrupt
attribution or silently undo itself. A per-entity tombstone
(`deleted_strats` ui_state KV, keyed by entity key `star:{c}:{s}` /
`segment:{id}`) filters observed occurrences at view time instead.

### Semantics (user-confirmed)

- **Delete (custom strat):** remove ladder + user_videos from the standards
  store; unregister from the star's registered-strategies KV (segments have
  none); add the tombstone; if it is the star's active strat, publish a
  journaled `strat_set` null (existing `set_strat` path). Views mask any
  remaining reads of a tombstoned name (section `last_strat`, top-level
  `last_strat_by_star`, `rank_by_star` grading, route-candidate ranks,
  target `strat_tag`) to None.
- **Historical data preserved:** attempts keep their `strat_tag`; PBs,
  markers, comparisons stay in the db, invisible until the name exists
  again (one exception: the Compare tab's "load existing" library lists
  saved comparisons under their original strat label — historical data
  display, accepted). **Re-creating the same name clears the tombstone** and re-attaches
  the old data (undo-able delete, by design) — both creation paths
  (`_register_strategy`, `create_rank_strategy`) clear it.
- **Seeded strats protected:** "custom" = not in the bundled seed for that
  entity (`RankStandards.seeded_strategies(ek)`, the same distinction
  `_reconcile` uses). Purging a seeded strat → ValueError → 409.
- **Affordance (user-picked: dual-meaning ×):** the standards table's edit
  mode keeps ONE × per column — seeded strat: existing clear-data behavior
  and confirm; custom strat: "Delete strategy" confirm (spells out dropdown
  removal + past attempts keep their times + re-create restores) →
  `DELETE …?purge=true`. Tooltip varies to match.

### Surface

- `ranks/standards.py`: `seeded_strategies(ek)`.
- `tracking/service.py`: `purge_strategy(ek, strat)` command +
  `_clear_tombstone` on both create paths.
- `tracking/views.py`: filter section strategy unions against the
  tombstone; mask tombstoned active-strat reads.
- `server/ranks_api.py`: `DELETE …/{strategy}?purge=true`; GET gains
  `"seeded"` list.
- `ui/components/standards.js`: dual-meaning ×.
- This ENDS the branch's pure-frontend status; the new behavior gets real
  pytest coverage (standards/service/views/api).

### Out of scope

- Deleting the orphaned auxiliary data (markers, comparisons, PBs) — kept,
  by design (restoration on re-create).
- A "hide" distinct from delete; un-delete UI (re-create the name instead).
- Purging seeded strats or editing the seed.
