---
paths:
  - "src/sm64_events/tracking/importing.py"
  - "src/sm64_events/tracking/recordings.py"
  - "src/sm64_events/library/import_runner.py"
  - "src/sm64_events/server/import_api.py"
  - "src/sm64_events/ui/components/importsection.js"
  - "src/sm64_events/ui/components/importflow.js"
  - "tests/test_importing.py"
  - "tests/test_import_*.py"
  - "tests/test_service_import.py"
  - "tests/import_fixture.py"
  - "src/sm64_events/ui/components/addtime.js"
  - "src/sm64_events/ui/components/importsheet.js"
  - "tests/test_ui_import_surfaces.py"
---

# Bringing in a time the trainer never watched — where to change what

An **imported time** is an attempt the trainer never watched him make — a real
practice-log row, stamped with the moment he pressed Save, carrying the personal
best it brought. Its own zone rather than rows scattered across three rule
files, because the whole point is that the doors are one back room — a reader
who finds only the door they are standing in front of will build the next one
differently.

| Door | Reads | Lands on |
|---|---|---|
| By hand, on a star's card | one time, the card's own strategy | that star, IGT |
| A runner's Ultimate Sheet column | the live sheet | stars (IGT); the six seeded Bowser movements, and any segment of yours a Library row is LINKED to (RTA); every other row is HELD — kept aside under its row key until a link gives it a home (round 28) |

**Three doors were built and REMOVED.** Round 2 (2026-08-22), a pasted block
and a LiveSplit `.lss`: *"These are too difficult to get quite right, and I
think we'll spend too much time getting distracted here."* Round 3
(2026-08-23), a link to your own spreadsheet — an Ultimate copy or any grid of
star / time / strategy — and with it the name resolver and block parser
(`tracking/import_names.py`), the workbook reader (`library/sheet_link.py`)
and the CHECK→IMPORT preview step that only it used: *"this is also too much
for us to handle, we need to just get the Ultimate Sheet parsing as good as
possible, and assume that people are using that, if they truly care about
importing times."* All three are backlog task 0103; commit `d70721b9` last
carries the first two and the commit before the link door's removal last
carries the third. Restoring is a cherry-pick, not a rebuild.

Everything downstream of the candidate is shared: the improvement rule, the
provenance, the version attribution, the watermark absorption, the undo — and
in the UI, everything after the input. A new source means a parser that
produces `ImportCandidate`s, one `finish` call in the router, and a door that
renders its input beside the shared flow. The `test_import_*_api.py` files
drive one harness, `tests/import_fixture.py::make_client`.

**The sheet reader lands a star row on its own; everything else, the ROUTER
places — with the Library's facts, in the Library's order.** A star target
carries our entity key, so a star row needs nobody's say-so. A subsection, a
castle movement the mapper never paired, a Bowser row stamped with a foreign
`segment:N` — none of those carries a local id, and a bare segment id is
LOCAL to each database (the Ultimate Sheet's came from whichever machine
scraped it; it may well exist here naming a different movement). So
`candidates_for(payload, runner, place)` asks `place(target, item, kind)` for
every non-star row, and `server/import_api.py::sheet_row_placer` answers from
the same three facts the Library tab shows, in the same order of authority:

1. **the row's explicit link** (`library/adoptions.py`, `{row_key: entity}`,
   the user's own file) — a subsection's or a movement's, landing under the
   strategy the link names (a piece's community timing is its segment's
   Standard). His question, 2026-08-23: *"If an entry is a subsection AND
   we've successfully linked an actual subsection segment that we've recorded
   to that library entry, then when we import, it should import correctly.
   Is this the case?"* It was not — the link fed the ranker and the import
   never read it;
2. **the name-match** an entity-less target gets unasked (round 6: *"we
   should autoassign any segments that exist already"*) — which is why
   "Lakitu skip" lands on the seeded Lakitu Skip in every database;
3. **the seed_key behind a sheet Bowser id**
   (`library/mapping.py::BOWSER_SEGMENT_SEED_KEYS` → `segment_seed_key`):
   `segment:6` is the BitFS pipe entry — the stage's No Reds card — on the
   machine that scraped it, and HERE it is whichever `segment_defs` row
   carries `seg:bitfs-pipe`. His ruling (2026-08-23): *"'Bowser in the Fire
   Sea Course' … are just the No Reds options for each bowser course. Bowser
   in the Dark World Battle == Bowser 1 … These should also be allowed to be
   imported, as they're obviously valid."*

Every answer is a local id on that row's own clock (the placer names it; the
reader never assumes RTA). A row none of the three can place stays in the
could-not-use list under its reason. The import and the Library page read one
set of facts, so they cannot disagree about where a row lives.

