# Mario Cap Rank Icons Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace every rank symbol in the app with a Mario cap — tinted per tier, wings per division, an Arabic digit in the sign field — driven by one registry.

**Architecture:** Nine tier keys stay exactly as they are (they are scraped from xcams). A JS registry `ui/components/caps.js` maps each key to a cap name, fill colour and treatment, and becomes the single source of tier colour; the Python `RANK_COLORS` table is deleted. One `Hat` component replaces both `Medal` and `Crest`, composing layered PNGs: each tinted part is a mask-coloured layer under a `mix-blend-mode: multiply` greyscale layer built **from the same PNG**, which is what makes the tint exact and backdrop-independent.

**Tech Stack:** Preact + htm (vendored, no build step), plain CSS in `ui/index.html`, Pillow for the one-off asset derivation, headless Chrome for verification, pytest.

## Global Constraints

- **Tier keys never change.** `Mario, Grandmaster, Master, Diamond, Platinum, Gold, Silver, Bronze, Iron` are scraped by `tools/scrape_ranks.py`; renaming them is reverted by the next scrape and breaks stored celebration watermarks. Cap names are display-only.
- **Cap display names are one word.** `RankBanner` prints tier + division on one line inside `.objective-card`'s hard 122px height; round 4 of 2026-07-25 dropped the word "Rank" from that row for space.
- **Arabic digits everywhere.** The hat shows `5 4 3 2 1`; text reads `TOAD 3`. Storage stays Roman (`DIVISION_NUMERALS`, `progression_key`, watermark keys) — this is a display mapping only.
- **The mask and the shade must come from the same PNG.** Measured 2026-07-25: with both layers off one file the tint samples to the exact hex over navy, a light card, a gradient wash and transparency, with and without `isolation: isolate`. If they ever diverge, the page backdrop leaks into the blend.
- `uv run pytest -q` passes before any commit. New behaviour has tests.
- **Never start `python -m sm64_events.main`** for UI checks — the recorder lock is the only thing protecting a live recording. Use headless Chrome against static files or a fixture server, and kill anything you start in the same session.
- Verify UI by rendering, never by `node --check` plus unit tests alone.
- Never put a backtick inside an `` html`` `` template, including in comments.
- Commit messages explain WHY, in the style of `git log`.

## Measured constants

Every number below was measured from the real exports on 2026-07-25. **Re-derive rather than trust these if the PSD is re-exported.**

| Fact | Value |
|---|---|
| Export canvas | 1283 × 675, RGBA, all seven layers, nothing clipped on any edge |
| Cap bounding box | `(200, 131, 1083, 672)` → `left 15.5885% top 19.4074% width 68.8231% height 80.1481%` |
| Patch bounding box | `(458, 178, 825, 455)` → `left 35.6976% top 26.3704% width 28.6048% height 41.0370%` |
| Cap centring | left margin 196, right margin 196 — exactly centred, so a canvas-centre mirror is valid |
| Raw patch/spots grey | `(236,236,236)`, not white — must be normalised or every tint darkens ~7% |
| Cap value channel | V min 53, median 216, p95 250 — the shading is fully carried by HSV value |

## File Structure

- `src/sm64_events/ui/components/caps.js` — **new.** Import-free data + pure helpers: `CAP`, `RANK_NAMES`, `capName`, `divisionDigit`, `wingTiers`, `rankColor`, and the geometry constants. Import-free so node can unit-test it, mirroring how `ui/entities.js` relates to `entityicons.js`.
- `src/sm64_events/ui/components/hat.js` — **new.** The `Hat` preact component. Rendering only; every fact it needs comes from `caps.js`.
- `src/sm64_events/ui/assets/hat/*.png` — **new.** Nine derived, downscaled sprites.
- `src/sm64_events/ui/assets/fonts/SuperMario256.ttf` — **new.**
- `assets/hat_raw/*.png` — **new.** The seven Photoshop exports at full canvas, the input to the derivation script. Not shipped in `ui/`.
- `tools/build_hat_assets.py` — **new.** Reproducible raw → shipped derivation.
- `tools/hat_sheet.py` — **new.** Contact sheet of all 45 states at three sizes.
- `ui/components/ranks.js` — loses `RANK_COLORS`, `FG`, `Medal`; keeps `RankBanner`, `RANK_MODE_OPTIONS`; re-exports `RANK_NAMES`/`rankColor` from `caps.js` for existing importers.
- `ui/components/marelo.js` — loses `Crest`; keeps `MareloBar`, `toPoints`, `fmtPoints`, `fmtScore`.
- `ranks/standards.py` — loses `RANK_COLORS`.

---

### Task 1: The cap registry becomes the single source of tier colour

