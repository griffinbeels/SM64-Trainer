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

Split out of `.claude/rules/ui-ranks.md` on 2026-07-28: changing a cap colour
should not load the whole animation engine, and tuning the climb should not
load the Rank tab's layout history.

**Also loaded, and both worth reading here:** `.claude/rules/ui-core.md` (the
shell + the UI verification norms — a snap that runs in one direction and cuts
in the other is a CSS-specificity problem described there) and, when you open
one of their files, `.claude/rules/ui-ranks.md` (the icon registry and the
banner this climbs on).

**The standing process rule for anything judged by feel:** build the tuning
inspector first, let the human tune it live, codify what he saves. Recipe:
the `tuning-demo` skill. Worked example: `/ui/tune.html`.

| To change... | Edit |
|---|---|
| Celebration overlays (the SCOPE rank) | `ui/components/marelocelebrate.js`'s `MareloCelebration` — ONE overlay for every overall rank-up since 2026-07-28, replacing celebrate.js's two hand-rolled scope treatments (`TierRankUp`'s fill→flip→hold, `DivisionRankUp`'s top banner), which this row had flagged as a KNOWN DEVIATION from 2026-07-26 until this landed. User, 2026-07-28: "it should maybe even animate from its current position to overlay very big in the center of the screen first, and THEN do the celebratory animation, and then animate back to its normal position." The thing that flies is the REAL `RouteRankCard` (`components/marelo.js`), not a lookalike — cloned by rendering the SAME component a second time, parked at the BEFORE rank (`rank`/`interactive=false` props) with its header twin hidden (`.marelo-slot.is-celebrating{opacity:0}`, so there is never a double image and the header card does not ALSO climb — one event, one thing celebrating it, the mistake the deleted entity toasts made). Four phases (`"out"→"climb"→"hold"→"back"`): "out" is the FLIP itself — a `useLayoutEffect` measures the live header card's `getBoundingClientRect()` before paint (the FLIP's first half), then a DOUBLE `requestAnimationFrame` flips a separate `lifted` boolean true, which is what the card's `transform` (identity vs. centred+scaled) reads. The double rAF is load-bearing, not decorative: a CSS `transition` needs a genuine PAINTED prior value to interpolate FROM, and the clone does not exist in the DOM at all until origin is measured, so painting it already-lifted on its very first frame leaves nothing to animate from -- measured as 100% of the travel landing in the first sampled frame before this was caught. "climb" is where `celebration.to` replaces `celebration.from` and the card's own `useRankClimb` (via `RouteRankCard`'s `rank`/`identity="marelo-celebration"` props) walks from→to — this file owns ONLY the flight; every beat at the centre (digit reel, wing grow, tier burst, flash) is `ui/rankclimb.js` walking `ui/celebrations.js`, the identical registry the star banners run, so "same type of celebration effects" is structural rather than a resemblance. `useRankClimb`'s third argument gained an optional `tune` override for exactly this caller (`{ lane, order, replayKey, tune }`) — read once per climb same as the module-level `tuning()` slot, so a caller can hand it an AMPLIFIED tuning without a second climb implementation; nothing else passes one. "hold" holds the finished rank at the centre; "back" flips `lifted` false again (no double-rAF needed here, since the card already has a real painted "lifted" value to leave from) and acks (`POST /api/marelo/ack`) once `flyBackMs` elapses, or immediately on a click (the card's own `onclick` jumps straight to "back" from any phase — "click to dismiss"). A division-up runs the IDENTICAL sequence at `divisionScale` (a marelotuning.js row, default 0.55) of the flight/hold durations — the ONE named difference between a division-up and a tier-up, not a second component. The shared CSS custom properties (`--fly-ms`, `--fly-ease`, `--backdrop-alpha`, `--backdrop-tint`, `--shake-px`, `--climb-color`) live on the OUTER wrapper (`.marelo-celebrate`), never the inner card: a custom property only flows DOWN the DOM tree, and the backdrop element reads them too — declaring them on the card alone (an earlier draft's mistake) left the backdrop reading nothing but its own hardcoded fallbacks, silently breaking "the background gradient... should animate from the original coloring to the new coloring" (user, 2026-07-27) the moment a value was actually tuned away from its default. `.marelo-celebrate*` are COMPONENT selectors with no size `@media` at all (`.claude/rules/ui-core.md`'s law) — the card's geometry is measured from the live header at runtime and expressed as a transform, correct at every width by construction. Verified by FRAME-SAMPLING (`tools/cdp.py`), never by eye: both end states of a transition always look right, which is how a snap survives review indefinitely. The confirmed defect this caught (2026-07-28, fixed same session) is documented above; the working trace reads first-frame displacement ~0.07% of total travel (curve starts at rest) and the final three per-frame deltas strictly decreasing to ~0 (comes to rest), for BOTH directions. Ack semantics, the pref-off no-show ack, and the app-root mount are unchanged from the deleted overlays: `RankUpCelebration` (this file) is celebrate.js's old `RankUpOverlay` under a new name, still gated on `celebrationsEnabled()` |
| Rank-up CELEBRATIONS — the level-up climb (banners + MARELO bar) | **Adding or changing a celebration is ONE entry in `ui/celebrations.js`** and nothing else (user requirement, 2026-07-27: "make sure the system is flexible so that we can add new celebrations / iterate on this easily as we go"). Five modules, each replaceable on its own: `ui/components/caps.js`'s `rankPosition`/`rankAt` (WHICH RANK a level is — **full detail below: [Rank-up CELEBRATIONS — the level-up climb (banners + MARELO bar)](#rank-up-celebrations-the-level-up-climb-banners-marelo-bar)** |
| Tuning how the climb FEELS (and the pattern for any future "feel" work) | `ui/climbtuning.js` is THE registry of every number that decides it — one row per tunable, carrying the SHIPPED DEFAULT plus the label/range/why a control needs. `climbcurve.js` and `celebrations.js` take their tuning as an ARGUMENT defaulting to those values, so they stay pure and node-testable; `rankclimb.js` reads the one active slot ONCE per climb (a mid-flight change would retime an animation already half-played). **`/ui/tune.html` + `ui/tune.js` is the inspector**, served by the existing `/ui` static mount with no server route: the real `RankBanner` ×2 in the real card chrome wearing the real stylesheet (fetched out of index.html at runtime, never copied — a stylesheet that drifted by one value would make the page judge something else), any start rank → any higher destination, and a generated control per registry row. **SAVE writes into the repo**, not into localStorage (user, 2026-07-27: "what if I can access that page at any time, mess with the settings, SAVE, and then it automatically applies to my repo immediately?"): `POST /api/tuning/climb` → `server/tuning_api.py` (one row per tunable surface in `TUNING_REGISTRIES`) rewrites the `value:` fields of climbtuning.js so the tuned numbers BECOME the shipped defaults and land in `git diff` ready to commit. That endpoint validates against the FILE's own `min`/`max`/`options` rather than a copy of them, can only replace a `value:` that already exists, and 409s when frozen. Two guards carry the weight, both in tests/test_ui_climbtuning.py: every tunable must be READ by climbcurve/celebrations/rankclimb (a row nobody reads is a slider that does nothing — mutation-proved), and no module may read a key the registry lacks (an undefined tuning value is a NaN duration, i.e. an animation that never ends, which only the 20s hold ceiling would notice). Plus a golden test that the plan built with DEFAULTS is byte-identical to the plan built by callers passing no tuning at all, and its inverse proving a changed value actually moves the timings. **The standing process rule this establishes:** for animation/"feel" work, build the inspector first, let the human tune it, and codify what they save — "like how I would work with an Inspector in Godot". **Two consequences of SAVE writing the defaults, both learned the hard way on the first tuning session (2026-07-27):** (1) **no test may assert a shipped default** — `tests/test_ui_climb.py` and the plan's wall-clock test pin the LAW against an explicit `REFERENCE` tuning, because reading `DEFAULTS` turns the suite red for every tuning round, which is the tool working; whether the live values are coherent and in range is `tests/test_ui_climbtuning.py`'s job. A choice-valued test must flip to whichever option is NOT currently stored. (2) **a floor may never override the ceiling above it.** All three durations were `max(floor, min(ceiling, wanted))` with the floor OUTSIDE, so a ladder step set to 100ms against the 220ms floor ran at 220 at every step count while the slider read 100 — a whole session judged against a number no control was showing ("the output was actually totally different that what I had changed my settings to… probably something to do with floors"). `climbcurve.js::floored(wanted, floor, ceiling)` puts the floor INSIDE, so it bounds only the budget squeeze; identical at any tuning whose floor sits below its ceiling, pinned by a property test over the whole floor/ceiling/budget/step-count space plus its inverse (a crowded climb must still be caught by the floor). The inspector's **"What will play"** readout is the mechanism that makes the remaining, legitimate clamps visible: the real plan, step by step with its own ms, plus a named warning when a control's number is not the number that runs — a per-control "effective" badge would have had to re-derive the arithmetic that produced the discrepancy, which is the second door this repo keeps learning not to build. KNOWN GAP, flagged rather than hidden: the card CHROME around the banners is a replica built from the app's own classes, not the real `StarSection`, which is welded into practice.js behind a live store; the banners — the only part that animates — are the shipped component driven exactly as practice.js drives it (same lane, same order, same props). Extracting that section is the follow-up |
| Tuning the OVERALL rank-up (2026-07-28, second tuning surface) | `ui/marelotuning.js` — twelve rows (Flight/Beats/Backdrop/Intensity groups: `flyOutMs`, `flyBackMs`, `flyEaseIn`, `flyEaseOut`, `centreScale`, `beforeHoldMs`, `holdMs`, `divisionScale`, `backdropOpacity`, `backdropTint`, `tierAmplify`, `shakePx`), same exact surface shape as climbtuning.js (`MARELO_TUNABLES`/`MARELO_DEFAULTS`/`MARELO_GROUPS`/`withMareloDefaults`/`mareloTuning`/`setMareloTuning`) so `ui/tunecontrols.js` and `server/tuning_api.py` need no special case for a second registry. The flight curve's cubic-bezier exposes ONLY its two horizontal control points (`flyEaseIn`/`flyEaseOut`) — the vertical ones are pinned at 0 and 1 in code, never tunable, which is what makes "starts from rest" and "comes to rest" structural rather than a value someone could mistune away; guarded by `tests/test_ui_marelotuning.py::test_the_flight_curve_starts_and_ends_at_rest` (asserts no row ends in `y1`/`y2`, not a value). `tierAmplify` multiplies four of the CLIMB's own tunables (`levelFlashTier`, `levelFlashDivision`, `shakeAmplitude`, `burstOvershoot`, the `AMPLIFIED` list in marelocelebrate.js) for this surface only, which is how the overall rank-up outranks a star banner running the identical registry without a second set of effects. **`ui/tunecontrols.js`** is what makes a second inspector safe to add at all: `Control` (one registry row → one number/range pair or a select for a `CHOICES`-shaped row, `defaults` taken as a PROP rather than a module import since the two inspectors read different registries) and `ControlGroups` (the group loop) are extracted from `tune.js`, so the climb inspector and this one cannot render a tunable differently or disagree about when it counts as "changed". `LevelPicker` (the from-rank/to-rank picker) lives here too, and for a sharper reason than DRY: `tune.js` is a served PAGE with its own module-level `render()` call, so importing anything from it — even a pure component — would ALSO execute that render as an import side effect and mount the climb inspector into whatever page did the importing; `tunecontrols.js` has no such side effect. **`/ui/tunemarelo.html` + `ui/tunemarelo.js`** is the inspector: a REPLICA of the header's context grid (same reasoning as `ObjectiveCard` above — the real header is welded into store.js behind a live store) wrapping the REAL `RouteRankCard`, a from/to `LevelPicker` pair, a Play button that fabricates a synthetic `{from, to, key: Date.now()}` celebration and mounts the REAL `MareloCelebration` KEYED on that key (so pressing Play again mid-flight remounts from "out" instead of resuming a stale `phase`), `ControlGroups` over `MARELO_TUNABLES`, and SAVE → `POST /api/tuning/marelo` (same `rewrite_defaults`, same `TUNING_REGISTRIES` map, no Python of its own). `MareloCelebration` is called directly rather than through `RankUpCelebration`, deliberately bypassing the "Celebrate rank-ups" pref gate — a tuning session must always be able to press Play, even with celebrations switched off in the main app's settings drawer (that state only shows as a warning banner here, matching tune.js's own `prefersReducedMotion`/`celebrationsEnabled` warnings, which likewise never block Play). Reachable from the settings drawer (`header.js`, origin-relative `href="/ui/tunemarelo.html"`, beside the climb link) — `tests/test_header_ui.py::test_the_tuning_page_is_reachable_from_the_app_and_from_the_launcher` pins the header half; the launcher script (`run-test-server.bat`) line for it is not yet added, tracked as a follow-up rather than silently left inconsistent |

## Rank-up CELEBRATIONS — the level-up climb (banners + MARELO bar)

**Adding or changing a celebration is ONE entry in `ui/celebrations.js`** and
nothing else (user requirement, 2026-07-27: "make sure the system is flexible
so that we can add new celebrations / iterate on this easily as we go"). Five
modules, each replaceable on its own: `ui/components/caps.js`'s
`rankPosition`/`rankAt` (WHICH RANK a level is — import-free, pinned against
Python's `scoring.progression_key` over all 45 pairs by tests/test_ui_caps.py)
· `ui/climbplan.js` (WHAT HAPPENS, IN WHAT ORDER — import-free, node-tested) ·
`ui/climbcurve.js` (HOW FAST — import-free, node-tested) ·
`ui/celebrations.js` (WHAT IT LOOKS LIKE — the registry) · `ui/rankclimb.js`
(`useRankClimb`, the rAF loop and the bookkeeping between them). **THE fix,
and it is a coordinate change rather than a special case:** the surfaces used
to `useTween` the server's `fill`, which is progress WITHIN the current
division, so a rank-up sent it .95 → .05 and the bar animated BACKWARDS on the
one event it exists to celebrate ("it feels like a level DOWN when we level
up"). The climb animates ladder POSITION — tier + division + fill collapsed
into one monotone number where 1.0 is one division — and the bar is its
FRACTIONAL PART, so it cannot decrease during a rise; the rank is
`rankAt(floor(position))` and a level-up IS that floor incrementing. (Round 1
drew the bar through a `rankFrame` helper because a maxed rank is position 45,
whose fractional part is ZERO, so the raw floor emptied the bar at the highest
rank in the game — caught on the LAST frame of a Capless→Mario render trace
with every earlier frame correct. **Round 5 below deleted both the helper and
the trap**, by keeping level and bar as two separate values.) Round 1's motion
was a TRAPEZOID (accelerate → cruise → decelerate) rather than an ease-in-out
— **superseded by round 5, which sweeps at most one division and so dropped
the cruise phase entirely**: an ease-in-out's peak speed scales with distance,
so a twelve-division jump would blur through the middle and no celebration in
there would be readable. Both halves of the duration law derive from two
constants (`SHORT_MS`, `CROSSOVER_DIVISIONS`) and meet C¹-continuous at one
tier; the decel ramp is a whole division long, so the bar is already slowing
as it crosses into the division you landed in. Targets: ~0.7s for a fill
nudge, ~1.5s for one division, ~3.4s for a tier, ~5.7s for twelve, hard-capped
at 7s. **`identity` is what stops a false celebration** — entity key + which
banner + active strat + rank mode (practice.js's `rankIdentity`; header.js
builds the MARELO bar's from scope label + mode). Change any of them and the
banner SNAPS instead of climbing, because switching strategy/mode/target
legitimately produces a higher rank nobody earned. A drop always snaps;
`prefers-reduced-motion` snaps (verified by render — no reel, no flash, no
intermediate ranks). **The HOLD** (user, 2026-07-27: "prevent the practice UI
from transitioning to the next stage until the celebration is completed"):
`useHeldWhileCelebrating` freezes practice.js's SELECTION — target, stage,
`armedOrder`, `lastPinnedSeg` — while any rank on screen is mid-climb, with a
12s ceiling so a stuck token can never freeze the page. **Hold the selection,
never the whole view**: the first cut froze `t.view` and DEADLOCKED, measured
— the header's MARELO bar reads `t.marelo`, not the view, so it begins
climbing first, the hold engages, and the frozen view then withholds the very
rank-up that would have made the card's own banner climb. Section DATA flowing
through is also just correct: the attempt that earned the rank-up belongs in
the log while the bar climbs. Icon motion is CONTINUOUS numbers
(`growWings`/`growProgress`/`foldProgress`/`flapPhase`/`flip`/`roll`), never
keyframe classes — numbers compose (a wing growing AND flapping), interrupt
cleanly (a second rank-up mid-climb) and need no remount trick to re-fire on
the next crossing. The wing GROW is the existing fold played backwards on the
same measured pivots; the digit reel is TWO cells and a slide, so 4→3, a tier
wrap 1→5 and a climb into Mario 1→M are all one motion with no wrap to
special-case, clipped by a `clip-path` ellipse because the sign field is a
DOME. Colour is per-TIER, so only a tier crossing cross-fades it; the cap
cannot lerp (Capless is a dashed outline, Metal has a highlight layer, Toad
has spots) so it swings in edge-on instead. Guarded by tests/test_ui_climb.py
(monotonicity at 1ms granularity across the whole climb — endpoints alone are
exactly how the shipped bug survived review), tests/test_ui_celebrations.py
(every registry entry reaches a prop Hat reads or a variable index.html uses;
mutation-proved), and a row in tests/test_single_source.py. **Round 2
(2026-07-27), five live reports.** (a) NOTHING between the icon and the card
may clip: `.rank-banner-row` and `.rank-slot` both had `overflow: hidden` and
were cutting the wing tips off, so both lost it and
`.rank-banner-name`/`.rank-banner-next` ellipsise themselves instead -- a
container clip cannot tell a wing from a word (measured: the icon paints 9px
above the banner box, card height and page width unchanged across the trace).
(b) The rank WASH moved from `.rank-slot-wrap::before` onto
`.rank-banner::before` and reads `--climb-color`, so it cross-fades with the
bar and the cap instead of painting the tier the climb is heading FOR --
hiding it during a climb (round 1) was rejected outright: "all the colors
should animate from the original coloring to the new coloring".
`--rank-wash-split`, `--rank-glow`, `rankWashStyle` and `.rank-slot-wrap` are
all gone: the split was the DOM boundary between the two banners all along.
(c) A strategy with a ladder but no time renders **Capless V, empty bar**
(`atFloor` in ranks.js) rather than a sentinel sentence -- which is also what
gives a FIRST-ever rank somewhere to climb from, the user's stated priority;
`no_ladder`/`no_strat` keep their sentinel. (d) A TIER crossing halts the
climb (`climbcurve.js::tierDwell`): anticipation -- shake growing in amplitude
AND frequency while the cap squashes to a flat line, bar full on the old
tier's last subdivision -- then the crossing, then a payoff beat. Release is a
burst out of the flat with an overshoot plus sequenced four-point sparkles
(`Sparkles` in hat.js), colours turning over across it. Replaced the edge-on
cap flip, which was a transition where an event was wanted; the pause sits
BEFORE the boundary because that is where anticipation belongs and it hides
the rank swap in the flattest frame. (Round 2 shared a BUDGET between dwells —
**superseded**: a crossing now interpolates from its one-tier duration down to
its many-tier one, see round 5.) `ms` on a registry entry may be a FUNCTION of
the beat for exactly this reason -- a dwell's length depends on how many tiers
the climb crosses. **Round 3 (2026-07-27).** (e) The wings STAY through the
whole anticipation: the fold used to expire mid-build and `wingTiers`, reading
a position still on the old tier, put all four straight back -- they now
squash with the cap and go when the new cap arrives, on the flattest frame.
(f) **Both banners render from the first strategy pick**, both at Capless V,
so the star's does not appear out of nowhere with a first time -- and
therefore BOTH climb. Whether it is one measure or two is answered by the
SERVER from the ladders (`views.py::ranks_share_ladder` -> `sec.one_ladder`),
never by comparing two graded values: that could not answer before a first
time existed, and it merged two different measures whenever a run happened to
grade them alike. `practice.js`'s `ranksAreAtFloor` passes `atFloor` down,
since the ENTITY banner has no payload of its own at the floor -- everything
in RankBanner below the early return has to survive `banner == null`. (g) The
two climbs run in TURN via `lane`/`order`: each publishes the timestamp it
will finish at and the next order starts at `max(now, laneFree +
LANE_GAP_MS)`; a waiting climb holds the practice page exactly as a running
one does, and a lone banner or the MARELO bar passes no lane and starts
immediately. (h) The next step reads "0.22s to rank up". Round 5 below
replaced its left-to-right MASK wipe -- fired by a settle beat, with ranks.js
pinning `--climb-reveal` to 0 for the rest of the climb -- with an opacity
FADE the engine drives off the bar's own progress; a beat fires at a moment
and so could only ever be TUNED to line up with the bar rather than tied to
it. (i) Mario's `M` gets its own tighter width budget
(`GLYPH_WIDTH_MARGIN_MARIO`, hat.js): PATCH_BOX is the rectangle around a
DOME, so 80% of the BOX put the widest glyph in the game off the white patch
and onto the cap. **Round 4 (2026-07-27).** A FIRST rank climbs from Capless V
instead of snapping, and three states had to be told apart, all of which look
like "no previous position": the first effect run of a MOUNT (page load,
switching star -- snaps, or every card animates on every refresh); a later run
on a banner that was showing a sentinel (climbs); and a banner that ARRIVED
LATE into a card already on screen (climbs). The third is answered by
`laneMembers`/`laneFirstSeen` -- both banners of a card mount in one tick, a
banner that appears because a strategy was just picked is seconds later, and
the lane's start time is cleared when its last banner unmounts so revisiting a
star reads as a fresh mount. An identity change also no longer CUTS a climb
already heading for the same target (`climbingToRef`): picking a strategy
changes a card's identity but not the star's own rank, and snapping there
killed the animation at the moment the user triggered it. **Round 5
(2026-07-27), the multi-rank climb** (spec 2026-07-27-multi-rank-climb): a
climb is a PLAN of steps now, not one continuously moving position —
`ui/climbplan.js` builds it and the hook walks it. The coordinate change that
made round 1 possible (bar = fractional part of one monotone position) is
exactly what made this impossible: the bar cannot **stay full** across several
rank-ups without the position lying about which rank is on screen ("ONCE IT'S
FULL, AND IF WE HAVE MORE RANKS TO FULLY LEVEL UP THROUGH, IT STAYS FULL"). So
level and bar are two values, and every step between the first rank-up and the
arrival is simply built with `barFrom === barTo === 1` — the rule is the shape
of a table rather than a case to remember. A tier the climb both ENTERS and
LEAVES is condensed to one step; the tier you start in and the one you land in
are still climbed division by division, because those divisions are progress
the player actually made. Two styles of that condensation ship together behind
a settings-drawer select (`CLIMB_SKIP_STYLES`, localStorage `sm64.climbSkip`,
default `pop`), to be judged live and collapsed to one — `pop` lands on
division V and grows all four wings back, `chain` lands straight on division I
so the wings never come off and three crossings read as one run of caps. **A
progress bar may never OVERSHOOT** (user, 2026-07-27: "you gave me progress
and then took it away!!!!") — `climbcurve.js::barEase` is a smoothstep and is
THE easing for any bar; `easeOutBack` stays for the digit reel and the cap
squash, which are objects springing rather than progress being claimed. The
bar still resets 1 → 0 once, entering the arrival, which is the thing the user
asked for and not overshoot. Fallout worth knowing: `climbcurve.js` lost its
trapezoid (no sweep is longer than one division now, so cruise was
unreachable), `caps.js::rankFrame` was deleted with the position-45 trap it
existed for, and `ranks.js`'s next-step label reads `climb.level + 1` instead
of rebuilding a position from the bar — with the bar pinned at 1 that
expression already WAS the next level, so it would have named the wrong rank
for the whole climb. Guarded by tests/test_ui_climbplan.py (the user's own
Capless V → Waluigi IV walk transcribed step by step in both styles, the pin,
and all 990 rising level pairs), tests/test_ui_climb.py (the bar is monotone
AND never exceeds its target, at 1ms), and a `tests/test_single_source.py` row
(mutation-proved) stopping a second file — celebrate.js's still-hand-rolled
scope overlay above all — from growing its own opinion about the order a climb
happens in
