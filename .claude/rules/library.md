---
paths:
  - "src/sm64_events/library/**"
  - "src/sm64_events/ui/components/library*.js"
  - "tools/scrape_sheet.py"
  - "tests/test_library_*.py"
  - "tests/test_ui_library_*.py"
  - "tests/test_export_column.py"
---

# The Library — where to change what

Full detail, evidence and history: [docs/rules/library.md](../../docs/rules/library.md) — read it when a change needs the measurement, ruling or incident behind a contract below, or the historical calibration record.

Startup refresh is `library/background.py`: read Log column A first, skip
unchanged revisions, build/fit/stamp/compress newer workbooks in one quiet
lower-priority child that **never opens app data**; the parent reads and fits
on a thread, stages and commits on the loop (`LibraryStore.stage_absorb` /
`commit_absorb`, which restages if an input moved), installs atomically, then
runs adoption and regrade notifications. The last calibration is saved to
`data/rank_calibration.bin` and reused only on an identical key
(`library/calibration.py::SavedCalibration`). Source runs the base Python with this
checkout on PYTHONPATH; frozen builds dispatch via `gui_entry.py`. Timeout/cancel
reaps the owned worker. `ranks_api.absorb_after_regrade` prepares scores once
per sweep, builds no display labels or replay links, and keeps no cache past the
call. Tests: `test_regrade_batch.py`, `test_library_background.py`,
`test_library_store.py`, `test_library_refresh.py`.

The phase-1 spec `2026-08-04-ultimate-sheet-library-design.md` is a LOCAL file:
anything load-bearing from it belongs here or in the glossary.