| To change... | Edit |
|---|---|
| Which brought-in times actually land | `tracking/importing.py` — pure (`ImportCandidate` + `decide`), no db, no clock; the caller supplies the lookup. THE rule is IMPROVEMENT: a candidate lands only when it BEATS the current best for that target and strategy, which is what makes the button safe to press twice. Two details are load-bearing and each has a test that goes red without it — **per STRATEGY** (`db.current_pb(..., strat_tag=)`; omit that argument and the comparison is against the strategy-blind DISPLAY best, wrongly rejecting a first time on a strategy he has never run) and **counting what THIS batch already landed** (rows insert in order and the latest wins, so a batch holding one target twice would otherwise leave the SLOWER row current — the exact regression the rule exists to prevent). Conversion goes through `core/timefmt.frame_at_or_after`, never restated: only 30 of every 100 centisecond values are displayable, and rounding UP is the conservative direction |
| The command that writes them, and what a landed time IS | `TrackerService.import_times` / `remove_imported`. **Each landed time is ONE journaled `time_imported` event and the PROJECTOR makes the attempt row from it** (`projection.Projector._imported_attempt`, handled FIRST in `feed` so it closes no open run, moves no target and counts as no grab): a success with no anchor, started = ended = the Save, the number on the clock the source measures, `timed_by: "imported"`, `timed_at: "xcam"` for a star so it wears no caveat — his ruling 2026-08-22: *"It should show the new entry in the practice log as an entry row. This is because it then affords us all of the functionality of a practice log entry row (deleting, undoing, etc)"*. This REVERSED the v1 rule ("no attempt is created, it would break replay"): journaling the import and deriving the row keeps replay idempotent AND gives him the row. `publish` returns the journal id so the PB row links to the attempt exactly as `save_pb`'s does — which is what makes the row's own × erase its PB through `clear_attempt`, and `_reproject`'s orphan sweep collect it if the event is ever erased. Removal ERASES the journal rows and replays rather than marking — *"marking them as 'removed' is still worthless. Just completely erase them"* (2026-08-02); safe where a played attempt's events are not, because an import row is read without state and cutting it rewrites no neighbour. Migration v28 deletes the attempt-less pb rows v27 wrote for a few days on this branch. **An import FILLS AN EMPTY HAND** (round 4, 2026-08-24): every entity the batch landed on that has NO active strategy gets its fastest current PB's strategy selected (`_select_freshly_earned_strats` — per-strategy bests via `views.current_pbs_by_strat`, the entity's own clock; *"If there are multiple entries for a given star/segment using different strategies, whichever's fastest becomes selected"*). An entity with an active strategy keeps it — explicit picks are never displaced, and a seeded movement's `default_strat` means it never reads as empty. The fill is a journaled `strat_set`, so replay keeps it and the lifetime kind=all wipe erases it with the journal (*"if I clear all practice data, naturally, all of these strategy selections should also be wiped out"* — no wipe code was needed, pinned by test). Undoing the import deliberately does NOT unset it: a selection is configuration, and unselecting could not restore the prior hand anyway. This is also why `set_strat_segment`'s existence guard reads `db.segment_defs()` rather than the service's cached list — the cache lags a row created outside the service's own CRUD, and the fill made the two sources meet |
| A runner's Ultimate Sheet column | `library/import_runner.py::candidates_for(payload, runner, place)` — pure, so it runs against the bundled snapshot with no network; `place` is how the caller vouches for every non-star row (above). Every row it cannot place comes back as a NAMED row in the shared reject shape, never a count (round 3, 2026-08-23) — and since round 28 that row carries its `row_key`, `time_cs` and ROM, because the caller HOLDS it rather than dropping it (next row). Detail, the four hold reasons and the measured fixture numbers: `.claude/rules/library.md` |
| **Which SLOT a sheet row is** — the strategy name both doors file it under | `library/adoptions.py::sheet_strategy(target, item, kind)` — ONE rule, read by `import_runner.py` and `export_column.py` alike (a test scans both for a second copy). A row named after its target is Standard; a subsection is its segment's Standard; a target that SHARES its entity (a 100-coin star's four routes) keeps its own label and qualifies every sub-row by it ("Slip Slidin' Away + 100c › 100 coin star Xcam"); a name that REPEATS inside one target is qualified by the approach it sits under ("Xiah cycle pipe entry › Red coin star Xcam" — BitDW reds carries five such rows). The qualifier is ` › `, deliberately not the standards store's ` · ` (that glyph means exit-star variant, and `tests/test_single_source.py` keeps it to the modules that own it). Round 28 (2026-09-04): 20 of Raisn's 294 star rows shared a slot with a sibling before this — the database kept one time and printed it on all of them |
| **A HELD TIME** — a row the trainer has nowhere to put yet | Kept, never dropped, never minted a segment for ("we never invent 113 segments"). `db.hold_times` stores `(source, row_key, ROM, cs, reason)` beside the journal; `TrackerService.import_times(held=)` writes them with the batch and `remove_imported` erases them with it; `server/scorecard_api.py::_held_lookup` lets the column export print one wherever nothing else answers; `server/library_api.py::decorated` puts `held` on every row so the Library strip and section head show it (`librarytarget.js::HeldTime`, flags never letters); and `server/import_api.py::held_row_lander`, wired into the library router's adopt doors as `on_adopt`, lands the cells on a row the moment it is linked — through `sheet_row_placer` and the ordinary improvement rule — and releases the hold either way. Four reasons: `subsections`, `no_entity` (castle movements, stage RTAs, the one not-a-target), `segments`, `real_time` (the sheet's one "[N64 REAL TIME]" approach: a frame-counted star cannot hold 42.52). `importflow.js` draws them under "kept aside", not red; the undo offers itself for a batch that held anything |
| The [[Sheet legend]] (round 29 item 2) -- a cell's FILL as its platform | `library/workbook.py` reads every cell's fill (`Cell.fill_rgb`, rgb or `theme=` through the workbook's own palette in Excel's index order -- 0 is lt1, 1 dk1; Raisn's N64 cells are theme 8 = accent5 FF6D01). `library/sheet.py::runner_legends` finds a column whose rows 2/3 read emu/n64 in two different fills (Raisn's convention, and his alone: 2 of 473 columns carry any two-fill legend there, the other's labels a name and a course), `platform_for_fill` stamps a timed cell by the NEAREST legend fill within `LEGEND_FILL_TOLERANCE` (his hand-picked near shades), and the platform rides `SheetRow.entries[runner]` -> `build.py::_entries` (`platform`) -> `ImportCandidate.platform` -> the `time_imported` payload -> `projection._imported_attempt` through `platform_from_payload`, so a reproject keeps it. A column with no legend stamps nothing; `core/modes.py::platform_of` reads that as the emulator. Live measurement 2026-09-04: Raisn 330 n64 / 237 emu, everyone else 44,468 cells unstamped. The literals come from `TrackerMode`, never spelled here |
| The sheet import as a JOB (round 29) | `POST /api/import/sheet/job` + `GET .../job/{id}` in `server/import_api.py`, on `server/jobs.py::JobBoard` — the SAME board the column export runs on, extracted when the import became the third copy of registry + thread + status GET. `_read_sheet(body, step)` is steps 1 of the door off the loop (refresh with `LibraryStore.refresh(step=)`'s three real boundaries, then `candidates_for`), shared by the one-request door and the job; the landing (`finish`) must run ON the loop, so the job thread hands it back through `run_coroutine_threadsafe` and waits. The UI: `importsheet.js` posts the job and polls with `api.js::pollJob`; `useImportFlow` carries `progress`; `states.js::ProgressLine` draws it (the column export's line, shared). Render gate: `test_ui_import_surfaces.py::test_the_sheet_import_narrates_its_steps_on_a_progress_line` |
| The REST surface | `server/import_api.py` — every door is READ its source into candidates, then one `finish(source, candidates, rejected, held=(), **extra)`: land through the service (holding `held` with the batch), absorb the rank move, answer in the one shape every door shares (`{source, ...summary, rejected: [{text, reason}], held: [{text, reason, row_key}]}`). A third door is a reader and one `finish` call. `sheet_row_placer` builds the placer per request from `adoptions` (the SAME `Adoptions` the library router holds, passed in by `server/app.py`), `db.segment_defs()` and the standards store's clock. **`absorb_after_regrade` lives in `finish`, not in the service** (`tracking/` must not import `server/`), so any NEW caller of `import_times` owes that call too — without it the next rank fetch reads the climb as earned and fires a full-screen celebration for something he did not just do. Detail: `.claude/rules/server.md` |
| Whether an imported time appears on any screen | Nothing special-cases it: it is an attempt, so every surface built from one pass over `db.attempts()` — the practice log's sections, the picker's rank map — sees it the way it sees a played one, in the SESSION it was brought in as well as lifetime. (`views.pb_backed_stars`, which made a PB-without-attempt visible, is deleted.) The one thing `_attempt_json` stamps is `imported: true`, and the log reads it for exactly one purpose: no replay button, a blank keeping the row's buttons in column — "We just obviously can't see any video for it". `tests/test_import_visibility.py` pins both halves |
| A time's ROM version, and why grading needed no new machinery | `StandardsStore.ladders(ek, version)` / `ladder_cs(ek, strat, version)` always resolved per call — the gap was that nothing STORED a time's version. `views.grading_basis` carries the graded row's own `game_version` and four sites pass it: `_strat_rank`, `_best_strategy_graded` (which computes its basis BEFORE its ladder now, since the ladder depends on it), `_section_banner`, and `entity_rank`. That last matters most — it is the number MARELO aggregates, so a JP time on a US best-possible ladder would inflate the whole rating rather than one banner — and `marelo._pb_scores` pays for a second lookup only on a row that carries a version. An AVERAGE basis carries None on purpose: an average is over attempts, and an attempt stores no version. NULL resolves to the running version, so every played best grades byte-for-byte as before (`tests/test_import_version_grading.py` pins that no-regression case explicitly) |

| The hand-entry surface | `ui/components/addtime.js`, mounted in `practicelog.js`'s card BODY (the head's grid comment names adding a fifth named area as the bug it exists to avoid). Reuses `TimeFields` — three boxes reading `{m}'{s}"{cc}`, THE way a time is typed here — and carries NO strategy picker: the card already has one, and the strategy showing there is what the time is filed under ("whatever strategy is selected is the default"). The field SNAPS to a displayable centisecond and shows the snapped value BEFORE saving, via `format.js::attainableCs`; that is not polish, see the section below. **Gated on `isSegment`, never on `course_id`** — full detail below |
| Where the Settings doors live, and how many there are | `ui/components/importsection.js` — ONE section, a chip row naming the ways in (one since round 3), only the chosen one drawn and NOTHING open by default. As stacked `.settings-section`s they pushed Display and Sessions most of a drawer away, and a control you have to scroll to hunt for gets redesigned rather than scrolled to. Each door renders a plain `.importdoor` div, never a section of its own. `tests/test_ui_import_surfaces.py`'s placement gate asserts one section, exactly one chip and zero open doors, so neither the stack nor a removed door can come back quietly |
| **Everything after a door's input** — the one-sentence outcome, the strategy-less note, the rows that could not be used, the error, the undo and the undone note | `ui/components/importflow.js` — `useImportFlow({source, post, onDone})` is the state machine (idle → working → done → undone, or error), `ImportOutcome` draws it, `REASONS` is the one table of reject reasons in words. The rejects draw in FULL, grouped under their reason's sentence (`groupRejects`) — the 12-row cap with its "…and N more" went in round 3 (2026-08-23): a sheet column drops 30-odd rows and those are the ones he reviews, so the box scrolls past 320px rather than hiding any. Render gate: `tests/test_ui_import_surfaces.py::test_the_sheet_door_lists_every_dropped_row_by_name_under_its_reason`, which replaces the live download in-process and drives the real door. A door is its input plus those two components; each door carried a copy of all of this before, so a wording change was several edits. `.importdoor-*` classes belong to this module |
| The sheet picker | `ui/components/importsheet.js` — names from `GET /api/library/runners` (bundled snapshot, so the list is there the moment the drawer opens); times from a fresh download at import time, which is why this door has NO preview step (the preview would be the same 7 MB download). `SearchSelect` with one flat group — 448 names, and the panel's own filter floor is what makes a list that long usable. **`ImportOutcome`'s undo button is the only caller of `DELETE /api/import/{source}`** — a delete route with nothing calling it is a capability that does not exist, and the one moment it is wanted is right after several hundred bests land |

