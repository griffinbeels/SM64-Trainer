# MARELO — Overall Rank & Progression System

**Date:** 2026-07-24
**Status:** Approved design, pending implementation plan
**Supersedes nothing** — extends `2026-06-22-ranks-design.md` (per-entity rank
standards) and `2026-07-23-average-rank-mode-design.md` (the rank-mode registry).
**Parallel work:** 100-coin star identity + the scraper's dropped ladders are a
SEPARATE concurrent effort (§12). This spec assumes nothing from it.

## 1. Goal

Today a rank exists only per (entity, strategy): "your PB on Nuts Pless is
Platinum". There is no answer to *"how good am I, overall?"* and no visible
progression while practicing.

MARELO adds a single 0–100 rating, derived from everything you practice, that:

- collapses to a **tier + division** (Gold III) so improvement is legible;
- is **scope-aware** — your 16 Star rating, your SSL Stage RTA rating, your
  120 Star rating, and your overall rating are separate numbers, because a
  route already *is* a set of things to be good at;
- moves **every session**, so practicing produces visible progress;
- makes the distance to the maximum explicit — "given the top of the ladder,
  how close are you?";
- **celebrates** rank-ups, because the point of the system is motivation.

## 2. Prior art

### 2.1 sm64-xcams player scores (the community's own system)

Read directly from the site's shipped bundle (`_next/static/chunks/318-*.js`),
not from documentation — none is published. Per star, out of 100:

```
totalPts = basePts + rankPts + stratPts
  basePts  = (N − yourPlacement)/N × 60      # percentile among ALL players' times
  stratPts = (percentile within your best single strat) × 20
  rankPts  = RANK_POINTS[tier]               # max 20
  flags shift the 60/20 split: noStratScore → 80/0, mainStratScore → 20/60
```

```
RANK_POINTS = {Mario 20, Grandmaster 18, Master 17, Diamond 15, Platinum 13,
               Gold 10, Silver 8, Bronze 5, Iron 2, Unranked 0}
```

Player overall = **sum of your best 30 star scores ÷ 30** — divided by 30, not
by how many you have, so unpracticed stars count as zero. Banded by:

```
PLAYER_BANDS = Mario ≥95, Grandmaster ≥90, Master ≥80, Diamond ≥70,
               Platinum ≥60, Gold ≥45, Silver ≥25, else Bronze
```

(plus a joke `Atmpas 100` tier). A player with fewer than 20 ranked stars gets
no standard at all.

**Two findings drive our design.** First, `(N − place)/N` is a *leaderboard
percentile*, not a ratio to an ideal time — which is why xcams' worst-placed
player still scores above zero, and why **we cannot reproduce it**: we have no
player pool, only ladders. Second, `PLAYER_BANDS` is a score→tier map, so if we
define our score to pass through those exact values at the ladder cutoffs, our
score and our medal agree by construction (§4).

### 2.2 MCSR Ranked (Minecraft speedrunning)

Head-to-head Elo across six tiers (Coal→Netherite) with sub-divisions, seasonal
resets, placement matches, and inactivity decay for the top 150.
<https://wiki.mcsrranked.com/gameplay/elo_and_ranks>

**Rejected as a model.** Elo requires opponents; this is a solo practice tool.
Decay and seasonal resets punish absence — actively hostile to a practice
trainer. The one transferable property is tier *density*: wide low tiers, narrow
top tiers, which our anchors reproduce (Silver spans 20 score points, Mario
spans 5).

### 2.3 League of Legends / Valorant divisions

Riot's stated purpose for tiers-with-divisions is giving players both a
short-term and a long-term goal, and their removal of inter-division promo
series was explicitly to reduce "stickiness" and make progress feel granular.
<https://support.riotgames.com/league-of-legends/gameplay/ranked-tiers-divisions-and-queues>

### 2.4 Goal-gradient and endowed progress

Effort accelerates as a goal appears closer (Hull 1932; replicated in humans),
and a *visible* progress indicator is what supplies the proximity signal.
Nunes & Drèze (2006): a loyalty card pre-filled with 2 of 12 stamps was
completed 82% more often than an 8-stamp card requiring identical work.

