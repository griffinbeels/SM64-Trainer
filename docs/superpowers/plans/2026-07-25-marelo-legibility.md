# MARELO Legibility Pass — Implementation Plan

> **For agentic workers:** each task below is dispatched to a fresh implementer. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make the shipped MARELO surfaces readable at a glance — fix the chart's letterboxing and missing time axis, replace unexplained `+1.88` deltas with round points plus the rank they buy, and add two at-a-glance summaries borrowed from op.gg.

**Architecture:** Presentation-layer only, with one new read-only endpoint. The 0–100 score, the tier anchors, and THE score/medal invariant are unchanged — points are a display multiplier applied in one JS helper.

**Source:** live user feedback 2026-07-25 against the merged feature (`8429ad3`).

## Global Constraints

- Python 3.12 via **uv** (`uv run pytest -q`), never pip. Baseline **1896 passing** — must not regress.
- **The server stays canonical 0–100.** `marelo`, `gain`, `next_division_at` keep their units. The ×100 conversion happens in exactly ONE exported JS helper; no component may multiply on its own.
- Tier anchors, `defined_tiers`, division maths and the score/medal invariant are **not** to be touched. If a change seems to need them, stop and report.
- CSS goes in the one design-system block in `ui/index.html`. Variables that exist: `--border`, `--border-soft`, `--gold`, `--dim-*`. `--line` and `--panel` do **not** — grep before using one.
- Naming: no single-letter names, including loop variables.
- **UI is verified by rendering**, never by `node --check` alone. Headless Chrome needs `--virtual-time-budget=3000` — `--screenshot` fires on `load` and will otherwise capture a spinner. Port 8123 is occupied; pick another and confirm it is free. Kill any server you start and delete the harness in the same session. Never start `python -m sm64_events.main` — the user may be playing and the recorder lock protects their recording.
- Screenshots go in `.superpowers/sdd/2026-07-25-marelo-legibility/` and stay there.
- Commit messages explain WHY, in this repo's style.

**A note on this plan's form:** the previous MARELO plan carried hand-written reference implementations, and fourteen of them were wrong — invented APIs, wrong data shapes, undefined CSS variables, a no-op CSS technique. Its prose and contracts were reliable. So this plan states **intent, exact files, and contracts** and leaves the code to the implementer, who can read the real APIs. Do not expect copy-paste snippets here.

---

## Task A: `GET /api/marelo/summary`

**Files:** `src/sm64_events/server/ranks_api.py` · `tests/test_ranks_api_marelo.py`

**Why:** the Rank tab can only show one scope at a time, so seeing your 16 Star rank next to your 120 Star rank costs a dropdown round-trip each. op.gg shows every season's tier at once; this is the data behind that.

**Contract:**

```
GET /api/marelo/summary -> {"chips": [
  {"scope_id", "label", "tier", "division", "marelo", "n", "practiced"}, ...]}
```

- Scopes included, in this order: `overall`, then every route whose `category` begins `Main Categories`, then the active scope if not already present. Cap at 6 chips.
- Each chip is the same aggregate `/api/marelo` computes for that scope — **reuse `_build_marelo`'s scoring path**, do not reimplement it. It is fine to build a leaner payload (no `entities`, no `celebration`); it is not fine to compute tier/division a second way.
- **Do not touch the watermark.** `_build_marelo` syncs, seeds and reads watermarks; a summary fetch must not fire, seed, or lower a celebration. Factor the scoring out or pass a flag — whichever keeps that guarantee obvious at the call site.
- An empty route list yields `{"chips": [ {overall...} ]}`, never an error.

- [ ] Write failing tests: the shape above; overall always first; cap respected; a summary call leaves `marelo_watermarks` byte-identical.
- [ ] Run them, watch them fail.
- [ ] Implement.
- [ ] Full suite, then commit.

## Task B: The Progress chart

**Files:** `src/sm64_events/ui/components/rankpage.js` · `src/sm64_events/ui/index.html`

**Why (root cause, already diagnosed — do not re-derive):** the SVG declares `viewBox="0 0 720 220"` (3.3:1) while CSS gives it `width:100%; height:auto; max-height:240px`, so at a wide window the box is ~7.7:1. SVG's default `preserveAspectRatio="xMidYMid meet"` therefore renders the chart at its own aspect and **centres it**, dead-spacing both sides. The tier labels at `x=4` land mid-card, overlapping the plot.

