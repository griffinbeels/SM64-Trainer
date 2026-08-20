---
paths:
  - "src/sm64_events/tracking/importing.py"
  - "src/sm64_events/library/import_runner.py"
  - "src/sm64_events/server/import_api.py"
  - "tests/test_importing.py"
  - "tests/test_import_*.py"
  - "tests/test_service_import.py"
  - "src/sm64_events/ui/components/addtime.js"
  - "src/sm64_events/ui/components/importsheet.js"
  - "tests/test_ui_import_surfaces.py"
---

# Bringing in a time the trainer never watched — where to change what

An **imported time** is a personal best recorded with no attempt behind it. Its
own zone rather than three rows across three rule files, because the whole point
is that the five imagined sources (typed by hand, an Ultimate Sheet column, a
runner's own xcam sheet, a paste template, LiveSplit golds) are five front doors
onto ONE back room — a reader who finds only the door they are standing in front
of will build the sixth one differently.

| To change... | Edit |
|---|---|
| Which brought-in times actually land | `tracking/importing.py` — pure (`ImportCandidate` + `decide`), no db, no clock; the caller supplies the lookup. THE rule is IMPROVEMENT: a candidate lands only when it BEATS the current best for that target and strategy, which is what makes the button safe to press twice. Two details are load-bearing and each has a test that goes red without it — **per STRATEGY** (`db.current_pb(..., strat_tag=)`; omit that argument and the comparison is against the strategy-blind DISPLAY best, wrongly rejecting a first time on a strategy he has never run) and **counting what THIS batch already landed** (rows insert in order and the latest wins, so a batch holding one target twice would otherwise leave the SLOWER row current — the exact regression the rule exists to prevent). Conversion goes through `core/timefmt.frame_at_or_after`, never restated: only 30 of every 100 centisecond values are displayable, and rounding UP is the conservative direction |
| The command that writes them | `TrackerService.import_times` / `remove_imported`. **No attempt is created** — the journal records what the GAME did and `tracking/projection.py` re-derives every attempt from it on replay, so inventing one would break replay idempotency. The `times_imported` row is record/broadcast only, the standing `pb_saved` and `pb_undone` already have. Only a `star:` key can land, and the command refuses anything else outright rather than splitting it, because the API takes an entity key from anyone. Removal ERASES (`delete_pbs_imported_from`) rather than marking — *"marking them as 'removed' is still worthless. Just completely erase them"* (2026-08-02) — and latest-row-wins means each deletion restores whatever that row superseded, exactly as `undo_pb` does for one |
| A runner's Ultimate Sheet column | `library/import_runner.py::candidates_for(payload, runner)` — pure, so it runs against the bundled snapshot with no network. Detail, the three counted drops and the measured fixture numbers: `.claude/rules/library.md` |
| The REST surface | `server/import_api.py` — detail in `.claude/rules/server.md`. **The one thing to carry across from there: `absorb_after_regrade` is called in the ROUTE, not in the service** (`tracking/` must not import `server/`), so any NEW caller of `import_times` owes that call too. Without it the next rank fetch reads the climb as earned and fires a full-screen celebration for something he did not just do |
| Whether an imported star appears on any screen | `tracking/views.py::pb_backed_stars` — THE door both surfaces ask. `build_session_view`'s sections and `build_entity_ranks` are each built from one pass over `db.attempts()`, so without this a brought-in best lands in the store and shows on no screen at all: import 400 times, open the tab he lives in, see nothing. **LIFETIME sections only** — a time belonging to no session must not sit in every future session's log, so the session view stays a record of what he just did, and the picker's rank map (not session-scoped) is where an import is visible FIRST. Absence still means never practised: a star with neither attempts nor a saved best appears nowhere. That file is at its size ceiling, which is why this row lives here and it carries only a pointer |
| A time's ROM version, and why grading needed no new machinery | `StandardsStore.ladders(ek, version)` / `ladder_cs(ek, strat, version)` always resolved per call — the gap was that nothing STORED a time's version. `views.grading_basis` carries the graded row's own `game_version` and four sites pass it: `_strat_rank`, `_best_strategy_graded` (which computes its basis BEFORE its ladder now, since the ladder depends on it), `_section_banner`, and `entity_rank`. That last matters most — it is the number MARELO aggregates, so a JP time on a US best-possible ladder would inflate the whole rating rather than one banner — and `marelo._pb_scores` pays for a second lookup only on a row that carries a version. An AVERAGE basis carries None on purpose: an average is over attempts, and an attempt stores no version. NULL resolves to the running version, so every played best grades byte-for-byte as before (`tests/test_import_version_grading.py` pins that no-regression case explicitly) |

| The hand-entry surface | `ui/components/addtime.js`, mounted in `practicelog.js`'s card BODY (the head's grid comment names adding a fifth named area as the bug it exists to avoid). Reuses `TimeFields` — three boxes reading `{m}'{s}"{cc}`, THE way a time is typed here — and carries NO strategy picker: the card already has one, and the strategy showing there is what the time is filed under ("whatever strategy is selected is the default"). The field SNAPS to a displayable centisecond and shows the snapped value BEFORE saving, via `format.js::attainableCs`; that is not polish, see the section below. **Gated on `isSegment`, never on `course_id`** — full detail below |
| The sheet picker | `ui/components/importsheet.js`, in the settings drawer ABOVE Display, because a first-day gesture must not sit below every tuning link. Names from `GET /api/library/runners` (bundled snapshot, so the list is there the moment the drawer opens); times from a fresh download at import time. `SearchSelect` with one flat group — 448 names, and the panel's own filter floor is what makes a list that long usable. The summary is ONE sentence and opens on what happened rather than on a bare number |

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

## What is deliberately not built yet

A paste template, a runner's own xcam sheet with format detection, and
LiveSplit segment golds. Each becomes a parser producing `ImportCandidate`s;
none of them needs the record, the improvement rule, the version attribution or
the watermark handling rebuilt — which is the point of the shape.

Also not built: a way to say which ROM a hand-typed time was set on. It sends
no version, so it grades on whichever is running, exactly as every time stored
before this feature does. Stamping the running version onto a time he set years
ago would assert something he never told us.