**Applied here:** divisions exist so the next goal is always minutes away, not
months; the header bar is always visible; and unpracticed entities are surfaced
as *available gains* rather than as invisible absence.

## 3. Corpus and scopes

### 3.1 Rankable entity

A star or segment that (a) has ≥1 strategy ladder in the rank-standards store,
and (b) is not user-excluded. Today: **92 stars + 10 segments = 102**.

The 55 seeded castle movements have no standards and are **absent** from
ranking — not zero. Absent vs. zero is load-bearing:

- **zero** = "this counts, you haven't practiced it, it is costing you";
- **absent** = "this is not part of the game being scored".

Entities gained later (§12) *add* to denominators: coverage dips, mastery does
not. This is correct and must be stated in the UI when it happens.

### 3.2 User exclusion

A per-entity opt-out, stored in `ui_state` KV `rank_excluded` (a list of entity
keys) — user preference, not community data, so it never belongs in the
standards JSON. An excluded entity leaves the numerator *and* denominator of
every scope. Toggled from the Rank tab's breakdown list.

### 3.3 Scopes

A scope is a named set of rankable entities. All three kinds are derived; there
is no scope registry to maintain.

| Scope id | Members |
|---|---|
| `overall` | every rankable entity |
| `course:<id>` | that course's rankable stars + segments starting in it (BitDW = reds star + pipe + fight) |
| `route:<id>` | the route's steps, resolved as below |

**Every route in the library is automatically a scope** — the 13 main routes,
the 37 Stage RTA routes, and every user-created route. A custom route becomes a
rankable, history-tracked category the instant it is saved, with no extra
authoring: the route already *is* the entity list.

**K-of-N step resolution.** For a step needing K of N candidates: drop
candidates with no standards; let `k = min(K, rankable_count)`; the step
contributes `k` to the denominator and the **best-k candidate scores** to the
numerator. A step with no rankable candidate drops entirely. This mirrors
`tracking/routes.py::_step_rate`'s existing best-K convention.

Castle-interior segments with no course (LBLJ, MIPS clip, Lakitu skip, BitS
entry) appear in `overall` and in any route containing them, never in a
`course:` scope.

### 3.4 Scope has one source of truth

The **focus route** (Practice focus / Run route selection) *is* the active
scope. The header MARELO bar follows it with no second control; when no route is
selected the active scope is `overall`. The Rank tab's scope picker is a
*browser* — it lets you inspect another scope without changing what you are
practicing, and it snaps back to the active scope when the focus route changes.

## 4. Entity score (0–100)

### 4.1 Two scores, deliberately

| Score | Graded against | Answers | Feeds |
|---|---|---|---|
| **Strat score** | the ladder of the strategy you ran | "how well do I execute this strat?" | the per-section banner |
| **Entity score** | the entity's **best-possible ladder** | "how close is this to the fastest this star can be?" | every aggregate |

Both are shown on the practice section header (`Strat: Gold II · Star: Silver I`).
A mastered slow strategy maxes the left number and honestly does not max the
right one — a Mario-tier leftside A-Maze-Ing Emergency Exit is still ~0.46 s
behind the SS ladder and scores ~93 as an entity, not 95+.

### 4.2 Best-possible ladder

```
best_ladder[tier] = min over all of the entity's strategies of cutoff[tier]
```

Pointwise minimum, over tiers each strategy actually defines. This is
monotone whenever the inputs are (the min of monotone sequences is monotone), so
it always produces a valid ladder, and it means "the best time achievable at
this tier by any known strategy" rather than committing to one strategy that
might be fastest at Mario and slower at Gold.

### 4.3 The curve

Score anchors — **§2.1's `PLAYER_BANDS` verbatim**, extended with a Bronze
anchor at 10 (xcams defines no Bronze threshold; everything below Silver is
Bronze there). Reusing their exact values is what makes the system
self-consistent, and it means our numbers are directly comparable to a player's
xcams standard:

```
SCORE_ANCHORS = {Mario 95, Grandmaster 90, Master 80, Diamond 70,
                 Platinum 60, Gold 45, Silver 25, Bronze 10}
```

§2.1's `RANK_POINTS` is *not* used. It is xcams' way of adding a tier bonus on
top of a percentile; our score already encodes the tier positionally, so a
second tier term would double-count.

