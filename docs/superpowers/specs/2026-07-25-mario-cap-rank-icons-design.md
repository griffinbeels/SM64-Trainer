# Mario caps as rank icons — one hat, nine tiers, five divisions

**Date:** 2026-07-25
**Status:** approved design, not yet implemented

## Problem

The rank system's visual vocabulary is generic. A tier is a coloured disc with
a ★ in it (`Medal`); a division is a coloured rounded square with a Roman
numeral in it (`Crest`). Neither says anything about the game being practised,
and the names underneath them — Grandmaster, Platinum, Diamond — are borrowed
from ladders that have nothing to do with Super Mario 64.

Replace the whole symbol layer with **Mario caps**. Each tier is a cap in that
tier's colour; each division within a tier is a digit inside the cap plus a
progressively larger pair of wings, so that the top division of the top tier is
literally the Wing Cap. Graph dots stay plain colour, as they are today.

## What exists today (verified, 2026-07-25)

- **Nine tiers**, hardest first: `Mario, Grandmaster, Master, Diamond,
  Platinum, Gold, Silver, Bronze, Iron` (`ranks/classify.py::RANK_NAMES`).
  These keys are **external data** — `tools/scrape_ranks.py` re-fetches them
  from xcams into `data/rank_standards.seed.json`, and `ranks/scoring.py`'s
  `SCORE_ANCHORS` are xcams' player bands verbatim. Renaming the keys would be
  reverted by the next scrape and would break every stored celebration
  watermark. **Cap names must therefore be display-only.**
- **Five divisions per tier**, `DIVISION_NUMERALS = ["V","IV","III","II","I"]`
  (`ranks/scoring.py`), index 0 at the bottom of the tier.
- **`RANK_COLORS` has no runtime consumer.** It is defined in
  `ranks/standards.py:11` and imported by exactly two files, both tests
  (`tests/test_ranks_standards.py:3`, `tests/test_ui_rank_chart.py:15`).
  Nothing in `server/`, `tracking/`, `stats/`, `ranks/` or `tools/` reads it.
  `tests/test_ui_rank_chart.py:87` exists solely to assert that the JS copy in
  `ui/components/ranks.js:10` equals the Python one. It is a declaration whose
  only purpose is to be mirrored into the place that actually renders.
  `docs/architecture.md:836` names the pair as the authority for tier colour.
- **Two icon components.** `Medal({rank, size})` takes a tier and no division;
  `Crest({tier, division, size})` takes both. `ui.md` records that Crest was
  deliberately *not* a medal so an aggregate would not read as "just another
  star's rank" — but `Crest` now renders for individual entities in four places
  on the Rank tab (`rankpage.js:471,510,598` and the entity detail), so that
  distinction has already eroded. The live distinction is **data**: does this
  thing have a division or not.
- **The Iron/Silver precedent.** `RANK_COLORS` carries a comment explaining
  that Iron moved from grey `#8a8a8a` to rust `#7c5347` because at 24px an Iron
  medal beside a Silver one was indistinguishable (live report 2026-07-25).
  `tests/test_ui_rank_chart.py:95-99` pins that outcome. Any new palette must
  answer to the same failure mode.
- **`stagebanner.js:34` imports `Medal` and never renders it** — dead since the
  cell was extracted into `practicecell.js`. Delete it in passing.

## Decisions

### 1. The ladder

Cap names are single words, because `RankBanner` prints the tier name plus the
division on one line inside `.objective-card`'s hard 122px height, and round 4
of 2026-07-25 dropped the word "Rank" from that row because 13 characters was
unaffordable at ~390px. Every cap name below is **shorter than the tier name it
replaces** — worst case WALUIGI (7) against the old worst GRANDMASTER (11).

| Tier key | Cap | Fill | Treatment |
|---|---|---|---|
| Mario | **Mario** | `#e23b3b` red | `M` glyph instead of a digit; warm glow |
| Grandmaster | **Metal** | `#78899c` steel | opaque, specular sweep |
| Master | **Vanish** | `#bfe6ff` pale | ~55% translucent, soft glow |
| Diamond | **Luigi** | `#3fbf5f` green | solid |
| Platinum | **Wario** | `#e8b21c` yellow | solid |
| Gold | **Waluigi** | `#8b4fc0` purple | solid |
| Silver | **Toadsworth** | `#d8ce80` khaki | large brown `#7a4f2a` spots |
| Bronze | **Toad** | `#f5f7f8` white | small red `#e0453f` spots |
| Iron | **Capless** | `#7c5347` rust | outline only, no fill |

Two adjacencies are deliberate risks, both mitigated by something other than
hue, which is only possible because the icon is composited at runtime rather
than shipped as a flat disc:

- **Toad above Capless, Toadsworth above Toad.** Two spotted mushroom caps in
  neighbouring tiers is the Iron/Silver failure mode exactly. Separated by a
  hue gap (cool white vs warm khaki), a value gap, and — most durably at 13px —
  a *shape* difference: several small spots against one large plus one partial.
- **Vanish directly above Metal.** Two light neutrals. Metal is pushed dark
  (`#78899c`) for a large value gap, and translucent-with-glow against
  opaque-with-specular separates them at the material level.

**These hex values are a starting point and may be wrong.** They were chosen on
paper. The contact sheet (below) is what decides them, and the adjacent-pair
guard is what keeps them honest afterwards.

### 2. Arabic numerals everywhere

The sign field on the cap is roughly 14px wide on a 28px crest. "III" is three
glyphs in that space. Roman numerals cannot be read there, so the hat shows
`5 4 3 2 1`, and the **text follows** — the banner reads `TOAD 3`. Storage keys
stay Roman (`DIVISION_NUMERALS`, `progression_key`, the celebration watermark);
this is a display mapping only, `divisionDigit(numeral)`.

Mario tier shows `M` rather than a digit, so within Mario tier the division is
signalled by wings alone. That is intentional and is an argument for keeping
wings per-division at least through the top tier.

### 3. Wings mark divisions, behind one knob

Division V has no wings; each division up adds one of four cumulative wing
chunks, so division I of any tier wears the full pair. This is Griffin's
intuition and ships as-is, but the rule is isolated in a single function:

```
wingTiers(tier, division) -> 0..4
```

Today it returns the division index for every tier. Reserving wings for the top
tier only is changing that one function to return non-zero for `Mario` alone.
Nothing else in the system knows the rule.

### 4. One component replaces both

`Hat({tier, division, size})` replaces `Medal` and `Crest`. It draws what it is
given:

- no `division` → silhouette in the tier colour, no numeral, no wings. This is
  exactly the information `Medal` carries today.
- `division` present and `size >= 22` → numeral and wings.
- `division` present and `size < 22` → silhouette only; the numeral would be
  sub-pixel.

