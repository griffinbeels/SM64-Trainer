# ATTEMPT TIME THRESHOLDS + LAST-STAR GUARDS — Design Spec

**Date:** 2026-07-23
**Status:** Approved design, pending implementation plan

## 1. Goal

Two related additions to attempt filtering and segment arming:

1. **Time-validity bounds.** Every star and segment gets a minimum (default
   0.5 s) and optional maximum completion time. A "success" outside the bounds
   fundamentally didn't count (e.g. a 2 s WF→Basement row is a detection
   artifact — the segment is impossible under 6 s) and must be excluded from
   stats, success rates, PBs, graphs, and run-step completion — while staying
   visible, greyed out, so a mis-set threshold never silently eats data.
   Bounds are user-adjustable per star (inline in its section) and per segment
   (as guard rows in the definition, visible in the builder).

2. **Last-star guards.** Two new arm-time segment guards: *"Last star grabbed
   was {course} {star}"* and *"Last star attempted was {course} {star}"* — so a
   segment can arm only when the player's recent star history matches (e.g. a
   basement segment that only makes sense right after Watch for Rolling Rocks).

## 2. Prior art

- **In-repo (strongest):** the AFK discard (`PAUSE_DISCARD_FRAMES`), the
  no-op-reset discard (`mario_acted`), and the `cleared` discipline — every
  consumer (stats registry, `routes.route_stats`, timeline, progress, PB
  delta, the UI hidden bucket) already excludes cleared attempts. This feature
  is "cleared, stamped automatically by a range check."
- **Industry:** the pattern is a data-cleaning *range/validity check*.
  [argiopetech/timer](https://github.com/argiopetech/timer) (a stats-focused
  practice timer) saves invalid splits but excludes them from statistical
  analysis — exactly the keep-plus-flag model chosen here.
  [LiveSplit](https://livesplit.org/) has no attempt-validity filtering.

## 3. Decisions (user-confirmed 2026-07-23)

| Question | Decision |
|---|---|
| Fate of out-of-range entries | **Auto-ignore, keep visible** — recorded, flagged, excluded everywhere cleared is; greyed in the hidden bucket with a reason |
| What gets filtered | **Successes only** — a 1 s reset is a legitimate failed attempt; failures never flagged |
| UI placement | **Chip in each section header** (stars and segments), plus the segment builder's guard rows |
| Defaults | **Implicit code default** (min 0.5 s = 15 frames, no max) + stored per-entity overrides only |
| Last-star guard shape | **Two separate guard types**: `last_star_grabbed` and `last_star_attempted` |

## 4. Effective bounds resolution

One resolver, used for both kinds:

- `DEFAULT_MIN_FRAMES = 15` (0.5 s at 30 fps), default max = None. Code
  constants (projection layer), not settings.
- **Stars:** `ui_state` KV `time_filters`, key `"<course>:<star>"` →
  `{"min_frames": int, "max_frames": int|null}`. Key absent → defaults.
  `min_frames: 0` = **no minimum** (successes can't be negative — 0 disables
  the check); `max_frames: null` = no maximum.
- **Segments:** guard rows on the definition — `{"type": "min_time",
  "frames": N}` / `{"type": "max_time", "frames": N}`. Guard absent →
  default. `min_time` with `frames: 0` = no minimum (same rule as stars).
  `frames` is an int (existing guard param validation); range/relation
  validation — min ≥ 0, max ≥ 1, max > min — is enforced by
  `validate_definition`'s cross-check of the resolved bounds (added
  post-review 2026-07-23; the guard-param check alone let a segment ship
  `min_time > max_time` or a negative `max_time` where the star-side
  `set_time_filter` already rejected the equivalent input).
- Times are **frames** in storage and on the wire (the project's primary
  clock, domain rule 7); the UI edits in seconds (×30, rounded).

## 5. Filtering semantics (projection-time auto-ignore)