## What the undo's gate cannot cover, and why

`DELETE /api/import/{source}` is tested at the API layer, including the source
shape that actually bites: real roster names carry a SECOND colon (`adelyn :3`,
`bee :3`), a space (`Salt & Ginger`) and non-ASCII (`ガミル`), so
`sheet:<name>` has to survive percent-encoding and Starlette's `:path` decode.
No name in the roster contains a slash, which is the one character that would
break the route.

The BUTTON has no render gate. Reaching the state that draws it means
completing a real import, and the panel always refreshes — a 7 MB download per
run, which is not a gate, it is a network test. Driven by hand instead
(2026-08-20): 1 star with a PB → import DentoriousRed → 13 added, 10 stars with
PBs → undo → 13 erased, back to 1. Written down rather than left implied,
because a missing gate that nobody names reads later as coverage.

## The kind guard, and a render test that could not fail

The add-a-time control is gated on `!isSegment(sec)`. The obvious guard —
`sec.course_id != null`, meaning "this is a star" — is WRONG: a segment that
originates in a course carries a `course_id` too (`views.py` stamps
`origin_course`), so it drew the control on ~30 course movements where every
save comes back 422. A dead control whose reason lives nowhere near the click
is the exact shape he reports as a bug.

**The render test for it cannot fail, and that is the transferable part.** The
two guards differ ONLY on a course-originating segment, and every segment
`tools/ui_fixture.py` seeds is a castle movement whose `course_id` is null — so
both answer identically on every card the rig can draw. Measured by mutation:
putting the bug back left the render version green. The gate is a comment-
stripped source scan instead (`tests/test_ui_import_surfaces.py`), probed in
both directions per `tests/source_scan.py`.

