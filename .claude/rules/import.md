---
paths:
  - "src/sm64_events/tracking/importing.py"
  - "src/sm64_events/tracking/import_names.py"
  - "src/sm64_events/library/import_runner.py"
  - "src/sm64_events/library/sheet_link.py"
  - "src/sm64_events/server/import_api.py"
  - "tests/test_sheet_link.py"
  - "src/sm64_events/ui/components/importsection.js"
  - "src/sm64_events/ui/components/importflow.js"
  - "src/sm64_events/ui/components/importlink.js"
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
| A runner's Ultimate Sheet column | the live sheet | stars, IGT |
| A link to your own spreadsheet | an Ultimate copy, or any grid of star / time / strategy | stars and your own segments |

**Two doors were built and REMOVED in round 2 (2026-08-22)** — a pasted block
and a LiveSplit `.lss`: *"These are too difficult to get quite right, and I
think we'll spend too much time getting distracted here."* They are backlog
task 0103, and commit `d70721b9` is the last that carries their
UI, routes, reader and tests; restoring is a cherry-pick, not a rebuild. The
name resolver and block parser they used STAYED, because the link door's grid
path reads a non-Ultimate sheet through them.

Everything downstream of the candidate is shared: the improvement rule, the
provenance, the version attribution, the watermark absorption, the undo — and
in the UI, everything after the input. A new source means a parser that
produces `ImportCandidate`s, one `finish` call in the router, and a door that
renders its input beside the shared flow. The `test_import_*_api.py` files
drive one harness, `tests/import_fixture.py::make_client`.

**The name resolver is what the sheet door never needed.** The sheet already
speaks our vocabulary; a linked sheet of his own arrives as names a person wrote, so
`tracking/import_names.py` answers "which star is `BoB 1`?" against the names
already in play — the game's own star names, the abbreviations runners type,
the sheet's target labels, and the segments built here. Precedence is add-order
and star names go in FIRST, so nothing later can redirect a real one.

**Your own segments can be imported onto; somebody else's id cannot.** A
segment id resolved by NAME against this database is exactly what it says,
while the Ultimate Sheet's segment rows carry ids from whichever machine
scraped them — and a foreign id is worse than a missing one, because it very
likely exists here too and names a different movement. Segments are RTA-only,
so a segment candidate on the IGT clock is refused rather than re-clocked.

| To change... | Edit |
|---|---|
| Which brought-in times actually land | `tracking/importing.py` — pure (`ImportCandidate` + `decide`), no db, no clock; the caller supplies the lookup. THE rule is IMPROVEMENT: a candidate lands only when it BEATS the current best for that target and strategy, which is what makes the button safe to press twice. Two details are load-bearing and each has a test that goes red without it — **per STRATEGY** (`db.current_pb(..., strat_tag=)`; omit that argument and the comparison is against the strategy-blind DISPLAY best, wrongly rejecting a first time on a strategy he has never run) and **counting what THIS batch already landed** (rows insert in order and the latest wins, so a batch holding one target twice would otherwise leave the SLOWER row current — the exact regression the rule exists to prevent). Conversion goes through `core/timefmt.frame_at_or_after`, never restated: only 30 of every 100 centisecond values are displayable, and rounding UP is the conservative direction |
| The command that writes them, and what a landed time IS | `TrackerService.import_times` / `remove_imported`. **Each landed time is ONE journaled `time_imported` event and the PROJECTOR makes the attempt row from it** (`projection.Projector._imported_attempt`, handled FIRST in `feed` so it closes no open run, moves no target and counts as no grab): a success with no anchor, started = ended = the Save, the number on the clock the source measures, `timed_by: "imported"`, `timed_at: "xcam"` for a star so it wears no caveat — his ruling 2026-08-22: *"It should show the new entry in the practice log as an entry row. This is because it then affords us all of the functionality of a practice log entry row (deleting, undoing, etc)"*. This REVERSED the v1 rule ("no attempt is created, it would break replay"): journaling the import and deriving the row keeps replay idempotent AND gives him the row. `publish` returns the journal id so the PB row links to the attempt exactly as `save_pb`'s does — which is what makes the row's own × erase its PB through `clear_attempt`, and `_reproject`'s orphan sweep collect it if the event is ever erased. Removal ERASES the journal rows and replays rather than marking — *"marking them as 'removed' is still worthless. Just completely erase them"* (2026-08-02); safe where a played attempt's events are not, because an import row is read without state and cutting it rewrites no neighbour. Migration v28 deletes the attempt-less pb rows v27 wrote for a few days on this branch |
| A runner's Ultimate Sheet column | `library/import_runner.py::candidates_for(payload, runner)` — pure, so it runs against the bundled snapshot with no network. Detail, the three counted drops and the measured fixture numbers: `.claude/rules/library.md` |
| A block of lines, and the format itself (what a linked grid becomes) | `tracking/import_names.py::parse_block`. ONE time per line, the target before it and the strategy after; tabs, runs of spaces, commas and pipes all separate, so a spreadsheet row, a hand-aligned line and a CSV read the same with no format flag. The LAST parseable field is the time, or `BoB 1 0:23.57` loses its own slot to the shorthand. Blank lines and `#` comments are punctuation; everything else that fails comes back with its number, its text and a reason |
| A link to somebody's own sheet | `library/sheet_link.py` — the WORKBOOK decides: `is_ultimate_shaped` (by the main tab, so a personal COPY is read by the real reader) else `candidates_from_grid`, which turns every tab into lines for the block parser. Rows keep the sheet's own numbering and are stamped `Times!5:` — "row 5 of Times" is advice somebody can follow. **Only `docs.google.com` is fetched**, refused before any request: the SERVER does the fetching, so any-URL means any URL reachable from this machine |
| The REST surface | `server/import_api.py` — every door is READ its source into candidates, then one `finish(source, candidates, rejected, dry_run, **extra)`: preview or land through the service, absorb the rank move, answer in the one shape every door shares (`{source, ...summary, rejected: [{line, text, reason}], dry_run}`). A sixth door is a reader and one `finish` call. `_rows` turns `Unresolved`s into that shape and `_tally_rows` turns the sheet reader's per-kind COUNTS into it (`line` 0 = nothing to point at). **`absorb_after_regrade` lives in `finish`, not in the service** (`tracking/` must not import `server/`), so any NEW caller of `import_times` owes that call too — without it the next rank fetch reads the climb as earned and fires a full-screen celebration for something he did not just do. Detail: `.claude/rules/server.md` |
| Whether an imported time appears on any screen | Nothing special-cases it: it is an attempt, so every surface built from one pass over `db.attempts()` — the practice log's sections, the picker's rank map — sees it the way it sees a played one, in the SESSION it was brought in as well as lifetime. (`views.pb_backed_stars`, which made a PB-without-attempt visible, is deleted.) The one thing `_attempt_json` stamps is `imported: true`, and the log reads it for exactly one purpose: no replay button, a blank keeping the row's buttons in column — "We just obviously can't see any video for it". `tests/test_import_visibility.py` pins both halves |
| A time's ROM version, and why grading needed no new machinery | `StandardsStore.ladders(ek, version)` / `ladder_cs(ek, strat, version)` always resolved per call — the gap was that nothing STORED a time's version. `views.grading_basis` carries the graded row's own `game_version` and four sites pass it: `_strat_rank`, `_best_strategy_graded` (which computes its basis BEFORE its ladder now, since the ladder depends on it), `_section_banner`, and `entity_rank`. That last matters most — it is the number MARELO aggregates, so a JP time on a US best-possible ladder would inflate the whole rating rather than one banner — and `marelo._pb_scores` pays for a second lookup only on a row that carries a version. An AVERAGE basis carries None on purpose: an average is over attempts, and an attempt stores no version. NULL resolves to the running version, so every played best grades byte-for-byte as before (`tests/test_import_version_grading.py` pins that no-regression case explicitly) |