The single filtering site is `tracking/projection.py` — the SegmentEngine FSM
is untouched (arming, closures, echo shapes all unchanged).

- Applies to `outcome == "success"` only, star AND segment attempts.
- Clock: stars use `igt_frames`, falling back to `rta_frames` when igt is
  None; segments use `rta_frames`. Both None → cannot judge → not flagged.
  (Side effect: same-tick reset-race segment rows with rta 0 get auto-hidden
  by the default 0.5 s min — a known junk-row class.)
- Out of range → the Projector stamps `cleared=True`,
  `cleared_reason="auto: below 6.00s min"` / `"auto: above 90.00s max"`. The
  `auto: ` prefix is the machine marker distinguishing auto from manual
  reasons.
- **Manual precedence:** an attempt id that has EVER appeared in a journaled
  `attempt_cleared`/`attempt_restored` event is exempt from the auto rule —
  its state comes solely from the journal (existing `cleared_ids` semantics).
  So Restore on an auto-ignored row is a per-row exemption; Clear on any row
  keeps working. `cleared_ids()` grows a companion "touched ids" return.
- **Retroactive:** thresholds feed the Projector at construction; any change
  triggers `service._reproject()` (same path segment edits use), re-deriving
  every attempt from the journal with the new bounds.
- Plumbing: `Projector.__init__` gains a `time_filters` dict (the parsed KV);
  segment bounds are derived internally from the defs it already receives
  (helper reads `min_time`/`max_time` guard rows). `replay()`/`project()`
  pass-through param; service supplies `db.get_state("time_filters", {})`.
- Saved PB rows are manual artifacts and are untouched; an auto-ignored
  attempt can't be *newly* saved as PB (the Save button already hides on
  cleared rows), and Undo-PB remains available on an existing row.

## 6. Guard registry changes (`tracking/segments.py`)

- `GuardType` gains `phase: str = "arm"`. The engine's arm-loop evaluates
  only `phase == "arm"` guards. Close-phase guards carry
  `check=lambda p, ctx: True` (never block arming; the projector reads their
  params directly — they are declarative storage + builder UI).
- New rows:

| key | phase | label | params | template |
|---|---|---|---|---|
| `min_time` | close | "Takes at least" | `frames`: kind **seconds**, required | `{frames}` |
| `max_time` | close | "Takes at most" | `frames`: kind **seconds**, required | `{frames}` |
| `last_star_grabbed` | arm | "Last star grabbed was" | `course` required, `star` optional | `{course}, star {star}` |
| `last_star_attempted` | arm | "Last star attempted was" | `course` required, `star` optional | `{course}, star {star}` |

- Param kind `seconds`: stored/validated as int **frames** (≥ 0); the builder's
  `ParamInput` gains one branch rendering a decimal-seconds input
  (`value/30`, stores `round(x*30)`). `course`/`star` kinds already render
  (same dropdowns as the `star_grabbed` trigger — star disabled until a
  course is picked, "(any star)" when empty).
- `vocab()` ships `phase` per guard so the builder can group close-phase
  rows (e.g. under a "Result filters" divider) — display-only.
- Validation (`_check_clause`) is unchanged: int params, required/unknown
  checks apply to the new rows automatically.

## 7. Last-star tracking (`tracking/projection.py` → `MatchContext`)

- The Projector tracks two values, both `(course_id, star_id) | None`:
  - `last_star_grabbed` — updated on every closed **success** star attempt
    (the grabbed star).
  - `last_star_attempted` — updated on every closed star attempt **with
    attribution** (success → the grabbed star; reset/death/abandoned →
    the targeted star). Rows with `course_id None` don't update it; merely
    setting a target does not count.
- Updated from the current event's closures BEFORE the SegmentEngine sees the
  event (closures happen in `_dispatch`, ctx is built after), so a guard
  evaluated on the very event that closed the attempt sees the fresh value.
- `game_reset` clears both to None (the save file can change at the title
  screen — same rationale as the `num_stars` reset).