**Required outcome:**

1. **No letterboxing and no scaled text.** Measure the container and drive the viewBox from the measured width so the chart draws 1:1. `preserveAspectRatio="none"` is NOT acceptable — it would stretch the label text.
2. **A left gutter** (~64px) that tier labels live in, so they never sit on the plot area or its dashed lines.
3. **An X axis showing time.** Points carry `utc`. Show a handful of dated ticks and a span caption (e.g. "18 Jun – 25 Jul · 5 weeks"). Choose tick density from the span; do not hardcode a count that breaks on a one-day or one-year range.
4. **Auto-zoom the Y axis** to the data's range plus one tier of headroom above and below, clamped to [0,100]. Today the axis is a fixed 0–100 and a real Iron→Bronze climb renders as a flat line at the bottom. Tier bands and labels still draw, but only those in view.
5. Degenerate inputs must not crash or divide by zero: one point, all points identical, all timestamps identical.

- [ ] Harness page with three fixtures: a flat low-range series (~9.6, the user's real shape), a wide-range series, and a single-point series.
- [ ] Screenshot at 1400px and 900px for each fixture; confirm no dead space, no label overlap, a readable time axis, and a visible slope on the flat-low fixture.
- [ ] Commit.

## Task C: Points, next-rank, and the PB label

**Files:** `src/sm64_events/ui/components/marelo.js` · `rankpage.js` · `index.html`

**Why:** `+1.88` next to `+0.60` tells a user nothing. LP works because it is a round integer on one scale.

1. **One conversion helper**, exported from `marelo.js` (suggested `toPoints(score)` → `Math.round(score * 100)`), used by every surface. The server stays 0–100; **no component multiplies on its own**. Document the multiplier at the helper.
2. **Points everywhere a MARELO number is shown**: header bar, rank card, `next division at`, and the breakdown's gain column. Rating `9.6` renders `960`; gain `1.88` renders `+188`. Label the unit once per surface, not per row.
3. **A next-rank column** in the breakdown: the tier+division this entity reaches next (`Bronze V → Bronze IV`), or for an unpracticed entity what a first time buys (`→ Gold`). This is the motivating half; points are the precise half. Both columns, points on the right.
4. **Mastery stays 0–100** — it is a mean score, not a rating, and converting it would imply a fourth scale. State that in a comment.
5. **The PB-mode label.** MARELO grades your best valid run; the practice card's PB banner grades the PB you explicitly saved, and saving is a manual button. Add one line of copy on the rank card making that legible — the user chose no-friction deliberately, so the difference should read as intentional rather than as a bug.

- [ ] Render and screenshot: card, header bar, and a breakdown with a practiced, an unpracticed and an excluded row.
- [ ] Commit.

## Task D: Scope chips and the Top-N strip

**Files:** `src/sm64_events/ui/components/rankpage.js` · `index.html` (+ read-only reuse of the icon helpers)

**Depends on:** Task A's endpoint, Task C's `toPoints`.

1. **Scope chip row** at the top of the Rank tab, from `/api/marelo/summary`: crest + label + points per scope. Clicking one switches the scope picker to it. The active scope reads as selected.
2. **Top-N strip** — the user's best-scoring entities as **per-star icons**, tier-tinted, N=12. Source the entities from the data the Rank tab already holds; **no new endpoint**. Reuse the existing icon resolution rather than duplicating it — `ui/components/stagebanner.js` owns `COURSE_ICON_PREFIXES` and the stem→URL rule, and `iconpicker.js` owns `iconSrcFromStem`. If a shared helper has to be extracted to avoid duplication, extract it; do not copy the mapping.
3. A segment (no star icon) falls back the same way the stage banner already falls back. An unpracticed entity never appears in the strip.

- [ ] Render and screenshot at 1400px and 900px; confirm icons resolve (no broken-image glyphs) and the strip does not wrap raggedly.
- [ ] Commit.

---

## Verification for the whole pass

- `uv run pytest -q` ≥ 1896.
- Every changed surface screenshotted and looked at.
- The score/medal invariant untouched: `tests/test_ranks_scoring_seed.py` still green (it will be, unless someone edits scoring).
