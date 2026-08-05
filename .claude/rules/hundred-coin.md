---
paths:
  - "src/sm64_events/tracking/hundred_coin.py"
  - "src/sm64_events/ranks/standards.py"
---

# The 100-coin star — its own zone

Lifted out of `.claude/rules/tracking-storage.md` on 2026-08-03, when merging
the exit-star variants work pushed that file past its 80,000-char auto-load
ceiling. Nothing was summarized; both sections are here verbatim.

Scoped to the two modules that OWN this knowledge rather than to every file it
touches. `tracking/projection.py` and `tracking/views.py` are load-bearing for
it and are deliberately NOT in the globs above: they already pull in
tracking-storage.md, so listing them here would satisfy the size test while
leaving the real per-edit cost exactly where it was. That table keeps pointer
rows into this file instead.

## Which 100-coin ladder a run is graded against

Spec 2026-08-03-hundred-coin-exit-variants. A main course's 100-coin run is
timed separately per EXIT star -- the star you grab to leave once the 100th
coin has given you the star -- and xcams publishes 19 such ladders across the
15 courses, four courses carrying two variants each. The exit star is therefore
a DISCRIMINATOR on the strategy, and the qualification is forced rather than
stylistic: CCM's two variants both define "Standard" AND both define "Open",
so a bare strategy name cannot identify a ladder on `star:4:6`. Storage,
grouping and the seed shape live in `.claude/rules/ranks.md`; what belongs
here is the PROJECTION half.

The split the rule encodes is between what the system OBSERVES and what the
user CHOOSES. The exit star is observed -- the closing `star_collected`'s own
`star_id` says which star it was -- and the sub-strategy inside a variant is
the user's. So `classify(strategies, exit_variants, current, exit_star)`
keeps the current strategy when it already belongs to the variant that was
run, else moves variant and KEEPS THE LEAF (`100c + Slide · Open` plus a Big
Penguin Race exit is `100c + Race · Open`), else takes the variant's first
strategy. User's ruling, 2026-08-03.

Wired at the `hundred_coin_entity` reattribution site in `projection.py`, so
it takes the exact path an ordinary star closure takes; a per-attempt
`_strat_overrides` reclassification still wins, since a manual answer outranks
a derived one. The resolver arrives as an INJECTED callable
(`hundred_coin_strat=None` leaves the pre-2026-08-03 behaviour byte for byte)
because the answer lives in the rank STANDARDS -- a user-editable file the
projector has no business opening -- and because a callable keeps replay
driveable from a test with no store at all. It is threaded through BOTH
`replay()` call sites in `service.py` as well as the constructor: `start()`
REPLACES `self._projector` with `replay()`'s own, so wiring the constructor
alone was silently dead. Read LIVE rather than snapshotted, the same choice
`ranks/history.py` makes -- adding a variant re-classifies the history that
belongs to it on the next reprojection, and freezing the map at startup would
leave a strategy the user just created unable to claim the run it was created
for.