| The hand-entry surface | `ui/components/addtime.js`, mounted in `practicelog.js`'s card BODY (the head's grid comment names adding a fifth named area as the bug it exists to avoid). Reuses `TimeFields` — three boxes reading `{m}'{s}"{cc}`, THE way a time is typed here — and carries NO strategy picker: the card already has one, and the strategy showing there is what the time is filed under ("whatever strategy is selected is the default"). The field SNAPS to a displayable centisecond and shows the snapped value BEFORE saving, via `format.js::attainableCs`; that is not polish, see the section below. **Gated on `isSegment`, never on `course_id`** — full detail below |
| Where the Settings doors live, and how many there are | `ui/components/importsection.js` — ONE section, a chip row naming the ways in (two since round 2), only the chosen one drawn and NOTHING open by default. As stacked `.settings-section`s they pushed Display and Sessions most of a drawer away, and a control you have to scroll to hunt for gets redesigned rather than scrolled to. Each door renders a plain `.importdoor` div, never a section of its own. `tests/test_ui_import_surfaces.py`'s placement gate asserts one section, exactly two chips and zero open doors, so neither the stack nor a removed door can come back quietly |
| **Everything after a door's input** — the preview→import button, the one-sentence outcome, the strategy-less note, the rows that could not be read, the error, the undo and the undone note | `ui/components/importflow.js` — `useImportFlow({source, post, onDone})` is the state machine (idle → checking → ready → working → done → undone, or error), `ImportButton` and `ImportOutcome` draw it, `REASONS` is the one table of reject reasons in words. A door is its input plus those two components; each door carried a copy of all of this before, so a wording change was several edits. `.importdoor-*` classes belong to this module, `.importlink-url` to the door's own input |
| The sheet-link door | `ui/components/importlink.js` — a URL field; the CHECK previews through `TrackerService.preview_import`, the same planner the button performs. Asks for a runner name ONLY when the preview comes back `needs_runner` (an Ultimate copy), because every other sheet is entirely yours already and asking would be a question with one answer. That is a 200 on a dry run and a 422 on a real one — the preview ANSWERS it as a finding, so the door needs no error-message sniffing |
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

Three of the task file's five doors ship; the paste and LiveSplit doors are
backlog (round 2, above). What is genuinely still open on the three:

  * **a way to say which ROM a hand-typed or linked-grid time was set on.**
    They send no version, so they grade on whichever is running, exactly as
    every time stored before this feature does. Stamping the running version
    onto a time he set years ago would assert something he never told us. The
    SHEET door sends one because the sheet actually says.
  * **a tab picker for a linked sheet.** Every tab is read and merged; a sheet
    with a "practice" tab and an "old times" tab cannot yet import only one.
  * **a non-Google sheet host.** Only `docs.google.com` is fetched, and that is
    a deliberate restriction rather than an omission — the SERVER does the
    fetching, so any-URL means any URL reachable from this machine.