- Guard match: `course` must equal, `star` optional (None = any star of that
  course). Unknown (None) conservatively **fails**, mirroring
  `star_count_min`; legacy journals rebuild both values naturally on replay
  (they contain the same star/attempt events).

## 8. UI

- **Section chip** (`ui/components/practice.js`): each star/segment section
  header gets a small `⏱ ≥ 0.5s` / `⏱ 6s – ∞` chip showing effective bounds
  (dimmed when default). Click → inline min/max editor in seconds.
  - Inputs prefill with effective values. Blank min = the 0.5 s default;
    typed `0` = no minimum; blank max = no max. A "Reset" affordance drops
    the override entirely (stars: DELETE; segments: remove the guard rows).
    One shared editor component for both kinds.
  - Stars → `PUT /api/stars/{course}/{star}/time-filter`; segments → the
    existing `PUT /api/segments/{id}` with updated `guards` (builder path
    reused; the chip editor rewrites just the `min_time`/`max_time` rows).
- **Sections carry the data**: `views.py` adds `"time_filter":
  {"min_frames", "max_frames", "is_default"}` to star and segment sections
  (effective values after defaults) so the chip renders without a second
  fetch.
- Auto-ignored rows land in the existing hidden bucket (cleared || abandoned)
  with their `cleared_reason` badge; Restore appears as on manually-cleared
  rows and acts as the per-row exemption (§5).
- Segment builder (`ui/components/segments.js`): the new guards appear via
  vocab; only additions are the `seconds` param branch and the optional
  close-phase grouping divider.

## 9. API (`server/api.py`)

- `PUT /api/stars/{course}/{star}/time-filter` body
  `{"min_frames": int, "max_frames": int|null}` — RMW on the
  `time_filters` KV via the service (mirrors the strategies/markers
  pattern), then `_reproject()`.
- `DELETE /api/stars/{course}/{star}/time-filter` — removes the override
  key (revert to defaults), then `_reproject()`.
- Validation: ints ≥ 0, `max > min` when both set (ValueError taxonomy →
  400/409 per api.py's mapping).
- Segment bounds ride the existing segment-update endpoint (validation +
  reproject already there).
- No new read endpoint: effective bounds ship in the session view (§8).

## 10. Run engine fix (`tracking/runs.py`)

Step completion currently accepts any success; it now skips
`a.cleared` successes — a bogus out-of-range success (or a manually cleared
one) no longer advances a run step. Consistent rule: **cleared attempts don't
advance runs.**

## 11. Testing contracts (tests mirror modules)

- `test_projection.py`: below-min/above-max star success flagged (igt clock;
  rta fallback when igt None); segment success judged on rta from def guards;
  default 0.5 s applies with no override; `min_frames: 0` disables the min;
  failures never flagged; manual clear/restore exempts an id from the auto
  rule; replay with changed filters re-flags retroactively; reset-race
  segment row (rta 0) auto-hidden; last-star tracking (grab vs attributed
  failure vs unattributed row vs game_reset clear).
- `test_segments.py`: validation + vocab for the four new guards (phase
  shipped); arm-loop skips close-phase guards; `last_star_grabbed`/
  `last_star_attempted` gate arming (course-only = any star; None fails
  conservatively).
- `test_runs.py`: a cleared success does not complete a run step.
- `test_api.py`: time-filter PUT (validation, KV write, reproject side
  effect, revert-to-default deletes the key).
- `test_views.py`: sections carry `time_filter` with `is_default`.

## 12. Out of scope

- Filtering failures (a separate failure floor) — revisit if reset-spam rows
  that survive the existing no-op discard actually appear.
- A user-editable global default; per-strategy thresholds.
- Converting out-of-range successes into failures (they are excluded from
  both numerator and denominator).
- FSM changes (e.g. staying armed through a bogus end trigger) — the engine
  closes/disarms exactly as today; only the row's flag changes.
