---
paths:
  - "src/sm64_events/ui/components/celebrate.js"
  - "src/sm64_events/ui/components/marelocelebrate.js"
  - "src/sm64_events/ui/celebrations.js"
  - "src/sm64_events/ui/rankclimb.js"
  - "src/sm64_events/ui/climbplan.js"
  - "src/sm64_events/ui/climbcurve.js"
  - "src/sm64_events/ui/climbtuning.js"
  - "src/sm64_events/ui/marelotuning.js"
  - "src/sm64_events/ui/routeswap.js"
  - "src/sm64_events/ui/tunecontrols.js"
  - "src/sm64_events/ui/tune.js"
  - "src/sm64_events/ui/tune.html"
  - "src/sm64_events/ui/tunemarelo.js"
  - "src/sm64_events/ui/tunemarelo.html"
  - "src/sm64_events/server/tuning_api.py"
  - "tests/test_ui_climb*.py"
  - "tests/test_ui_celebrat*.py"
  - "tests/test_ui_marelotuning.py"
  - "tests/test_tuning_api.py"
---

# Rank-up celebrations and the level-up climb

Full detail, evidence and history: [docs/rules/ui-climb.md](../../docs/rules/ui-climb.md) — read it when changing how a celebration, the climb, the route swap or a tuning inspector behaves, or before undoing one of the rules below.

**Also loaded:** `.claude/rules/ui-core.md` (shell + UI verification norms; a
one-direction snap is a CSS-specificity problem described there) and
`.claude/rules/ui-ranks.md` (icon registry, the banner this climbs on).

**Not here: the PB row's GOLD!! and its one-shot arrival** (task 0147). It is
a practice-log row effect, not a climb beat, and lives in
`.claude/rules/pb-strategy.md`'s last row; its numbers are the "Personal best"
group of `/ui/tunelog.html`, not this inspector. It honours the same
Celebrations preference (`celebrationsEnabled()`) for the arrival only.

**Anything judged by feel:** build the tuning inspector first, let the human
tune it live, codify what he saves (`tuning-demo` skill; example `/ui/tune.html`).

