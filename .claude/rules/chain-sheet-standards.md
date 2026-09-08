---
paths:
  - "src/sm64_events/library/ladders.py"
  - "src/sm64_events/library/strategy_signature.py"
  - "src/sm64_events/library/ladder_estimates.py"
  - "src/sm64_events/library/placements.py"
  - "src/sm64_events/library/practice_catalog.py"
  - "src/sm64_events/library/adoptions.py"
  - "src/sm64_events/library/populations.py"
  - "src/sm64_events/library/calibration.py"
  - "src/sm64_events/library/store.py"
  - "src/sm64_events/library/assignment_transaction.py"
  - "src/sm64_events/ranks/standards.py"
  - "src/sm64_events/ranks/curve_types.py"
  - "src/sm64_events/ranks/curves.py"
  - "src/sm64_events/ranks/overall.py"
  - "src/sm64_events/ranks/policy.py"
  - "src/sm64_events/ranks/calibration.py"
  - "src/sm64_events/ranks/timecurve.py"
  - "src/sm64_events/ranks/scoring.py"
  - "src/sm64_events/server/library_api.py"
  - "src/sm64_events/server/overall_api.py"
  - "src/sm64_events/server/rank_history.py"
  - "src/sm64_events/server/scorecard_standards.py"
  - "src/sm64_events/ui/components/librarytarget.js"
  - "src/sm64_events/ui/components/librarymodel.js"
  - "src/sm64_events/ui/timecurve.js"
  - "src/sm64_events/ui/components/standards.js"
  - "src/sm64_events/ui/store.js"
---
# Chain: a Sheet row's effective rank standards

- **Value:** local entity, canonical strategy, original ROM/clock, independent
  Strategy/Overall standards, and one effective calibration revision.
- **Source truth:** current workbook observations; `library.store.build_and_stamp`
  fits the same bytes used by the import/export readers.
- **Sink:** Library standings, Practice standards, MARELO, board and history.
- **Coverage:** all eight cutoffs and all 45 reachable subdivisions, including
  Capless. A nonempty ladder is insufficient. The current fitting, identity,
  and edit contract for Strategy is in `docs/sheet-rank-standards.md`.
  [Living rank calibration](../../docs/ranking-calibration.md) owns the
  independent Overall curve, tuning and activation contracts. Display cutoffs
  cannot reconstruct generated Overall; readers consume full compiled nodes.
- **One clock:** displayed centiseconds on the entity's clock; the Sheet's
  segment numbers are seed references, never local database IDs.