| To change... | Edit |
|---|---|
| Automatic movement and piece placement | `placements.py` resolves seed identities against live IDs and exact piece names against their parent; `practice_catalog.py` creates missing ones as manual entries and remembers renames/deletions. Explicit links win. **No Sheet row supplies detection triggers.** See `chain-sheet-standards.md`. |
| Turning a runner's column into times he can IMPORT | `library/import_runner.py::candidates_for(payload, runner)` — pure, runs on the bundled snapshot. Every drop is a NAMED row in the shared `{text, reason}` shape, **never a silent count**: subsections, entity-less targets, unplaceable `segment:` keys (resolved per-db via `resolve_segment`/`mapping.py::segment_seed_key`; segment ids are local). Held rows: `.claude/rules/import.md`. Strategy slot is `adoptions.py::sheet_strategy`. Re-measure fixture counts with the snippet in `tests/test_import_runner.py`, never loosen them. |
| Writing YOUR times back out as a sheet column | `library/export_column.py::column_lines` walks `read_rows` and `targets` in lockstep by order; **`split_from` targets open no block**. Row ids break name ties. A row naming the thing asks with no strategy (`names_the_thing` → `adoptions.py::strategy_name`), narrowed to leftovers (`_claimed_names`); unanswered rows print the held time. Ask the same questions, same order, through `server/import_api.py::sheet_row_placer`. `sheet_time` uses the sheet's notation. `scorecard_api.py::get_column` reads a LIVE fetch, never the cached snapshot. Measure with `tools/roundtrip_sheet.py`. |
| The sheet RENAMING a tab under us | `library/workbook.py::LOG_TABS` + `_first_present`: resolve by candidate, name what the workbook has on a miss. **A snapshot-backed suite cannot see upstream drift — run a sheet-reading change against the LIVE document once.** Story: `.claude/rules/import.md` |
| A cell's FILL, and a runner's platform legend | `workbook.py::Cell.fill_rgb` (rgb or `theme=` via `_theme_palette`, Excel's order); `sheet.py::runner_legends`/`platform_for_fill` by nearest fill within `LEGEND_FILL_TOLERANCE`; `build.py::_entries` carries `platform`. Detail: `.claude/rules/import.md` |
| The sheet-fitted rank standards FOLLOW the library | `library/adoptions.py::library_ladders` via `Adoptions.ladders()`/`load()` → `RankStandards.apply_sheet_ladders`; vetted ladders still win on read; `sheet_ladders.seed.json` is only a floor. **`store.build_and_stamp` fits BEFORE it stamps.** `LibraryStore.absorb(payload)` is refresh's tail. Every door re-derives after an applied refresh (manual route, `import_api._resync_standards`, `_column_work`'s `resync`, `app.py::_refresh_library_quietly` at startup). Gates: `test_library_refresh.py`, `test_library_adoptions.py`, `test_library_store.py::test_a_build_fits_before_it_stamps_so_a_live_refresh_keeps_the_vetted_pairing` |
| WHICH ROM a sheet entry belongs to | `library/sheet.py::entry_version(entry)` — the entry's OWN row's version, `None` = both; the one door for `import_runner.candidates_for` and `ratings._entries_set_on`. **Never fall back to the TARGET's version.** `_visible_entries` keeps its looser display rule. Gates: `tests/test_scorecard_parity.py`, `tests/test_import_runner.py` |
| Reading the `.xlsx` export | `library/workbook.py` — stdlib only (no openpyxl). Read videos from BOTH relationship hyperlinks and `HYPERLINK()` formulas. **Parse attributes per-tag, never anchored to attribute order.** Column A is a formula cell referencing `'Best Time(Raw)'`; edit the worksheet, not `sharedStrings.xml`, to mutate a label |
| Where a target BEGINS | `library/sheet.py` — the **id lineage restart** (a non-grey row that opens a section or re-introduces `[1]` alone), not bold; the grey veto stops subsection reuse of lineage 1. Bold stays on `SheetRow.bold`; disagreements land in `styling_drift` |
| The row grammar, and approach-vs-subsection | `library/sheet.py` — rules in order: (0) `… star Xcam` is an APPROACH; (1) **already-seen ids ⇒ subsection** (a new id proves nothing); (2) grey font `FF434343`, one vote; (3) veto at `SUBSECTION_VETO_RATIO` = 0.70, stage RTA exempt. A disagreement RAISES naming the row. `PLACEHOLDER_CS` refuses `9:59.96`. Fill is banding, not a boundary. `SheetRow` carries `group` and `section`. `tests/test_library_seed.py` pins both Xcam halves |
| A sheet name → an entity key | `library/mapping.py` — misses are OUTPUT; `unknown` must stay empty (`tests/test_library_seed.py`), non-targets carry a reason in `KNOWN_NON_TARGETS`. **A stage RTA is a ROUTE.** Every `+ 100c` row is the 100-coin star. Bowser reds need their own clause |
| One sheet target that is TWO of ours | `library/mapping.py::TARGET_SPLITS` + `build.py::_apply_splits` — the split is STATED by the sheet's marker, applied after assembly, carved target kept right after its parent; a shared piece stays with its heading. Guard: `tests/test_library_build.py` |
| The payload shape, JP/US pairing, coverage counts | `library/build.py` — `schema_version` (ours, forces rebuild) vs `sheet_revision` (sheet's newest Log entry). Merge only into a sibling lacking that version. **The version is a comma-separated TOKEN in the trailing parenthetical** (`sheet.py::version_of`/`base_name`); pairing drops only that token. Targets sharing an entity stay separate |
| The audit view, and the human's corrections | `library/audit.py` + `tools/audit_library.py`/`.html` — **the human's verdict OUTRANKS ours**, applied by `build.py`. Keys by NAME with discriminators (target: ROM version; row: bracket ids), uniqueness asserted in `tests/test_library_seed.py`. A row-kind override MOVES the row. Server binds an OS-chosen free port |
| Fitting a row's rank ladder | `library/ladders.py` model 4: all eight cutoffs, 45 subdivisions; `ranks/timecurve.py` + JS twin interpolate in frames. Contract: [docs/sheet-rank-standards.md](../../docs/sheet-rank-standards.md) |
| Sparse rows and estimates | Every nonempty US/JP/untagged population fits independently, no fill-rate floor; empty rows use a published or audited estimate with sample count zero and explicit provenance; observations replace estimates; every ladder keeps every tier |
| Which approaches become practiceable strategies | `adoptions.library_ladders` + `placements.row_identity`; `strategy_signature.py` feeds `adopt.match_vetted` independently of grading; `sheet_strategy` keeps canonical and variant names |
| Serving a fitted ladder to the ranker | `RankStandards.apply_sheet_ladders` replaces the Sheet foundation on refresh; unchanged seed defaults yield, legacy edits and US/JP per-cutoff overrides survive. Library and Practice read the same ladders. See `docs/sheet-rank-standards.md`, `chain-sheet-standards.md` |
| Which copy of the library a running app reads | `library/store.py` — **newest sheet revision wins, both directions**; `schema_version` checked FIRST and independently. A refresh onto an older sheet is `{applied: false}` and writes nothing |
| The library's REST surface | `server/library_api.py` (`docs/api.md`), mounted unconditionally. **Refresh runs in a worker thread** — the poller shares the process. An untimed entity is 200 + empty list, never 404; a LINK counts as a mapping (adopted rows, then name match; own mapping wins). UI gate: `test_ui_library_link.py` |
| Serving the [[Rank board]] | `library/board.py`, routed at `/api/leaderboard*` in `server/ranks_api.py` (never `/api/library/*`). **Never re-grade a time here** — ladders via `tracking.marelo.entity_ladders`, classification via `tracking.marelo.classify_entity` (single-source rows in `tests/test_single_source.py`). `RatingsCache` rebuilds on revision, adoptions, grading version and a hash of the whole vetted store. PB-basis always; exclusions APPLY. `omitted` counts every runner left off. Routes are plain `def`. `star:15:1` is a known scoring floor, left uncorrected on purpose |
| The player-click door: a runner's name opens their [[Runner page]] | `librarytarget.js` — `onOpenRunner` threaded from `library.js::openRunner` into ONE `RunnerName` component (`.textlink` button only when the prop exists; never on the "You" row). A [[Leaderboard mode]] row is one condensed `LeaderboardRow`; `ExampleMedia` is the ONE player for a sheet entry. Reverse trip lands on `[data-runner][data-time-cs]` (on `ExampleCard` AND `PlainEntry`); `you: true` → `focusYou`. Detail: `.claude/rules/ui-ranks.md` |
| Assigning a library row to a segment the user BUILT | `library/adoptions.py` — an assignment is the USER's fact in their data dir, keyed by stable row name; **never confuse it with committed `library_overrides.json`**. A subsection always contributes `Standard`. `apply_sheet_ladders` re-merges from scratch on every change. Every refusal is a 409 naming its reason |
| Where the sheet is fetched from | `library/source.py` — one place, because the tool and the server both need it now |
| Library entries as example clips for the standards table | `library/examples.py::example_clips(payload, adoption_rows, entity, has_jp_ladder)`, wired in `server/ranks_api.py::create_ranks_router(service, library=, adoptions=)` from app.py; see the BUILT section below |
| The **sheet best** for a strategy | `library/examples.py::sheet_best`, sharing `strategy_entries` with `example_clips`; a videoless entry still counts; ties keep the sheet's answer; a dead video costs the LINK, never the row (`dead_urls=`). Drawn by `ui/components/standards.js` |
| Whether an example video is still watchable | `library/videocheck.py` + `tools/check_videos.py` + `data/video_checks.seed.json.gz` (`core/paths.py::bundled_video_checks`). Standards API filters pools through `dead_urls`; user override URLs are exempt. See [Two facts](../../docs/rules/library.md#two-facts-about-the-data-that-are-not-bugs) |
| Refreshing the bundled snapshot | `tools/scrape_sheet.py` — **READ the `unknown:` list it prints**; `--from <file.xlsx>` offline; gzip with `mtime=0` so an unchanged sheet is byte-identical |
| The WIKI MARK beside a target's name | `librarytarget.js` from `server/library_api.py::decorated`'s `ukikipedia` field (`links.py::ukikipedia_url`; resolver in `.claude/rules/ui-practice.md`). Null → no mark, never a dead one; art `ui/assets/ukikipedia.png`. Gate: `tests/test_ui_library_target.py` |
| The Library tab's own rules (section order, bands, scoring twin, video providers, grid math, trim→import) | `ui/components/librarymodel.js` — import-free except caps.js/format.js, driven by `tests/test_library_model_js.py`. `sectionOrder` deliberately differs from `ladderorder.js::slowestFirst`. **Floor tier key is `Iron`** (via `capName`). Scoring twin mirrors `ranks/scoring.py`, pinned by `tests/test_cross_language_parity.py`; `pyRound` is half-even. `videoSource(url, parentHost)`; `trayToImport` |
| SEARCHING the library | `ui/librarysearch.js` (`tests/test_library_search.py`) + `librarynav.js`. A result row is always a TARGET; results REPLACE the grid; never touch `openGroupKey`. Prefix-per-word AND over a fold; name, then approaches, then runners, each claiming only unclaimed targets. Data rides `GET /api/library` (`approach_names`, `runners` roster) — no per-keystroke fetch; older index degrades to label-only. Own `.library-find*`/`.library-result*` classes; `.library-result` sets `justify-content`. Sweep: `library-search` in `tools/uilab_project.py` |
| Browsing — course grid → a group's target grid | `ui/components/librarynav.js` — inline page state (`openGroupKey`), **never a second `PickerDialog`**; reuses `PracticeCell`. Entity-less targets hand back a numeric `index`; `library.js` resolves either door |
| The target page — one section per [[approach]] | `librarytarget.js` — single-open accordion owned by the parent; auto-expands on the matched strategy (`librarymodel.js::autoExpandName`). Five collapsed `DivisionGroup`s per band; empty ones are dimmed rows. `ExampleCard` for video entries, `PlainEntry` otherwise. **Anchors are `lib-band-<targetIndex>-<slug>-<slug(tier)>`.** Region is a filtering SET (see the JP-standards section). `entryTrayKey` lives here. Link doors (`LinkControl`/`TargetLinkControl`/`LinkDoor`, `linkable`, `adopt_target`), `auto_match`, `standingOn` and the record door: re-read `adopted` from the server, never guess it; fixtures pass `adoptions_path`; the menu overlays. Mechanics: [docs](../../docs/rules/library.md) and `.claude/rules/recorder.md` |
| A runner search on the TARGET page | `librarytarget.js` — sections without a match do not render; matching ones are forced open via `effectiveOpen` (never write `expanded`); `OverallStandards` stands down; "No runner here matches" once per page. `hasRunnerMatch` is the ONE predicate both read |
| The **Overall Rank Standards** section | `ui/components/overallstandards.js` reads `overall_curve`, `overall_overrides`, `calibration_revision`; `librarymodel.js::overallCurveOf`/`curveBands`/`curveStandingOn` through `ui/timecurve.js`. **Never reconstruct generated Overall from eight cutoffs**; legacy adapter only for payloads lacking `overall_curve`; malformed data fails visibly. Nine bands × five divisions; no time → Capless V. Edit/Reset via `PUT`/`DELETE /api/ranks/overall/...?version=`. Guards: `tests/test_overall_model_js.py`, `tests/frontend/overallstandards.test.js`, `tests/test_ui_overall_refresh.py`. [Living rank calibration](../../docs/ranking-calibration.md) owns fitting |
| The [[tray]] and the "vibes grid" | `ui/components/librarytray.js` builds only tier one (labelled `HONESTY_LINE`, not frame-synced); `onStudy` is a prop, `library.js::studyInCompare` owns tier two. The grid animates both ways (not `Modal`), reusing `feedTuning()`/`curve()` |
| Compare's fold-in — WHY `compare.js` is untouched | `library.js::studyInCompare` imports each tray item via `POST /api/compare/import` (one failure never sinks the batch), then calls the existing `enterCompare`/`openIds` door. Leave `Compare` and its props unchanged |

## "Subsection" is the `subsection-tracking` branch's word, not ours

The noun and its definition belong to `subsection-tracking` (`docs/glossary.md`),
as does **moment** (`detectors/moment.py`). Never add a competing definition.
**Do not build moment detection, live moment feeds, or subsection triggers here.**
Detail: [docs/rules/library.md#subsection-is-the-subsection-tracking-branchs-word-not-ours](../../docs/rules/library.md#subsection-is-the-subsection-tracking-branchs-word-not-ours).

## JP standards: registered, resolved by one rule, the game version setting decides which

A JP time gets its own standard only where a difference is ANNOTATED; else the
base ladder applies to both (`ranks/standards.py::ladder_cs(..., version="jp")`).
The game version setting decides grading (`core/modes.py::effective_version`,
`.claude/rules/ranks.md`); this zone owns the DATA (`has_jp_ladder`, `ladder_jp`).
The Library's region control is ONE page-level `RegionSwitch`, a SET defaulting
to both that grades nothing; flags come only from `ui/components/regionflag.js`
(rectangles, no CSS, no emoji); untagged entries wear no flag. Do not build mode
detection here. Detail: [docs/rules/library.md#jp-standards-registered-resolved-by-one-rule-the-game-version-setting-decides-which](../../docs/rules/library.md#jp-standards-registered-resolved-by-one-rule-the-game-version-setting-decides-which).

## Owed on the Overall Rank Standards section, both measured 2026-08-10

Owed: label a sheet-fitted "set by" strategy (never present it as vetted), and
thread one revision counter through the page's three fetch hooks. Before trusting
a guard on a new state, ask which fixture entity can reach it. Detail:
[docs/rules/library.md#owed-on-the-overall-rank-standards-section-both-measured-2026-08-10](../../docs/rules/library.md#owed-on-the-overall-rank-standards-section-both-measured-2026-08-10).

## Every standard links to its own examples — BUILT (task 0098, 2026-08-14)

An example belongs to the threshold whose band holds its time
(`classify.resolve_cutoff_videos`); `standards.js` bands clips through
`librarymodel.js::bandsOf` so both surfaces agree. Detail:
[docs/rules/library.md#every-standard-links-to-its-own-examples--built-task-0098-2026-08-14](../../docs/rules/library.md#every-standard-links-to-its-own-examples--built-task-0098-2026-08-14).

## Two facts about the data that are not bugs

A row can carry its own `entity_key`; **fit a ladder against a row's OWN
entity**. Dead video links are checked only where one link is THE example;
only a dead-shaped status kills a link, never a timeout. Never treat a 404 as a
data bug. Detail: [docs/rules/library.md#two-facts-about-the-data-that-are-not-bugs](../../docs/rules/library.md#two-facts-about-the-data-that-are-not-bugs).

## Numbers this zone is pinned against

Counts in `tests/test_library_seed.py` are FLOORS, never equalities; re-derive a
drop rather than assuming it is a loss. Detail:
[docs/rules/library.md#numbers-this-zone-is-pinned-against](../../docs/rules/library.md#numbers-this-zone-is-pinned-against).

## Proving the classification guard

**A mutation two guards both catch proves neither**: disable every other guard
in the mutant. A test over the shipped ARTIFACT cannot guard the RULE —
`test_no_correction_is_load_bearing_without_a_reason` does. Detail:
[docs/rules/library.md#proving-the-classification-guard](../../docs/rules/library.md#proving-the-classification-guard).

## Historical calibration evidence — 2026-08-05

Historical only, not current instructions; the current contract is
[docs/sheet-rank-standards.md](../../docs/sheet-rank-standards.md). Detail:
[docs/rules/library.md#historical-calibration-evidence--2026-08-05](../../docs/rules/library.md#historical-calibration-evidence--2026-08-05).
