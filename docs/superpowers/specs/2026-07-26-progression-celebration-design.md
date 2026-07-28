# Progression Celebration — design

Task `.tasks/open/0012-progression-celebration.md`, 2026-07-26.

> **§2's motion model is SUPERSEDED** by
> `2026-07-27-multi-rank-climb-design.md`. The trapezoid, `climbDuration` /
> `climbPosition` / `climbTravelled`, and "the bar is the fractional part of one
> monotone position" were all replaced: a climb is an ordered PLAN of steps, the
> bar is its own value pinned full across every rank-up, and no sweep is longer
> than one division. Everything else here still holds. Read that spec first.

## The problem

`ui/components/ranks.js`'s `RankBanner` animates the server's `fill` field
directly (`useTween(rawFillPct)`). `fill` is progress **within the current
division**, so a rank-up sends `{Gold IV, fill .95}` → `{Gold III, fill .05}`
and the bar tweens **95% → 5%: backwards**, while the tier name and the cap
swap in one frame with no transition at all. `ui/components/marelo.js` has the
identical bug on `division_progress`.

The user's words: *"when we level up, the progress bar animates… downwards? it
feels like a level DOWN when we level up right now."*

## What it should do instead

> "the progress bar should fill up from your previous rank all the way to the
> end of the progress bar, like we're literally leveling up. Then, when it
> reaches the end of the progress bar, there should be a celebratory shift from
> the current rank to the new rank… and then the progress bar should continue
> animating towards however much progress you made."
>
> "what if you rank up MULTIPLE SUBDIVISIONS AT ONCE? MULTIPLE DIVISIONS AT
> ONCE? it should start from a slow crawl, easy ease into the pace, and then
> reach a max progression speed, and show celebrations for each of the levels…
> and then it eventually easy ease and slow down to the final progression bar
> position based on your actual time in the final division you land on."

Plus: wings that grow out of the cap and flap; the division digit ticking in
like a slot machine; a colour tween when a boundary changes the colour.

## Decisions taken with the user (2026-07-26)

1. **Surfaces**: the two `RankBanner`s on the active-target card **and** the
   header `MareloBar`. All three are tier + division + a within-division bar
   and all three have the backwards-bar bug. The Rank tab's band ladder is a
   whole-ladder visualisation, not a within-division bar, and stays out.
2. **The climb replaces today's card celebrations** — `EntityCelebration`'s
   glow pop and tier-up toast both go. One effect on one 122px card.
3. **Live only, no replay.** A rank-up earned while the card is not on screen
   does not queue up to climb later. This is what makes both banners use ONE
   identical client-side mechanism with no server change and no asymmetry
   between the strategy rank and the entity rank.
4. **The system must stay easy to extend** (user, mid-design): adding a new
   celebration, or iterating on an existing one, must be a single registry
   entry — not an edit to the animation engine or to any component.

## Architecture

### 1. One continuous coordinate

Stop animating `fill`. Animate **ladder position**:

```
position(tier, division, fill) = progression_key(tier, division) + fill
```

a monotone real number where `1.0` = one division and Iron V = 0 — the exact
ordering `ranks/scoring.py::progression_key` already defines for the
celebration watermark. Everything the surfaces draw is derived from it, per
frame:

| Rendered thing | Derivation |
|---|---|
| bar fill | `position − floor(position)` |
| tier / division | `rankAt(floor(position))` |
| a level-up event | `floor(position)` incrementing |

The backwards bar is fixed **by the coordinate change**, not by a special
case: within one division the bar runs 0→100%, resets to 0 and runs again, so
it is incapable of decreasing during a rise. The same code path serves a 0.3
division nudge and a 12 division jump.