| # | hop | value is true here as | module | probe (reads it) | inject (forces it) | when the hop is broken, the probe shows | when the probe itself is broken, it shows |
|---|-----|-----------------------|--------|------------------|--------------------|------------------------------------------|--------------------------------------------|
| 1 | fit | eight cutoffs, 45 reachable divisions, actual sample count and estimate provenance | `src/sm64_events/library/ladders.py` | `tests/test_complete_sheet_ladders.py` | replace a row's entries with one observed time | a tier or division disappears | checking only truthiness accepts a one-tier ladder |
| 2 | place | `(local entity, sheet_strategy)` | `src/sm64_events/library/placements.py` | `tests/test_library_practice_sync.py` | explicitly adopt the missing row in a temporary database | Library has a ladder but Practice has no strategy | raw seeded number appears valid only in a freshly seeded database |
| 3 | population | strict compatible observations, one best time per runner/family, estimate provenance | `src/sm64_events/library/populations.py` | `tests/test_overall_model.py` | add an alias, wrong-ROM entry, or sparse record | duplicate people/stages or incompatible times move the curve | counting rows instead of identities can agree with itself |
| 4 | compile | independent Overall nodes and attainable inverse goals | `src/sm64_events/ranks/overall.py`, `src/sm64_events/ranks/curves.py` | `tests/test_rank_curves.py`, `tests/test_overall_model_js.py` | probe interior nodes and every division in Python/JS | cutoff-only reconstruction changes scores or printed goals miss their grade | checking only tier cutoffs hides the missing curve |
| 5 | resolve | current Strategy fits/edits and separate region-specific Overall pins | `src/sm64_events/ranks/standards.py` | `tests/test_living_rank_standards.py` | edit Strategy, pin Overall, refresh, restart and reset one region | one layer changes the other or reset restores an old fit | checking only edited values misses leakage |
| 6 | activate | observations, both assignment maps, Strategy layers and Overall publish once | `src/sm64_events/library/store.py`, `src/sm64_events/library/assignment_transaction.py` | `tests/test_library_calibration_store.py`, `tests/test_living_rank_standards.py` | fail fitting/write or refresh while a reader is pinned | partial rows/curves or changed saved assignment bytes survive failure | mutating private `_payload` bypasses the transaction |
| 7 | decorate | local identity, effective US/JP standards and calibration revision | `src/sm64_events/server/library_api.py` | `tests/test_library_practice_sync.py` | change a cutoff and fetch both APIs | raw Library fit differs from Practice's effective value | comparing copies of the raw payload falsely agrees |
| 8 | invalidate | mounted views reload after standards events or reconnect | `src/sm64_events/ui/store.js` | `tests/test_ui_standards_sync.py` | change standards through a second API client | hidden Library or open Practice retains old cutoffs | remounting conceals missing invalidation |
| 9 | render | nine bands and five division targets evaluated from the correct curve | `src/sm64_events/ui/components/librarymodel.js`, `src/sm64_events/ui/timecurve.js` | `tests/test_ui_complete_sheet_ladders.py`, `tests/test_overall_model_js.py` | remove slow observations and supply nodes differing from their tier interpolation | Capless disappears or Overall uses Strategy interpolation | populated-only and tier-only fixtures conceal both defects |

## Counterfactual recipe

In a temporary database, read Lakitu skip's `JD -> Speedkick ending` row and
its Practice standards. Explicitly adopt that row onto the actual Lakitu
segment, then repeat both reads. The task-0126 diagnosis recovered all six
tiers without changing the fitted row: placement was the first failing hop.
`test_lakitu_all_strategies_have_the_same_standards_in_both_pages` pins that
outcome without requiring adoption, then changes a cutoff and compares both.

## Failure catalogue

- 2026-09-08, population/resolve — a star with only explicit real-time rows
  returned through a legacy Strategy fallback after the fitter excluded its
  rows. Reserving the placed target with an empty compatible population keeps
  it unrankable. `test_living_rank_standards.py` exercises this independently
  of opposite-ROM and excluded-unannotated cases.
- 2026-09-08, activate — replacing `_payload` in the adoption refresh fixture
  left the published calibration untouched. The fixture now changes source
  observations and calls real `store.absorb`; a second activation in the API
  would restore an obsolete refresh model. Historical hop numbers below refer
  to the earlier Strategy-only six-hop chain.
- 2026-09-06 round 3, hop 1 — the compulsory best-plus-one-frame elite target
  made JRB's 57-player 2.46 plateau rank below Mario I. Supported fast peaks
  may equal the record; sparse/smooth data retains the percentile fallback.
  `test_library_peak.py` pins shared records, nearby frames, bridging tails,
  slow-mode growth and false peaks; model-version refitting updates old caches.
- 2026-09-06 round 2, hops 1/3/6 — 178/634 base ladders dropped tied tiers;
  Lakitu Standard's three inherited cutoffs replaced its fit; Library hid
  empty Capless. A 30-phase consecutive-frame test also exposed centisecond
  interpolation skipping subdivisions. Complete frame spacing, seed/edit
  provenance, and retaining empty bands fix these independent boundaries.
- 2026-09-06 round 2, identity — changing the grading formula reassigned 38
  strategy matches on identical workbook bytes. Matching now uses its own
  calibrated signature; same-workbook comparison returns zero reassignments.
- 2026-09-06 round 2, snapshot rebuild — reserving Standard excluded title
  rows from alias matching and erased names such as TJ Owlless. A constrained
  second pass recovers unclaimed aliases without reassigning ordinary slots;
  `test_library_adopt.py` pins both boundaries. Rebuilding with two formulas
  that share the same stamp policy cannot expose that policy's alias loss.

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