Given a ladder and a displayed time `t` (centiseconds, via
`classify.display_cs`, per the project's Usamune-clock rule):

- `score(cutoff[R]) = SCORE_ANCHORS[R]` for every tier the ladder defines;
- **between two defined tiers:** linear in time;
- **faster than the hardest defined tier:** extrapolate that tier's slope to
  the next one, capped at **100**;
- **slower than the easiest defined tier** (the Iron zone): asymptotic decay
  `score = anchor_last × cutoff_last / t` — trends toward 0, never reaches it
  (matching xcams' property that last place is not a zero);
- **no valid time:** score **0**. This is the coverage penalty and is distinct
  from *absent*.

### 4.4 The invariant

> `tier_from_score(score, defined_tiers) == classify.rank_for(ladder, t)`
> for every ladder and every time.

The number and the medal can never disagree. `tier_from_score` returns the
hardest tier in `defined_tiers` whose anchor is ≤ the score.

**`defined_tiers` is required, not optional.** A ladder missing e.g. Master
interpolates through the 80–90 score range between its Grandmaster and Diamond
cutoffs; a full-table lookup would report "Master" for a time `rank_for` calls
Diamond. Entities pass their ladder's tiers; aggregates (§5) pass all eight.
This is a pinned test against every ladder in the bundled seed, at every tier
boundary.

### 4.5 Divisions

**Five per tier**, equal score-width slices, numbered V (bottom) → I (top).
Gold spans 45–60, so Gold V = 45–48 … Gold I = 57–60. Above Mario is Mario V–I
over 95–100; the Iron tail is Iron V–I over 0–10, so even the floor has five
climbable steps. Divisions are a pure function of the score — no new data, no
registry, and they apply identically to entity scores and aggregates.

Band edges come from `defined_tiers`, the same argument §4.4 requires: on a
ladder missing Master, the Grandmaster band runs 80–90 and its five divisions
slice that wider span. The division can therefore never name a tier the ladder
does not define.

### 4.6 Which time is graded

The existing header rank-mode picker (`classify.RANK_MODES`: PB / Avg 10 /
Avg 50 / Best 10 / Best 50 / Lifetime) selects the basis, through the existing
`views.py::_grading_basis` resolver — one knob, already built, and MARELO
differs per mode by design.

Per strategy we take that mode's basis time; the **entity time is the best
across strategies**. Pooling attempts across strategies before averaging would
conflate different skills (an Avg-10 mixing two strats measures neither).

## 5. Aggregation

For a scope with *n* rankable entities:

```
mastery  = mean score over the entities you have practiced   # 0..100
coverage = practiced / n                                     # 0..1
MARELO   = Σ score / n  =  mastery × coverage
```

MARELO is the fixed-denominator rating the user asked for: *given the maximum
performance bar, how close are you?* Decomposing it into mastery × coverage is
presentation, not a different number — but it is what makes a low rating
actionable ("am I shallow or narrow?") instead of merely discouraging.

Tier + division from MARELO via §4.5 with all eight tiers defined.

**Measured against the live db at design time** (25 of 102 entities practiced,
753 valid successes):

| Scope | Mastery | Coverage | MARELO | Tier |
|---|---|---|---|---|
| Overall | 22.4 | 25 % | 5.5 | Iron |
| 16 Star — LBLJ | — | — | 26.4 | Silver |
| 70 Star — HMC Late | — | — | 6.9 | Iron |
| 120 Star | — | — | 4.4 | Iron |

(Route figures from a prototype covering star candidates only.) This is the
scope design working: 16 Star reads Silver because it has genuinely been
practiced, while 120 Star is honestly Iron with 95 stars untouched.

**Available gain.** For each entity in scope, `gain = (next_tier_anchor −
score) / n` — the MARELO the *next tier on this entity* is worth. An entity
already in the top tier uses 100 as its target, so a Mario-tier entity still
shows a (small) remaining gain rather than dropping off the list. Sorted
descending, this is the "what should I practice" list, and it is how unpracticed
entities become quests instead of invisible dead weight.

## 6. History

**No new storage.** Score at time *T* is recomputable from `attempts.ended_utc`,
so a scope's history is: replay that scope's successful attempts
chronologically, recomputing MARELO after each one. Every scope gets its own
independent curve.

Two properties that must be documented in the UI, not hidden:

- history is always recomputed against **current** standards, so a seed bump
  reshapes the past — consistent, but not a recording of what was displayed then;
- editing a route or excluding an entity **retroactively** rewrites that scope's
  curve, because the curve is derived, not journaled.

## 7. Surfaces

All in `ui/`, so they appear in both the browser tab and the desktop GUI
(project rule 10). Star↔segment parity (rule 11) is automatic: MARELO consumes
entity keys and never branches on kind.

### 7.1 Practice section header — both ranks
`Strat: Gold II · Star: Silver I`. The existing rank banner gains a division and
a second, entity-level medal. Sentinel wording for unranked/no-ladder/no-strat
states is unchanged.

### 7.2 Header MARELO bar
Always visible. Scope chip (focus route name, else "Overall") · tier+division
crest · the number · a thin track to the next division · mastery × coverage on
the sub-line. **Fixed height** so OBS layouts never reflow (project design
rule). Click opens the Rank tab.

### 7.3 Rank tab (own sidebar entry)

One component, because three requested features are the same view under
different scopes:

- **Scope picker** — Overall / any route / any course; defaults to and follows
  the active scope (§3.4).
- **Rank card** — crest, MARELO, mastery and coverage as separate bars,
  "next division in +X.X".
- **History chart** — MARELO over time for the selected scope, tier bands as
  colored horizontal regions with division ticks.
- **Breakdown** — every entity in scope: score, tier+division, and
  `+X.XX MARELO to your next tier`. **A route scope renders in route order by
  default with the weakest steps flagged** — that is the route-performance
  visualization, not a separate screen; `overall` and `course:` scopes default
  to sorting by available gain. Either order is one toggle away in both cases.
  Exclusion toggles live here.

### 7.4 Rank-up celebration

Fires when an entity's tier, or the active scope's tier/division, rises above
the last celebrated value. The last celebrated value is **persisted per
entity/scope** in `ui_state`, so a celebration fires exactly once and survives
restarts and journal replays.

- **division-up** → compact pop on the badge;
- **tier-up** → full overlay, animating *through* each intermediate crest when
  several tiers are gained at once.

Dismissible, never input-blocking, confined to a fixed region, and with an off
switch in settings. **Defaults ON** — motivation is the feature.

### 7.5 Standards table — "you are here"

Your time is essentially never *at* a cutoff, so you are not in a cell: you are
at a point **between two rows, within one column**. Render a **horizontal marker
across the active strategy's column at the interpolated position**, labelled
with your time and score, with already-beaten cutoffs in that column subtly
tinted. This shows position *and* depth into the tier (your division and the
exact gap) and reuses the §4.3 interpolation.

Fallback if it reads as noisy on the widest tables: mark the column header plus
the two bracketing cells.

## 8. Backend wiring

| Concern | File |
|---|---|
| Pure score curve, best-possible ladder, tier/division, `SCORE_ANCHORS` | `ranks/scoring.py` (new) |
| Pure scope resolution + aggregation (`mastery`/`coverage`/`MARELO`/gains) | `ranks/scopes.py` (new) |
| Pure chronological replay → history series | `ranks/history.py` (new) |
| REST + exclusion CRUD | `server/ranks_api.py` (extend) |
| Header bar + crest | `ui/components/marelo.js` (new) |
| Rank tab (card + chart + breakdown) | `ui/components/rankpage.js` (new) |
| Rank-up overlay | `ui/components/celebrate.js` (new) |
| Column marker | `ui/components/standards.js` (extend) |
| Section header second medal | `ui/components/ranks.js`, `practice.js` |

**No DB migration.** Scores derive from attempts + standards; exclusions and
celebration watermarks are `ui_state` KVs.

`ranks/classify.py` keeps ownership of tier ORDER and `RANK_MODES`;
`ranks/scoring.py` owns score anchors and divisions. Neither duplicates the
other's registry.

## 9. REST surface

- `GET /api/marelo?scope=route:3` → `{scope, marelo, mastery, coverage, tier,
  division, next_division_at, entities:[{key, name, score, tier, division,
  gain, practiced, excluded}]}`
- `GET /api/marelo/history?scope=route:3` → `[{utc, marelo, tier, division}, …]`
- `GET /api/marelo/scopes` → the pickable scopes with display names
- `POST /api/marelo/exclude` / `DELETE …` → toggle an entity's exclusion
- broadcast-only `marelo_changed` so open clients refetch

Same error taxonomy as `server/api.py`.

## 10. Testing

**Unit (pure, the bulk):**
- the §4.4 invariant at every tier boundary of every ladder in the bundled seed;
- curve edges: faster-than-Mario cap at 100, Iron asymptote never reaching 0,
  single-tier ladders, ladders with gaps;
- division boundaries, including the Mario (95–100) and Iron (0–10) bands;
- best-possible ladder = pointwise min, including ragged tier sets;
- aggregation: unpracticed = 0, excluded leaves both numerator and denominator,
  empty scope, K-of-N best-k, step with no rankable candidate;
- history: monotonic timestamps, recompute determinism, retroactive rewrite on
  exclusion.

**Rendering (project rule — unit tests + `node --check` once shipped an
invisible feature):** header bar, Rank tab, celebration overlay, and the
standards marker verified via headless Chrome or the chrome-devtools MCP.

**Parity:** `tests/test_ui_section_parity.py` extended so the second (entity)
medal and MARELO surfaces exist for both stars and segments.

## 11. Open items and risks

- **Empty-scope and tiny-scope behaviour.** A 1-step route yields a MARELO equal
  to that entity's score; xcams suppresses a rating below 20 ranked stars. We do
  *not* suppress — a scope is exactly as large as the user made it — but the UI
  labels small scopes with `n`.
- **Celebration on journal replay.** The watermark must be written before the
  broadcast, or a re-projection replays every historical rank-up at once.
- **History cost** is O(in-scope successes) per request; fine at today's 753
  successes, worth a cache if scopes grow.
- **Standards churn** rewrites history (§6). Accepted, documented.

## 12. Parallel work — 100-coin stars (NOT in this spec)

A concurrent session owns 100c identity and the scraper. Established during
design and handed over:

- xcams publishes 118 standards keys; `tools/scrape_ranks.py::key_to_entity`
  maps 98. The 20 dropped are 19 `<stage>_100c<N>` keys plus `5_premc`
  (HMC pre-Metal-Cap, a segment). Cause: `"100c6".isdigit()` is False.
- A 100c run is a **two-star trip**: SSL 100c (w/ star 6) Mario is **1:50.06**
  against **0:25.63** for SSL star 6 alone. Collecting the 100-coin star does
  not exit the course; the *paired* star ends the trip. Six of the seven stars
  with no standards are red-coin stars, because they exist only as the
  exit-star of a 100c pair.
- Consequence: the trainer's per-star attempt model splits the trip in two and
  produces no time comparable to the ladder. Likely fix is a plain
  (waypoint-free) segment def — **live-verify first**.

**Interface contract:** MARELO consumes any entity that has a ladder in the
standards store and resolves to `star:<course>:<star>` or `segment:<id>`. New
100c entities under either key shape enter the corpus automatically with zero
changes here. A third identity shape is a shared-contract change and must be
coordinated.

**File ownership:** that session owns `tools/scrape_ranks.py`,
`data/rank_standards.seed.json`, `tracking/segments.py`, `tools/corpus_*.py`,
`data/defaults.seed.json`. This one owns `ranks/scoring.py`, `ranks/scopes.py`,
`ranks/history.py`, `server/ranks_api.py`, `ui/`. `ranks/standards.py` and
`ranks/classify.py` are shared contracts.

## 13. Non-goals (YAGNI)

- **No Elo, no opponents, no seasons, no decay** (§2.2) — this is a practice
  tool; absence must never cost rating.
- **No percentile scoring** — requires a player pool we do not have (§2.1).
- **No best-N-of-corpus overall rating.** Scope selection already solves
  "don't judge me on 120 stars" better than a magic N does.
- **No new rank modes** — MARELO rides the existing registry.
- **No server-side history storage** — it is derivable (§6).
- **No promotion series / demotion protection.** They exist to damp matchmaking
  noise; a time compared to a fixed ladder has none.
