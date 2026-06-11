# Practice Tracker Platform — Design

Date: 2026-06-10
Status: approved in brainstorming; feeds the implementation plan.

## 1. Goal

Grow sm64_tracker from an event broadcaster into a practice-tracking
platform: detect more in-game events (dust/rollouts, deaths, doors,
practice resets), group them into **attempts** with two clocks, persist
everything in a local SQLite database, compute per-star statistics and
personal bests, and estimate route success odds — all rendered in a richer
built-in UI. The architecture must make every future stat, failure reason,
trigger, and link a **data/registry addition, not a code restructure**.

User-confirmed decisions (brainstorm 2026-06-10):

- Retry loop: Usamune level resets + Usamune section states (not PJ64
  savestates). These are the attempt anchors.
- Stat identity: `(course, star, strat_tag)` — strat tag is user-set text.
- Practice target follows the **last valid star grab**; marking a grab as
  a mistake tombstones it and retroactively re-attributes subsequent
  failures to the previous valid target.
- Session = server run, plus a manual "New Session" button. Sessions are
  grouping only; all data persists forever.
- Opening the Usamune menu for more than a buffer (~2 s, configurable)
  fails the open attempt (`outcome=menu`). Needs a menu-open address
  (to be hunted; everything else works without it).
- UI: zero-build, served by FastAPI, vendored Preact+htm (no node, no
  build step). Tabbed layout: **Practice | Routes | Live feed**.
- Per-attempt rollout display is a rate ("3/5 dustless"), never a single
  checkmark — one attempt can contain many rollouts.
- Persistence approach: **Option A — append-only event journal + derived
  views** (chosen over direct tables and a separate stats service).

## 2. Architecture

Five layers; each consumes only the layer above it. Layers 1–2 already
exist; this design extends 1 and adds 3–5.

```
1 DETECTION   memory/* + detectors/*  (extended: 4 new detectors, 5 new snapshot fields)
      │ events
2 EVENT BUS   core/events.py + server/broadcaster.py  (unchanged envelope, new types)
      │ events                          │
3 TRACKING    tracking/*  — attempt state machine, target attribution ──▶ emits derived
      │ attempts                          events (attempt_completed…) back onto the bus
4 STORAGE+STATS  storage/* (SQLite journal + derived tables) · stats/* (registry) · routes
      │ queries/commands
5 UI + API    server/app.py + server/api.py + ui/*  (REST commands/queries + same WS feed)
```

Single process. External consumers keep using `ws://…/ws/events`
unchanged; derived tracking events appear on the same feed.

## 3. Detection layer (extends existing)

### New snapshot fields (`core/snapshot.py`, defaulted; addresses VERIFY-gated)

| Field | Source | N64 address | Feeds |
|---|---|---|---|
| `particle_flags` | u32 Mario struct +0x08 | 0x8033B178 | rollout corroboration |
| `health` | s16 Mario +0xAE | 0x8033B21E | death corroboration |
| `num_lives` | s8 Mario +0xAD | 0x8033B21D | death corroboration |
| `curr_level` | s16 gCurrLevelNum (already in registry) | 0x8032DDF8 | level context |
| `mario_pos` | 3× f32 Mario +0x3C | 0x8033B1AC | door disambiguation |

Decomp-verified facts (cross-checked 2026-06-10 against n64decomp/sm64):
`PARTICLE_DUST = 1<<0`; `ACT_DIVE 0x0188088A`, `ACT_DIVE_SLIDE
0x00880456`, `ACT_FORWARD_ROLLOUT 0x010008A6`, `ACT_BACKWARD_ROLLOUT
0x010008AD`; death actions `ACT_STANDING_DEATH 0x00021311`,
`ACT_QUICKSAND_DEATH 0x00021312`, `ACT_ELECTROCUTION 0x00021313`,
`ACT_SUFFOCATION 0x00021314`, `ACT_DEATH_ON_STOMACH 0x00021315`,
`ACT_DEATH_ON_BACK 0x00021316`, `ACT_EATEN_BY_BUBBA 0x00021317`,
`ACT_DROWNING 0x300032C4`, `ACT_WATER_DEATH 0x300032C7`; door actions
`ACT_UNLOCKING_KEY_DOOR 0x0000132E`, `ACT_ENTERING_STAR_DOOR 0x00001331`.
All go into `memory/addresses.py` with source comments, marked VERIFY
until the live gate passes.

