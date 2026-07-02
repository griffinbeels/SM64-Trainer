# SM64-Style Star Selector — a line of gold stars in the practice banner

Date: 2026-07-02 · Status: approved by user (brainstorming session)

## Problem

The stage quick-select's main-course star row (`StarRow` in
`ui/components/stagebanner.js`) is a `flex-wrap` grid of flat `.stagebtn`
text cards. It works, but it looks like a generic button grid — nothing
about it evokes Super Mario 64, and on narrow panes it wraps to multiple
rows, losing the "one course, a line of stars" shape.

Goal: make the row **look like the SM64 star-select screen** — a single
horizontal line of gold stars — while carrying **exactly the same
information it does today**: the star name, the selected strategy, the rank
for that strategy, and which star is the active practice target. As the pane
scales the shape must be preserved: **always a single line of stars, never a
wrap.** Stated motivation: "that'd be more fun."

## Scope (decided with user)

- **Only `StarRow`** — the main-course (BOB→RR) stars-mode banner, i.e. the
  case in the reference screenshot. The other `StageBanner` modes
  (`BowserCourseRow` reds/no-reds, `ArenaRow` fight, `SegmentRow` castle
  segments) are **untouched** and keep their `.stagebtn` look. Extending the
  gold-star treatment to Bowser "Reds" is explicitly deferred (the `GoldStar`
  primitive below makes it a small follow-up).
- **Layout:** the user's chosen "full card under each star" — every star keeps
  its own name + strat + rank medal beneath it (not a single shared info line).
- **Data:** no new fields. The session view already carries everything —
  `v.catalog.courses[].stars` (names, 7 entries incl. the 100-coin star),
  `v.last_strat_by_star["course:star"]`, `v.rank_by_star["course:star"]`,
  and `v.target` (active). **This is a pure-frontend restyle: zero backend,
  view, or storage change.**
- **Behavior:** unchanged. Clicking a star still calls `pick(i)` →
  `POST /api/target` with that star's last strat, driving the normal
  `target_changed` flow. No new endpoints, no new state.
- **Parity:** all changes live in `ui/` + `index.html`, so the redesign
  appears identically in the browser tab and the desktop GUI window
  (domain rule 10). The `desktop/` shell is not touched.

## Decision — Approach A (self-contained SVG star + rewritten StarRow)

Chosen over two alternatives:

- *Nintendo star sprite (PNG asset)*: rejected — the app ships as a packaged
  onefile exe under a strict no-external-asset / CSP posture, and the SM64
  star sprite is copyrighted art. A bundled image is a licensing and
  packaging liability.
- *Plain `★` glyph styled gold*: rejected — cheapest, but flat and generic;
  it does not deliver the "more fun / looks like SM64" goal that motivated
  the request.

Approach A draws the star as **inline SVG + CSS** — a five-point star path
with a radial-gradient gold fill, a dark rim, and a soft inner highlight.
Self-contained (no asset, no CSP issue), scales crisply at any size, and is
faithful enough to read as an SM64 star.

## Components

### New: `ui/components/goldstar.js`

Exports one focused visual primitive:

```
GoldStar({ size = 64, shaded = true, eyes = false, active = false, dim = false })
```

- Renders the star `<svg>` (fixed viewBox `0 0 100 100`, sized by `size` or
  by CSS when width is `100%`).
- `shaded`: radial-gradient dimensional fill (light center → deep gold edge)
  + inner highlight rim. (The **flat** alternative — single-tone fill, thin
  outline — is kept behind this flag so the look is a one-line switch, but
  the shipped default is `shaded: true`.)
- `active`: soft gold `drop-shadow` glow.
- `dim`: slight desaturation/opacity for idle stars so the target pops.
- `eyes`: the SM64 sleeping-eyes flourish. **Default off** (too busy on a
  dense 7-star row); wired as a prop so the human-audit playtest can flip a
  single call-site constant to try it.

Kept as its own file (one concern, reusable for the deferred Bowser-Reds
extension) so `stagebanner.js` stays lean — consistent with the small-focused-
modules discipline.

### Rewrite: `StarRow` in `ui/components/stagebanner.js`

Same data derivation (`course`, `tgt`, `lastStratFor`, `rankFor`) and the
same `pick(i)` handler — only the render changes. Instead of the
`.stagebanner-row` / `.stagebtn` grid it emits a single **non-wrapping row of
star cells**. Each cell, top to bottom:

1. a small number (`i + 1`), SM64 star-select style (decorative, default on);
2. `<GoldStar>` (active/dim per target state);
3. the star **name** (two-line clamp, full name in a `title` tooltip);
4. `<Medal rank=...>` (reused from `ranks.js`) + the strat tag, or `—` when
   no strat is set.

The active star's cell gets the gold frame + name tint. The other three
banner modes and their `.stagebtn` CSS are left exactly as-is.

## Layout & the single-row guarantee

- `.starrow { display: flex; flex-wrap: nowrap; gap }` — **never wraps.**
- `.cell { flex: 1 1 0; min-width: 0 }` — cells share the width and shrink
  together.
- The star holder is sized `width: min(74px, 72%)` of its cell, so stars
  scale **down with the pane** while staying one row.
- Long names clamp to two lines (`-webkit-line-clamp: 2`) with a hover title.
- Belt-and-suspenders floor: the row container may carry `overflow-x: auto`
  so an absurdly narrow width scrolls rather than crushes — rarely triggered
  in the desktop window, but it keeps the invariant honest.

## States & flourishes (shipped defaults, all trivially reversible)

- **Active** (current target): star ~1.16× scale, gold card frame reusing the
  app's `--gold2` / `.active-star` convention, soft glow, gentle **bob**
  (gated on `prefers-reduced-motion`). Name tinted gold.
- **Idle:** normal size, **dimmed** so the active star pops; medal + strat
  beneath.
- **Numbers:** on. **Sleepy eyes:** off (flag-flippable at the human-audit).

## Styling

New CSS goes in `index.html`'s `<style>` block, reusing existing tokens
(`#ffd75f`, `#e0c36a`, `.stagebanner`, the `RANK_COLORS`). New classes are
namespaced (`.starrow`, `.cell`, `.star-holder`, `.num`, `.subline`) so they
never collide with the retained `.stagebtn` / `.stagebanner-row` rules still
used by the other modes. Motion is `prefers-reduced-motion`-gated.

## Testing & definition of done

- No pytest surface changes (no server/view/data change) — `uv run pytest -q`
  stays green untouched; it is run to confirm nothing regressed.
- **Frontend smoke test:** load a main-course banner, confirm the row renders
  a single line of stars with names/medals/strats, that it does not wrap as
  the window narrows, and that clicking a star still POSTs `/api/target`
  (target header + pinned section update).
- **Human-audit playtest** for feel: shaded-vs-flat, bob, dim, numbers, and
  the eyes toggle confirmed live by the human (the parts Claude can't judge).
- Module map row for `stagebanner.js` updated to mention the star-row look and
  the new `goldstar.js`; README only if the consumer-facing surface changed
  (it does not — behavior is identical).

## Reference

Interactive mockup published during brainstorming (full-card layout, shaded
vs flat, width-scaling demo, flourish toggles), rendered with live Lethal
Lava Land data in the app's own palette.