**Files:**
- Create: `src/sm64_events/ui/components/caps.js`
- Create: `tests/test_ui_caps.py`
- Modify: `src/sm64_events/ui/components/ranks.js:1-21` (drop `RANK_COLORS`, `FG`, re-export)
- Modify: `src/sm64_events/ranks/standards.py:11-21` (delete `RANK_COLORS`)
- Modify: `tests/test_ranks_standards.py:3,15` (drop the import and the assertion)
- Modify: `tests/test_ui_rank_chart.py:15,87-99` (drop the mirror assertion, move the colour guards)
- Modify: `docs/architecture.md:836`

**Interfaces:**
- Produces: `CAP` (object keyed by tier name), `RANK_NAMES: string[]` (hardest first, `Object.keys(CAP)`), `capName(tier) -> string`, `divisionDigit(numeral) -> string`, `wingTiers(tier, numeral) -> 0..4`, `rankColor(tier) -> string`, `CANVAS`, `CAP_BOX`, `PATCH_BOX`.
- Consumes: nothing.

- [ ] **Step 1: Write the failing test**

Create `tests/test_ui_caps.py`. The three guards below are the whole point of the task — a palette edit that reintroduces the Iron/Silver bug must fail here.

```python
"""ui/components/caps.js is THE tier registry: name, colour, treatment.

Two regressions have real precedent. Colour: Iron shipped at #8a8a8a and read
as a dim Silver at 24px (live report 2026-07-25) -- and the pair that failed
scored 168 on the redmean distance used here, so the floor is set above it.
The check is over EVERY pair, not adjacent ones: `rank-ladder-scale` renders
all nine medals in one 13px row and the chart draws a dot per tier, so any two
can end up side by side. Order: the JS key order IS the ladder, and a reorder
would silently mis-rank every entity.
"""
import re
from math import sqrt
from pathlib import Path

from sm64_events.ranks.classify import RANK_NAMES
from tests.source_scan import strip_comments

CAPS_JS = Path(__file__).resolve().parents[1] / "src" / "sm64_events" / "ui" / "components" / "caps.js"

# Anything at or below this failed in production; the palette must clear it
# with margin. Raising it is a decision, not a cleanup.
MIN_SEPARATION = 185.0


def _cap_table() -> dict[str, str]:
    """{tier: hex} in declaration order, comments stripped."""
    source = strip_comments(CAPS_JS.read_text(encoding="utf-8"))
    block = re.search(r"export const CAP = \{(.*?)\n\};", source, re.S)
    assert block, "CAP table not found in caps.js -- did it move or get renamed?"
    entries = re.findall(r'(\w+):\s*\{[^}]*?color:\s*"(#[0-9a-fA-F]{6})"', block.group(1), re.S)
    assert entries, "CAP parsed to nothing -- the entry shape changed"
    return dict(entries)


def _channels(hex_color):
    return [int(hex_color[index:index + 2], 16) for index in (1, 3, 5)]


def redmean(first, second):
    """Cheap perceptual distance. Weights green heaviest and red by level,
    which is why it catches two light neutrals that plain RGB distance calls
    far apart."""
    r1, g1, b1 = _channels(first)
    r2, g2, b2 = _channels(second)
    mean_red = (r1 + r2) / 2
    dr, dg, db = r1 - r2, g1 - g2, b1 - b2
    return sqrt((2 + mean_red / 256) * dr * dr + 4 * dg * dg
                + (2 + (255 - mean_red) / 256) * db * db)


def test_registry_covers_every_tier_in_ladder_order():
    assert list(_cap_table()) == list(RANK_NAMES)


def test_every_pair_of_tiers_is_visually_distinct():
    table = _cap_table()
    tiers = list(table)
    for index, first in enumerate(tiers):
        for second in tiers[index + 1:]:
            distance = redmean(table[first], table[second])
            assert distance >= MIN_SEPARATION, (
                f"{first} {table[first]} and {second} {table[second]} are only "
                f"{distance:.0f} apart; the Iron/Silver pair that shipped as a "
                f"bug scored 168")


def test_the_guard_can_still_fail():
    """A guard that cannot fail is not one (tests/source_scan.py)."""
    assert redmean("#8a8a8a", "#c2c2c2") < MIN_SEPARATION   # the shipped bug
    assert redmean("#f5f7f8", "#eeeae4") < MIN_SEPARATION   # white vs off-white
    assert redmean("#e23b3b", "#3dc05c") > MIN_SEPARATION    # red vs green
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_ui_caps.py -q`
Expected: FAIL — `caps.js` does not exist.

- [ ] **Step 3: Write `caps.js`**

The palette below is the output of a search that maximises the minimum pairwise
distance while staying as close as possible to each character's real colour;
its worst pair is 190. **If the contact sheet in Task 3 says a pair does not
read, change the hex here and raise `MIN_SEPARATION` — do not weaken the test.**