The data clause is load-bearing, not a nicety: two of the eight `Medal` call
sites are already at 22px (`practice.js:147` attempt rows, `rankpage.js:165`
the ladder's YOU marker) and neither has a division, so a purely size-based
rule would draw an empty sign field there.

Collapsing the two components continues the round-2 decision of 2026-07-25 —
*one component rendered twice, never two components that happen to look
similar* — rather than contradicting it. Aggregates stay distinguishable from
entity medals by **carrying a numeral**, which no entity medal does; that is a
content difference rather than a shape difference, and it survives a redesign.

## Architecture

### The registry

One table, `ui/components/caps.js`, keyed by tier name, one line per tier:

```js
export const CAP = {
  Mario:       { name: "Mario",      color: "#e23b3b", treatment: "glow",  glyph: "M" },
  Grandmaster: { name: "Metal",      color: "#78899c", treatment: "metal" },
  Master:      { name: "Vanish",     color: "#bfe6ff", treatment: "translucent" },
  Diamond:     { name: "Luigi",      color: "#3fbf5f" },
  Platinum:    { name: "Wario",      color: "#e8b21c" },
  Gold:        { name: "Waluigi",    color: "#8b4fc0" },
  Silver:      { name: "Toadsworth", color: "#d8ce80", pattern: "spots_large", patternColor: "#7a4f2a" },
  Bronze:      { name: "Toad",       color: "#f5f7f8", pattern: "spots_small", patternColor: "#e0453f" },
  Iron:        { name: "Capless",    color: "#7c5347", treatment: "outline" },
};
```

Every field except `name` and `color` has a default, so an entry states only
what is unusual about that tier. `base` is a field too, defaulting to `"cap"`;
a future tier wanting a different silhouette is a new PNG and one word, not a
new code path.

**The tier colour moves here and the Python copy is deleted**, along with
`tests/test_ui_rank_chart.py`'s mirror assertion and
`tests/test_ranks_standards.py:15`. This is required by the swappability
requirement: with colour in Python and theming in JS, replacing Toadsworth with
Peach is three edits across two languages. With one table it is one line —

```js
  Silver: { name: "Peach", color: "#f19ec2" },
```

— and dropping `pattern` stops the spots rendering. `docs/architecture.md:836`
is rewritten to point here.

Two helpers ship in the same file: `capName(tier)` and
`divisionDigit(numeral)`. `RANK_NAMES` becomes `Object.keys(CAP)` so tier order
has one JS source; `ranks.js` re-exports it for existing importers.

### The layer model

Six layers, bottom to top. Each is a slot the registry fills, skips or
recolours:

1. **Wings** × `wingTiers(tier, division)` — behind the cap, tinted neutral,
   never the tier colour, so wings read as wings on every tier
2. **Cap body** — tinted `color`, or stroked-only when `treatment: "outline"`
3. **Pattern** — spots, tinted `patternColor`, clipped to the cap
4. **Patch** — the white sign field, never tinted
5. **Glyph** — live text in Super Mario 256: the division digit, or `M`
6. **Treatment** — specular sweep (Metal), glow (Vanish, Mario), else nothing

Below 22px, or with no division, only layers 2–3 render.

### Tinting, and the risk that could sink it

Photoshop's Hue slider is a true HSL rotation; CSS `filter: hue-rotate()` is a
fixed linear matrix that **cannot desaturate**. From a red master it can never
produce khaki Toadsworth, pale Vanish or rust Capless, and will not land on the
other hexes exactly either. The master is therefore **white**, and tint is:

`isolation: isolate` on the wrapper → a colour layer masked by the PNG's alpha
→ a `mix-blend-mode: multiply` layer of the same greyscale PNG on top. White
multiplies to the exact hex, the low-poly shading survives as darker shades of
it, the black outline stays black.

**`multiply` blends against its backdrop, and this app's backdrop is dark
navy.** Per the compositing spec an isolated group makes this correct, but that
is a claim about Chromium's compositor, not something to assert without a
render. Proving it is the **first implementation task**, before anything else
is built.

**Named fallback:** if the blend misbehaves in Chrome or WebView2, pre-multiply
each tier once into a `<canvas>` at load and cache the data URL — nine bitmaps
per part, no blend modes. Same registry, same assets, different renderer.

## Assets

Source PSD is already in the repo at `assets/hat_rank.psd` (untracked as of
this writing). Exports land in `src/sm64_events/ui/assets/hat/`. The whole
`ui/` tree is already bundled by `tools/build_exe.py:57`, so no build change is
needed.

| File | What it is |
|---|---|
| `cap.png` | Cap body alone — no patch, no M, no wings |
| `cap_outline.png` | The outline stroke of the cap, for Capless |
| `patch.png` | The white sign field alone |
| `wing1.png` … `wing4.png` | The four Tier layers, **incremental**, **left side only** |
| `spots_small.png` | Toad's spots, positioned on the cap — new art |
| `spots_large.png` | Toadsworth's spots — new art |

Export rules, in order of how badly getting them wrong hurts:

1. **The cap must be exported white, not red.** Desaturate, then push Levels
   until the brightest lit face is pure 255. If the brightest face is 90% grey,
   every tier renders 10% dark and Toad's white cap comes out grey. This one
   step decides whether the palette is faithful.
2. **One canvas, full document size for every layer** — Photoshop's
   *File → Export → Layers to Files* with **Trim Layers OFF** — so layers stack
   at `position:absolute; inset:0` with zero per-layer offsets. Canvas sized to
   the full tier-4 wingspan, ~1024px wide (largest on-screen use is a 96px
   crest).
3. **Left wing only, cap horizontally centred on the canvas.** The art is
   symmetric, so CSS mirrors it for the right side: half the files, and the two
   wings can flap independently, which one combined image cannot do. The mirror
   pivots on the canvas centre, hence the centring requirement.
4. The cap's bounding box within that canvas is recorded as **one constant** so
   the sub-22px sizes can zoom to the cap without a second export.

`SuperMario256.ttf` (16 KB, already installed on this machine) is copied to
`ui/assets/fonts/` with an `@font-face`. It is a fan font; it gets a line in
`assets/credit.md` alongside the course portraits and star icons.

## Surfaces

### Icons — mechanical once `Hat` exists

Eight `Medal` sites: `practice.js:147` (22), `practice.js:831` (16),
`practicecell.js:49` (16), `rankpage.js:160` (13), `rankpage.js:165` (22),
`rankpage.js:615` (14), `routes.js:214` (16), `routes.js:475` (18).

Eight `Crest` sites: `marelo.js:56` (34), `rankpage.js:471` (22),
`rankpage.js:510` (26), `rankpage.js:598` (28), `rankpage.js:756` (64),
`celebrate.js:119` (96), `celebrate.js:146` (40), `celebrate.js:210` (28).

### Text — every raw tier name routes through `capName()`

`ranks.js:25` (Medal title), `ranks.js:154` (banner name), `marelo.js:38`
(crest title), `marelo.js:58` (bar label), `rankpage.js:159` (ladder band
tooltip), `rankpage.js:179` (division line tooltip), `rankpage.js:222` (chart
point tooltip), `rankpage.js:389` (SVG gridline labels), `rankpage.js:434-435`
(Next-rank column), `rankpage.js:441` (Gain tooltip), `rankpage.js:543` (entity
tile title), `rankpage.js:758` (card heading), `standards.js:185` (standards
table rank column), `standards.js:218` (video modal description),
`stratmodal.js:148` (ladder chips), `celebrate.js:212` (celebration copy).

`standards.js` and `stratmodal.js` are where the **xcams bridge** matters: they
are the screens where cutoff times are entered per rank, and xcams calls those
ranks Gold and Silver. Both get a dual label — "Toadsworth · Silver on xcams".

### Colour-only — no work

Chart gridlines, rank-up dots, ladder bands, card washes and `progress.js:116`
graph dots all read `rankColor(tier)` and recolour themselves.

### Animation

The celebration's tier-up flip (`celebrate.js:68`) already slices `RANK_NAMES`
and turns the crest once per tier gained; it becomes watching the cap change
Toad → Toadsworth → Waluigi for free.

**Wing flap fires on the rank-up celebration only.** Constant idle flapping
across a screen of medals is motion noise, and this app runs on stream. It
honours `prefers-reduced-motion` the way `useTween` already does.

## The tool

`tools/hat_sheet.py` renders **all 9 tiers × 5 divisions at 13 / 22 / 96px**
onto one contact sheet, screenshots it headless, and kills its own server. This
is not an icon generator — runtime composition makes all 45 states from 10
files — it is the artifact where Toad beside Toadsworth either works or
obviously does not, and it is what gets looked at after every palette swap. If
a stream overlay ever needs real PNGs, that is an `--export` flag on the same
page.

## Guards

1. **Registry completeness.** `Object.keys(CAP)` equals `classify.RANK_NAMES`
   in order. This is the one cross-language mirror that survives: tier *order*
   is data, tier *colour* no longer is.
2. **Adjacent-pair separation.** Generalises the Iron-vs-Silver assertion at
   `tests/test_ui_rank_chart.py:95` to every adjacent pair in the ladder. This
   is the guard that protects the next Peach-style swap. The threshold is
   calibrated against the real palette, and the test is **probed in both
   directions** per the `test_the_guards_can_still_fail` norm — a guard that
   cannot fail is not one.
3. **No raw tier name printed.** Source scan over `ui/` on
   `strip_comments(source)` (`tests/source_scan.py`), allowlisting only the
   `CAP` table. Catches a call site rendering "Gold" in purple.
4. **Asset coverage both ways.** Every `base`/`pattern` named in `CAP` has a
   file on disk, and every file is named by something — the shape
   `tests/test_ui_empty_states.py` already uses for the cast art.
5. **`wingTiers` bounds.** Returns 0–4 and never more than the wing assets
   present.

Guards 1 and 2 replace the current mirror test.

## Verification

1. **The blend-mode probe first** — a throwaway page, headless screenshot,
   before anything else is built. If `multiply` misbehaves against the navy
   backdrop, the render model changes to pre-baked canvas bitmaps and the rest
   of the plan is written against that instead.
2. **The contact sheet**, eyeballed by Griffin, to settle the hexes.
3. **The real `index.html` served against captured fixtures** —
   `/api/session`, `/api/marelo` and friends, mutated to reach a Mario-tier
   state and a Capless state live data will not have (the recipe in auto-memory
   `verify-ui-effects-with-harness-page`). Reads off the live server are GETs
   and safe while Griffin plays; never start `python -m sm64_events.main` for
   this.
4. **A width continuum sweep, not sample points.** A cap is roughly 1.6:1 where
   a disc is 1:1, so at 24px inside `RankBanner` the icon is ~14px wider on a
   row that already dropped a word for space, inside a card with a hard fixed
   height. The shorter cap names should more than pay for it, but 1400/900/700
   all passed once while every width from ~1101 to ~1500px was broken. Gate on
   `@container` against `.practice-page`, not viewport width.
5. Any server started dies in the same session.

Nothing in `desktop/` changes — it is all `ui/`, so the GUI window inherits it
(rule 10). The hat is kind-agnostic and reaches stars and segments identically
through `PracticeCell` (rule 11).

## Out of scope

- Runtime-editable theming (an endpoint, storage, a settings UI). This codebase
  is edited by Claude; a JS object literal is already the fastest possible
  change path.
- Baked PNG export for stream overlays — a flag on `hat_sheet.py` if it is ever
  wanted.
- Idle or hover wing animation.
- Renaming the tier keys. They are scraped external data; see §What exists
  today.
