# Multi-rank climb: the bar stays full, whole tiers get condensed

Date: 2026-07-27
Supersedes the climb's motion model from `2026-07-26-progression-celebration-design.md`
(§2, the trapezoid). Everything else in that spec stands.

## The report

> "if a new PB means that the user will go up multiple ENTIRE DIVISIONS (e.g., I
> go from capless 5 to Waluigi 4 … 3 animations and 3 entire chunks of levels to
> go through), then we should show the first set of subdivision level ups, and
> then once we trigger the overall rank transition … it should show the full rank
> up animation, and then IMMEDIATELY TRANSITION INTO THE MAX RANK OF THE NEXT
> DIVISON"
>
> "The bar should STAY FULL WHILE RANKING UP GOING FORWARD. ONCE IT'S FULL, AND
> IF WE HAVE MORE RANKS TO FULLY LEVEL UP THROUGH, IT STAYS FULL."

Walked through in full for Capless V → Waluigi IV, and closing with: "the bar
should now grow from 0 to our actual position inside the bar. Done!"

## Why the current engine cannot do this

`useRankClimb` moves ONE number — `position` — continuously, and derives
everything from it: `fill = fract(position)`, `rank = rankAt(floor(position))`.
That coupling is what fixed the original backwards-bar bug, and it is also what
makes this impossible: the bar cannot stay full without the position lying about
which rank is shown.

So the bar is decoupled from the position, and the climb becomes an ordered
**plan of steps** built up front. "The bar stays full while ranks remain" then
stops being a rule someone has to remember and becomes the shape of a table.

## The plan

| step | rank shown | bar | length |
|---|---|---|---|
| `approach` | the division you were in | startFill → 1 | sweep |
| `division` | ticks up one | **pinned 1** | ladder step |
| `anticipate` | old tier's division I | **pinned 1** | `tierDwell().anticipateMs` |
| `tier` | ticks into the new tier | **pinned 1** | `tierDwell().payoffMs` |
| `tierskip` | division V → I | **pinned 1** | ladder step (*pop* style only) |
| `arrive` | the destination | 0 → targetFill | sweep |
| `fill` | unchanged | startFill → targetFill | sweep (no rank-up at all) |

`ui/climbplan.js` builds it (import-free, numbers only — it never learns what a
tier is), `ui/climbcurve.js` sizes it, `ui/rankclimb.js` walks it,
`ui/celebrations.js` still owns what each beat LOOKS like.

## Which tiers get condensed

A tier is **fully traversed** when the climb both enters it (crosses to division
V) and leaves it (crosses out of division I). Only those are condensed.

* The tier you START in is never condensed — you climb out of it division by
  division. That is the user's own Capless 5 → 4 → 3 → 2 → 1 walk: those
  divisions are progress they actually made.
* The DESTINATION tier is never condensed either — you stop inside it.

Two styles, both shipped, switchable in the settings drawer
(`sm64.climbSkip`, default `pop`), expected to collapse to one once judged live:

* **pop** — the crossing lands on division V; a `tierskip` beat immediately
  grows all four wings and rolls the digit 5 → 1; then straight into the next
  anticipate. This is the literal reading of "IMMEDIATELY TRANSITION INTO THE MAX
  RANK OF THE NEXT DIVISON".
* **chain** — the crossing lands directly on division **I**. Wings never come
  off, the digit never leaves 1; only the cap and the colours change, so three
  crossings chain back to back with nothing between them. ("we would go CAPLESS
  -> TOAD, TOAD -> TOADSWORTH, TOADSWORTH -> WALUIGI immediately, where we
  basically stay at full wings the entire time.")

## The bar never overshoots

> "we should NEVER overshoot in a progress bar. It reads as annoying and an
> error -- you gave me progress and then took it away!!!!"

Every bar sweep (`approach`, `arrive`, `fill`) uses a monotone smoothstep and
lands exactly on its target. `easeOutBack` stays only where something is a
physical object springing — the digit reel, the cap squash — never where it
reads as progress being claimed and revoked. This is a STYLE RULE for the app,
not a property of this one animation.

Note the bar still resets 1 → 0 at the final rank-up. That is not overshoot and
it is what the user asked for: "let's show you how close you are to actually
ranking up to the next tier -> animate from empty to our actual progress".

## Lengths

Ladder steps (`division` + `tierskip`) share a budget exactly the way tier
dwells already do, so the pathological climb stays bounded:

| | |
|---|---|
| bar sweep | `1500 × sqrt(divisions)`, floored 450ms, ≤1 division by construction |
| ladder step | 460ms, sharing a 3400ms budget, floored 220ms |
| tier dwell | unchanged — 1600ms, sharing 5200ms, floored 700ms |

Capless V → Waluigi IV: **~9.5s** (pop) / **~8.6s** (chain); today's engine takes
~11.8s for the same jump. Worst case in the game (a first-ever rank grading
Capless V → Mario I): ~12.0s / ~10.6s, both inside the existing 20s hold ceiling.

Ladder steps are bounded at ≤4 out of the start tier + ≤4 into the destination
tier + ≤7 skips = 15, without needing the budget to enforce it.

## Fallout

* **A live bug this surfaces.** `ranks.js` derived the next step as
  `rankAt(rankPosition(tier, division, climb.fill) + 1)`. With the bar pinned at
  1 that expression already IS the next level, so "→ next rank" would read one
  rank too far for the whole climb. The hook returns `level` now and the call
  site uses it, which is also one fewer round trip through a coordinate.
* **`climbcurve.js` loses its trapezoid.** Accelerate → cruise → decelerate
  exists so a twelve-division sweep does not blur through the middle; no sweep is
  ever longer than ONE division now, so `climbProfile`, `climbTravelled`,
  `climbPosition`, `RAMP_MS`, `CROSSOVER_DIVISIONS` and `CRUISE_DIVISIONS_PER_S`
  became unreachable and came out.
* **`caps.js::rankFrame` became unused** — the shown state is
  `{...rankAt(level), fill: bar}` now, so the position-45 special case it existed
  for cannot arise. Deleted with its test.
* Both rank banners and the MARELO header bar get this from the one hook.

## Tests

* `tests/test_ui_climbplan.py` — the step list for a table of climbs, including
  the user's exact Capless V → Waluigi IV example in BOTH styles; the pin (the
  bar is exactly 1 on every step between the first rank-up and `arrive`); no
  sweep exceeds one division; ladder-step count bounds.
* `tests/test_ui_climb.py` — sweeps are monotone AND never exceed their target,
  sampled at 1ms; tier dwells unchanged.
* `tests/test_ui_celebrations.py` — the registry still only listens for kinds the
  engine emits, now that the kinds are declared in `climbplan.js`.