`rankPosition(tier, division)` and `rankAt(position)` go in
`ui/components/caps.js` — THE tier registry, deliberately import-free so
`tests/test_ui_caps.py`'s existing `run_node` harness can execute them and pin
them against Python's `progression_key` over all 45 tier×division pairs.
`DIVISION_NUMERALS` is exported from the same file (today it is implicit in
`DIGITS`' key order) and pinned against `scoring.DIVISION_NUMERALS`.

### 2. Motion — trapezoidal velocity, measured in divisions per second

Distance in divisions drives a genuine accelerate → cruise → decelerate
profile. Not an ease-in-out: over a long climb that makes the middle
arbitrarily fast, and the user asked specifically for a *max* progression
speed.

- ramp in over `RAMP` = 1.0 division, cruise at `CRUISE` ≈ 2.2 divisions/s,
  ramp out over 1.0 division;
- distance ≤ 2·`RAMP` degenerates to a single ease-in-out over the whole
  distance, which is what makes the common one-division case read as "slow
  crawl off the old fill, then settle";
- total duration hard-capped (`MAX_MS`) by raising cruise speed, so no climb
  can hold the UI hostage.

| Climb | Target duration |
|---|---|
| fill improves, no rank change | ~0.7s (today's feel, unchanged) |
| 1 division | ~1.5s |
| 1 full tier (5 divisions) | ~3.2s |
| 12 divisions | ~6.3s, capped at 7s |

Because the deceleration ramp is a whole division long, it begins **before**
the final boundary — the bar is already slowing as it crosses into the
division you landed in and settles onto your real fill. That is the user's
"eventually easy ease and slow down to the final progression bar position".

These are targets, not measured values; the implementation picks the exact
curve and the numbers are verified by sampling the real animation.

### 3. Beats — what the engine emits

The engine converts one position change into a timeline of **beats**:

```js
{ kind: "start" | "division" | "tier" | "settle",
  level,                 // floor(position) after this beat
  tier, division,        // the rank this beat lands on
  fromTier, fromDivision,
  wingsGained,           // wingTiers(to) − wingTiers(from), ≥ 0
  tiersGained,           // total tiers in this whole climb
  divisionsGained }      // total divisions in this whole climb
```

Beats are the extension point. The engine knows nothing about wings, digits,
flashes or colours — it only knows when a boundary was crossed and what was on
each side of it.

### 4. `ui/celebrations.js` — THE registry (requirement 4)

One entry per visual effect. An entry declares **when** it fires, **how long**
it lasts, and **what it contributes** — a class name, CSS custom properties,
or props for the rank icon:

```js
export const CELEBRATIONS = {
  levelFlash: { on: "division", ms: 260, className: "climb-flash" },
  wingGrow:   { on: "division", ms: 450, icon: (beat) => ({ growWings: beat.wingsGained }) },
  wingFlap:   { on: "division", ms: 1100, delay: 450, icon: { flapOnce: true } },
  digitRoll:  { on: ["division", "tier"], ms: 320, icon: (beat) => ({ roll: beat.division }) },
  wingFold:   { on: "tier", ms: 320, icon: (beat) => ({ foldWings: wingTiers(beat.fromTier, beat.fromDivision) }) },
  tierFlip:   { on: "tier", ms: 600, delay: 320, className: "climb-flip" },
  tierColor:  { on: "tier", ms: 600, vars: (beat, progress) => ({ "--tier": mixTier(beat, progress) }) },
};
```

The engine merges every currently-active entry into one render state:
`{ className, vars, icon }`. Adding a celebration — confetti on a three-tier
jump, a sound, a screen shake — is **one entry plus its keyframe**, with no
edit to the engine, the hook, or any of the three call sites. `when(beat)` is
available on an entry for intensity gating (`beat.tiersGained >= 2`).

This mirrors the registries this codebase already runs on: `ICON_STYLES`
(rankicon.js), `MARKERS` (timeline.js), `CAP` (caps.js).

`prefers-reduced-motion: reduce` drops every entry and snaps to the end state,
the contract `useTween` already honours.

### 5. Module boundaries

| Module | Job | Imports |
|---|---|---|
| `ui/components/caps.js` | + `rankPosition` / `rankAt` / `DIVISION_NUMERALS` | none (stays node-testable) |
| `ui/climbcurve.js` **(new)** | pure motion math: `climbDuration(distance)`, `climbPosition(from, to, elapsedMs)`, `beatsBetween(from, to)` | none (node-testable) |
| `ui/celebrations.js` **(new)** | the registry above + `activeEffects(beats, elapsedMs)` | caps.js |
| `ui/rankclimb.js` **(new)** | `useRankClimb({tier, division, fill}, identity)` — the rAF loop | preact/hooks, the three above |

`useRankClimb` returns `{ tier, division, fill, color, className, vars, icon }`.
`icon` is a **ready-assembled prop bundle** for `RankIcon`, spread at the call
site (`<${RankIcon} ...${climb.icon} size=${24} />`). No call site ever builds
icon props itself — the rule this repo learned the hard way when three
surfaces each assembled an icon context their own way.

### 6. Identity gating — what stops a false celebration

`identity` is a string built by the call site from *entity key + which banner +
rank mode + active strategy*. When it changes, the hook **snaps** with no
climb. This is what stops a bogus level-up when the user switches strategy in
the dropdown, changes rank mode in the header, or picks a different target —
all of which legitimately replace the banner's numbers.

A rise on unchanged identity climbs. A drop snaps (no celebration; the app
never rubs a regression in). No previous value (first render after load)
snaps.

A new, higher target arriving mid-climb **retargets** from the current
position rather than restarting — the same contract `useTween` already
documents.

### 7. Icon work (`ui/components/hat.js`)

- `growWings: n` — the last `n` wing pairs render with a `wing-new` class;
  keyframe scales and rotates them out from behind the cap, cap-side
  transform-origin, so they read as *growing out of the hat*.
- `flapOnce: true` — the existing `.hat-flap` keyframes with
  `animation-iteration-count: 1`.
- `roll: division` — the glyph becomes a vertical reel of `M 1 2 3 4 5`,
  translated to the target digit with an ease-out-back settle. **Masked by
  the patch sprite** (`patch.png`, the same mask technique `.fill` already
  uses) so a digit in transit can never paint outside the dome-shaped sign
  field.
- Existing `foldWings` and `flap` are untouched — the scope overlay still uses
  them.

### 8. Colour

Colour in this system is per **tier** (`caps.js::CAP`), so the only colour
change is at a tier boundary. Flat surfaces (the progress bar, the banner's
`--tier` wash) tween with `color-mix(in srgb, …)` — already used in this
codebase. The **cap itself cannot lerp**: Capless is a dashed outline, Metal
carries a highlight layer, Toad and Toadsworth carry spots. So the cap
**flips** (the existing `rankup-flip` rotateY) while the flat colours tween.
Named here rather than left to look like an oversight.

## What is deleted

Decisions 2 and 3 leave the entity-celebration machinery with no consumer:

- UI: `EntityCelebration` and `entityCelebrationFor` (celebrate.js), their CSS
  (`.rank-slot-pop`, `.entity-rankup-toast`, `entity-rank-pop`,
  `entity-toast-in`), the two wrappers in practice.js, and
  `store.clearEntityCelebration`.
- Server: `_entity_celebrations` and `_entity_scores`' celebration role
  (ranks_api.py), `TrackerService.sync_and_seed_entity_watermarks` /
  `entity_watermarks` / `ack_entity_celebration`, the `entity_rank_watermarks`
  KV, and the `{entity, key}` arm of `POST /api/marelo/ack`.

That server path currently does a KV read plus a write on **every**
`/api/marelo` request, for a payload nothing would render.

`RankUpOverlay` (the scope-level MARELO tier-up, `celebration` — *not*
`entity_celebrations`) is untouched, along with `sync_watermark`,
`seed_watermark`, `ack_celebration` and the `{scope, key}` arm of the ack
endpoint.

## Known deviation, stated rather than hidden

The full-screen `TierRankUp` / `DivisionRankUp` overlay keeps its own
hand-rolled fill→flip→hold machine instead of running on the climb engine. It
is a different surface with a different job (a full-screen takeover for the
aggregate MARELO scope, not an in-place bar). Folding it onto the registry is
a clean follow-up and is **not** in this branch.

## Testing

Unit tests structurally cannot catch this class of bug: both end states
already look right, which is how a snap survives end-state-only review
indefinitely.

1. **Node** (`tests/test_ui_caps.py`'s `run_node`): `rankPosition` ↔ Python
   `progression_key` over all 45 pairs; `DIVISION_NUMERALS` ↔
   `scoring.DIVISION_NUMERALS`; `rankAt(rankPosition(t, d)) == (t, d)`
   round-trip.
2. **Node** (`ui/climbcurve.js`, import-free): position is monotone
   non-decreasing across the whole climb at 1ms granularity; `fill` never
   decreases *within* a level; duration targets hold for 0.3 / 1 / 5 / 12
   divisions; the cap holds at 12+.
3. **Registry guard**: every `CELEBRATIONS` entry's `className` exists as a
   CSS rule in index.html, and every `icon` key is a prop `Hat` actually
   reads — so a new entry that silently does nothing fails the build.
4. **Render, mid-transition.** Restore a db snapshot into the scratchpad, run
   the real app on a scratch port, drive a rank-up through CDP and sample bar
   width, cap digit, wing count and colour **every frame**. The assertions
   that matter are on the trace, not the end state: bar width never decreases
   during a rise, the digit changes exactly at each boundary, and a
   mid-transition frame differs from both end states.
5. **Parity**: `tests/test_ui_section_parity.py` must still pass — the star
   and segment cards render the same banner through the same hook.

## Round 2 — five live reports (2026-07-27)

All five verified by frame-by-frame render trace, not by end-state screenshot.

1. **Wing tips were being cut off.** `.rank-banner-row` (the icon's direct
   parent) and `.rank-slot` both carried `overflow: hidden`. `.hat` paints its
   wings ~0.24x the icon size ABOVE the cap by design, so any clipping
   ancestor decapitates them. Both clips are gone; the two elements that can
   actually overflow (`.rank-banner-name`, `.rank-banner-next`) ellipsise
   themselves instead — a container clip cannot tell a wing from a word. The
   icon now paints 9px above the banner box with no reflow (card height and
   page width byte-identical across the whole trace).

2. **The wash must cross-fade, not disappear.** Round 1 hid it during a climb
   because it painted the DESTINATION tier — a Luigi-green wash under a
   Wario-gold cap. The user rejected hiding outright: "all the colors should
   animate from the original coloring to the new coloring." The wash moved
   from `.rank-slot-wrap::before` down onto `.rank-banner::before`, where
   `--climb-color` already lives, so the wash, the bar and the cap now read
   one value and cannot disagree. The `--rank-wash-split: 50%` constant and
   `rankWashStyle` are gone with it — the boundary between two banners IS the
   DOM boundary between them, at every width and in the stacked layout. So is
   `.rank-slot-wrap`, whose only two reasons (the deleted toast, the wash)
   both went away.

3. **A first-ever rank must climb the whole way.** Falls out of 4.

4. **Capless V is the default rank**, shown through the normal UI: a strategy
   with a ladder but no time yet renders Capless 5 with an empty bar and
   "→ Capless 4" instead of a sentinel sentence. That is what gives the first
   rank you ever earn a position to climb FROM — verified end to end: a star
   with no attempts reads CAPLESS 5, and one fast time climbs all 44 levels to
   Mario 1. `no_ladder` (no standards at all) and `no_strat` keep their
   sentinel; the second is the user's own call ("you must select a strat to
   see a rank for the strat").

5. **A tier crossing is now an event, not a transition.** The climb STOPS at
   every tier boundary (`climbcurve.js::tierDwell`): anticipation while the
   bar sits full on the last subdivision of the old tier — the cap shaking
   harder and faster while squashing toward a flat line — then the crossing,
   then a beat to look at the new cap. The release is a burst out of the flat
   line with an overshoot, plus four-point sparkles thrown out in sequence,
   with the colours turning over across it. This replaced the edge-on cap
   flip, which was a transition where an event was wanted.

   The pause is BEFORE the boundary, not after: that is where anticipation
   belongs, and it also hides the rank swap inside the flattest frame. Dwells
   share a budget (1.6s for a single crossing, floored at 700ms when a climb
   crosses many) so the once-ever full-ladder climb stays watchable rather
   than costing thirteen seconds of dwell on top of the movement.

## Risks

- **CSS `transition` is not additive.** A higher-specificity `:hover`/`:focus`
  block declaring its own `transition` wholesale replaces the base rule's,
  which is the classic cause of "smooth one way, snaps the other". One
  `transition` declaration per element; state rules set target values only.
- **`display` is not interpolable.** Nothing in this feature may toggle
  `display` to show or hide an effect — `opacity` + `transform`, with
  `visibility` where hit-testing matters.
- `.objective-card` is a **hard fixed height** (122px desktop / 258px under
  760px). No celebration may add layout height; effects are transforms,
  colours and overlays only.
- Wing spill is horizontal and `.hat` never clips it; `.rank-icon-slot`
  reserves it. A growing wing must not push its neighbours — the grow is a
  `transform`, which does not affect layout.