### New detectors (one file each, existing `process(prev, curr)` Protocol)

- **RolloutDetector** → `rollout {dustless, frames_late, level}`.
  Intra-frame action chaining (decomp `execute_mario_action` loops until
  stable) means a frame-perfect rollout shows as a direct
  `ACT_DIVE → ACT_*_ROLLOUT` edge with **no visible ACT_DIVE_SLIDE
  frame** — that absence IS the dustless signal. A late rollout shows N
  `ACT_DIVE_SLIDE` frames first: `frames_late = N` (each such frame sets
  PARTICLE_DUST; `particle_flags` is the corroborating read).
  Known suppressors (no event, by design): steep-slope INPUT_ABOVE_SLIDE,
  fall-damage knockback diversion.

  > **CORRECTED 2026-06-11 — the timing model above is wrong; do not
  > build from it.** Landing transitions run `set_mario_action(...);
  > break;` (no same-frame re-execution), so a direct
  > `ACT_DIVE → ACT_*_ROLLOUT` edge is impossible: every rollout shows
  > ≥ 1 visible dive-slide frame, and exactly ONE visible frame is the
  > frame-perfect (dustless) input. Disproven by a 50-trial live session,
  > confirmed against the decomp (evidence quoted in
  > `src/sm64_events/memory/addresses.py`). Shipped as the generalized
  > `detectors/dust.py` (TRICKS registry: rollouts + chained double/triple
  > jumps, `frames_late = visible_landing_frames - 1`).
- **DeathDetector** → `death {cause, level}` on edge into the death-action
  set; cause derived from which action (the failure-reason vocabulary).
  Health/lives are corroboration only, not the primary signal.
- **TriggerDetector** → `trigger {name, level}` — fully registry-driven:
  `TRIGGERS` rows of `{name, action_id, level?, position_box?}`. Ships
  with star-door and key-door rows; any new trigger is one data row.
- **AnchorDetector** → `practice_reset {}` (igt_overall decreased to
  near zero — below ~1 s of frames — while global_timer stayed
  continuous) and `state_loaded {igt_frames_restored}` (global_timer
  jumped backward). Needs **no new addresses**. Existing GameResetDetector keeps `game_reset`.
- **LevelChangeDetector** → `level_changed {from, to}` (curr_level edge).
- **MenuDetector** (deferred until address found) → `menu_opened` /
  `menu_closed`.

### Known limitations (documented, accepted)

- Usamune "non-stop" star option suppresses star-dance actions and blinds
  star detection. Documented; a code-patch probe (autosplitter technique:
  read the interaction-handler word) can be added later if needed.
- Savestate-during-star-dance re-emission limitation carries over from
  README as-is.

## 4. Tracking layer (new: `tracking/`)

**Attempt = anchor → outcome.** State machine over the event stream:

- IDLE → OPEN on `practice_reset` or `state_loaded`.
- OPEN → closed by first of:
  - `star_collected` → `outcome=success` (if grabbed star ≠ current
    target: still success, target switches to it; a later "mark as
    mistake" tombstones the attempt and reverts the target)
  - `death` → `outcome=death`, detail = cause
  - new anchor → `outcome=reset` (and a new attempt opens — retry
    spam becomes a clean attempt chain)
  - `game_reset` → `outcome=hard_reset`
  - menu open past buffer → `outcome=menu` (once MenuDetector exists)
  - `level_changed` away from the target's level with no other outcome
    → `outcome=abandoned` (excluded from rates by default)
- Both clocks recorded per attempt: `igt_frames` (Usamune IGT at close)
  and `rta_frames` (global_timer delta anchor→outcome, back-computed to
  the touch frame like star_grab does). Timer mode is a display choice.
- Sub-events occurring while OPEN attach to the attempt (rollout counts:
  `rollouts_total`, `rollouts_dustless`).
- **Target attribution is derived, not stamped**: current target =
  last non-tombstoned `star_collected` (or manual override / strat-tag
  setting via API). Tombstoning a grab re-derives attribution of every
  later attempt. Strat tag changes apply from that moment forward.
- Self-healing per existing domain rule: state resets when global_timer
  jumps backward in ways that don't classify as known anchors.

Tracking emits derived events onto the same bus + journal:
`attempt_completed {attempt_id, course, star, strat_tag, anchor_type,
outcome, outcome_detail, igt_frames, rta_frames, rollouts_total,
rollouts_dustless}`, `pb_saved`, `session_started`, `target_changed`.

## 5. Storage layer (new: `storage/`)

SQLite, single file (default `data/tracker.db`, gitignored), WAL mode,
migrations via `PRAGMA user_version`. In-process; per-event inserts are
tiny and off the implausible-read hot path. Detector/tracking exceptions
are isolated per-component so the poll loop never dies.

| Table | Content |
|---|---|
| `events` | journal: id, session_id, seq, type, frame, wall_time_utc, payload JSON — **append-only, never deleted** |
| `attempts` | derived, materialized: target identity, anchor, outcome(+detail), both clocks, rollup counts, `cleared_at`/`cleared_reason` tombstone |
| `sessions` | id, started/ended UTC, label |
| `pbs` | every "Save as PB" click: target identity, timer_mode, frames, attempt ref, saved UTC. Current PB = latest row per (target, mode); history retained |
| `routes` | id, name, segments JSON (ordered refs to targets/triggers) |
| `ui_state` | key-value JSON: stat-menu selection, manual target override, failure-outcome config |

A maintenance command rebuilds `attempts` from `events` (the projection is
deterministic), used after attribution-affecting edits and as the
correctness oracle in tests.

## 6. Stats + routes

**Stats registry** (`stats/registry.py`) — one authoritative table; each
stat = `{key, label, params_schema, compute(attempts|events, params)}`.
Ships with: `avg_last_n` (N=10/25/50…), `avg_lifetime`, `best`, `worst`,
`success_rate` (param: which outcomes count as failures — the #11
extension knob), `dustless_rate` (rollout events in scope), `pb_delta`.
Adding a stat = one registry entry; the UI stat menu renders generically
from registry metadata. Scopes: session / star / star+strat / lifetime.

**Routes** — ordered segments referencing targets. Per-segment
`p = success_rate(last 50 attempts, Laplace-smoothed (s+1)/(n+2))`;
route board shows per-segment p and cumulative survival `∏ p`. The
estimator is itself a registry stat (swappable).

**Links registry** (`links.py`, sibling of addresses.py name tables):
per (course, star): auto-generated Ukikipedia RTA-guide URL
(`https://ukikipedia.net/wiki/RTA_Guide/<Star_Name>`, spaces→underscores;
100-coin stars use course abbreviation form e.g. `WF_100_Coins`) + an
optional manual-override URL (e.g. star spreadsheet deep link
`…/edit?gid=<gid>#gid=<gid>&range=<cell>`; gid/range harvest is a content
task, not code). Ukikipedia blocks bot fetches (403) — links are emitted
for the user's browser, never validated server-side.

## 7. API surface

WS `/ws/events` unchanged (new event types are additive; envelope v1).
New REST under `/api`:

- `GET /api/session` — current session: per-star sections with times,
  stats (per the stat-menu selection), PB compare
- `POST /api/session/new`
- `POST /api/target` `{course, star, strat_tag}` (manual override +
  strat-tag changes)
- `POST /api/attempts/{id}/clear` `{reason}`; `POST
  /api/attempts/{id}/restore` (undo)
- `POST /api/pb` `{attempt_id, timer_mode}`; `GET /api/pb`
- `GET /api/stats/registry`; `GET/PUT /api/statmenu`
- `GET/POST /api/routes`; `GET /api/routes/{id}/board`
- `GET /api/links/{course}/{star}`
- `/health` extended with db + session info

## 8. UI (`ui/`)

Zero-build: FastAPI serves `ui/index.html` + ES modules + vendored
Preact/htm (`ui/vendor/`, committed, offline-safe). Edit + refresh stays.

Tabbed layout (chosen): **Practice | Routes | Live feed**, header bar with
attach status, session controls, declared target + strat tag, clock toggle
(IGT / anchor→grab), stat-menu gear.

Star section (repeated per unique star seen this session): header (name,
strat tag, links, current PB) · recent-times table (outcome with cause,
PB delta, per-attempt rollout rate "3/5 dustless", Save-as-PB on
successes, clear [×] with undo) · stat chips from the stat-menu selection.

UI consumes `GET /api/session` for initial state + `/ws/events` for live
updates (attempt_completed et al.), same as any external consumer.

## 9. Error handling

- Poll loop: per-detector and tracking exception isolation (log + skip);
  implausible-read refusal untouched.
- DB unavailable/corrupt at startup: server runs in broadcast-only mode,
  `/health` reports `db: error`; never blocks event broadcasting.
- Journal write failure: log loudly, keep broadcasting (events are also
  in the live feed; the journal is best-effort durable, not a gate).
- Attempt machine: unknown/contradictory sequences self-heal to IDLE and
  log the journal ids involved.

## 10. Testing

Existing patterns extend; no new test infrastructure:

- Detectors: synthetic `snap(**overrides)` sequences; regression tests
  carry live-trace numbers (e.g. dive→rollout chains, death actions).
- Tracking: synthetic event sequences → expected attempts; the journal
  rebuild command doubles as a property check (project(journal) ==
  materialized attempts).
- Storage: tmp-file SQLite per test; migration round-trips.
- Stats: pure-function tables of attempts → expected numbers.
- API: TestClient with OfflineMemory stub + seeded db.
- Live gate: `tools/verify_addresses.py` extended with the new fields;
  new addresses pass it with the human before un-VERIFYing.
- UI: frontend smoke via Chrome DevTools MCP after each UI change.

## 11. Build order (each phase is mergeable and useful alone)

1. **Tracking core** — AnchorDetector (no new addresses), tracking/,
   storage/ (journal + attempts + sessions + pbs), stats registry
   (avg/best/worst/success_rate/pb_delta), REST API, UI: Practice tab
   with star sections, clear/undo, Save-as-PB, stat chips, links
   registry. Delivers features #3, #4, #6, #9, #11(reset failures).
2. **New detectors** — snapshot fields + RolloutDetector + DeathDetector
   (+ LevelChangeDetector); live VERIFY session. Delivers #2 and full
   #11; dustless_rate stat turns on.
3. **Triggers + menu** — TriggerDetector with door rows (needs
   mario_pos + door position boxes), menu-open address hunt +
   MenuDetector + menu failure reason. Delivers #5, menu refinement.
4. **Routes** — routes storage + probability board + Routes tab.
   Delivers #10.

Each phase: tests first, full suite green, live gate for any new memory
reads, docs (module map / README / architecture.md) updated per the
definition of done.

## 12. Out of scope / deferred

- Non-stop-mode patch probe (documented limitation until needed).
- USS spreadsheet gid/range harvesting (content task; registry has the
  override slot ready).
- PJ64-savestate-anchored attempts (user doesn't use them; AnchorDetector
  classification covers section states already).
- Multi-user / cloud anything. Timestamps stay UTC; frames stay the
  primary clock.
