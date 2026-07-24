# Default Routes — Spec #2: the corpus

Date: 2026-07-24
Status: design — approved 2026-07-24, ready for writing-plans

Companion: `2026-07-24-default-routes-corpus-sources.md` (the raw Ukikipedia
transcription + the decomp evidence + the community-alias glossary). Predecessor:
`2026-07-23-default-routes-foundation-design.md` (spec #1, merged to `main` as
93fa9eb — every contract used here is frozen there).

## 1. Overview

Spec #1 built the capabilities — waypoint sequences, route-scoped arming,
categories, seed/reconcile — and proved them with the ten pre-existing segments.
This spec fills them with content:

- **13 main-category routes** (16★ ×4, 70★ ×5, 120★ ×2, 0★, 1★)
- **37 Stage RTA routes** (per-course ordered star lists)
- **~55 castle-movement segments**, shared across routes (spec #1 decision 3)

Everything ships as **seed data** in `src/sm64_events/data/defaults.seed.json`,
generated from compact tables by a new `tools/build_defaults_seed.py` and
reconciled at startup by the existing `tracking/defaults.reconcile_defaults`.

**Non-goals:** no engine changes (the matcher is frozen); no `ui/components/*.js`
edits (a concurrent session owns the UI redesign); no waypoints editor; no new
trigger or guard types.

## 2. Decisions (user, 2026-07-24)

1. **Star-level steps everywhere.** Every star is its own route step, movements
   interleaved, step labels prefixed with the stage. A 120★ route is ~160 steps.
   Rationale: Practice RouteFocus walking you star-by-star through a full run is
   the point of the feature; the run view's Focus / click-to-hide already tames
   the split count. (Rejected: stage-level `need=N` steps for 70★/120★ — fewer
   splits, but within-stage order would exist only in the Stage RTA routes.)
2. **Castle-secret stars are real stars.** `addresses.STAR_NAMES` gains a course-0
   row so MIPS ×2 and Toad ×3 are nameable, pickable, and usable as route
   candidates. (Rejected: omitting them — the routes would be missing real steps;
   and modelling them as segments — breaks star↔segment parity.)
3. **The seed is generated, not hand-written.** `tools/build_defaults_seed.py`
   emits the JSON from compact Python tables, mirroring the established
   `tools/scrape_ranks.py` → `rank_standards.seed.json` pattern. The JSON stays
   checked in and is still THE artifact the app reads.

## 3. Prior art

- **Content**: the Ukikipedia RTA guides name the variants and, for 16★, list
  castle movements as first-class route items ("BoB to WF Castle Movement"). The
  per-course pages supply both the Stage RTA lists and the expansion of the
  full-game guides' "all stars" shorthand. Transcription, not invention — see
  the sources companion.
- **Movement segments as a practiced unit**: the community already treats them
  that way (the per-course "Castle movement" sections with their own reference
  clips, and the guide's nav bar's "Castle Movement" row). We are not inventing
  a taxonomy; we are naming what runners already drill.
- **Generated seed**: `tools/scrape_ranks.py` → `data/rank_standards.seed.json`
  is the same shape in this repo (compact input → checked-in JSON artifact →
  reconcile at startup). `tools/make_manifest.py`'s round-trip test is the model
  for the drift guard here.

## 4. Section 1 — the movement-segment grammar

The shape of every movement is **forced by the frozen matcher**, not chosen. Two
engine facts do the forcing (`tracking/segments.py`):

- A **plain** (waypoint-less) def is disarmed with no row by any `area_changed`
  away from its arm position (`_at_arm_position` relocation rule, `segments.py`
  ~846), and silently disarmed by any `level_changed` matching neither its start
  nor its end (~936).
- A **waypoint-bearing** def is silently cancelled by any *major action* —
  `star_collected`, `key_grabbed`, or a real-edge `level_changed` that is not the
  next waypoint (`_is_major_action`, `_feed_waypoint`) — while `area_changed`,
  `warp_entered` and `spawned` stay transparent.

### 4.1 Three independent axes

A movement is described by its **start form**, its **end form**, and **whether it
needs waypoints**. The three are orthogonal; §4.4 lists all 55 explicitly.

**Start form** — the completion event immediately preceding the movement:
- `level_exit from=A` — the usual case (leaving a course).
- `level_enter to=6 from=16` — the run-opening castle entry.
- `star_grabbed course=0 star=N` — the movement follows a castle-secret star. Used
  when the movement needs waypoints (a star grab would otherwise cancel it) or
  simply reads better as "from MIPS to X".

**End form:**
- `level_enter to=B` — the usual case, and the only form for a movement that
  precedes a course's star block.
- `area_enter level=6 area=R` — the movement ends at a castle region because a
  castle-secret star is grabbed there next. A plain def may legally end this way:
  the end check runs *before* the `area_changed` relocation disarm.
- Never `star_grabbed` — see §4.2 and §5.2.

**Waypoints are required when** the movement crosses a hub level (courtyard 26,
grounds 16, or the castle interior 6 when entering from one of them) — a plain def
silently disarms on a `level_changed` matching neither end — **or** when it crosses
a castle region while ending on a course — a plain def is disarmed by the
`area_changed` — **or** when it re-enters and pause-exits a course
(`[[level_enter to=X]], [[level_exit from=X]]`), which no plain def can express.

A movement that stays in one region and ends on a course needs none; and a
movement that ends at a region boundary needs none, because its end fires on the
very `area_changed` that would otherwise disarm it.

### 4.2 Two invariants

- **A movement may START on a `star_grabbed` clause but must never END on one.**
  Ending on a star grab is a run-ordering trap (§5.2). Starting on one is how a
  waypoint-bearing movement that follows a castle star avoids being cancelled by
  it — e.g. `MIPS 2nd → VCUtM` starts on `star_grabbed course=0 star=4`.
- **A waypoint def's start clause must be at least as specific as any waypoint
  clause it could collide with** (spec #1's documented authoring caveat). The SL
  and HMC re-entries hit this exactly — start `level_exit from=10` and
  `waypoints[1]` `level_exit from=10` are the *same clause*. It is safe only
  because `_feed_waypoint` advances `progress` before the major-action check, and
  because `feed`'s arm phase is gated on `not (d.waypoints and d.id in
  self._armed)`. Any new re-entry movement must preserve that property.

### 4.3 Guards, category, enabled

Every movement carries `[{"type": "in_active_route"}]` and
`"category": "Castle Movement"`, and ships `enabled: true`. Enabling is safe
precisely because the guard gates arming: a movement arms only inside the active
route or as the standalone segment target, so 55 new enabled defs cannot
cross-arm during ordinary practice.

The ten pre-existing seeded defs are **referenced by `seed_key`, never
duplicated** — including `seg:bits-entry`, which already *is* the
endless-staircase BLJ (`area_enter level=6 area=2` → `level_enter to=21`), and
`seg:mips-clip`, which already *is* 16★'s HMC → DDD movement. They keep their
current triggers and stay unguarded (unchanged behaviour).

### 4.4 Movement inventory (55 new segments)

`seed_key` convention `seg:<from>-><to>`. `exit N` = `level_exit from=N`;
`enter N` = `level_enter to=N`; `area R` = `area_enter level=6 area=R`;
`star 0:N` = `star_grabbed course=0 star=N`. Empty waypoints = plain def.

| seed_key | Start | Waypoints | End |
|---|---|---|---|
| `seg:castle-entry->bob` | `enter 6 from=16` | — | `enter 9` |
| `seg:bob->wf` | `exit 9` | — | `enter 24` |
| `seg:bob->pss` | `exit 9` | — | `enter 27` |
| `seg:bob->ccm` | `exit 9` | — | `enter 5` |
| `seg:bob->basement` | `exit 9` | — | `area 3` |
| `seg:pss->wf` | `exit 27` | — | `enter 24` |
| `seg:wf->pss` | `exit 24` | — | `enter 27` |
| `seg:wf->ccm` | `exit 24` | — | `enter 5` |
| `seg:wf->sa` | `exit 24` | — | `enter 20` |
| `seg:wf->bitdw` | `exit 24` | — | `enter 17` |
| `seg:wf->ssl` | `exit 24` | `area 3` | `enter 8` |
| `seg:sa->jrb` | `exit 20` | — | `enter 12` |
| `seg:jrb->pss` | `exit 12` | — | `enter 27` |
| `seg:pss->totwc` | `exit 27` | — | `enter 29` |
| `seg:totwc->pss` | `exit 29` | — | `enter 27` |
| `seg:totwc->bitdw` | `exit 29` | — | `enter 17` |
| `seg:pss->bitdw` | `exit 27` | — | `enter 17` |
| `seg:pss->bob` | `exit 27` | — | `enter 9` |
| `seg:ccm->bitdw` | `exit 5` | — | `enter 17` |
| `seg:ccm->bbh` | `exit 5` | `enter 26` | `enter 4` |
| `seg:bowser1->bob` | `exit 30` | — | `enter 9` |
| `seg:bowser1->wf` | `exit 30` | — | `enter 24` |
| `seg:bowser1->ccm` | `exit 30` | — | `enter 5` |
| `seg:bowser1->ssl` | `exit 30` | `area 3` | `enter 8` |
| `seg:bowser1->ddd` — Crackslide (1★) | `exit 30` | `area 3` | `enter 23` |
| `seg:bowser1->bitfs` — SBLJ / DDD Skip (0★) | `exit 30` | `area 3` | `enter 19` |
| `seg:bbh->basement` | `exit 4` | `enter 6` | `area 3` |
| `seg:bbh->ddd` | `exit 4` | `enter 6` | `enter 23` |
| `seg:mips1->ssl` | `star 0:3` | — | `enter 8` |
| `seg:ssl->lll` | `exit 8` | — | `enter 22` |
| `seg:ssl->hmc` | `exit 8` | — | `enter 7` |
| `seg:lll->hmc` | `exit 22` | — | `enter 7` |
| `seg:lll->ddd` | `exit 22` | — | `enter 23` |
| `seg:hmc->lll` | `exit 7` | — | `enter 22` |
| `seg:hmc->ddd` | `exit 7` | — | `enter 23` |
| `seg:hmc->rr` — re-entry, pause-exit → tippy | `exit 7` | `enter 7`, `exit 7` | `enter 15` |
| `seg:mips2->hmc` | `star 0:4` | — | `enter 7` |
| `seg:mips2->vcutm` | `star 0:4` | `enter 16` | `enter 18` |
| `seg:vcutm->ccm` | `exit 18` | `enter 6` | `enter 5` |
| `seg:ddd->bitfs` — via the sub | `exit 23` | — | `enter 19` |
| `seg:ddd->wdw` — BitFS re-entry, pause-exit | `exit 23` | `enter 19`, `exit 19` | `enter 11` |
| `seg:bowser2->ddd` | `exit 33` | — | `enter 23` |
| `seg:bowser2->wdw` | `exit 33` | `area 2` | `enter 11` |
| `seg:bowser2->upstairs` — up for the BLJ | `exit 33` | — | `area 2` |
| `seg:wdw->thi` | `exit 11` | — | `enter 13` |
| `seg:thi->ttm` | `exit 13` | — | `enter 36` |
| `seg:ttm->sl` | `exit 36` | — | `enter 10` |
| `seg:sl->basement` — re-entry, pause-exit | `exit 10` | `enter 10`, `exit 10` | `area 3` |
| `seg:sl->rr` — HMC Early | `exit 10` | — | `enter 15` |
| `seg:sl->wmotr` | `exit 10` | — | `enter 31` |
| `seg:wmotr->ttc` | `exit 31` | — | `enter 14` |
| `seg:rr->ttc` | `exit 15` | — | `enter 14` |
| `seg:ttc->rr` | `exit 14` | — | `enter 15` |
| `seg:rr->bits` | `exit 15` | — | `enter 21` |
| `seg:ttc->bits` | `exit 14` | — | `enter 21` |

Three rows deserve their reasoning spelled out, because each is the *only*
instance of its pattern:

- `seg:bowser2->upstairs` is plain even though it crosses basement → upstairs,
  because its end **is** that region crossing — the end check runs before the
  relocation disarm.
- `seg:sl->basement` and `seg:bbh->basement` end at a region rather than at a
  course because a castle-secret star (MIPS 2nd, MIPS 1st) is grabbed there next,
  and §4.2 forbids ending on a star grab.
- `seg:mips2->hmc` is plain and deliberately spans the HMC Toad grab — a plain def
  treats `star_collected` as transparent, which is exactly the wiki's "go to HMC
  after MIPS, and get the Toad star before entering HMC".

## 5. Section 2 — route step ordering

### 5.1 The constraint

`RunTracker._apply` only ever considers `steps[act["current"]]`. A closed attempt
that matches no candidate of the current step is discarded. **Therefore route
steps must be listed in completion-event order or a run stalls permanently.**
This is a hard authoring contract, not a style preference.

Ordering rules that fall out:

- A movement step goes **immediately before its destination's star block** — it
  completes on `level_enter to=<destination>`, which precedes every star there.
- A castle-secret star grabbed *during* a movement goes **immediately before that
  movement's step** (its grab event precedes the movement's `level_enter`). This
  is also exactly how the wiki reads: "Upstairs Toad (near TTM) | Tall, Tall
  Mountain".
- A Bowser block reads `[seg → BitDW] [star reds] [seg BitDW Pipe] [seg Bowser 1]`
  — `level_enter 17` < the reds grab < `warp_entered 17` < `key_grabbed 30`.

### 5.2 Why a movement must never end on a star grab

Within one event, `Projector._dispatch` builds `closed` as **star attempts first,
then segment attempts** (`projection.py` ~374 vs ~409-430), and `_runs.feed`
consumes that list in order. So on a `star_collected` that both closes a star
attempt and closes a movement ending on that grab:

- route `[… star_step, seg_step …]`, current = `star_step` → star completes,
  current advances, the segment attempt then matches `seg_step`. **Both land.**
- route `[… seg_step, star_step …]`, current = `seg_step` → the star attempt is
  discarded (no match), the segment completes, current advances to `star_step` —
  whose completing attempt is already gone. **Permanent stall.**

Rather than encode a fragile "list it after" rule, §4.2 forbids the shape: a
movement that would end on a castle star ends at the region boundary instead
(`seg:sl->basement`), and the next movement *starts* on the star grab
(`seg:mips2->hmc`, `seg:mips1->ssl`, `seg:mips2->vcutm`).

### 5.3 Steps, groups and labels

- Single star → `{"need": 1, "candidates": [{"type":"star","course":C,"star":S}]}`.
- A star collected **with** the 100-coin star ("Big Penguin Race + 100 Coins") →
  one step, `need: 2`, both candidates, `label` naming the pair. K-of-N is
  order-free, which is right: the 100-coin star pops whenever the coin count
  crosses.
- A documented **either/or** ("Lava Boost OR Elevator Star") → `need: 1` with both
  candidates and a label.
- Movement / trick → `{"need":1,"candidates":[{"type":"segment","seed_key":…}]}`;
  `resolve_steps` rewrites `seed_key` → the local `segment_id` at reconcile.
- Every step carries a `label` prefixed with the stage ("WF — Blast Away the
  Wall") so the star-level lists stay readable at 160 steps.

## 6. Section 3 — castle-secret stars

`memory/addresses.py` gains one additive row:

```python
STAR_NAMES[0] = ("Toad Star (Basement)", "Toad Star (Upstairs)",
                 "Toad Star (Tippy)", "MIPS 1st Star", "MIPS 2nd Star")
```

Consequences, all desirable: `star_count(0)` goes 0 → 5, so `vocab()["stars"]["0"]`
offers them and every star picker, the segment builder's star dropdown, and the
route builder can name and select them; `star_name(0, n)` stops returning
"Star n+1". The `1 <= course_id <= 15` guard in `star_name`/`star_count` keeps the
100-coin rule off course 0 (it has no 100-coin star), which is correct.

Evidence and the VERIFY item live in the sources companion §"Castle-secret stars".
`addresses.py` is on CLAUDE.md's "never edit in two branches at once" list — this
change is purely additive to a name table, and must be flagged at merge.

## 7. Section 4 — route inventory

### 7.1 Main categories (`category: "Main Categories"`, `start_condition: reset_game`)

| `seed_key` | Name |
|---|---|
| `route:16-no-lblj-standard` | 16 Star — No LBLJ (Standard) |
| `route:16-no-lblj-beginner` | 16 Star — No LBLJ (Beginner, no DW Reds) |
| `route:16-no-lblj-wf100c` | 16 Star — No LBLJ + WF 100c (CCM Skip) |
| `route:16-lblj` | 16 Star — LBLJ (Standard) |
| `route:70-hmc-late-beginner` | 70 Star — HMC Late (Beginner) |
| `route:70-hmc-late-intermediate` | 70 Star — HMC Late (Intermediate, TTC100) |
| `route:70-hmc-late-advanced` | 70 Star — HMC Late (Advanced, CCM17) |
| `route:70-hmc-late-expert` | 70 Star — HMC Late (Expert, Island Hop) |
| `route:70-hmc-early` | 70 Star — HMC Early |
| `route:120-non-lblj` | 120 Star — Non-LBLJ |
| `route:120-lblj` | 120 Star — LBLJ |
| `route:1-star` | 1 Star |
| `route:0-star` | 0 Star |

All four full-game categories open with `seg:lakitu-skip` + the castle entry
movement. The 120★ guide omits Lakitu Skip from its written route; including it
is a deliberate, documented deviation (it is the same run opening, and every
other category lists it).

### 7.2 Stage RTA (`category: "Stage RTA"`)

One route per documented per-course list — 37 in all (see the sources companion's
per-course table). Named `"<Course> — <category>"`, e.g. `WDW — 120`,
`WDW — 120 (Beginner)`, `WDW — 70`; `seed_key` `route:stage-wdw-120` etc. Pure
star lists: **no movement steps**, and `start_condition` is
`{"type": "level_enter", "to": <level>}` so the run clock starts when you enter
the stage rather than on F1.

## 8. Section 5 — the generator and the seed

`tools/build_defaults_seed.py`:

- Input: module-level tables — `MOVEMENTS` (seed_key → from/to/shape/waypoint
  spec), `MAIN_ROUTES`, `STAGE_ROUTES` — plus the ten existing seeded defs copied
  forward verbatim.
- `region_of(level)` is **derived from `addresses.WORLD_EDGES_TWO_WAY`** (the
  castle area whose neighbour list contains the level, with a documented one-hop
  fallback through the courtyard/grounds/HMC hubs), so the topology stays
  single-sourced in `addresses.py` and a fix there propagates.
- Output: `src/sm64_events/data/defaults.seed.json` with `seed_version` bumped to
  2, `segments` first then `routes` (reconcile resolves route candidates'
  `seed_key` → local `segment_id` in that order).
- Deterministic: sorted keys, fixed 2-space indent, trailing newline.

`--check` mode re-generates and diffs against the checked-in file, for the test
below and for CI-style use.

## 9. Section 6 — verification

Blind-authored data needs more than "it validates". Three layers, in
`tests/test_defaults_corpus.py` (new):

1. **Structural** — every seeded segment passes `validate_definition` (now
   including `waypoints`); every seeded route passes `validate_route`; every route
   candidate's `seed_key` resolves to a segment in the same seed; no seeded
   movement is unreferenced by any route (orphan guard); `seed_key`s are unique.
2. **Movement simulation** — for each movement segment, synthesize its event
   stream from an **independent world model** (the castle topology + a
   region-to-region walk, derived in the test, *not* read off the def under test):
   `level_changed`, the establishing/real `area_changed` events a real walk emits,
   and the door-echo `practice_reset`s. Feed `SegmentEngine`; assert **exactly one
   success attempt and no other rows**. Then feed a *different* movement's stream
   and assert the def produces nothing.
3. **Route simulation** — for each of the 13 main-category routes, synthesize the
   whole run's event stream (movements and star grabs in the authored order) and
   assert `RunTracker` reaches `status="finished"` with every step completed. This
   is the layer that catches §5's ordering traps, and it is the reason the corpus
   can be trusted without 13 live playthroughs.

Plus a **drift guard**: `tools/build_defaults_seed.py --check` must report no
difference from the checked-in `defaults.seed.json` (mirrors
`tests/test_make_manifest.py`'s round-trip).

Existing gates that must stay green:
`test_seed_reconcile.test_real_bundled_seed_does_not_alter_existing_segment_defs`
(the ten migrated rows must still reconcile to identical triggers) and
`test_segments.test_all_db_seeds_pass_validate_definition`.

## 10. Section 7 — folded-in follow-ups (spec #1's final review)

- **`reconcile_defaults` seed-shape hardening.** Today a malformed row raises
  `KeyError`/`TypeError`; `main.py` catches only `(OSError, ValueError)`, and one
  bad row would cost the entire corpus refresh. Change to per-row
  validate-and-skip: each segment/route row is checked (`validate_definition` /
  `validate_route` plus the required seed keys) inside a `try`, a bad row is
  skipped and collected, and `reconcile_defaults` returns the list of problems for
  the caller to log. Good rows still land.
- **`waypoints` in `test_all_db_seeds_pass_validate_definition`'s projection** —
  the test currently projects only `name/start_triggers/end_triggers/guards`, so a
  malformed seeded waypoint list is invisible to it.
- **Rename `tracking/defaults._resolve_steps` → `resolve_steps`** (it is a
  cross-module concept and is about to be referenced by the generator's tests).
- **Live-gate VERIFY, documented against the new defs**: (a) real-anchor rewind
  vs relocation for a multi-level movement — `seg:sl->basement` and `seg:hmc->rr`
  are the first defs that can exercise it; (b) the Toad↔star-index binding (§6).

## 11. Touched files

`src/sm64_events/data/defaults.seed.json` (regenerated),
`tools/build_defaults_seed.py` (new), `src/sm64_events/memory/addresses.py`
(`STAR_NAMES[0]`), `src/sm64_events/tracking/defaults.py` (hardening + rename).
Tests: `tests/test_defaults_corpus.py` (new), `tests/test_seed_reconcile.py`,
`tests/test_segments.py`, `tests/test_addresses.py`.
Docs: this spec + its sources companion, CLAUDE.md module map, README if the
seed/tooling surface changes, `docs/architecture.md` for the movement grammar and
the run-ordering contract.

**Not touched:** `src/sm64_events/ui/**` (concurrent UI redesign owns it).

## 12. Definition of done

- `uv run pytest -q` green (worktree baseline 1373); §9's three layers plus the
  drift guard all present.
- No new memory reads; the two VERIFY items above are recorded for the live gate.
- CLAUDE.md module map gains rows for `tools/build_defaults_seed.py` and the
  corpus's authoring rules; `docs/architecture.md` records the movement grammar
  (§4) and the run step-ordering contract (§5) with their evidence.
- **Release note to carry forward:** a user-*deleted* default segment or route
  resurrects on the next update — reconcile re-inserts any seed row missing from
  the db. **Disable** is the protected hide path; Delete is not.
- `addresses.py` flagged as a shared-contract touch at merge.