This is the fixture-reach problem from `.claude/rules/ui-core.md` in its
sharpest form: not a state the fixture renders wrongly, but two rules the
fixture cannot tell apart.

## The celebration guard, and why its first version was toothless

A landed batch moves ranks for a reason that is not a run — the same shape as
the game-version flip, which is why `absorb_after_regrade` already existed.

The test for it must assert on the **watermark**, not on a celebration payload.
A fresh scope's first rank is SEEDED silently and its first view ABSORBS
(`server/ranks_api.py::_build_marelo`), so "assert no celebration" on a new
database is green whatever the code does. The first version of
`tests/test_import_api.py::test_an_import_absorbs_the_rank_it_produced` passed
with the absorb call replaced by `pass`. The working shape: import a slow time,
fetch `/api/marelo` once so the watermark exists and the arrival is absorbed,
import the real batch, then assert the watermark MOVED with the rank — plus an
assertion that the rank rose at all, so the test cannot pass vacuously.

It also needs the REAL bundled `rank_standards.seed.json`. With a hand-made
seed the imported stars have no ladder, nothing grades, and the guard cannot
fail for a second reason.

## The snap, and why it is not optional

The timer is a frame counter, so only 30 of every 100 centisecond values can
ever appear on it. A time TYPED by hand therefore has a **70% chance** of
naming one that cannot — the honest answer to 15.01 is 15.03.

