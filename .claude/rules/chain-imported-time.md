---
paths:
  - "src/sm64_events/library/workbook.py"
  - "src/sm64_events/library/sheet.py"
  - "src/sm64_events/library/build.py"
  - "src/sm64_events/library/store.py"
  - "src/sm64_events/library/adoptions.py"
  - "src/sm64_events/library/adopt.py"
  - "src/sm64_events/library/import_runner.py"
  - "src/sm64_events/library/ratings.py"
  - "src/sm64_events/library/export_column.py"
  - "src/sm64_events/server/import_api.py"
  - "src/sm64_events/server/scorecard_api.py"
  - "src/sm64_events/tracking/importing.py"
  - "src/sm64_events/tracking/recordings.py"
  - "src/sm64_events/tracking/views.py"
  - "src/sm64_events/storage/db.py"
  - "src/sm64_events/ranks/scorecard.py"
  - "src/sm64_events/ui/components/scorecard.js"
---
# Chain: an imported time, from the Ultimate Sheet cell to the Scorecard tile

Read this before editing any module named below. A symptom is seen at the
SINK; its cause sits at the FIRST hop where the value stops being true, and a
fix downstream of that hop cannot hold. Before any fix: run the counterfactual
(force the known-good value in at a hop, re-run to the sink, see whether the
symptom vanishes) — the earliest hop whose correction prevents the failure is
the one to fix.

This chain has TWO sinks and they check each other, which is what makes it
diagnosable at all: the Scorecard tile (`tools/scorecard_parity.py` — import a
runner, point the goal at him, every tile must read +0.00) and the pasted
column (`tools/roundtrip_sheet.py` — import a column, export it, every row must
match). A cause that moves only one of the two names its own hop.

- **Value:** one runner's time for one thing, as the Ultimate Sheet records it.
- **Source truth:** the runner's own column in the live workbook, read by `uv run python tools/scrape_sheet.py`.
- **Sink:** the YOU number on that entity's Scorecard tile, and the cell the column export writes back.
- **One identity:** `(entity, clock, strategy slot, ROM)`. Every hop from 4 on keys on all four, and each of the four has been the hop that broke — see the catalogue.