```js
// src/sm64_events/ui/components/caps.js — THE tier registry.
//
// Tier KEYS are external data (tools/scrape_ranks.py re-fetches them from
// xcams), so this file never renames them; it maps each key to the Mario cap
// that represents it. Colour lives here and nowhere else: the old Python
// RANK_COLORS had no runtime consumer, existing only to be mirrored, and the
// mirror is what made swapping a tier a three-edit job across two languages.
//
// Swapping a tier is ONE line. Replacing Toadsworth with Peach is:
//     Silver: { name: "Peach", color: "#f19ec2" },
// Dropping `pattern` stops the spots rendering; `treatment` changes the
// material; `base` (default "cap") would point at different art entirely.
//
// Import-free on purpose, so node can unit-test it — same reason ui/entities.js
// is import-free and entityicons.js is the layer above it.

export const CAP = {
  Mario:       { name: "Mario",      color: "#e23b3b", treatment: "glow",  glyph: "M" },
  Grandmaster: { name: "Metal",      color: "#82a0b5", treatment: "metal" },
  Master:      { name: "Vanish",     color: "#8fecfd", treatment: "translucent" },
  Diamond:     { name: "Luigi",      color: "#3dc05c" },
  Platinum:    { name: "Wario",      color: "#e8af16" },
  Gold:        { name: "Waluigi",    color: "#8d42c3" },
  Silver:      { name: "Toadsworth", color: "#dad68c", pattern: "spots", patternColor: "#7a4f2a" },
  Bronze:      { name: "Toad",       color: "#ffffff", pattern: "spots", patternColor: "#e0453f" },
  Iron:        { name: "Capless",    color: "#735648", treatment: "outline" },
};

// The ladder order IS this object's key order — hardest first, mirroring
// ranks/classify.RANK_NAMES (pinned by tests/test_ui_caps.py).
export const RANK_NAMES = Object.keys(CAP);

export const rankColor = (tier) => (CAP[tier] || {}).color || "#3a4250";
export const capName = (tier) => (CAP[tier] || {}).name || tier || "Unranked";

// Roman is what scoring.py stores; Arabic is what every surface shows. A "III"
// is three glyphs in a sign field ~14px wide and cannot be read there, so the
// hat forced the decision and the text follows it (spec §Decisions 2).
const DIGITS = { V: "5", IV: "4", III: "3", II: "2", I: "1" };
export const divisionDigit = (numeral) => DIGITS[numeral] || "";

// THE wing policy, isolated so it can change without touching a renderer.
// Division V is the bottom of a tier and wears no wings; division I wears all
// four, which makes the top division of the top tier the actual Wing Cap.
// Reserving wings for the top tier alone is:
//     return tier === "Mario" ? ... : 0;
export const WING_TIERS = 4;
export function wingTiers(tier, numeral) {
  const digit = Number(divisionDigit(numeral));
  if (!digit) return 0;
  return Math.max(0, Math.min(WING_TIERS, 5 - digit));
}

// Geometry measured off the exports (see the plan's Measured constants). The
// sprite canvas is wider than the cap because it must hold the full wingspan;
// these fractions are how a renderer finds the cap and the sign field inside it.
export const CANVAS = { width: 1283, height: 675 };
export const CAP_BOX = { left: 0.155885, top: 0.194074, width: 0.688231, height: 0.801481 };
export const PATCH_BOX = { left: 0.356976, top: 0.263704, width: 0.286048, height: 0.410370 };
```

- [ ] **Step 4: Run the test and watch it pass**

Run: `uv run pytest tests/test_ui_caps.py -q`
Expected: PASS, 3 tests.

- [ ] **Step 5: Cut the Python colour table over**

In `src/sm64_events/ranks/standards.py`, delete the `RANK_COLORS` dict at lines 11-21 and its explanatory comment. In `tests/test_ranks_standards.py`, drop `RANK_COLORS` from the import on line 3 and delete the `RANK_COLORS["Mario"].startswith("#")` assertion on line 15.

In `src/sm64_events/ui/components/ranks.js`, delete the `RANK_NAMES` and
`RANK_COLORS` declarations (lines 8-17) and the local `rankColor` (line 21),
replacing them with a re-export so existing importers keep working:

```js
// src/sm64_events/ui/components/ranks.js — the rank BANNER and the rank-mode
// list. The tier registry moved to caps.js (spec 2026-07-25-mario-cap-rank-icons);
// these re-exports keep the call sites that import RANK_NAMES/rankColor from
// here working, and are the only reason this file still exports them.
export { RANK_NAMES, rankColor } from "./caps.js";
```

**Keep `FG` (line 18-19) for now.** It is `Medal`'s text colour and `Medal`
still lives in this file until Task 4 deletes both together. Removing `FG`
here breaks `Medal` on the very next line — this task must leave the file
working, not merely smaller.

In `tests/test_ui_rank_chart.py`, drop the `RANK_COLORS` import on line 15 and delete the mirror test (lines ~80-99) — `tests/test_ui_caps.py` now owns colour. Leave every chart-geometry test in that file untouched.

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS. If anything else imports `RANK_COLORS` from Python, this is where it surfaces — fix the importer, do not restore the table.

- [ ] **Step 7: Update the architecture note**