The server rounds UP on save (`core/timefmt.frame_at_or_after`, the
conservative direction: it never credits a time the timer could not display).
Showing the rounded value in the field BEFORE saving is what stops the card
coming back with a number nobody typed, which would read as the app losing
input. That is the whole reason `ui/format.js` carries a second copy of the
rule — a deliberate duplicate, with its row in
`tests/test_cross_language_parity.py` comparing both REAL implementations over
every centisecond in the first two seconds.

Measured on the sheet's own data: 97.1% of 44,701 hand-typed entries already
land on the displayable set, which is the OPPOSITE bias to a keyboard — people
write down times they read off a screen. Do not use that figure to argue the
snap is rare for typed input.

## The live sheet moves under us

Found 2026-08-20 by driving the real drawer, not by any test: the Ultimate
Sheet renamed its `Log` tab to `Log (Main)` / `Log (Extensions)` and grew an
`Ultimate Sheet Extensions` tab. `workbook.log_revision` RAISES rather than
degrading, so that one rename took down **every** path that reads the live
sheet — `POST /api/library/refresh`, `tools/scrape_sheet.py` and this import
alike — while every test stayed green, because they all read the bundled
snapshot.

The tab is resolved by CANDIDATE now (`workbook.LOG_TABS`, historical name
first) and a miss names what the workbook DOES have. Nothing else had drifted:
against the 2026-08-21 workbook the parse still yields 252 targets, 448 runners
and 44,952 entries.

