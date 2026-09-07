---
paths:
  - "src/sm64_events/library/ladders.py"
  - "src/sm64_events/library/strategy_signature.py"
  - "src/sm64_events/library/ladder_estimates.py"
  - "src/sm64_events/library/placements.py"
  - "src/sm64_events/library/practice_catalog.py"
  - "src/sm64_events/library/adoptions.py"
  - "src/sm64_events/ranks/standards.py"
  - "src/sm64_events/ranks/timecurve.py"
  - "src/sm64_events/ranks/scoring.py"
  - "src/sm64_events/server/library_api.py"
  - "src/sm64_events/ui/components/librarytarget.js"
  - "src/sm64_events/ui/components/librarymodel.js"
  - "src/sm64_events/ui/timecurve.js"
  - "src/sm64_events/ui/components/standards.js"
  - "src/sm64_events/ui/store.js"
---
# Chain: a Sheet row's effective rank standards

- **Value:** local entity, canonical strategy, ROM, and tier cutoffs for one row.
- **Source truth:** current workbook observations; `library.store.build_and_stamp`
  fits the same bytes used by the import/export readers.
- **Sink:** Library standings and Practice Rank standards.
- **Coverage:** all eight cutoffs and all 45 reachable subdivisions, including
  Capless. A nonempty ladder is insufficient. The current fitting, identity,
  edit and curve contract is in `docs/sheet-rank-standards.md`.
- **One clock:** displayed centiseconds on the entity's clock; the Sheet's
  segment numbers are seed references, never local database IDs.

| # | hop | value is true here as | module | probe (reads it) | inject (forces it) | when the hop is broken, the probe shows | when the probe itself is broken, it shows |
|---|-----|-----------------------|--------|------------------|--------------------|------------------------------------------|--------------------------------------------|
| 1 | fit | eight cutoffs, 45 reachable divisions, actual sample count and estimate provenance | `src/sm64_events/library/ladders.py` | `tests/test_complete_sheet_ladders.py` | replace a row's entries with one observed time | a tier or division disappears | checking only truthiness accepts a one-tier ladder |
| 2 | place | `(local entity, sheet_strategy)` | `src/sm64_events/library/placements.py` | `tests/test_library_practice_sync.py` | explicitly adopt the missing row in a temporary database | Library has a ladder but Practice has no strategy | raw seeded number appears valid only in a freshly seeded database |
| 3 | resolve | current fit under genuine per-cutoff user edits; inherited seed defaults yield | `src/sm64_events/ranks/standards.py` | `tests/test_sheet_seed_precedence.py` | edit one fitted cutoff, then replace the fitted layer and reload | sparse seed wins or an edit erases untouched tiers | checking only the edited cutoff stays green |
| 4 | decorate | local row identity and effective US/JP ladders | `src/sm64_events/server/library_api.py` | `tests/test_library_practice_sync.py` | change the effective cutoff and fetch both APIs | raw Library fit differs from Practice's effective value | comparing two copies of the raw payload falsely agrees |
| 5 | invalidate | mounted views reload after standards events or reconnect | `src/sm64_events/ui/store.js` | `tests/test_ui_standards_sync.py` | change standards through a second API client | hidden Library or open Practice keeps the previous cutoff | switching tabs remounts Practice and conceals the missing live invalidation |
| 6 | render | nine visible bands and five subdivision targets per band | `src/sm64_events/ui/components/librarymodel.js` | `tests/test_ui_complete_sheet_ladders.py` | remove all entries below Bronze | Capless disappears | populated-only fixtures conceal empty-band filtering |

## Counterfactual recipe

In a temporary database, read Lakitu skip's `JD -> Speedkick ending` row and
its Practice standards. Explicitly adopt that row onto the actual Lakitu
segment, then repeat both reads. The task-0126 diagnosis recovered all six
tiers without changing the fitted row: placement was the first failing hop.
`test_lakitu_all_strategies_have_the_same_standards_in_both_pages` pins that
outcome without requiring adoption, then changes a cutoff and compares both.

## Failure catalogue

- 2026-09-06 round 2, hops 1/3/6 — 178/634 base ladders dropped tied tiers;
  Lakitu Standard's three inherited cutoffs replaced its fit; Library hid
  empty Capless. A 30-phase consecutive-frame test also exposed centisecond
  interpolation skipping subdivisions. Complete frame spacing, seed/edit
  provenance, and retaining empty bands fix these independent boundaries.
- 2026-09-06 round 2, identity — changing the grading formula reassigned 38
  strategy matches on identical workbook bytes. Matching now uses its own
  calibrated signature; same-workbook comparison returns zero reassignments.

- 2026-09-06 hop 2 — task 0126, “in the library ... I can see a ladder” but
  Practice lacked it. Name matching was decorative only; automatic row links
  now feed the same ranker layer as explicit assignments.
- 2026-09-06 hop 2 — a replaced Bowser seed retained a cached local ID.
  Durable catalog links cover created entries only; seed/name matches resolve
  from current definitions. The existing import regression checks replacement.
- 2026-09-06 hop 2 — repeated Warp fadeout rows matched one existing child.
  Ambiguous names do not auto-reuse a child; row IDs disambiguate new pieces.
- 2026-09-06 hops 3–5 — a fitted cutoff edit replaced its entire strategy,
  Library read raw cutoffs, and mounted views retained stale responses.
  Per-cutoff overlays, effective API decoration and standards invalidation
  fix these separate boundaries.
- 2026-09-06 hop 2 — automatic links returned immediately after Unlink.
  Explicit unlink reservations survive reload and block subsequent import
  placement; reassigning clears the reservation. The persistence and
  reassignment case is pinned in `tests/test_library_practice_sync.py`.
- 2026-09-06 hops 4–5 — cached title rows paired a canonical Standard slot
  with a historical vetted name. Navigation falls back to that alias only
  when no canonical row matches. Its PB is a labeled comparison, graded on
  the displayed ladder; the 11.93-second browser case checks both ROMs.