| # | hop | value is true here as | module | probe (reads it) | inject (forces it) | when the hop is broken, the probe shows | when the probe itself is broken, it shows |
|---|-----|-----------------------|--------|------------------|--------------------|------------------------------------------|--------------------------------------------|
| 1 | the workbook cell | the runner's own column cell, its text and its fill | `src/sm64_events/library/workbook.py` | `uv run python tools/scrape_sheet.py` — rebuilds the snapshot and prints its `unknown:` list, which IS the deliverable | none — the sheet is a document nobody here owns, so the known-good cell goes in at hop 3 | a runner's column absent, or a fill nothing maps to a platform | a clean rebuild of a CACHED snapshot: the tool refuses on a non-2xx, so silence means it never fetched |
| 2 | the worksheet row | `SheetRow` — its label, its declared ROM (`version_of`), its time, its runner | `src/sm64_events/library/sheet.py` | `uv run python tools/audit_library.py` — every target, its verdict and the ratio behind each row, by eye | build a `SheetRow` directly, the shape `tests/test_export_column.py` uses | a row read as version-less whose label names one (58 rows wrote it beside other words, 2026-08-09), or a time the `_TIME` regex refuses | a verdict list over a snapshot rather than the live sheet — right about a document that has moved on |
| 3 | the library payload | targets → approaches/subsections → entries, each entry carrying `runner`, `version`, `platform` | `src/sm64_events/library/build.py`, `src/sm64_events/library/store.py` | `uv run python tools/check_videos.py` and the payload's own gates in `tests/test_library_seed.py` | write a payload dict by hand — `tests/test_library_ratings.py` and `tests/test_import_runner.py` both do | a (JP)/(US) pair that never merged into one target, or `matched_strategy` missing from every approach (0 of 203, which is what a store rewritten by a test looks like) | 18 green assertions about a seed file nobody imports from — the app reads `data/sheet_library.json.gz`, the tests the bundled seed |
| 4 | the slot rule | which strategy name a row files under — the target-named row is `Standard`, a repeated name is qualified by its approach, a 100-coin route by its route | `src/sm64_events/library/adoptions.py`, `src/sm64_events/library/adopt.py` | `uv run python tools/roundtrip_sheet.py --only Raisn` — two rows in one slot show up as `extra` and `differs` | pass an `overrides` dict to `store.build_and_stamp`, the shape `tests/test_library_store.py` uses | several worksheet rows of one entity resolving to one name: one row's time printed on all of them (21 of Raisn's 28 star misses, 2026-09-04) | MATCH on a runner whose column has no repeated names — pick the fullest column, never a short one |
| 5 | the import's candidate | `ImportCandidate(entity_key, strat_tag, time_cs, game_version, timer_mode, platform)`, or a HELD row naming why not | `src/sm64_events/library/import_runner.py` | `uv run python tools/roundtrip_sheet.py` — prints `landed` and the held reasons per runner | call `candidates_for(payload, runner)` directly, as `tests/test_import_runner.py` does | a time stamped with a ROM its row never declared (3,123 entries inherited a merged target's label, 2026-09-05), or a whole class held with no reason a person can read | counts that look plausible because every refused row is counted as held rather than as missing |
| 6 | the saved PB | a `pbs` row with `imported_from`, `game_version`, and its attempt's `platform` | `src/sm64_events/tracking/importing.py`, `src/sm64_events/storage/db.py` | `uv run python tools/what_happened.py --list` names the live journal; a read-only SELECT over `pbs` answers by value | `db.insert_pb(...)` — the same call the import makes, used throughout `tests/test_scorecard_api.py` | two of a runner's times collapsed into one slot, or a row landed on the wrong entity | a query against the repo's database while he plays on the installed exe's |
| 7 | the current-PB map | latest row per `(course, star, segment, clock, strategy, ROM)`, and the fastest of those per identity | `src/sm64_events/tracking/views.py` | `uv run python tools/scorecard_parity.py --runner <name>` — prints every PB row it can see beside the sheet's own entries | build the row list by hand and call `latest_pbs_by_strategy` / `fastest_current_pbs` | a slower row shadowing a faster one on the same identity (round 33: strategy; round 34: ROM) | 0 mismatching tiles under one region setting only — run it under `--regions us`, `jp` and both |
| 8 | the tile, both sides | `you_cs` (your fastest across strategies and the card's regions) and `goal_cs` (what the goal offers for that entity) | `src/sm64_events/server/scorecard_api.py`, `src/sm64_events/library/ratings.py`, `src/sm64_events/ranks/scorecard.py` | `uv run python tools/scorecard_parity.py` — the whole walk, runner by runner, both sides printed per mismatching tile | `PUT /api/scorecard/goal` and `PUT /api/scorecard/regions` through `TestClient`, as `tests/test_scorecard_parity.py` does | a runner you imported grading against himself at anything but +0.00 | agreement: both sides can share a wrong rule and the walk stays green (2026-09-05, the ROM fallback both readers used) |
| 9 | what he reads | the card's YOU / GOAL / Δ cells, and the column cell the export writes back | `src/sm64_events/ui/components/scorecard.js`, `src/sm64_events/library/export_column.py` | `uv run python tools/contact_sheet.py .score-card` — the card at four widths, and you LOOK at it | `serve_ui_live(db_path=...)` in `tools/ui_fixture.py` — the real app offline on a seeded snapshot | a cell reading `set…` where a goal exists, or a blank column cell where a PB was landed | a fixture card holding no imported rows — nobody is looking at the card he reported |

## Counterfactual recipe

Recording links follow the same time through hops 1–6: `Cell.link` →
`SheetRow.video` → entry `video` → candidate `video`/`row_key` → imported
journal payload. Held times retain `video` under their source row key.
`tracking/recordings.py::backfill_matches` repairs historical imports only
when source, performance identity and row evidence agree unambiguously.
`TrackerService.recording_link` reads the imported original or the durable
`attempt_recordings` edit (including an explicit removal). At hop 9 the export
asks for the SELECTED PB's attempt link; `scorecard.js::columnHtml` places it
on that time's clipboard cell. Probe with `tests/test_recording_links.py` and
`tests/test_ui_recording_links.py`; inject two attempts with different links,
select the faster unlinked attempt, and require no hyperlink. The independent
sink check reads hyperlinks from the pasted workbook, not the export JSON.

Take one tile he reported. `uv run python tools/scorecard_parity.py --runner
<name> --verbose` prints, for every mismatching tile, the sheet's own entries
(target, row, slot, ROM) beside the database's PB rows and held times — the two
halves of hops 4 to 7 in one screen, which is usually the whole diagnosis. If
both sides look right there, the boundary is at hop 8 or 9: put the same
database in front of the real app with `serve_ui_live` and shoot
`uv run python tools/contact_sheet.py .score-card`.

Then run the OTHER sink. A cause at hops 4 to 6 moves both the tile and the
column, so `uv run python tools/roundtrip_sheet.py --only <name>` goes red too;
a cause at hop 7 or 8 moves only the tile. That split is the cheapest way to
tell an import bug from a reading bug.

## Failure catalogue

- 2026-09-02 hop 5 — a merged (JP)/(US) approach holds both regions' times while the TARGET keeps one label, so Raisn's US 45.70 landed as a JP time and its own (US) row exported blank: 57 of his 58 missing star cells. Fix by reading the ENTRY's version, never the target's.
- 2026-09-04 hop 4 — `mapping.py` files every "+ 100c" row under `star:<course>:6`, and a course opens one target per route ending on a different star (CCM opens four). All four were target-named, so all four became `Standard` on one entity: four times in one PB slot, the fastest printed on every row. 21 of Raisn's 28 star misses. Fix by keeping a 100-coin star's rows under their own distinct names until a qualified name exists that the standards store also recognises.
- 2026-09-04 hop 9 — the export asked every worksheet row for the SHEET's strategy name, but times he PLAYED are filed under names he picked: 3 of 399 asks answered on a real database. Fix by asking the blind question for the leftovers, per row, excluding the names the block's other rows claim.
- 2026-09-05 hop 7 — the Scorecard's YOU asked the strategy-blind `current_pb`, which answers the LATEST save across strategies; an import lands a star's rows in sheet order, so RONC3NA's own column showed 11"50 against his 11"26 goal. Fix by taking the fastest of the identity's current rows, which is what a runner goal offers.
- 2026-09-05 hop 7 — the same map keyed a slot by strategy ALONE, so a merged (JP)/(US) row's second ROM shadowed the faster one: six of Raisn's tiles and two of RONC3NA's. Fix by keying on the ROM too, which `db.current_pb(game_version=)` always did.
- 2026-09-05 hop 5 — the import stamped `entry.version or target.version` and the runner goal read the same fallback, so both AGREED and both were wrong: 3,123 entries on 68 rows wore a ROM the sheet never claimed for them. Fix at `sheet.entry_version`, one door for both readers. Worth carrying: agreement between two readers is not correctness, and the parity walk cannot see a rule both sides share.
- 2026-09-05 hop 3 — a live refresh stamped the vetted pairing BEFORE fitting ladders, and the matcher compares ladders, so it matched nothing: 270 unmatched, zero vetted names, and an import that named strategies "Left side TJ" beside the vetted "Leftside". Fix by fitting before stamping.
- 2026-09-05 hop 3 — a test pointed the library STORE at the bundled seed, and the store owns the file it is given: every `matched_strategy` was stripped from a tracked file, turning 14 unrelated tests red in the next full run with nothing failing at the time. Hand the store a copy, never a file you want to keep.