**The lesson for the next reader**: a snapshot-backed test suite cannot see
upstream drift at all, and the failure surfaced as a UI that hung rather than
as anything that looked like a parse error. When a sheet-reading path is
changed, run it against the LIVE document once — `tools/scrape_sheet.py`
exists for exactly that, and its "unknown:" list is the deliverable.

## A time with no strategy lands, and that reverses an earlier rule

`decide` refused a strategy-less candidate until typed names existed. That
was right when every source had one — the sheet always does, and the card's
control uses its own picker — and wrong the moment people could write their
own rows. Most people writing down a best write the star and the time and
nothing else, so refusing those rejects the bulk of a real personal sheet.

The store already allows it: such a row shows a time and never GRADES, because
`views.current_pbs_by_strat` cannot attribute it, and `tracking/caveats.py`'s
`unattributed` mark is what says so where the click lands. It is compared
against the strategy-blind best (`current_pb` with no `strat_tag` clause), so
it has to beat everything to land — and counted as `without_strategy` in every
summary, so it is never silent. Stored as NULL, never `""`: one spelling of an
absence.

## A number that is a measurement rather than a choice

**A typed centisecond is snapped UP.** Only 30 of every 100 values can appear
on the timer, so a hand-typed time has a 70% chance of naming one that cannot;
`core/timefmt.frame_at_or_after` rounds up, which is the conservative
direction. See the snap section below for why the FIELD has to show it too.

## What is deliberately not built yet

Two of the task file's five doors ship; the other three are backlog (above).
What is genuinely still open on the two:

  * **a way to say which ROM a hand-typed time was set on.** The card's box
    sends no version, so it grades on whichever is running, exactly as every
    time stored before this feature does. Stamping the running version onto
    a time he set years ago would assert something he never told us. The
    SHEET door sends one because the sheet actually says.
  * **the sheet's castle-movement rows that nothing links or name-matches**
    are HELD since round 28 (22 of GTM's, 128 of Raisn's): each shows on
    its Library row and lands the moment its target is linked to a segment
    in the Library (or a segment of that name exists), which is the
    mechanism he asked for rather than a per-row picker. What is still
    open is the segments themselves — 128 castle movements the sheet times
    at a granularity a fresh database has no definitions for.
  * **a sheet cell the timer cannot show.** 6 of 3,041 cells across eleven
    runners (2026-09-04) name a centisecond off the 30-per-second set
    (50.92); the import rounds UP by rule and the column prints 50.93.
    `tools/roundtrip_sheet.py` classes those `snapped`, beside the verdict.

**The other direction has its own harness: `tools/scorecard_parity.py`.**
The round trip asks whether a column comes back byte for byte; parity asks
whether the SCORECARD agrees with the runner it was imported from — import
one runner, set the goal to him, every tile +0.00; import the next, set the
goal to both, again. His words (round 34, 2026-09-05): *"At each step
(importing player 1 -> setting scorecard to player 1, additionally importing
player 2 -> setting scorecard to player1 + player2, etc) it should be +0.00
for all cards, by definition."* It runs off the library snapshot on disk (no
fetch), computes its own runner set covering every importable worksheet row,
and prints both sides of each mismatching tile. It found round 34's cause in
one run: a merged (JP)/(US) row lands both of a runner's times under one
strategy, so a PB slot keyed by strategy alone hid the faster ROM's row.
`tests/test_scorecard_parity.py` is the same walk on the bundled seed.
