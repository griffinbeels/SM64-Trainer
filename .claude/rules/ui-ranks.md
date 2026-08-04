---
paths:
  - "src/sm64_events/ui/components/caps.js"
  - "src/sm64_events/ui/components/rankicon.js"
  - "src/sm64_events/ui/components/hat.js"
  - "src/sm64_events/ui/components/medal.js"
  - "src/sm64_events/ui/components/ranks.js"
  - "src/sm64_events/ui/components/rankpage.js"
  - "src/sm64_events/ui/components/marelo.js"
  - "src/sm64_events/ui/components/standards.js"
  - "tests/test_ui_cap*.py"
  - "tests/test_ui_rank*.py"
---

# Rank UI — icons, banners, MARELO, celebrations, the climb

Split out of `.claude/rules/ui.md` on 2026-07-28 — it was 57 KB of one 26k-token
file that loaded on every UI edit. **`.claude/rules/ui-core.md` loads alongside
this** and carries the shell, the shared primitives and the verification norms
— read both. Server-side rank rules are in `.claude/rules/ranks.md`.

| To change... | Edit |
|---|---|
| Rank icon SPACING — any container next to a rank icon | `ui/components/caps.js`'s `HAT_LAYOUT_WIDTH_RATIO` (~1.632) / `HAT_WING_SPILL_RATIO` (~0.370), both DERIVED from `CAP_BOX`/`CANVAS` (never hand-copied) — a Hat is 1.632× as wide as tall before its wings spill a further 0.370× its `size` past that box on EACH side, by design (`.hat` never clips its own wings). Round 4 found this crowding the rank banner's neighbours with a hand-picked 9px margin at that one call site's size (24px); round 5 (addendum, task 8, 2026-07-26 — the user found `.attempt-medal` not centred, the same bug) swept all 17 `RankIcon` call sites and generalised the fix into `index.html`'s `.rank-icon-slot` — a shared class taking `--icon-size` (set inline to the SAME `size` prop a caller passes its RankIcon) and reserving `calc(var(--icon-size) * .370)` margin each side, so the next call site inherits the right geometry instead of re-deriving it. Reserves the FULL wing-inclusive extent on purpose, not just the layout box: wings are absent only at division V and on Capless, so 32 of the 45 tier+division combinations DO have at least one wing pair — reserving for them is the common case, not the exception. Fixed via this class: `.rank-banner-icon` (ranks.js, migrated off its original hand-picked round-4 margin), `.marelo-bar-icon`, `.scope-chip-icon`, `.entity-detail-icon`, `.rank-card-icon` (the Rank tab's own 64px icon — the biggest gap of the sweep, and the one instance no fixture had ever actually rendered WINGED before, since every prior round's test data happened to land on Capless), `.rankup-medium-icon`, `.entity-toast-icon`. Two explicit TABLE-CELL sites needed a plain width fix instead (a slot's margin does nothing inside a `<td>`): `.attempt-medal` (practice.js log rows) 34px→54px, `.entity-attempts td:first-child` (Rank tab EntityDetail) 22px→34px. Checked and left alone, with the reasoning recorded at each: `.starrank`/`.rankup-cap-flip` stack the icon in a flex COLUMN with nothing beside it horizontally (wing spill is strictly horizontal, so there is no neighbour to crowd); `.routestep-head`/`.shead` already have gaps wider than their icons' spill; `.rank-table`'s breakdown-row cell has no explicit width, so table auto-layout grows the column rather than clipping; `routes.js`'s route-average icon never passes a `division` prop, so `wingTiers` never draws a wing there regardless of size. Verified by MEASUREMENT (a CDP driver reading `.hat-canvas`'s `getBoundingClientRect()` — the element that actually carries the wing pixels, not `.hat`'s own box — against each site's next sibling or table cell), against a fixture with a genuinely winged rank (Diamond II), not by eye. |
| Rank UI (badge/banner/table/route medals) | `ui/components/ranks.js` (`RankBanner`, rendering a `RankIcon` inside its own `.rank-icon-slot rank-banner-icon` wrapper — see the Rank icon SPACING row above and the Rank icon STYLE row below for the registry + dispatcher — + `RANK_MODE_OPTIONS`) + `standards.js` (collapsible editable table; columns = store ∪ section strategies, ordered **SLOWEST on the left, FASTEST on the right** by `ui/ladderorder.js::slowestFirst` — the table is a PATH, read bottom-left to top-right as you improve (user, 2026-08-03: *"they would start from the slowest strat and ranking (bottom left)... so as they get better, they'd move from left to right"*; *"This should be for ALL rank standards now and forever"*). A column's key is its Mario cutoff, else its own fastest defined cutoff (a ladder may skip tiers, and dropping such a strategy to the edge would misplace a genuinely quick one), else the far LEFT for a strategy with no times at all — unproven rather than slow, and the left edge is where a run starts. Ties keep arrival order so a new strategy slots in instead of reshuffling its neighbours, and `Infinity - Infinity` is guarded because a comparator returning NaN has undefined results rather than merely wrong ones. On a 100-coin star the sort runs WITHIN each exit-star band and then orders the bands by their own fastest column, so the progression still reads left-to-right without a column leaving its heading. Rendering is pinned separately from the sort (`tests/test_ui_hundred_coin_render.py`) — a correct sort feeding a table that draws in some other order is exactly what the unit test cannot see; BANDED by exit-star variant on a 100-coin star — a `colspan`'d `.std-variant` header row over each variant's columns, with the LEAF in the column head, ordered so a column always sits under its own heading; bands come from the payload's `strategy_groups` and are empty for every ordinary entity, which renders exactly as before (spec 2026-08-03-hundred-coin-exit-variants); every cutoff prints in the Usamune notation `fmtSeconds` owns (`1'21"32`, `23"00` under a minute) rather than raw seconds — user, 2026-08-03: *"that matches the format we actually display in the practice log"*; the EDIT-mode input still takes plain seconds, which is the stored unit and what the strategy modal's ladder already asks for; × is dual-meaning — on a SEEDED strat clears its standards, on a CUSTOM strat DELETES via `?purge=true` with confirm; each cutoff time links to its tier's example video, Edit-mode ▶ per-cell override; the tier-label cell backgrounds from `capGradient(rank) || rankColor(rank)` (Rank icon STYLE row below) — a real base→spots→base gradient for the two patterned tiers rather than a flat fill, since a flat fill is a lie for a cap that's actually two-tone (addendum 2, 2026-07-25; a plain white Toad row was the live complaint); cell text and the strategy-modal ladder are `capName(rank)`, never the raw tier key — the xcams bridge is the one deliberate exception, `title="{capName(rank)} · {rank} on xcams"` (e.g. "Waluigi · Gold on xcams"), so the external site's own vocabulary stays findable; `markerPosition` draws a "you are here" mark BETWEEN two ladder rows — your time is essentially never AT a cutoff, so it is a 0..1 fraction of the gap the current grading basis falls in, interpolated against the ACTIVE strategy's OWN ladder rather than the entity's best-possible one so the bracketed cutoffs can never disagree with the rows on screen; the score shown next to it is never recomputed in JS, it rides `sectionRank.score` from `tracking/views.py`'s `_section_banner`/`ranks/scoring.py::score_for`); wired into practice.js (section banner + attempt medals), progress.js (medal nodes), routes.js + RouteFocus (per-step medals + route avg); banner "avg of N" basis line + mode-aware unranked sentinel — **full detail below: [How full a rank bar is DRAWN (and the one bar that is never anchored)](#how-full-a-rank-bar-is-drawn-and-the-one-bar-that-is-never-anchored)** |
| Rank icon STYLE registry (hat + medal, room for more) | `ui/components/rankicon.js` — **full detail below: [Rank icon STYLE registry (hat + medal, room for more)](#rank-icon-style-registry-hat-medal-room-for-more)** |
| Route rank card (`RouteRankCard`) + points conversion | `ui/components/marelo.js` — **slot 2 of the `.context-bar` grid** since 2026-07-26, where the deleted practice-target card used to sit — NOT the Rank tab, so it is visible from every tab, and no longer a `.marelo-row` of its own. Its height fills a grid cell sized by `.context-bar`'s min-height and its 54px siblings, so a rank-up cannot reflow the OBS capture. **Rebuilt 2026-07-28** on the live report "the M 25.6 and C 16% feels like worthless AI slop information to me. It should just be clear that this is the OVERALL RANKING FOR THE ROUTE THAT I'M PRACTICING. Maybe we can combine the 'practice plan' card with this rank display card to create a route rank card?": (a) it IS the route picker now — a `CardSelect` from `components/contextselect.js` stretched over the whole card, so it is one hit target like its three siblings, and the Rank tab is reached from the nav rail instead; (b) the mastery/coverage split line is DELETED from the face (both keep real meters on the Rank tab and the card's own `title`), and the freed line carries the scope name; (c) the label is scope-aware — `"Route"` / `"Overall"`, shortened from "Route rank"/"Overall rank" because `.context-label` is opted into `tools/responsive_probe.js`'s never-truncate set and the longer strings broke it; (d) it **never renders `null`** — it hosts the picker, and a control cannot wait for a rating to arrive, which retired `.marelo-slot:empty`. **The label derives from `marelo.scope_id`, never from the client's `activeRouteId`** — those are two different sources and they disagreed: any client whose localStorage had never been written labelled a route rating "Overall" directly above "16 Star - LBLJ (Standard)" (2026-07-28, caught by render). `rank`/`interactive`/`tune` are props for ONE caller, `components/marelocelebrate.js`, which renders this same component as the thing that flies to the centre of the screen — a lookalike would drift from it the first time the card changed. Being column-width rather than content-width, its text ellipsises: points print BEFORE the scope label so a narrow column drops "(Standard)" and not the number, and the track drops out on an `@container` query against the slot, rendering a `RankIcon` with `division` set (see the Rank icon STYLE row above — the deleted `Crest`'s "a crest not a medal on purpose" reasoning stopped being true once Crest had already spread to four per-entity Rank-tab sites; the real distinction was always DATA, whether `division` is passed, not shape). `division_progress` (how far into the current division the bar's fill sits) is computed server-side (`ranks/scopes.py::_division_progress` — it owns the band edges) and is never re-derived here. The track's fill AND the score number both animate through `useTween` (task F2, 2026-07-25) from the previous fetch's value — never appear at the new one. `toPoints`/`fmtPoints` (task C, 2026-07-25-marelo-legibility) are THE x100 display multiplier every points-showing surface imports (header bar, Rank tab card + breakdown) — the server stays 0-100 canonical (`marelo`, `gain`, `next_division_at`, entity `score`); no component multiplies on its own, so two surfaces can never round differently and disagree |
| Rank tab (scope chips / picker / card / coverage strip / chart / breakdown) | `ui/components/rankpage.js` — **full detail below: [Rank tab (scope chips / picker / card / coverage strip / chart / breakdown)](#rank-tab-scope-chips-picker-card-coverage-strip-chart-breakdown)** |

Celebrations, the level-up climb, and the tuning inspector moved to `.claude/rules/ui-climb.md` (2026-07-28) — it loads when you open one of their files.


## How full a rank bar is DRAWN (and the one bar that is never anchored)

**The bar's own scale is `caps.js::barFill(tier, division, fill)`** (2026-07-29), not the raw `fill`: every rank draws `0.5 + 0.5 × fill` so the bar starts HALF FULL, and only the ladder FLOOR (position 0, Capless V — where a strategy nobody has practiced grades) draws its true fill and so reads as empty. User: "All rank displays… should start from the MIDDLE OF THE BAR… The intention is to anchor the user towards feeling like they ALWAYS are making progress to the next rank", with the one carve-out "we've literally never practiced this thing, so it should be empty. Once we level up to Capless 4, it should start at least from the middle, hence forth for the remainder of the ranks." The floor test is `rankPosition(tier, division, 0) === 0`, never a `tier === "Iron"` literal, so a registry swap cannot strand it. **`ui/rankclimb.js` converts ONCE, at the plan's boundary** (`targetBar`/`startBar` — `buildClimbPlan` is handed `barFill`-derived `fromFill`/`toFill`), so every bar value inside `climbplan.js` is a DRAWN width and `useRankClimb` hands out `climb.bar`, already anchored. Round 1 converted at the two call sites instead and was wrong in exactly one place, which is the whole reason this is written down: the plan builds the closing sweep as `arrive: barFrom 0 → barTo`, and with raw fills going in that `0` meant "the bottom of this division", which the anchoring then painted HALF FULL — "when we fill up the meter on the final beat of the animation, it STARTS AT 50% visually, which is wrong. It should START AT 0% visually, and move to the destination %… but ALWAYS END PAST 50%, and should animate through that lerp" (user, 2026-07-29). With drawn widths going in, `0` means EMPTY and `barTo` is the destination's anchored width, so the sweep is right by construction — the same shape as climbplan's own pin (`barFrom === barTo === 1` instead of a rule). Two consequences worth knowing: a sweep's DURATION now matches the distance it actually travels on screen (`barSweepMs` scales with `|barTo − barFrom|`), and the floor→next-rank seam is continuous only because every rank at full draws 1.0, floor included (`test_a_finished_division_always_draws_a_full_bar`). Verified by frame-sampling a live Capless 3 → Toad 3 climb (`uilab.trace`): resting 0.717 → pinned 1.0 through the rank ticks → sweep restarting at **0.000**, easing to **0.681**, at rest both ends. DISPLAY only: the progress track's `title` reports the honest within-division percentage, taken from the rank being LANDED ON (`graded.fill`) so it is a settled sentence rather than one ticking every frame. Two consumers, `ranks.js` (`climb.bar`) and `marelo.js` (`climb.bar` at rest; its route SWAP still calls `barFill` itself, because `routeswap.js` snapshots the raw payload, and it lerps the two DRAWN widths — only one endpoint can be the floor, and anchoring after the lerp would jump the bar on the exchange frame). **NOT applied to `LadderBar`, and that is a RULING rather than an oversight** — "the RANK PAGE bar should NOT be a lie. It should truly show your exact position in the entire ecosystem of tiers. That is deliberate" (user, 2026-07-29, asked directly). The plausible failure there is not forgetting the anchoring rule but APPLYING it: anchoring every band lights all eight tiers you have not reached to half, the exact inverse of the round-10 complaint that shipped the current treatment; anchoring only the band you stand in desynchronises the fill from the YOU marker, which lives in the SAME element and coordinate space. `tests/test_ui_rank_bar.py::test_the_rank_tab_ladder_is_never_anchored` is an INVERSE guard on both expressions, and fails just as loudly when its pattern stops matching (a rename) as when the ladder gets anchored — mutation-proved both ways. Also not applied to the Rank tab's Mastery meter, a 0-100 mean rather than a rank. Guarded by `tests/test_ui_rank_bar.py` — the law in node against caps.js, plus a scan that resolves every `width:${…}` expression through same-file bindings and fails if one reaches a raw `fill`, both mutation-proved — and a `tests/test_single_source.py` row on `FILL_ANCHOR`

## Rank icon STYLE registry (hat + medal, room for more)

`ui/components/rankicon.js` — `RankIcon({tier, division=null, size=18,
title=null, flap=false, foldWings=0})` is the DISPATCHER every rank-icon call
site renders (task 8, 2026-07-26-mario-cap-rank-icons, on user feedback
wanting a hat/medal toggle plus room to keep adding styles while
experimenting: the seventeen sites that used to import `Hat` directly now
import `RankIcon` instead — same prop surface, a mechanical swap). It resolves
the active style out of `ICON_STYLES` (`{key: {label, render}}` — `hat` FIRST
and default, its default-ness load-bearing per the user) and delegates; a
style that doesn't understand a prop (a medal has no wings) simply never reads
it, no error. A style owns only how a rank is DRAWN, never which rank it is or
what it's called — name/colour/pattern stay in `caps.js::CAP`, so the medal
style renders a Waluigi-purple disc labelled Waluigi, not a return to the
pre-cap palette. The active style is a client display preference shaped
exactly like `starIcons` (localStorage `sm64.rankIcons`, default `"hat"`,
header.js settings-drawer control, `t.rankIcons`/`pickRankIcons` in store.js)
but not plumbed the same way: most of the seventeen call sites (`LadderBar`,
`ScopeChips`, `RankBanner`, `MareloBar`, `PracticeCell`, celebrate.js's three
overlays) have no `t` in scope at all, so the value lives in ONE module-level
slot inside rankicon.js (`setRankIconStyle` persists + notifies; a small
subscribe hook re-renders every mounted `RankIcon`) and store.js's own state
is a thin proxy over that same pair, purely so header.js's `<select>` has the
familiar shape.
`tests/test_ui_caps.py::test_no_call_site_imports_a_style_renderer_directly`
(a source scan allowlisting only `rankicon.js`) is what keeps "adding a style
touches no call site" true rather than aspirational, and
`test_hat_is_first_and_every_icon_style_has_a_renderer` pins the registry's
own shape. `tools/hat_sheet.py`'s contact sheet renders every registered style
side by side at 13/30/96px so a new style is judged against the others, not
alone. | `ui/components/hat.js` — `Hat(...)`, the `hat` style
(2026-07-25-mario-cap-rank-icons: seventeen call sites converted from the
deleted `Medal`/`Crest`, `.marelo-crest` CSS removed).
`ui/components/medal.js` — `Medal(...)`, the `medal` style: a disc in
`rankColor(tier)` showing the same `divisionDigit` the hat style shows (shared
`DETAIL_MIN_SIZE` floor, caps.js, so both styles switch from digit to
silhouette/star at the same size) or a ★ below that floor / with no division;
foreground ink is derived from the tier colour's own relative luminance rather
than a hardcoded per-tier table (the deleted `FG` map, `ranks.js@6ec7f5b`, was
exactly that, and would already be stale after the fix wave moved two of the
nine colours). Palette/name/pattern/treatment/glyph + the sprite geometry
(`CANVAS`/`CAP_BOX`/`PATCH_BOX`, measured off the raw PSD exports) live in
`ui/components/caps.js::CAP` — THE tier registry, import-free and
node-testable like `ui/entities.js`; swapping a tier is one line (replaced the
deleted Python `ranks/standards.py::RANK_COLORS` mirror,
`.claude/rules/ranks.md`). `capName()`/`divisionDigit()` are the ONLY display
conversions — storage stays Roman/keyed everywhere (`DIVISION_NUMERALS`,
`progression_key`, watermarks); `tests/test_ui_cap_names.py` guards every
template literal in `ui/` against printing a bare tier key (exceptions: a
`tier=`/`division=`/`rank=`/`banner=`/`sectionRank=` prop handoff to
`Hat`/`PracticeCell`/`RankBanner`/`standards.js` (whole-object handoffs the
component reads internally, never prints raw), and the xcams-bridge text).
`Hat`'s own `title` defaults to the cap name + division digit (or "Unranked"
with no tier) so no call site has to remember one (final review I2, 2026-07-25
— all seventeen production call sites omitted it); an explicit `title` still
wins. Calling `Hat` with `tier == null` is only safe because of that default —
the two AGGREGATE surfaces that used to call it unconditionally (MareloBar,
the Rank tab card) now gate it behind `tier ? <Hat .../> : "–"` instead,
matching `PracticeCell`'s own "–" sentinel for the same state (final review I5
— a Hat drawn with no tier used to render a plain grey cap, which reads as an
unfamiliar tier rather than as none). Whether a `Hat` draws a numeral + wings
is a DATA rule ALONE (`division != null`), with NO size floor (correction,
addendum, task 8, 2026-07-26 — the user rejected an earlier `size >=
DETAIL_MIN_SIZE` gate outright: "if we're using the cap system, we must be
using the wing system," every cap, every size): a division draws its numeral
and wings at 13px exactly as it does at 96px. `division == null` still means
neither draws — a silhouette-only call site (no division passed) must never
draw an empty sign field, which is what keeps the tier-only ladder-scale marks
clean. `DETAIL_MIN_SIZE` survives only as a size threshold backing two purely
visual, content-preserving tunings: the Capless outline ring's fill needs more
opacity below it (a thin ring can't survive downscaling on its own), and the
glyph claims a larger share of the sign field below it (`glyphFraction` — more
legible with nothing else competing for those pixels; unclamped above the
threshold, so a 30px+ caller sees exactly the prior, already-verified look).
`wingTiers(tier, division)` gates wing COUNT on division, not tier — division
V (bottom of any tier) wears none, division I wears all four — with ONE
exception: Capless (Iron) wears NO wings at ANY division (correction,
addendum, task 8, 2026-07-26, overriding an earlier "wings everywhere"
instruction) — Capless means you have no cap, and wings are a thing a cap
earns; isolated entirely inside `wingTiers` itself (caps.js), never
special-cased in hat.js/medal.js/celebrate.js. Capless's own ring (the
`outline` treatment) is DOTTED, not solid (addendum, task 8, 2026-07-26 —
dashed/dotted reads as "you don't have this yet," a solid ring just reads as a
thin cap): `index.html`'s `.hat .fill.dotted` intersects the existing
`cap_outline.png` mask with a SECOND mask-image, a diagonal
`repeating-linear-gradient` whose tile size is set in `%` (`mask-size`), so
the dash period scales with the icon instead of being baked at one resolution
— a 96px and a 13px cap dash proportionally the same, verified by render at
all three sizes. `mask-composite: intersect` (`-webkit-mask-composite:
source-in` for the legacy prefixed form) punches the gaps in without touching
`--art` or the shared `.fill`/`.shade` pair every OTHER tinted layer depends
on — the dim under-fill beneath the ring stays SOLID on purpose (it is what
keeps Capless findable at 13px, and dotting it too would remove the one thing
carrying that job). At 13px the individual dashes are below legible pixel
resolution and blur into a soft rather than crisp edge — not a regression from
the prior solid ring, which the code's own comment already conceded was "too
thin to survive downscaling" at that size; the dim fill was always the thing
carrying legibility there, not the ring. `Hat`'s outer box is exactly the
cap's own footprint, BOTH axes — `size`px tall and `size ×
(CAP_BOX.width/CAP_BOX.height) × (CANVAS.width/CANVAS.height)`px wide
(≈1.63×`size`), matching every fixed-height card `Medal`/`Crest` used to
occupy; the sprite CANVAS is both taller AND wider (`CAP_BOX.height` ≈0.8,
holding the wingspan above/beside the cap) and renders in an inner
`.hat-canvas` shifted up AND left to align — sizing the OUTER box to the
canvas instead grew every caller's row ~6px on height (fix round 1,
2026-07-25) and left every caller ~45% wider than its cap on width, unfixed
until round 2 (final review I1, 2026-07-25: round 1's own argument — the box
matches what `Medal`/`Crest` occupied — was axis-neutral and had simply never
been applied to width). Wings deliberately spill outside `.hat` (`overflow:
visible` — never give `.hat` itself `overflow: hidden`) so a division change
can't resize a row. Two CSS traps in `index.html`'s `.hat` block, both pinned
by `tests/test_ui_caps.py`: a tinted layer's `.fill` (mask) and `.shade`
(multiply) must read the SAME `--art` PNG or the page background leaks into
the multiply — enforced STRUCTURALLY on the JS side too (final review I4,
2026-07-25): every `.fill`/`.shade` pair (the cap, each wing side) is built by
hat.js's `tintedPair(stem, color, extraClass)` helper, which resolves
`art(stem)` exactly ONCE and reuses it for both layers, so there is nowhere to
hand them different files without editing that one function;
`test_the_mask_and_the_shade_are_built_from_one_helper_call` asserts the
structure, not just the CSS text (the CSS-only version cannot see a JS-side
divergence and stayed green when it shipped one); `.hat .glyph` needs TWO
classes because `.hat i` (class+element) outranks a bare `.glyph` class, and
inside that rule `inset: auto` must precede `left`/`top` since `inset` is
their shorthand and resets them if declared after. The palette-distinctness
guard (`test_every_pair_of_tiers_is_visually_distinct`) checks EVERY pair, not
adjacent ones — `rank-ladder-scale` draws all nine medals in one 13px row, so
any two tiers can end up side by side; the 185-redmean threshold sits just
above the Iron/Silver pair that shipped as a real bug (168). The guard is
PATTERN-AWARE (addendum 2, 2026-07-25 / whole-branch review M2): comparing
base colour alone measures an icon the two patterned tiers (Toadsworth, Toad)
do not render, since both are two-tone — where BOTH tiers in a pair carry a
`pattern`, `combined_distance` (tests/test_ui_caps.py) combines base and
pattern-colour distance in quadrature rather than requiring either alone to
clear the floor (Toad/Toadsworth: 135.2 base + 171.1 pattern -> 218.0
combined); a pair with matching pattern colours still collapses to base alone,
so a patterned pair is never waved through merely for having spots.
`accentColor(tier)`/`capGradient(tier)` (caps.js) expose this two-tone
identity to a LARGE flat-colour surface (the standards table row, the ladder
band, both below) as a real gradient — `capGradient` is null for a flat tier,
so a caller always falls back to `rankColor(tier)` unconditionally; small
marks (13px ladder dots, chart gridlines, rank-up dots) stay on the flat base
colour on purpose, since a gradient in a 4px dot is mud. `flap=true`
(celebrate.js's three call sites only, see the Celebration overlays row) adds
`.hat-flap`, whose keyframes rotate the `wing-l`/`wing-r` layers about pivots
measured off each wing sprite's own alpha bbox (the edge nearest the cap is
fixed across tiers, only the tip grows), gated under `prefers-reduced-motion:
no-preference` like `.starholder`. `foldWings` (task 10, addendum, 2026-07-25)
is `flap`'s one-shot counterpart, set only by `TierRankUp`'s own fill->flip
boundary tick: it decouples the WING COUNT from the shown `division` for
exactly that one render, so the outgoing wings can still be drawn (and folded
away with `.hat-fold`'s keyframes, same pivots) instead of the division swap
dropping them to zero with nothing shown in between — see the Celebration
overlays row for the full beat-by-beat

## Rank tab (scope chips / picker / card / coverage strip / chart / breakdown)

`ui/components/rankpage.js` — ONE view under three scope kinds (Overall /
route / course) instead of three near-duplicate pages; the scope picker
defaults to `GET /api/marelo/scopes`' `active` (the focus route — spec section
3.4) until the user deliberately browses elsewhere. `t.mareloRev` (bumped by
store.js on every `REFRESH_ON` event, not just a scope change) is a fetch
dependency, so the tab does not go stale while left open during play. A scope
switch clears the OLD scope's card/chart/breakdown UP FRONT, so a `404` on the
NEW scope can never leave stale data sitting under the new scope's label.
`Breakdown` defaults to route order for a route scope, else
biggest-gain-first; the breakdown's Score/Gain columns render in POINTS (a
x100 display multiplier, `marelo.js::toPoints`/`fmtPoints` — the server stays
0-100 canonical, see below) and a "Next rank" column
(`entity.next_tier`/`next_division`, server-computed via
`ranks/scoring.division_progress` against the entity's OWN ladder in
`server/ranks_api.py::_score_scope` — never re-derived in JS): target-only,
one format for every row (`"→ Platinum IV"`, `"→ Gold"` for an
unpracticed/excluded entity, `"Maxed"` at the ceiling — never the FROM
tier/division too, which the adjacent Hat column already shows).
`HistoryChart`'s tier gridlines mirror `ranks/scoring.SCORE_ANCHORS` as a
constant-table (not an algorithm — same convention as ranks.js/format.js); its
two axes follow different rules (round 6, 2026-07-25) — X is wheel-zoomed,
anchored at the NEWEST point, defaulting to the whole journey, and the line is
clipped rather than filtered so a narrow window still shows the curve passing
through it; Y eases from the WHOLE ladder (every higher tier on screen, the
default) toward `autoZoomDomain` over the visible points as you zoom in, which
is what makes one jump legible. Tier labels resolve top-down so a crowded top
of the ladder drops "Grandmaster" and keeps "Mario". Rank-up dots come from
`rankUpKind` — tier change = big dot, division change = small, both in
`rankColor(tier)` — and require the rating to have RISEN, mirroring
`scopes.celebration_delta`; `onWheel` uses the FUNCTIONAL `setZoom` so a burst
of wheel events in one frame composes (pinned by tests/test_ui_rank_chart.py).
The card's rating number and the Mastery meter both ride `useTween` (Coverage
lost its tween with its bar in round 9 — its change is a tile lighting up, not
a length growing); Mastery stays 0-100 on purpose (a mean score, not a rating
— converting it would imply a fourth scale, commented at the call site) and,
since round 4, renders as a plain `.rank-progress-track` fill rather than a
ladder — see below for why it moved off the ladder entirely. `ScopeChips`
(task D.1, op.gg-style "every season's tier at once") reads `GET
/api/marelo/summary` — reuse `_score_scope`'s side-effect-free path ONLY
(never `_build_marelo`, so a chip fetch can't seed/sync/lower a celebration
watermark for a scope the user hasn't opened); clicking a chip calls the SAME
`setScopeId` the `<select>` uses, so the two controls can never disagree about
the active scope. The Mastery bar is a plain `.rank-progress-track` meter
(round 4 — one of the two factors MARELO is made of, not a rank of its own;
`bandForScore`, the old Mastery-only full-table lookup, is gone). The RANK
ladder (`LadderBar`, since round 4 plotting MARELO's `tier`/`division`/tweened
score — never Mastery, a real live-caught mismatch: "card said Capless 5, the
ladder's marker sat in Toad territory") is its own element beside the main
card's rank display. Round 5 (addendum, task 8, 2026-07-26) replaced the
PERMANENT 45-icon showcase strip rounds 2-4 built with a HOVER-TO-EXPAND
interaction, because the static strip turned out to be unsatisfiable against
the user's own requirement ("the symbols for each subdivision should ONLY EVER
appear above the exact line/pip for that subdivision... otherwise, this is
meaningless"): Mario/Grandmaster are 5 score points wide each
(`ranks/scoring.SCORE_ANCHORS`), so their five division pips sit ~1% of the
bar apart at rest — five icons there would overlap by an order of magnitude no
matter how the strip was laid out. Equal-width groups (round 3) broke pip
alignment to fix icon legibility; score-proportional groups (round 2) broke
icon legibility to fix pip alignment; no STATIC layout satisfies both. Deleted
entirely, no flag left behind: `.rank-ladder-scale`,
`.rank-ladder-tier-group`, `.rank-division-row`, `.rank-division-mark`,
`.rank-ladder-tier-strip`, `is-floor`/`is-mine`, the `@container` collapse
rule, `DIVISION_MARK_SIZE`, `LADDER_STEPS`, `bandStops`/`BAND_GRADIENT`. The
bar is now nine `.rank-band` flex items, one per tier, `flex-grow: band.to -
band.from` (the tier's own SCORE width, `ANCHORS`/`TIER_BANDS`) so the resting
layout is proportionally truthful BY CONSTRUCTION — Toadsworth (Silver) is 20
of the 100 score points, Metal (Grandmaster) and Mario are 5 each, a 4x
spread, because five points of real skill is far harder to gain at the top
than at the bottom; the bar isn't distorting anything by giving Mario a sliver
of width, it's showing that truth. Each band paints its own
dim-base/bright-fill tint (`capGradient(tier) || rankColor(tier)`) instead of
one continuous gradient image. Round 10 (2026-07-27 — the user: "the bar is
incorrectly DIM for the section of the bar to the left... all of the bars,
across all ranks from 0...the users current rank, should be lit up"): the fill
width was already right (Capless's band measured #735648 in his own
screenshot, i.e. its tint at FULL opacity), but "reached" was carried ONLY by
opacity of the tier's own colour — a RELATIVE signal that reads inside the one
partially filled band and nowhere else, since a fully reached band has no dim
half beside it and gets judged against NEIGHBOURS in other hues. Capless is
the darkest cap in the registry and the first band anyone clears, so the first
thing a player earns was the least likely to look earned: 1.84x the luminance
of the brightest band he had NOT reached. The fix is `.rank-band-fill::after`,
a tier-INDEPENDENT white gloss over the reached fill — an ABSOLUTE lift, so a
lit Capless differs from a lit Toad in hue, not in whether it looks switched
on; a treatment derived from `--band-tint` scales with the very thing that
made the dark tiers unreadable and cannot fix them however hard it is tuned.
**`.rank-band-base` is NOT the lever, and round 1 spent a round proving it**:
it also dropped the unreached bands to `opacity: .14; filter: saturate(.55)`
(borrowing `.entity-tile.is-unpracticed`'s dim+desaturate) and drew the
opposite report the same day — "the dimmed bars should be way less dimmed...
they all basically look black. I think we should at least show a little bit of
color for each of them!!" A tile is a discrete have/have-not badge; these nine
bands are a MAP of the ladder, and a tier you have not reached is what you are
climbing toward — it has to stay findable and worth aiming at. The base is
back at its original `.26` with no filter, and the gloss absorbed the whole
job (average alpha .145 → .212). There is a ceiling on THAT too, found by
rendering every tier lit at once: past ~.22 average the gloss milks the hues
it lifts (Waluigi purple → lavender, Mario red → pink), which is the same
crime on the other side of the bar. Pinned by `tests/test_ui_rank_ladder.py`,
which composites both declared treatments over the ladder's own backdrop for
every tier (both ends of the two patterned gradients — they put the darkest
lit pixel next to the brightest unlit one, and bind every time) and is
**two-sided**: dimmest-lit ÷ brightest-unreached ≥ 2.4 (1.53 drew report one,
shipped 3.05) AND unreached-vs-plate redmean ≥ 40 (30.8 drew report two,
shipped 58.3). Both floors are anchored to a state a real person rejected
rather than to taste, and neither names a mechanism. Its compositing model is
validated against CDP-measured pixels in its own docstring, and every figure
quoted was executed against a mutated copy rather than estimated. NOT touched,
and worth knowing: a partially filled band paints `--band-tint` across the
FILL's own width, so a 13%-wide fill shows the whole white→red→white Toad
gradient compressed into that sliver rather than the left 13% of it — the user
called that sliver "perfect", so it stayed. Nothing shows permanently except
the user's OWN rank: one icon (`CURRENT_RANK_ICON_SIZE`, matching MareloBar's
own size), floating above its exact score position, bobbing and flapping if it
has wings; YOU stays text alone above it with enough gap that the bigger icon
can never grow into it, and hides while any band is expanded
(`.rank-ladder:has(.rank-band:hover, .rank-band:focus-within)`, the same
`:has()` pattern header.js's context-select focus ring already uses) since
YOU's position is only true in the resting proportional layout. Hovering OR
focusing any pip inside a tier's band (`.rank-band-pip`, a focusable 9px hit
box marking where a division BEGINS, carrying the SAME per-division tooltip
the old strip's icons did) reveals that tier's five division icons. Round 6
(addendum, task 8, 2026-07-26 — the user: "everything should be shifted over
to the right... each of the symbols are in the middle of their subdivision
section"): each icon centres on its division's own SLOT midpoint now, NOT on
its pip — a pip marks a boundary, a division is the span between one pip and
the next, and an icon labels the span, so sitting it on the boundary line made
it ambiguous which of the two neighbouring divisions it named.
`.rank-band-pip` (at `index/5 * 100%`, unchanged) and `.rank-band-icon` (at
`(index+0.5)/5 * 100%`, the slot's own midpoint) are SIBLINGS — both direct
children of `.rank-band`, not one nested in the other, since nesting the icon
inside the pip would position it relative to the pip's own 9px box rather than
the band's full width these two different percentages both need to share. The
alignment is still a STRUCTURAL guarantee, not per-band tuning: both
percentages are computed from the SAME `index` in one place (`rankpage.js`),
so they can never drift out of sync even though they no longer share one
literal position — pinned by a CDP-driven render check that recomputes each
icon's EXPECTED centre independently from the band's own live
`getBoundingClientRect()` (not copied from the code under test) and confirms
every rendered icon lands there, across all nine bands (task 8's report has
the numbers — max deviation 0.0125px, floating-point noise). Expansion is
`max(230px, calc(var(--band-weight) * 1%))` — the `max()` stops an
already-wide tier like Silver from ever shrinking on "expansion" — and the
other eight bands compress to absorb it, proportionally among themselves
(`min-width: 0` is what lets them shrink that far). `:hover, :focus-within` on
the SAME `.rank-band` rule is what makes this reachable without a mouse for
free — Tabbing onto a pip (real `tabindex="0"`, same pattern practice.js's
`.hint` spans use) puts the whole band in the exact state a mouse hover would,
no separate keyboard path to keep in sync; `:focus-visible` gets a visible
outline so that path is discoverable. The expand/collapse ACTUALLY ANIMATES,
round 8 (task 8, 2026-07-26 — the user: "it should animate from the starting
bar position to the expanded bar position... it should also animate back"):
`.rank-band` has exactly ONE `transition` declaration, covering
flex-grow/flex-basis/transform/box-shadow at one shared duration (.25s ease) —
before this round `:hover`/`:focus-within` declared its OWN narrower
`transition` (transform/box-shadow only), and since a rule's `transition` is
not additive across rules on the same element, the higher-specificity hover
rule WHOLESALE REPLACED the base rule's transition the instant a band matched
`:hover`, leaving flex-grow/flex-basis with no transition at all while hovered
— expansion snapped instantly, collapse animated fine (once `:hover` stops
matching, the base rule's transition — which DOES cover flex properties —
takes back over), an asymmetry only a frame-by-frame probe caught, not a
before/after screenshot. The eight compressing siblings and every
pip/icon/marker `left:%` position need no transition of their own: they were
never the problem (their own properties never change value), and once the ONE
genuinely-changing property (the hovered band's own flex-basis) actually
interpolates, the whole row naturally reflows around it every frame for free.
The YOU mark (icon + text + the `.rank-ladder-head` position line) no longer
hides during a hover, addendum round 6 (2026-07-26 — the user: "I would expect
my big symbol to still be visible, and for my exact progress inside the rank
to still be visible... when hovering over your rank's area"): round 5's hiding
rule was reasoned correctly ("YOU's position is a percentage of the WHOLE bar,
and expansion redistributes that axis") but fixed it the wrong way. The marker
is now a CHILD of the ONE band the user's score falls in (matched by `tier`,
not re-derived from the tweened `value` — the two can transiently disagree
mid-tween across a tier-up, and anchoring to the tier the card already names
is what keeps this ladder from repeating round 4's original bug), positioned
at that band's own local FRACTION (`(filled - band.from) / (band.to -
band.from)`, clamped to [0,1]) — the same coordinate space the pip/icon
alignment already uses, so it recomputes correctly whenever that band's width
changes instead of needing to hide. This also fixed a stacking bug for free:
the position line's `z-index: 2` used to compete against a hovered band's
`z-index: 3` inside the SAME parent and lose; nested inside its own band now,
it is compared only within that band's local stacking context, always above
that band's own content. Hovering the user's OWN band shows all three
(icon/YOU/line) survive expansion — verified by measurement (the marker's
fraction-within-its-band read identical at rest, with its own band expanded,
and with a DIFFERENT band expanded and this one compressed), not just by
screenshot. Round 7 (task 8, 2026-07-26 — the user: "your current icon is now
rendering behind the actual rank icon for that subdivision... there shouldn't
be duplicate symbols"): showing a showcase icon AND the marker for the SAME
division was drawing that division twice, not two different meanings sitting
near each other — the earlier "visibly distinct unless they coincide" framing
undersold it as a coincidence rather than naming the duplication. Fixed by
suppressing the showcase icon for the user's own division inside the user's
OWN band only (every other band still renders all five; PIPS are never
suppressed — all five always render, since a pip marks a boundary and carries
a tooltip regardless of its icon). The marker does NOT snap to the vacated
slot's midpoint — it stays at the true fraction (unchanged since round 6), so
it can visibly sit off-centre within the gap it fills; that is correct, since
it is showing where in the division the user actually is, not labelling the
division as a whole. `CURRENT_RANK_ICON_SIZE` (rankpage.js) dropped 34px→26px
and the expansion width (index.html) grew 230px→260px — both starting points
settled by render, once the marker no longer needed size alone to read as
"this one is you" (it keeps the label, the bob, the flap) and no longer has a
same-division neighbour to loom over. Verified across mid-band, division V,
division I, and Capless scenarios by measurement (own band always exactly 4
icons + 5 pips; the missing icon's slot always matches the user's own
division; the remaining icons' fractions identical to round 6's, confirming
they do not re-centre to fill the gap; every other band always exactly 5),
both icon styles. `prefers-reduced-motion: reduce` gets the expansion and the
icon reveal INSTANTLY (the end-state CSS is unconditional; only the
`transition` properties are gated, same contract every other animation in this
file follows). The breakdown's Next-rank column says "not practiced yet" for
an unscored entity rather than printing the server's Gold QUEST target as if
it were a next rank; that target moved to the Gain cell's tooltip. Coverage is
`CoverageStrip`, not a bar (round 9): every NON-EXCLUDED entity in
`data.entities` — already in scope order, route order for a route — as an
`EntityTile`, tier-ringed when practiced and dimmed+desaturated when not.
Filtering excluded rows is what keeps it honest: the server's `n` is the
non-excluded count, so tile count == n and lit count == practiced by
construction. Each tile's icon is `entityIconSrc(t, entity.key)` (see the
entity-icon-resolution row above), the SAME call the practice selector's cells
make — it derived a stem from the entity key ALONE until 2026-07-26, and a key
carries no start level, so every SEGMENT tile drew a plain gold star (live
report). Each tile also carries the banner's own hover **✎**, through
iconpicker.js's shared `useIconPicking` + `iconIdentityForKey` (user,
2026-07-26): this strip is the only surface listing every entity in a scope,
so it is the only place to repoint the art of a star in a course you are not
standing in. The ✎ stops propagation, so it never also expands the tile's
detail panel. Tiles are BUTTONS: clicking expands `EntityDetail` under the
strip (PB, rank, last 10 successful attempts, "Practice this") reading
`t.view.stars`/`t.view.segments` — the sections the store already holds, no
new endpoint — so it inherits the view's session/lifetime scope and
legitimately says "no attempts in the current view scope" for an entity
practiced in an older session. `.entity-tile` needs `flex: 0 0 auto`: a full
route's worth of tiles otherwise SHRINK to fit their row while the height
stays put, which is what "squished" art was. The rows wrapping these are
`.rank-factor` DIVs, never `<label>`s — a label forwards hover and click to
its first labelable descendant, so hovering the caption lit up the first tile.
Removed with this: the "Best in scope" top-12 card (task D.2) this strip made
redundant
