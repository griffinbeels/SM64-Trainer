# Star icon mode + scale-to-fit star selector

Date: 2026-07-24 · Status: implemented (user request, live-audit style)

## Problem

1. The main-course star selector (`ui/components/stagebanner.js` StarRow) always
   shows generic gold-star art (`ui/assets/star_{1..6}.png`). The user supplied
   the full per-star split-icon set (one 100×100 PNG per course/star, e.g.
   `wf1.png` = Whomp's Fortress star 1; credit in `assets/credit.md`) and wants
   a setting that swaps the row to those icons, keeping the existing
   dim/glow/scale/bob behavior.
2. On narrow windows the star row overflows into a horizontal scrollbar
   (`.starcell` had a fixed 128–142px flex basis). All 7 stars must instead
   stay on screen, scaling the cells — and the card — down to fit.

## Prior art

- Per-star split icons are the standard LiveSplit convention for SM64 runners —
  the shipped set IS the community split-icon pack (see `assets/credit.md`).
- Scale-to-fit rows are the container-query idiom (`container-type:
  inline-size` + `cqw`-clamped child sizes); Chromium (browser + WebView2)
  has supported it since 2022, no build change needed.

## Design

**Assets** — the whole icon set is copied to `ui/assets/star_icons/` (121
files, ~2.4 MB). The existing `/ui` StaticFiles mount and the build's
`--add-data ui` tree pick it up recursively; zero server/build changes. The
user's original stays untracked at `assets/star_icons/`; provenance committed
as `assets/credit.md`. Main-course rendering uses `bob wf jrb ccm bbh hmc lll
ssl ddd sl wdw ttm thi ttc rr` × `1..7` (7 = the 100-coin slot — real files,
unlike the generic set's clamped 6). The extra specials (bitdw, toad1…, etc.)
ship unused, available to future rows.

**Setting** — client display preference, so localStorage (same pattern as
`clock`/`scope` in `ui/store.js`): key `sm64.starIcons`, values
`classic` (default, today's look) | `course`. Exposed as `t.starIcons` /
`t.pickStarIcons`. UI: a "Display" section in the header settings drawer
(`ui/components/header.js`) with a labeled `<select>`.

**Rendering** — StarRow picks the `src` per slot: course mode →
`/ui/assets/star_icons/{prefix}{slot+1}.png` via a `COURSE_ICON_PREFIXES`
registry (course_id 1..15, index = id-1); classic mode → unchanged. The img
gains class `courseicon` in course mode; all state classes (`dim`,
`active-star` glow, holder scale/bob, rank bob) are untouched, so both modes
animate identically. Course icons are opaque full-bleed screenshots (no
alpha), so `courseicon` adds only `border-radius` (the drop-shadow glow
follows the rounded clip). `onerror` falls back to the generic star art and
drops `courseicon`, so a missing/corrupt file degrades to today's look.

**Scale-to-fit** — `.practice-page` becomes the size container
(`container-type: inline-size`). Then:

- `.selector-card .starcell { flex: 1 1 0; min-width: 0 }` — the fixed
  128/142/136px bases and `overflow-x: auto`/scroll-snap are removed at every
  breakpoint. The row can no longer scroll horizontally.
- Star art, name/strat font sizes, gaps clamp on `cqw` so cells shrink
  smoothly; desktop widths clamp at today's exact values (no visual change
  when everything already fits).
- `--selector-height` becomes a `cqw` clamp too, so the card shortens as the
  row shrinks. Height stays a function of container width only — stage-mode
  swaps at a fixed window still land in identical geometry (OBS stability
  rule).
- Below a container-width threshold the strat sub-line hides (unreadable at
  that size; the name + medal stay).

## Testing

`tests/test_star_icons.py`: every prefix×1..7 asset exists; the JS registry
lists exactly the 15 prefixes in course order; store/header/stagebanner wire
the same `sm64.starIcons` key / `t.starIcons` prop; the selector CSS keeps
`container-type` and never reintroduces `overflow-x` on `.starrow`.
Visual behavior (both modes × widths) verified via a static harness page +
Chrome screenshots, then human playtest.

## Out of scope

Bowser/arena/castle banner rows (text buttons, unchanged); using the special
icons (bitdw/toad/…) anywhere; a server-side copy of the preference.