**Every bar value inside a climb plan is a DRAWN WIDTH, never the server's
within-division `fill`.** `rankclimb.js` converts once at the plan boundary
(`targetBar`/`startBar` → `buildClimbPlan`'s `fromFill`/`toFill`) through
`caps.js::barFill`; a sweep's duration reads `|barTo − barFrom|`
(`climbcurve.js::barSweepMs`); the hook hands out `climb.bar`, there is no
`climb.fill`. Pinned by `tests/test_ui_rank_bar.py::test_the_climb_anchors_the_bar_it_hands_out`.

**The "X.XXs to rank up" line may only CHANGE while INVISIBLE, and fades in
EVERY case, including a climb that gains no rank.** `rankclimb.js` publishes
`climb.line` + `climb.atTarget`; `ranks.js` resolves them to a sentence in ONE
place, never a condition per symptom. A no-rank-up step exchanges via
`climbcurve.js::exchangeFade`; `atTarget` is resolved at RETURN time. The fade
rides the bar's progress on its own clock with a floor (`revealMinMs`), and the
loop keeps ticking while the line fades. Pinned by `tests/test_ui_rank_line.py`.

| To change... | Edit |
|---|---|
| Whose turn it is to celebrate (strategy → star → MARELO) | `ui/mareloturnstate.js` is the DECISION (import-free, `tests/test_ui_marelo_turn.py`); `ui/mareloturn.js` is the hook, read by BOTH `header.js` and `marelocelebrate.js`. The overlay must not fly and the header card must not climb early. Never gate the overlay on "is anything climbing" (it loops). `held` = last payload with NOTHING pending; `ready` is LATCHED per key; the 450ms grace is STATE, not a ref. **A celebration already pending when a client arrives is acked without being shown** (`unprompted`, `held == null`), via the celebrations-off ack path |
| Celebration overlays (the SCOPE rank) | `ui/components/marelocelebrate.js`'s `MareloCelebration` — ONE overlay for every overall rank-up. Fly the REAL `RouteRankCard`, parked at the BEFORE rank, header twin hidden (`.marelo-slot.is-celebrating{opacity:0}`). Phases `out→climb→hold→back`: measure in `useLayoutEffect`, then a **load-bearing double rAF** flips `lifted`. This file owns ONLY the flight; the beats are `rankclimb.js` walking `celebrations.js` (`useRankClimb`'s `tune` override exists for this caller only). "back" acks `POST /api/marelo/ack` after `flyBackMs` or on click. A division-up is the same sequence at `divisionScale`. Custom properties live on the OUTER `.marelo-celebrate`, never the inner card; no size `@media`. Verify by frame-sampling (uilab `trace.record()`), never by eye. `RankUpCelebration` stays gated on `celebrationsEnabled()` |
| Rank-up CELEBRATIONS — the level-up climb (banners + MARELO bar) | **Adding or changing a celebration is ONE entry in `ui/celebrations.js`** and nothing else. See [the section below](#rank-up-celebrations--the-level-up-climb-banners--marelo-bar) |
| Tuning how the climb FEELS (and the pattern for any future "feel" work) | `ui/climbtuning.js` is THE registry (one row per tunable, shipped default + label/range/why). `climbcurve.js`/`celebrations.js` take tuning as an ARGUMENT; `rankclimb.js` reads the slot ONCE per climb. `/ui/tune.html` + `ui/tune.js`: real `RankBanner`s, real stylesheet fetched from index.html, never copied. **SAVE writes the repo's defaults**: `POST /api/tuning/climb` → `server/tuning_api.py` (`TUNING_REGISTRIES`) validates against the FILE's own `min`/`max`/`options`, replaces only an existing `value:`, 409s when frozen. Every tunable must be READ; no module may read a missing key (`tests/test_ui_climbtuning.py`). **No test may assert a shipped default** — pin the law against a `REFERENCE` tuning; a choice test flips to the option NOT stored. A floor may never override the ceiling (`climbcurve.js::floored`). Surface clamps in the "What will play" readout, not per-control badges. Known gap: the card chrome is a replica |
| The route SWAP (picking a different route, never an earned rank-up) | `ui/routeswap.js`'s `useRouteSwap`, an EXCHANGE that must never read as an achievement; `useRankClimb`'s `identity` snap is unchanged. Key on `marelo.scope_id`, never `activeRouteId`. Drive all four parts off ONE `progress` (`components/marelo.js`); bar and crossfades use `climbcurve.js::barEase` (never overshoots); the icon calls `CELEBRATIONS.tierAnticipate`/`.tierBurst` `icon` functions directly, drawn rank swapping at `swapExchangeAt`. Texts sit in `.marelo-swap-fade` (grid overlay); use descendant selectors there, not child combinators. Set frame 0 synchronously in the effect. Tunables: `marelotuning.js` "Swap" group; `routeswap.js` is a second reader in `tests/test_ui_marelotuning.py::READERS`; the two readers never read each other's rows |
| The practice-log tuning fixture, when the CARD grows a field | `ui/tunelog.js` hand-builds the server's shapes, so add each new server-resolved field there. Each row STATES its `pb_action` as data, never derived; one foreign-strategy row draws no action and no delta. `tests/test_ui_rank_progress_track_log_card.py` pins COHERENCE, never contents |
| Tuning the OVERALL rank-up (second tuning surface) | `ui/marelotuning.js` — same surface shape as climbtuning.js (`MARELO_TUNABLES`/`MARELO_DEFAULTS`/`MARELO_GROUPS`/`withMareloDefaults`/`mareloTuning`/`setMareloTuning`) so tunecontrols/tuning_api need no special case. The flight bezier exposes ONLY `flyEaseIn`/`flyEaseOut`; **the vertical control points stay pinned at 0 and 1, never tunable** (`test_the_flight_curve_starts_and_ends_at_rest`). `tierAmplify` scales the `AMPLIFIED` list for this surface only. `ui/tunecontrols.js` owns `Control`/`ControlGroups`/`LevelPicker` (`defaults` as a PROP); never import from `tune.js` (its module-level `render()`). `/ui/tunemarelo.html` + `ui/tunemarelo.js`: replica header around the REAL `RouteRankCard`; Play mounts the REAL `MareloCelebration` keyed on a fresh key, bypassing the pref gate — warnings never block Play; SAVE → `POST /api/tuning/marelo`. Reachable from the settings drawer (`tests/test_header_ui.py::test_the_tuning_page_is_reachable_from_the_app_and_from_the_launcher`); launcher line is a follow-up |

## Rank-up CELEBRATIONS — the level-up climb (banners + MARELO bar)

A celebration is ONE `ui/celebrations.js` entry. Modules: `caps.js`
`rankPosition`/`rankAt` (which rank), `climbplan.js` (order), `climbcurve.js`
(speed), `celebrations.js` (look), `rankclimb.js` (`useRankClimb`, the loop).
A climb is a PLAN; level and bar are separate values; the bar never decreases
during a rise, stays full between rank-ups, resets 1 → 0 once entering the
arrival, and **may never overshoot** (`barEase`; `easeOutBack` only for reel and
squash). An `identity` change, a drop and `prefers-reduced-motion` SNAP, but
never cut a climb already heading for the same target (`climbingToRef`). Hold the
SELECTION, never the whole view (`useHeldWhileCelebrating`, 12s ceiling). Icon
motion is continuous numbers, never keyframe classes. Nothing between the icon
and the card may clip. Whether two banners share a measure comes from the SERVER
(`views.py::ranks_share_ladder`), never from comparing grades; banners climb in
turn via `lane`/`order`. Both banners render from the first strategy pick at Capless V (`atFloor`); `no_ladder`/`no_strat` keep their sentinel. Colours cross-fade via `--climb-color`; the next-step label reads `climb.level + 1`. Guards: `tests/test_ui_climb.py`,
`tests/test_ui_climbplan.py`, `tests/test_ui_celebrations.py`, `tests/test_ui_caps.py`,
`tests/test_single_source.py`.
Detail: [docs/rules/ui-climb.md#rank-up-celebrations--the-level-up-climb-banners--marelo-bar](../../docs/rules/ui-climb.md#rank-up-celebrations--the-level-up-climb-banners--marelo-bar).