**A FAILED run carries no exit star, so nothing classifies it** and the row
keeps whatever was remembered. That is why this feature does not resurrect the
868 historical 100-coin attempts measured on 2026-08-03 (every one a death or
a reset, none with a strategy, across the installed exe's journal): they stay
unlabelled and the startup prune eats them, which is the user's explicit call
("just drop 'em"). Forward, a failed run inherits the selected variant and so
survives. Zero 100-coin PBs existed in any of the three journals, so no
migration was owed for names that changed shape.

### One reset, one row

Live report 2026-08-03: *"Resetting during a 100 coins star triggers two
resets, for some reason."* It did. Two paths recorded the SAME span: the
engine turned the reset into its own row (which `feed()`'s `seg_closed` loop
reattributes to this very star), and `_close` recorded the plain attempt for
the active target as well. Measured in his journal before the fix — three
reset spans on WF's 100-coin star, each carrying a star-namespace row AND a
segment-namespace row with the same journal id, the same span and the same
strategy.

**The bug is as old as the reattribution**, not new: `_close_by_grab` has
always suppressed its half on the GRAB, and the reset/death path never did. It
was invisible until the 100-coin star got rank standards, because before that
nothing could set a strategy on one, so BOTH rows were unlabelled and the
startup prune ate them. Giving the star ladders is what made it reachable and
what made it show.

`_close` now asks `_engine_records_this_too(star_tgt, outcome)` and records
nothing when the answer is yes. Two clauses, both load-bearing:

* **the outcome** must be one the engine mirrors (`reset`/`hard_reset`/
  `death`). `abandoned` is deliberately absent — a foreign `level_changed`
  cancels a strict waypoint def SILENTLY, so the plain row is the only record
  that leaving mid-run happened, and suppressing it would lose the row rather
  than de-duplicate it.
* **an engine must be ARMED** for that entity. With a deleted, disabled, or
  already-cancelled def nothing else is recording, and the plain attempt is
  the fallback that keeps the retry visible — the same philosophy
  `_close_by_grab` uses. Safe to read the armed set there because `_dispatch`
  runs BEFORE `self._segments.feed()` in `feed()`, so the answer is the state
  as the event arrived.

**No migration is owed**: attempts are replay-derived, so re-projecting his
own journal against the fix took star-6 rows from 18 to 15 — exactly the three
duplicated spans, with every other row and both courses' outcome sets
unchanged. Restarting the server is the whole repair.

#### …and one DEATH, one row — the half the above left behind

Live report the next day: *"triggering a death caused TWO deaths
simultaneously… when we have a 100 coin star selected, there's always 2 deaths
(I tested this across courses, with and without 100 coins selected)."* ONE
`death` event in the journal, two rows out of it — confirmed by reading his
journal, which held exactly one event per death.

`_ENGINE_MIRRORED_OUTCOMES` **already listed `death`**, and that is the whole
lesson: `_close_by_death` calls `_build` DIRECTLY instead of routing through
`_close`, so nothing ever asked, and the set member was a *vacuous guard* —
present, documented, doing nothing. The fix is the same call in that method.

**The mechanism against a third round** is
`test_every_mirrored_outcome_is_actually_suppressed`, parametrised over the set
itself and driven end to end through the real service, so a member added later
with no closer consulting it fails there instead of shipping as a duplicate row
nobody notices. Mutation-proved by restoring the vacuous state.

Measured over all journals: his live one loses **4 duplicate death rows**, each
with a surviving twin for the same span and entity (so zero data loss), nothing
added and nothing else changed; the installed exe loses none, which is expected
— it predates the 100-coin standards that made this reachable at all.

#### …and one row, ONLY when the star is the target — the REVERSE asymmetry

Live report 2026-08-04: *"one reset records two attempts — one correctly in
Unassigned, one phantom row on the 100-coin star."* He walked into WF,
selected nothing, and reset. The two fixes above both added the SAME guard
(`_engine_records_this_too`) to `_close`/`_close_by_death`, and both were
built and tested only against the case where the 100-coin star was
**explicitly targeted** first (every existing "one reset, one row" test called
`service.set_target(2, 6, …)`). That guard's own first line — `if star_tgt is
None: return False` — exists precisely to LET `_close` record the plain
Unassigned attempt whenever nothing is targeted; it was never asked to stop
the OTHER producer, `feed()`'s `seg_closed` loop, from unconditionally
reattributing that same span to the star anyway.

His rule, and it is asymmetric on purpose: *"Untargeted 100 coin run that
successfully completed (we grab the 100 coin star + exit star) should trigger
the strategy and always be attributed."* Combined with the earlier rule this
whole section proves ("resets with nothing explicitly selected… should be
classified as unassigned"), a **SUCCESS always reattributes, targeted or
not** — completing the run IS the evidence — while a **FAILURE reattributes
only when the star IS the active target**; an untargeted failure is not
evidence of a deliberate 100-coin attempt, and reattributing it duplicated
the plain Unassigned row `_close` had already correctly recorded.

`Projector._untargeted_failure(hc, outcome)` is the new gate, consulted at
the reattribution site in `feed()`'s `seg_closed` loop — `_engine_records_this
_too`'s condition read from the OTHER side (`outcome != "success" and
self._star_target() != hc`). When it fires, the physical fact still updates
(`_last_star_attempted`, caveat 15) but no row is appended — `_close` already
recorded the real one, or (rarely) recorded nothing at all because its own,
UNRELATED `_open_is_castle()` rule had already discarded that span (an
attempt opened while Mario stood in a castle hub is never a star attempt,
regardless of where it closes) — in that specific case the fix leaves NO
row rather than a mislabelled one, which is the correct call: neither
producer considered that span a real attempt.

**Measured against both real journals, replayed with the fix versus the same
replay with `_untargeted_failure` forced to always return `False` (the exact
pre-fix behaviour, mutation-proved to reproduce it) — never the live file, a
`sqlite3.Connection.backup` snapshot both times:**

| | worktree | installed exe |
|---|---|---|
| phantom rows removed | **19** | **868** |
| — paired with a literal Unassigned row (the diagnosis's own count) | 1 | 107 |
| — paired with a row attributed to a DIFFERENT star practiced in the same course | 16 | 796 |
| — no surviving partner at all (producer 1 had already discarded its own span via the unrelated castle rule) | 2 | 65 |
| removed rows referenced by a saved PB | 0 | 0 |
| removed rows carrying a strat_tag | 18 (see below) | 0 |
| success rows | 15 → 15, byte-identical | 854 → 854, byte-identical |
| every other attempt id (not removed) | byte-identical, 0 differing | byte-identical, 0 differing |
| new rows added | 0 | 0 |

**The true blast radius is larger than the diagnosis's own 108-row estimate**,
because that measurement searched only for a phantom row paired with a
literal Unassigned row — it did not search for the same phantom paired with a
row attributed to a DIFFERENT star practiced in the same course (the ambient
100-coin engine arms on mere course entry, regardless of what is actually
targeted), which turns out to be the large majority of the real total. Both
shapes are the identical mechanism and the identical rule ("the star not
targeted"), just two different things `_close` legitimately records instead
of Unassigned.

The 18 worktree rows carrying a `strat_tag` ("100c + Secrets · Standard") are
**not a counter-example**: that is a REMEMBERED strategy from an earlier
explicit pick of the 100-coin star, riding along on a phantom row created
later while a DIFFERENT star was the live target — never a deliberate label
of that specific reset, and never a PB. The installed exe (his real practice
history, which never happened to have the 100-coin star actively targeted at
a reset or death) shows the cleaner case: all 868 removed rows are
unlabelled, none cleared, none PB-referenced.

#### …and the Bowser sibling: THREE competing identities, not one

Live ruling 2026-08-04, the diagnosis's own left-open question ("whether the
analogous 56 Bowser-pipe pairs should get the same treatment") answered: *"1.
See if the user has selected anything in the stage before. If so, we track
that. 2. If the user hasn't tracked anything, then we cannot assume that
they're doing reds (star / pipe) or no reds, because they haven't completed
anything yet. Resets should be unattributed, because we could be doing any of
those 3 options."*

A Bowser course offers three mutually-exclusive things to practice — the reds
star graded on the grab alone, the reds star graded on the whole run to the
pipe (`seg:reds->pipe:<abbrev>`), and the legacy no-reds pipe-only segment
(`seg:<abbrev>-pipe`) — and the SAME `arms_ambiently` idiom above arms BOTH
segment defs at once, with no choice from the player. Structurally different
from the 100-coin case: `hundred_coin_entity` is `None` for both, so neither
ever reattributes — each records honestly under its own segment identity in
`feed()`'s `seg_closed` loop, which is why the diagnosis found the symptom
under the movement's own name ("BitDW Pipe Entry") rather than merged into a
star. That also means TWO independent producer-2 rows can compete for one
reset (both defs armed pre-grab), not one — `_untargeted_failure`'s single
reattributed identity generalizes to `Projector._untargeted_ambient_failure
(segment_id, outcome)`, consulted per closing def, gating on `self.target !=
("segment", segment_id)` rather than on a fixed `hc`.

The mirrored half — `_close`/`_close_by_death` must ALSO stop recording their
own Unassigned row whenever the live target names one of the two armed
ambient defs — could not reuse `_engine_records_this_too` at all:
`_star_target()` answers `None` for a segment target BY DESIGN (caveat 10),
which is exactly the case this needs to catch. `Projector.
_ambient_family_records_this_too(outcome)` is the sibling predicate, OR'd
alongside the existing star-side check at both call sites, never replacing
it — the 100-coin path is byte-for-byte unchanged. Together the two
predicates make exactly one of `{reds star, reds->pipe, no-reds pipe}` win a
FAILURE, the one the live target names, or none when nothing does; SUCCESS
is never gated by either (my own extension of his ruling, for consistency
with the 100-coin star's — he did not restate the success half for Bowser).
Caveat 20 in `projection.py`'s module docstring.

**Rejected signals, and why**, since "previously indicated" had to be
answered from the journal alone (the projector is replay-derived — no
browser, no localStorage): `strat_by_segment`/`strat_by_star` were rejected
because they are STANDING preferences that `session_started` deliberately
never clears (caveat 6/17) — picking Reds once would misattribute every
future untargeted reset in that stage forever, not just the session it was
picked in, which reads exactly like caveat 19's earlier finding that a
`strat_tag` can ride along on a phantom row without being a deliberate label
of THAT reset. A separate "last completed" lookup was rejected as redundant
rather than wrong: a segment SUCCESS already auto-follows the live target
onto itself a few lines below in `feed()`, and a star grab already does the
same in `_close_by_grab` — completing one of the three already becomes the
`self.target` signal through the mechanism every other completion already
uses, so a second lookup would just be a second door onto state the first
one already exposes. The live target — the same one `_engine_records_this_too`
already reads for the 100-coin star, generalized to its segment half — was
the only signal answering "did he deliberately choose this" without
inventing a new source of truth; its SCOPE is therefore whatever the
target's own lifetime already is, per session (cleared on `session_started`)
with the SAME cross-course-transit retention every other target enjoys
(caveat 12) — not a bound invented for this family, the one every other
"is this deliberate" check in the codebase already uses.

**Measured against both real journals**, same method as above (a
`sqlite3.Connection.backup` snapshot, never the live file; "before" =
`_ambient_family_records_this_too`/`_untargeted_ambient_failure` monkeypatched
to always return `False` in memory, reproducing the pre-fix behavior exactly
since they are the only two new code paths this adds):

| | worktree | installed exe |
|---|---|---|
| phantom rows removed | **0** | **191** |
| — the def's own row (target didn't name it) | 0 | 178 |
| — the plain Unassigned row (target DID name an armed ambient def) | 0 | 13 |
| removed by outcome | — | 173 reset, 18 death |
| removed rows carrying a strat_tag | 0 | 0 |
| removed rows referenced by a saved PB | 0 | 0 |
| success rows | 15 → 15, byte-identical | 854 → 854, byte-identical |
| new rows added | 0 | 0 |
| every other attempt id (not removed) | byte-identical | byte-identical |

Every removed plain-namespace row was confirmed (not assumed) to have a
surviving segment-side partner at its own closing event — these are exactly
the "he'd already targeted the segment, and the old code recorded a phantom
Unassigned row beside its correctly-attributed one anyway" case, the segment
half of the same asymmetry `_engine_records_this_too` already fixed for a
star target. Two closing events in the installed exe's journal end up with
NO surviving row at all, and both were traced by hand to the identical
shape: a reds-star grab (`_close_by_grab`) closes and clears the plain
attempt's own open span as an ordinary matter of course — unrelated to this
fix, true before and after it — while auto-following the target onto the
star; a LATER reset then finds the reds→pipe segment still armed
mid-waypoint from an EARLIER arm, now describing a stale span for a measure
that is no longer what's targeted. The fix correctly leaves that reset with
no row rather than mislabelling it as a pipe-run attempt he was not
grading — the same "no row beats a wrong one" call the diagnosis made for
the 100-coin star's own no-partner cases.

**Two pre-existing tests corrected** (`tests/test_tracker_service.py`,
`test_segment_armed_broadcast_survives_recursive_publish` and
`test_reproject_during_track_tail_abandons_stale_attempts`): both armed
"BitDW Pipe Entry" ambiently and reset it with nothing ever targeted, to
exercise recursive-publish/reproject-race mechanics that need SOME row to
exist — not about targeting at all, but now silently untargeted-suppressed
by this fix. Both gained one `set_target_segment` call to keep testing what
they actually claim to test, the same shape 7ee5b0b's own report describes
for the 100-coin star's two incidentally-untargeted tests.

## The 100-coin star IS the segment

`tracking/segments.py::hundred_coin_entity(start_triggers, waypoints)` is THE resolver (spec 2026-07-28-multi-step-segments, "the 100-coin star IS the segment," superseding the redirect-based design of `2026-07-28`): (course_id, 6) when a def's own sequence -- start_triggers or any waypoint's clause-set -- includes grabbing a main course's 100-coin star, else None. Same structural clause-search the retired `service.py::_hundred_coin_redirect` used (deliberately NOT a category/seed_key lookup, so a user-reshaped or user-built def keeps matching by what it DOES), run in reverse and with THREE callers instead of one: `projection.py`'s `feed()` reattributes every closed HUNDRED_COIN_EXIT-family attempt (success, death, hard_reset -- every outcome) to the star entity (course_id/star_id, `segment_id` cleared, strat sourced from `strat_by_star` not `strat_by_segment`) BEFORE `_auto_ignored`/cleared-stamping run, so validity bounds and strat memory take the exact path an ordinary `star_collected` closure would; `_close_by_grab` SUPPRESSES the plain star-6 attempt it would otherwise record on the grab itself when an ENABLED engine covers that course (falls back to recording it when none does -- deleted/disabled def, same fallback philosophy the retired redirect used), while still updating `_last_star_grabbed`/`_last_star_attempted` directly (caveat 15's "the grab happened physically" applies even when no attempt records it); `views.py`'s `build_session_view` excludes every matching def (enabled or not) from `segments`/`segment_targets` entirely, and stamps the FIRST matching def's `armed_arms()` state onto the star section's own `armed_detail` (via the SAME `_armed_detail_for` helper segment sections use) -- so the progress line ("Step 1 of 2 · Waiting for Grab 100 Coins") survives the presentation change and reads as the star's own progress, and the star section is added to `seen` (rendered with zero attempts) whenever its engine is armed, mirroring how an armed segment gets a section. `views.stamp_origins` also stamps a plain boolean, `is_hundred_coin_engine`, onto every `GET /api/segments` row from the SAME resolver, so `ui/components/targetpicker.js` (the course-union picker grid) and `ui/components/routes.js` (route step candidates) can exclude the family without re-deriving the clause-search in JS -- a route step referencing this segment directly could never complete, since its attempts no longer carry `segment_id`. `service.py::request_target` ALSO redirects the opposite direction defensively: an explicit `kind="segment"` pick naming a hundred-coin def (a route candidate, a raw API call) commits the STAR instead, since nothing may set a visible target on this family any more. **Existing recorded attempts resolve themselves on replay** (projection is replay-derived): a star-6 attempt recorded under the old redirect design is simply not regenerated the next time the journal replays, since `_close_by_grab` now suppresses it given the SAME (already-reshaped) def; no migration was written or needed. Stars 0-5 are untouched by this family end to end (only star 6 changes). **The reattributed attempt's `igt_frames` is stamped from the CLOSING event's own payload** (`ev.payload.get("igt_frames")`, read at reattribution time) -- a segment's own `igt_frames` is always `None` (segments are RTA-only by design), but the reattributed row IS a star now and stars display/grade on IGT; without this it rendered with no time at all and could not be graded (live report: a WF exit-star grab closed both the reattributed row and the ordinary exit-star row on the SAME event, and only the exit star's row carried the real value). Never derived from `rta_frames` -- a frame-delta, not the Usamune IGT this project's timestamps rule requires; `rta`/`igt` coincide for this family only because the span starts at the reset, which is a coincidence of the shape, not a substitute source. Confirmed this backfills EXISTING historical rows for free on the next reproject (measured against a `sqlite3.Connection.backup` copy of the live db: the exact attempt id from the live report, `750000022095`, came back `igt_frames=2983` — 99.43s, matching the paired exit-star row's own igt exactly) and confirmed no PB or strategy was lost in the process (queried the raw `pbs` table and the journal for any `strat_set`/`target_set` naming one of the 15 seeded segment ids -- zero PBs, and every historical `strat_set` on them was an explicit-null clear, so "no strat yet" for the star entity is the honest state, not data loss). **`segments.arms_ambiently(start_triggers)`** is the sibling resolver for a DIFFERENT question -- "does this def arm merely by the player being present, not by a deliberate action" -- measured against the real corpus at exactly 21 of 84 seeded defs (15 hundred-coin + 3 `seg:reds->pipe:*` + 3 legacy pipe-entry trio, all sharing the `[level_enter, attempt_anchor]`-into-a-course idiom; NOT LBLJ, which enters the castle interior, a place with no course, and NOT the three Bowser fights, which auto-select on entry BY DESIGN so an ambient pin there is not a bug). `views.py` stamps it onto every segment SECTION (the 100-coin family has none left to stamp); `practice.js`'s pinned-card gate reads `sec.arms_ambiently` rather than a category string, which could never have covered `seg:reds->pipe:*` (its own corpus category is `Castle Movement`, indistinguishable by name from an ordinary movement). **projection.py's caveat 12 fix, GENERALIZED**: `Projector._clears_star_target(segment_id)` replaced an earlier version scoped to "only star 6, only its own engine" -- that fix was correct but left every OTHER star (0-5) losing its target the instant its OWN course's ambient engine armed, confirmed live (a `target_set` star (2,3) wiped to `None` by a bare `level_changed` into WF) and confirmed in the user's own journal (replayed `session_id >= 167` against both the fix and the retired unconditional rule: 575 of 3352 events diverge across 41 distinct episodes spanning 13 sessions and several hours of real play, each one a star target the old code held as `None` throughout a whole span of resets/attempts that the fix correctly keeps set). The real question is not "which family" but "is the arming def practiced FROM the same course the target star lives in" -- `origin_course(segment_origin(...))`, the SAME reader the segment-target half of this rule already used -- applied to the star half for the first time; arming from a different course (or the castle/a hub, origin `None`) still retires the target unchanged, and is unreachable in the "leaving" case regardless (the course-change rule in `_dispatch` already retired it on the SAME `level_changed`, before this check runs)


