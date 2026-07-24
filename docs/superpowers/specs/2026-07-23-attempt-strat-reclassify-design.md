# Reclassify an attempt's strategy after the fact

**Date:** 2026-07-23
**Status:** approved, ready for planning

## Problem

You declare a strategy before practicing ("I'm doing Cannonless"), then do
something else. Every attempt recorded in between is filed under the wrong
label — permanently. The strat drives PBs, rank medals, average rank modes,
the progress graph and compare, so a mislabeled run poisons all of them and
there is no way to correct it.

Secondary gap: an attempt with **no** strategy renders as an empty cell
(`practice.js:149`, `${a.strat_tag || ""}`), so "unlabeled" is invisible —
it reads as a rendering gap rather than a state.

## Goal

Every entry in the attempt list carries a strategy **dropdown** instead of
static text. Picking a different strategy reclassifies that attempt
retroactively; picking the blank option unlabels it. An unlabeled entry
reads `— no strategy —`.

Out of scope (v1): bulk reclassification. One row at a time is enough —
revisit if the per-row flow proves tedious in practice.

## Prior art

This is the **event-sourcing correction** problem: a derived read model
holds a value that was correct-by-construction at write time but wrong in
fact, and history is append-only. The conventional answer is a *compensating
event* — append a correction and let the projection fold it in — never an
in-place edit of the derived store, and never a rewrite of the original
event. (Fowler, *Event Sourcing* / "Retroactive Event"; Young, *Versioning in
an Event Sourced System*, on correction-by-append.)

This codebase already implements that pattern once: `attempt_cleared` /
`attempt_restored` are compensating events folded in by the `cleared_ids()`
pre-pass in `tracking/projection.py`. This design is a second instance of the
same shape, deliberately — a new mechanism would be the novel choice here,
not the conventional one.

## Approaches considered

**A. Journaled override event + projection pre-pass (chosen).** Append
`attempt_strat_set`, fold it in exactly like clearing.

**B. `UPDATE attempts SET strat_tag`.** Rejected: `attempts` is a derived
cache. Any reproject — restart, `_reproject()`, `tools/dedupe_journal.py
--fix`, a wipe — rebuilds it from the journal and silently reverts the edit.
The bug would come back and look like data corruption.

**C. Inject a synthetic `strat_set` at the attempt's timestamp.** Rejected:
`strat_set` moves the per-star/per-segment strategy *memory*, so it would
also re-attribute every later attempt up to the next real `strat_set`, and
would shift the live strat as a side effect. Too blunt for "fix this one
row".

## Design

### Journal contract

New journaled event, published through `TrackerService.publish` (so it lands
in the journal like `attempt_cleared`, not broadcast-only):

```
attempt_strat_set  { "attempt_id": int, "strat_tag": str | None }
```

`strat_tag: null` means "no strategy". Last write for an id wins, so the
event is idempotent and self-correcting — no undo event needed, because
picking again *is* the undo.

### Projection (`tracking/projection.py`)

- New pure pre-pass `strat_overrides(events) -> dict[int, str | None]`,
  sibling of `cleared_ids()`. Last-write-wins over the event list.
- `Projector.__init__` takes `strat_overrides=`; `replay()` computes and
  passes it, exactly as it already does for `cleared` and `touched`.
- Applied at the **two** sites that already stamp `strat_tag`, keyed by the
  attempt's first-event id (caveat 2 — for an anchored success that is the
  anchor's id, not the grab's):
  - `_build()` — star attempts, currently `strat=` from `strat_by_star`
  - the segment-close stamp in `feed()` — currently `strat_by_segment`
- The projector must continue to ignore `attempt_strat_set` in `_dispatch`
  (it is not a boundary event and must not close or open anything), which is
  the existing default for unknown types.
- Add a docstring caveat recording that an attempt's `strat_tag` is
  "remembered strategy at close time, unless a journaled override says
  otherwise".

Everything downstream keys off `Attempt.strat_tag`, so these follow for free
with no additional code: `_attempt_rank`, `_valid_frames` (average rank
modes), `_strategies_for` / `_seg_strategies` (observed-strat union), stats,
the progress graph, and compare.

### Service (`tracking/service.py`)

```python
async def set_attempt_strat(self, attempt_id: int,
                            strat_tag: str | None) -> None
```

Mirrors `clear_attempt` line for line: `_require_db()` → attempt-exists check
(`LookupError` otherwise) → `publish(...)` → `await self._reproject()`.