`docs/architecture.md:836` currently reads that a tier colour lives in `ranks/standards.py::RANK_COLORS` plus its mirror. Rewrite that row to name `ui/components/caps.js` as the single authority, and say the Python copy was deleted because it had no runtime consumer.

- [ ] **Step 8: Commit**

```bash
git add src/sm64_events/ui/components/caps.js src/sm64_events/ui/components/ranks.js src/sm64_events/ranks/standards.py tests/test_ui_caps.py tests/test_ranks_standards.py tests/test_ui_rank_chart.py docs/architecture.md
git commit -F <message file>
```

Message: why one table replaced two — the Python copy had no consumer, and the
mirror is what made a tier swap a three-edit job.

**Intermediate state to expect:** the app now renders the new palette with the
old tier names, so Gold shows purple until Task 5. That is deliberate.

---

### Task 2: The art pipeline — raw exports to shipped sprites

**Files:**
- Create: `assets/hat_raw/{cap,patch,spots_toad,wing1,wing2,wing3,wing4}.png` (copied from Griffin's exports)
- Create: `tools/build_hat_assets.py`
- Create: `src/sm64_events/ui/assets/hat/*.png` (generated)
- Create: `src/sm64_events/ui/assets/fonts/SuperMario256.ttf`
- Create: `tests/test_ui_hat_assets.py`
- Modify: `assets/credit.md`

**Interfaces:**
- Produces on disk: `ui/assets/hat/cap.png`, `cap_outline.png`, `patch.png`, `spots.png`, `wing1.png`..`wing4.png` — all the same pixel dimensions, all white-where-tintable with a clean alpha.

- [ ] **Step 1: Write the failing test**

`tests/test_ui_hat_assets.py`. Coverage runs both ways — a `pattern` naming a
missing file 404s into a broken image, and an orphan file is dead weight in the
exe. The white-normalisation check is the one that protects the palette: the raw
exports came out at 236 grey, which would darken every tint by 7%.

```python
"""The hat sprites: every part caps.js names exists, nothing is orphaned, and
the tintable ones are actually white.

The exports arrive from Photoshop at 236 grey, not 255. Multiply tinting scales
the tier colour by that grey, so shipping them unnormalised silently darkens
all nine caps -- and Toad, whose whole identity is a white cap, most of all.
"""
import re
from pathlib import Path

from PIL import Image

from tests.source_scan import strip_comments

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "src" / "sm64_events" / "ui"
HAT = UI / "assets" / "hat"
CAPS_JS = UI / "components" / "caps.js"

# Parts every hat draws regardless of tier, plus the four wing steps -- split
# per side, because a flap rotates the two wings in OPPOSITE directions and one
# image containing both can only rotate as a unit.
BASE_PARTS = ({"cap", "patch"}
              | {f"wing{n}_{side}" for n in range(1, 5) for side in ("l", "r")})


def _named_parts() -> set[str]:
    """Every art stem caps.js can ask for: bases, patterns, and treatments
    that resolve to their own file."""
    source = strip_comments(CAPS_JS.read_text(encoding="utf-8"))
    parts = set(BASE_PARTS)
    parts |= set(re.findall(r'pattern:\s*"([^"]+)"', source))
    if 'treatment: "outline"' in source:
        parts.add("cap_outline")
    return parts


def test_every_named_part_has_its_png():
    missing = {p for p in _named_parts() if not (HAT / f"{p}.png").exists()}
    assert not missing, f"caps.js names art with no file: {sorted(missing)}"


def test_no_orphan_sprites():
    on_disk = {p.stem for p in HAT.glob("*.png")}
    assert on_disk == _named_parts(), (
        f"orphans {sorted(on_disk - _named_parts())} ship in the exe for nothing")


def test_every_sprite_shares_one_canvas():
    """Layers stack at inset:0 with no per-part offsets; a differently sized
    sprite silently shifts."""
    sizes = {p.name: Image.open(p).size for p in HAT.glob("*.png")}
    assert len(set(sizes.values())) == 1, f"sprites disagree on canvas: {sizes}"


def test_tintable_sprites_reach_pure_white():
    """Multiply scales the tier colour by this grey; anything under 250 tints
    dark. The wings keep their own shading, so only their highlight matters."""
    for stem in ("cap", "patch", "spots", "wing1_l", "wing4_r"):
        art = Image.open(HAT / f"{stem}.png").convert("RGBA")
        alpha = art.getchannel("A")
        grey = art.convert("L")
        brightest = max(v for v, a in zip(grey.getdata(), alpha.getdata()) if a > 200)
        assert brightest >= 250, f"{stem}.png peaks at {brightest}, not white"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_ui_hat_assets.py -q`
Expected: FAIL — `ui/assets/hat/` does not exist.

- [ ] **Step 3: Land the raw exports**

Copy Griffin's seven exports from wherever he left them into `assets/hat_raw/`,
beside `assets/hat_rank.psd`. They are the *input* to the script, deliberately
outside the shipped `ui/` tree. Verify before going further:

Run: `uv run python -c "from PIL import Image; from pathlib import Path; [print(p.name, Image.open(p).size, Image.open(p).mode) for p in sorted(Path('assets/hat_raw').glob('*.png'))]"`
Expected: seven files, every one `(1283, 675) RGBA`.

- [ ] **Step 4: Write `tools/build_hat_assets.py`**

Three transformations, each with a reason:

1. **Cap → white master.** The cap is exported red. Its HSV *value* channel is
   the shading (a lit face reads 255 whatever its hue), so V — normalised so the
   99th percentile of opaque pixels hits 255 — *is* the white master. This
   replaces hand-desaturating in Photoshop and is reproducible.
2. **Outline.** Dilate the cap's thresholded alpha with `MaxFilter(9)` and
   subtract the original; the ring is white so it tints like everything else.
   That is the Capless tier's whole appearance.
3. **Patch and spots → pure white**, since they arrive at 236.
4. **Split each wing at the canvas midpoint** into `wingN_l.png` and
   `wingN_r.png`, each still on the full canvas so it stacks at `inset: 0`.
   The cap is centred to the pixel (196px margins both sides), so the midpoint
   is the correct seam. This is what makes the flap possible: the two wings
   rotate in opposite directions, and one image holding both can only turn as
   a unit. Assert each half is non-empty — a seam in the wrong place produces
   one blank file and a hat with a single wing. **Emit only the split halves**;
   a combined `wingN.png` alongside them is an orphan and fails
   `test_no_orphan_sprites`.

The shipped set is therefore exactly twelve files: `cap`, `cap_outline`,
`patch`, `spots`, and `wing{1..4}_{l,r}`. Note the raw export is named
`spots_toad.png` but ships as `spots.png` — one pattern serves both spotted
tiers, distinguished only by `patternColor`.

Then downscale every output to **512 px wide** (`Image.LANCZOS`) — the largest
on-screen use is a 96px cap, and 1283px sprites are ~120 KB each in an exe that
ships incremental updates.

The script must **hard-fail if an input is missing**, never skip silently. Print
the cap and patch bounding boxes as fractions and assert they match
`CAP_BOX`/`PATCH_BOX` in `caps.js` to 4 decimal places — if a re-export moves the
art, that assertion is the only thing standing between you and a glyph rendered
outside the sign field.

Also copy `%LOCALAPPDATA%\Microsoft\Windows\Fonts\SuperMario256.ttf` into
`src/sm64_events/ui/assets/fonts/`.

- [ ] **Step 5: Run it**

Run: `uv run python tools/build_hat_assets.py`
Expected: nine PNGs in `src/sm64_events/ui/assets/hat/`, and printed bounding
boxes matching `caps.js`.

- [ ] **Step 6: Run the test and watch it pass**

Run: `uv run pytest tests/test_ui_hat_assets.py -q`
Expected: PASS, 4 tests.

- [ ] **Step 7: Credit the art**

Add the Super Mario 256 font to `assets/credit.md` beside the course portraits
and star icons, noting it is a fan font, and say the cap sprites derive from
`assets/hat_rank.psd` via `tools/build_hat_assets.py`.

- [ ] **Step 8: Commit**

```bash
git add assets/hat_raw assets/credit.md tools/build_hat_assets.py src/sm64_events/ui/assets/hat src/sm64_events/ui/assets/fonts tests/test_ui_hat_assets.py
git commit -F <message file>
```

Message: why the red export is the master — the value channel is the shading, so
the white version is derived rather than hand-made, and the derivation is a
script so a re-export is one command.

---

### Task 3: The `Hat` component

**Files:**
- Create: `src/sm64_events/ui/components/hat.js`
- Create: `tools/hat_sheet.py`
- Modify: `src/sm64_events/ui/index.html` (one CSS block, one `@font-face`)
- Modify: `tests/test_ui_caps.py` (add the render-contract guards)

**Interfaces:**
- Consumes: everything `caps.js` exports.
- Produces: `Hat({ tier, division = null, size = 18, title = null })`. `size` is
  the **cap height in px**; the element is wider than that because the sprite
  canvas holds the wingspan. `division` is the Roman numeral the server sends.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_ui_caps.py`. These pin the three things a render bug would
otherwise hide, all of which cost a round trip during the design probe:

```python
HAT_JS = CAPS_JS.parent / "hat.js"


def test_the_mask_and_the_shade_come_from_one_sprite():
    """Measured 2026-07-25: the tint is exact and backdrop-independent ONLY
    because the masked colour layer and the multiplied greyscale layer read the
    same PNG. Both rules must therefore resolve their art from the SAME custom
    property, so a call site cannot hand them different files."""
    css = strip_comments((CAPS_JS.parents[1] / "index.html").read_text(encoding="utf-8"))
    fill = re.search(r"\.hat \.fill\s*\{(.*?)\}", css, re.S)
    shade = re.search(r"\.hat \.shade\s*\{(.*?)\}", css, re.S)
    assert fill and shade, "the .hat .fill / .hat .shade rules are missing"
    assert "var(--art)" in fill.group(1) and "var(--art)" in shade.group(1), (
        "both layers must take their art from --art; two sources let the "
        "page backdrop leak into the multiply")
    assert "mix-blend-mode: multiply" in shade.group(1)


def test_the_glyph_rule_outranks_the_layer_rule():
    """`.hat i { inset: 0; display: block }` is class+element and beats a bare
    `.glyph` class, which silently parked the numeral outside the cap twice
    during design. The glyph rule needs two classes."""
    css = strip_comments((CAPS_JS.parents[1] / "index.html").read_text(encoding="utf-8"))
    assert ".hat .glyph" in css, "the glyph rule must be .hat .glyph, not .glyph"
    assert re.search(r"\.glyph\s*\{[^}]*inset:\s*auto[^}]*left:", css, re.S), (
        "inset is the shorthand for top/right/bottom/left -- declaring it AFTER "
        "left/top resets them; it must come first")


def test_division_five_wears_no_wings_and_division_one_wears_four():
    source = strip_comments(CAPS_JS.read_text(encoding="utf-8"))
    assert "5 - digit" in source, "wingTiers must map division 5 -> 0 wings"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_ui_caps.py -q`
Expected: FAIL — `hat.js` does not exist.

- [ ] **Step 3: Write the CSS in `index.html`**

Add one block near the existing `.marelo-crest` rule at line 928, plus an
`@font-face` for `SuperMario256`. The contract the CSS must satisfy, all four
clauses measured during design:

- `.hat` is `position: relative; display: inline-block`, sized by the component.
- `.hat i` is `position: absolute; inset: 0; display: block` — the layer base.
- A tinted layer pair is `.hat .fill` (`background-color: var(--c)` with
  `mask: var(--art) center / contain no-repeat`, **both** prefixed and
  unprefixed) and `.hat .shade` (`background: var(--art) …` plus
  `mix-blend-mode: multiply`). One `--art` value feeds both.
- The spots' shade layer is the **cap's** greyscale masked to the spot shapes,
  so the spots curve with the hat instead of sitting flat on it.
- `.hat .glyph` — two classes, and `inset: auto` **before** `left/top/width/height`
  — positioned at `PATCH_BOX`, `display: grid; place-items: center`,
  `font-family: "SuperMario256"`.
- `.hat` honours `prefers-reduced-motion` by not animating; no animation ships
  in this task.

- [ ] **Step 4: Write `hat.js`**

Layer order, bottom to top, which is load-bearing: **wings → cap (or outline) →
spots → patch → glyph**. The patch sits above the spots deliberately — Griffin's
spot art includes a top spot that the sign field is meant to cover, and drawing
it above the patch looks wrong.

Rules the component implements:

- `division == null` → silhouette only: cap layers and pattern, no patch, no
  glyph, no wings. This is exactly what `Medal` shows today, and it is a **data**
  rule, not a size rule — two current call sites pass a 22px medal with no
  division, and a size-only rule would draw an empty sign field there.
- `division != null` and `size >= 30` → patch, glyph and `wingTiers` wings.
  **30, not 22:** the design contact sheet showed the digit is a smudge at 22 and
  26 and only becomes readable around 34. Both call sites currently below 30
  are raised in Task 4.
- `treatment: "outline"` → the outline sprite in the tier colour instead of the
  cap pair. **Capless must stay visible at 13px** — on the design sheet a bare
  outline at 13px was nearly invisible on navy, which reads as a broken render.
  Give it a dim fill of its own colour beneath the ring; verify on the sheet.
- `treatment: "translucent"` → reduced opacity plus a `drop-shadow` glow in the
  tier colour. `treatment: "glow"` → the glow alone. `treatment: "metal"` → a
  specular highlight.
- `glyph: "M"` → the M in Mario red instead of the division digit.

- [ ] **Step 5: Write `tools/hat_sheet.py`**

Renders all 9 × 5 states at **96, 30 and 13 px**, plus a 13px strip on a light
background (the celebration overlay's card), screenshots it with headless
Chrome, and **kills its own server**. It must import the real `caps.js` and
`hat.js` rather than restating the registry — a sheet that draws its own copy of
the palette cannot catch a palette bug.

Launch flags, per `.claude/rules/spawned-processes.md`: `--headless=new`, never
any foregrounding flag.

- [ ] **Step 6: Render and look**

Run: `uv run python tools/hat_sheet.py`
Then read the PNG it writes. Check specifically:
- Toad vs Toadsworth at 13px — the pair the palette guard is tightest on.
- Capless at 13px on both backgrounds — the known-weak state.
- The digit at 30px — the threshold chosen in Step 4.
- The glyph inside the sign field on all nine tiers, not above the cap.

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/test_ui_caps.py tests/test_ui_hat_assets.py -q`
Expected: PASS.

- [ ] **Step 8: Show Griffin the sheet and stop**

This is a review gate, not a formality — the palette hexes are his call and the
contact sheet is the artifact the spec named for deciding them. If a pair does
not read, change the hex in `caps.js` and raise `MIN_SEPARATION` to match.

- [ ] **Step 9: Commit**

```bash
git add src/sm64_events/ui/components/hat.js src/sm64_events/ui/index.html tools/hat_sheet.py tests/test_ui_caps.py
git commit -F <message file>
```

---

### Task 4: One `Hat` replaces `Medal` and `Crest`

**Files:**
- Modify: `src/sm64_events/ui/components/practice.js:11,147,831`
- Modify: `src/sm64_events/ui/components/practicecell.js:3,49`
- Modify: `src/sm64_events/ui/components/rankpage.js:10,11,160,165,471,510,598,615,756`
- Modify: `src/sm64_events/ui/components/routes.js:13,214,475`
- Modify: `src/sm64_events/ui/components/celebrate.js:24,119,146,210`
- Modify: `src/sm64_events/ui/components/marelo.js:6,36-42,56`
- Modify: `src/sm64_events/ui/components/ranks.js:153`
- Modify: `src/sm64_events/ui/components/stagebanner.js:34` (delete the dead import)

**Interfaces:**
- Consumes: `Hat` from Task 3.
- Produces: `Medal` and `Crest` no longer exist.

- [ ] **Step 1: Convert the eight tier-only sites**

Each is `<${Medal} rank=${x} size=${n} />` → `<${Hat} tier=${x} size=${n} />`,
no division. Sizes stay as they are: `practice.js:147` (22),
`practice.js:831` (16), `practicecell.js:49` (16), `rankpage.js:160` (13),
`rankpage.js:165` (22), `rankpage.js:615` (14), `routes.js:214` (16),
`routes.js:475` (18). `ranks.js:153` inside `RankBanner` is a ninth, at 24.

- [ ] **Step 2: Convert the eight tier-plus-division sites**

`<${Crest} tier=${t} division=${d} size=${n} />` → `<${Hat} … />`.
**Two sites must be raised to 30px** so the digit reads: `rankpage.js:471` (was
22, breakdown rows) and `rankpage.js:510` (was 26, scope chips). The rest keep
their size: `marelo.js:56` (34), `rankpage.js:598` (28 → 30),
`rankpage.js:756` (64), `celebrate.js:119` (96), `celebrate.js:146` (40),
`celebrate.js:210` (28 → 30).

- [ ] **Step 3: Delete both old components**

Remove `Medal` from `ranks.js:23-28` and `FG`; remove `Crest` from
`marelo.js:36-42`. Delete the unused `Medal` import at `stagebanner.js:34`,
dead since `PracticeCell` was extracted.

Leave a comment in `hat.js` recording that this supersedes `ui.md`'s
"a CREST not a medal on purpose" note, and why: `Crest` had already spread to
four per-entity sites on the Rank tab, so the distinction was data (does this
have a division) not shape, and one component rendered twice is the rule
established on 2026-07-25 round 2.

- [ ] **Step 4: Check every file parses**

Run: `for f in src/sm64_events/ui/components/*.js; do node --check "$f" || echo "FAILED $f"; done`
Expected: no output. This catches syntax only — Step 5 is the real check.

- [ ] **Step 5: Render the real app against fixtures**

Capture `/api/session?clock=&scope=`, `/api/marelo`, `/api/segments`,
`/api/vocab`, `/api/routes` off the running instance (GETs, safe while Griffin
plays), serve them plus `/ui/*` from a scratchpad static server, and screenshot
the Practice tab, the header bar and the Rank tab. Mutate the captured JSON to
reach a Mario-tier and a Capless state that live data will not have. Recipe:
auto-memory `verify-ui-effects-with-harness-page`. **Kill the server afterwards
and confirm the port is free** — `TaskStop` on a wrapper does not kill the
child that holds the port.

- [ ] **Step 6: Sweep the width continuum**

A cap is ~1.6:1 where a disc was 1:1, so `RankBanner`'s icon is wider on a row
that already dropped a word for space, inside `.objective-card`'s hard 122px
height. Screenshot every 50px from 1050 to 1550 and check for a second line or
a clipped `next:` target. Sample points are not enough — 1400/900/700 all passed
once while everything from ~1101 to ~1500 was broken. Gate any fix on
`@container` against `.practice-page`, never viewport width.

- [ ] **Step 7: Run the suite and commit**

Run: `uv run pytest -q`

```bash
git add src/sm64_events/ui/components
git commit -F <message file>
```

---

### Task 5: Cap names replace tier names in every string

**Files:**
- Modify: `ranks.js:154`, `marelo.js:38,58`, `rankpage.js:159,179,222,389,434-435,441,543,758`, `standards.js:185,218`, `stratmodal.js:148`, `celebrate.js:212`
- Create: `tests/test_ui_cap_names.py`

- [ ] **Step 1: Write the failing guard**

A source scan over `ui/`, on `strip_comments(source)`, allowlisting only
`caps.js`. It catches a call site that prints `Gold` while rendering purple.
Probe it in both directions per the `tests/source_scan.py` norm — feed it a
comment-only sample (must pass) and a real-code sample (must fail).

- [ ] **Step 2: Run it and watch it fail**

Expected: FAIL, naming the sites in Step 3.

- [ ] **Step 3: Route every site through `capName` and `divisionDigit`**

`ranks.js:154` becomes `capName(banner.rank).toUpperCase()` plus
`divisionDigit(banner.division)`. Every `${tier} ${division}` becomes
`${capName(tier)} ${divisionDigit(division)}`. `rankpage.js:389`'s SVG gridline
labels take `capName` — the shorter names also relieve the top-of-ladder
crowding that currently drops "Grandmaster".

- [ ] **Step 4: Add the xcams bridge**

`standards.js:185` and `stratmodal.js:148` are where cutoff times are entered
against a site that calls these ranks Gold and Silver. Both show the cap name
with the tier key in the `title`: `"Toadsworth · Silver on xcams"`. Without it
those two screens become unusable for cross-referencing.

- [ ] **Step 5: Run the guard, then render**

Run: `uv run pytest tests/test_ui_cap_names.py -q`, then re-render the fixture
screenshots from Task 4 Step 5 and read the actual words on the banner, the
header bar, the standards table and the Rank tab.

- [ ] **Step 6: Commit**

---

### Task 6: The celebration flips caps, and the wings flap

**Files:**
- Modify: `src/sm64_events/ui/components/celebrate.js:68-70,119,146,210,212`
- Modify: `src/sm64_events/ui/index.html` (the flap keyframes)

- [ ] **Step 1: Confirm the tier climb already works**

`celebrate.js:68-70` slices `RANK_NAMES` and flips the crest once per tier
gained. With `Hat` in place that becomes watching the cap change Toad →
Toadsworth → Waluigi for free. Verify it against a fixture with a multi-tier
jump before adding anything.

- [ ] **Step 2: Add the flap**

Wing layers are separate elements, so a flap is a `transform: rotate` keyframe
on each wing with the two sides mirrored. It fires **only on the rank-up
celebration** — constant idle flapping across a screen of medals is motion noise
and this app runs on stream. Wrap it in `@media (prefers-reduced-motion: reduce)`
so it does not animate there, matching `useTween`'s existing contract.

- [ ] **Step 3: Verify both overlays render**

Drive both `TierRankUp` and `DivisionRankUp` off mutated fixture JSON and
screenshot mid-animation. Confirm `.rankup`/`.rankup-medium` stay
`pointer-events: none` so neither eats a click meant for the game.

- [ ] **Step 4: Run the suite and commit**

Run: `uv run pytest -q` — `tests/test_ui_celebrate.py` covers this file.

---

### Task 7: Verification sweep and documentation

**Files:**
- Modify: `.claude/rules/ui.md`, `.claude/rules/ranks.md`, `AGENTS.md`, `docs/architecture.md`

- [ ] **Step 1: Full-suite green**

Run: `uv run pytest -q`

- [ ] **Step 2: Desktop parity**

Everything lives in `ui/`, so the GUI window inherits it (rule 10) — but
`mix-blend-mode` and `@font-face` have only been proven in Chrome 150 here.
WebView2 is the same engine at the same version, so confirm rather than assume:
open the desktop shell once and screenshot a hat. Do **not** start the server
for this while Griffin may be playing.

- [ ] **Step 3: Star ↔ segment parity**

`Hat` is kind-agnostic and reaches both through `PracticeCell` (rule 11).
Confirm `tests/test_ui_section_parity.py` still passes and screenshot one star
card and one segment card side by side.

- [ ] **Step 4: Update the rule files**

`.claude/rules/ui.md` — rewrite the "Rank UI" and "MARELO crest" rows: one
`Hat`, the registry, the same-sprite tint invariant, the 30px digit threshold,
and the `.hat .glyph` specificity trap. `.claude/rules/ranks.md` — note that
tier colour left `standards.py`. `AGENTS.md` mirrors the ranks row. Keep each
one line per fact; link, do not duplicate.

- [ ] **Step 5: Commit**

---

## Notes for the implementer

**Values in this plan may be wrong.** The palette hexes come from an
optimisation, the 30px digit threshold and the `PATCH_BOX` fractions come from
measuring one render. If a constant does not match what you observe, flag it
rather than bending working code to fit it — the contact sheet in Task 3 exists
precisely so a wrong number shows up as a picture instead of a bug.

**Two mistakes already cost a round trip each during design.** Both are pinned
by tests in Task 3, but they are easy to reintroduce elsewhere:
1. `inset` is the shorthand for `top/right/bottom/left`. Declaring `inset: auto`
   *after* `left`/`top` silently resets them.
2. `.hat i` is class + element and outranks a bare `.glyph` class, so the base
   layer rule wins on `inset` and `display` unless the glyph rule uses two
   classes.