Additionally:

- **Register the name** for the attempt's entity via the existing
  `_register_strategy(db, entity_key(...), strat_tag)` when `strat_tag` is
  non-null, so a strategy that now exists only on reclassified rows still
  appears in the section dropdown. This also clears the strategy's tombstone,
  which is the established "re-creating un-deletes" rule.
- **Retag saved PB rows** owned by the attempt (new
  `Database.retag_pbs_for_attempt(attempt_id, strat_tag)` beside
  `delete_pbs_for_attempts`). The `pbs` table snapshots `strat_tag` at save
  time, so without this the star's PB for the *old* strategy stays a time
  that was not achieved with it — the reported bug, one layer down.

No new UI refresh wiring: `_reproject()` already broadcasts
`attempts_invalidated`, which is in `store.js`'s `REFRESH_ON`.

### REST (`server/api.py`)

```
POST /api/attempts/{attempt_id}/strat   { "strat_tag": "Cannonless" | null }
```

Same error taxonomy as its neighbours, via the shared `_http` helper —
`LookupError`→404, `ValueError`→409, `RuntimeError`→503 (degraded mode).
Sits next to `/clear` and `/restore`, which is where a reader looking for
per-attempt mutations will already be.

### UI

**`ui/components/stratpicker.js`** — add an optional `submit` prop:

```js
StratPicker({ entity, identity, strategies, active, onChanged, submit })
```

Default `submit` keeps today's exact behaviour (`POST /api/strat` with
`identity`), so the two section cards are untouched. The attempt row passes a
`submit` that posts the per-attempt endpoint. Keeping **one** component means
the `+ new strat…` modal, the dropped-write alert, and the phantom-pick
snap-back stay identical everywhere — the structural-parity rule (domain rule
11), not a copy.

**`ui/components/practice.js`** — `AttemptRow`'s strat cell renders
`<Medal/> <StratPicker/>` instead of `${a.strat_tag || ""}`:

- `strategies` from `sec.strategies`, `active` from `a.strat_tag`
- blank option label reads **`— no strategy —`**
- no `needs-strat` red outline on rows: that highlight is the section
  header's "pick before you practice" nudge, and lighting up every historical
  unlabeled row would be noise
- `onChanged` → `t.refresh()`

`AttemptTable` is already shared by `StarSection`, `SegmentSection` and the
unassigned block, so star ↔ segment parity for the row dropdown is
structural — one shared component, not a second implementation to drift.
`tests/test_ui_section_parity.py` does NOT reach into that row: it compares
the top-level components `StarSection`/`SegmentSection` each render, so it
covers the section cards' own pickers, not `AttemptRow`'s. The row's parity
guarantee comes from the shared component, not from a test.

**Unassigned attempts** (no course/star/segment, so no entity and no
strategy list) keep static text reading `— no strategy —`. A dropdown there
would open an empty menu. `AttemptTable` is called without `sec` at that call
site, which is the natural discriminator.

## Testing

| Area | Test |
|---|---|
| `tests/test_projection.py` | override applies to a star attempt and to a segment attempt; last-write-wins; `null` clears an inherited strat; the override survives a full `replay()` rebuild; `attempt_strat_set` opens/closes no attempt |
| `tests/test_tracker_service.py` | journals the event and reprojects; unknown attempt id → `LookupError`; registers the name so it appears in the section's strategies; retags the attempt's saved PB row |
| `tests/test_api.py` | 200 on success, 404 on unknown id, `strat_tag: null` accepted |
| `tests/test_views.py` | the attempt JSON carries the overridden tag and its medal re-grades under the new strategy's ladder |
| `tests/test_ui_section_parity.py` | stays green (no new asymmetry) |

## Documentation

- `CLAUDE.md` module map: note the override in the `tracking/projection.py`
  and `ui/components/stratpicker.js` rows.
- `README`: the new REST route and the `attempt_strat_set` payload.

## Risks

- **Shared checkout.** `practice.js` and `stratpicker.js` are high-traffic
  files that a parallel session may also be editing. Stage explicit paths and
  re-read before every exact edit.
- **PB retag writes persisted, non-derived state.** Unlike the attempt
  itself, a `pbs` row is not rebuilt from the journal, so this write is not
  self-healing. It is still reversible in practice — the retag is keyed on
  `attempt_id` and always mirrors the attempt's current tag, so re-picking
  the original strategy retags the row back. Pin that with an explicit test
  rather than trusting it.
