# SM64-Style Star Selector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restyle the main-course quick-select `StarRow` into a single, non-wrapping line of SM64-style gold stars, keeping each star's name, strategy, rank medal, and active-target state.

**Architecture:** Pure-frontend restyle. Add one self-contained SVG `GoldStar` primitive (`ui/components/goldstar.js`), add namespaced CSS to `ui/index.html`, and rewrite only the `StarRow` render in `ui/components/stagebanner.js`. No backend, view, storage, or API change — the session view already carries names, strats, ranks, and the active target. The other `StageBanner` modes (bowser/arena/castle) are untouched.

**Tech Stack:** Preact 10 + `htm` (vendored, no build step), plain CSS in `index.html`, Python/uv only for running the server and the regression test suite.

> **Shell note (this is a Windows / PowerShell-primary machine):** run the shell snippets below with the **Bash tool** (Git Bash, POSIX), as the project's `CLAUDE.md` commands assume. Do **not** paste the multi-line `git commit -m "…"` messages into PowerShell 5.1 — embedded newlines/quotes break native-arg quoting there (use `git commit -F <file>` if you must use PowerShell). The `&&`/`||` inside the JS code fences are JavaScript operators, not shell chaining.

## Global Constraints

- **Pure frontend.** No change to any file under `server/`, `tracking/`, `storage/`, `memory/`, `core/`, or `detectors/`. `uv run pytest -q` must stay green (run it as a regression guard, not because new Python is added).
- **Self-contained visuals.** The star is inline SVG + CSS — no image asset, no external font/URL. This is mandatory: the app ships as a packaged onefile exe under a strict CSP (`ui/index.html` loads only same-origin vendored modules).
- **Browser ↔ GUI parity** (domain rule 10). All changes live in `ui/`, so they appear identically in the browser tab and the desktop window. Do **not** touch `desktop/`.
- **Do not disturb the other banner modes.** `BowserCourseRow`, `ArenaRow`, and `SegmentRow` keep using the existing `.stagebtn` / `.stagebanner-row` CSS. New CSS classes are namespaced (`.starrow`, `.starcell`, `.starholder`, `.starnum`, `.starsub`) so the two rule sets never collide.
- **Motion gated.** Any animation must be inside `@media (prefers-reduced-motion: no-preference)`.
- **No JS unit-test runner exists** in this repo. The verification gate for UI is: `uv run pytest -q` green + the page loads with no console errors (proves the new modules parse) + a **human-audit** playtest for the live render/feel (the human runs PJ64 and enters a main course). Do not add Jest/Vitest/etc — that is not the codebase pattern (YAGNI).

---

### Task 1: The gold-star row (GoldStar primitive + CSS + StarRow rewrite)

This is one deliverable — the redesigned row — because `GoldStar` has no standalone UI until `StarRow` renders it, and the runtime gate (page loads, row renders) exercises all three pieces together.

**Files:**
- Create: `src/sm64_events/ui/components/goldstar.js`
- Modify: `src/sm64_events/ui/index.html` (add a CSS block in the `<style>`, after the `.stagebtn.active-star` rule near line 39)
- Modify: `src/sm64_events/ui/components/stagebanner.js` (add one import near line 24; replace the `StarRow` function, currently lines 49–91)

**Interfaces:**
- Consumes: `Medal` from `./ranks.js` (already imported in `stagebanner.js`) — `Medal({ rank, size })`. The session view (`t.view` = `v`) already provides `v.catalog.courses[].stars` (array of star names, 7 entries incl. the 100-coin star), `v.last_strat_by_star["<course>:<star>"]`, `v.rank_by_star["<course>:<star>"]`, and `v.target`. `send("POST", url, body)` and `t.refresh()` from the existing module.
- Produces: `GoldStar({ size, shaded, eyes, active, dim })` exported from `ui/components/goldstar.js` — a Preact component returning an `<svg>`. `size` accepts a number (px) or the string `"100%"` (CSS-driven, used here). This is the reusable primitive a later Bowser-"Reds" follow-up would import.

- [ ] **Step 1: Create the `GoldStar` primitive**

Create `src/sm64_events/ui/components/goldstar.js` with exactly this content:

```js
// src/sm64_events/ui/components/goldstar.js — self-contained SVG gold star.
// A reusable visual primitive: no sprite art, no external asset, so it fits the
// packaged-exe / strict-CSP posture. Used by stagebanner.js's StarRow; the
// shaded/eyes flags keep the SM64 flourishes a one-line switch at the call site.
import { h } from "preact";
import { useRef } from "preact/hooks";
import htm from "htm";

const html = htm.bind(h);

// Five-point star, viewBox 0..100, one point up. Chunky inner radius gives the
// stubby SM64 silhouette.
const STAR_PATH =
  "M50,3 L61.8,33.8 L94.7,35.5 L69,56.2 L77.6,88 " +
  "L50,70 L22.4,88 L31,56.2 L5.3,35.5 L38.2,33.8 Z";

// SVG gradient ids are document-global, so each shaded instance needs a unique
// one. Assigned once per mount (lazy ref init) so re-renders keep a stable id.
let gradSeq = 0;

export function GoldStar({ size = 64, shaded = true, eyes = false,
                           active = false, dim = false }) {
  const idRef = useRef(null);
  if (idRef.current === null) idRef.current = `gs${++gradSeq}`;
  const gid = idRef.current;

  const fill = shaded ? `url(#${gid})` : "#ffcf45";
  const stroke = shaded ? "#a5670c" : "#c79017";
  const filter = active
    ? "drop-shadow(0 0 6px rgba(255,215,95,.85)) drop-shadow(0 0 2px rgba(255,215,95,.9))"
    : dim ? "saturate(.85)" : "none";
  const opacity = active ? 1 : dim ? 0.72 : 1;

  return html`<svg viewBox="0 0 100 100" width=${size} height=${size}
      style=${`display:block;overflow:visible;filter:${filter};opacity:${opacity}`}>
    ${shaded && html`<defs>
      <radialGradient id=${gid} cx="42%" cy="34%" r="72%">
        <stop offset="0%" stop-color="#fff6c9" />
        <stop offset="34%" stop-color="#ffe271" />
        <stop offset="72%" stop-color="#f5b722" />
        <stop offset="100%" stop-color="#d98a12" />
      </radialGradient>
    </defs>`}
    <path d=${STAR_PATH} fill=${fill} stroke=${stroke} stroke-width="3"
          stroke-linejoin="round" />
    ${shaded && html`<path d=${STAR_PATH} fill="none" stroke="#fff8d6"
          stroke-opacity=".55" stroke-width="1.2"
          transform="scale(.82) translate(11,7)" />`}
    ${eyes && html`<g fill="#241a05">
      <ellipse cx="41" cy="46" rx="3.6" ry="5.2" />
      <ellipse cx="59" cy="46" rx="3.6" ry="5.2" />
    </g>`}
  </svg>`;
}
```

- [ ] **Step 2: Add the CSS block**

In `src/sm64_events/ui/index.html`, immediately after the line
`  .stagebtn.active-star { border-color: #e0c36a; }` (≈ line 39), insert:

```css
  /* SM64 star-select row (StarRow only) — a single line of gold stars that
     shrinks with the pane but NEVER wraps. Namespaced away from .stagebtn,
     which the other banner modes (bowser/arena/castle) still use unchanged. */
  .starrow { display: flex; flex-wrap: nowrap; gap: .3rem; align-items: flex-start;
             overflow-x: auto; margin-top: .4rem; }
  .starcell { flex: 1 1 0; min-width: 0; display: flex; flex-direction: column;
              align-items: center; gap: .28rem; padding: .5rem .25rem .45rem;
              border: 1px solid transparent; border-radius: 10px; cursor: pointer;
              text-align: center; background: none; color: inherit; font: inherit;
              transition: background .12s, border-color .12s; }
  .starcell:hover { background: #20242b; }
  .starcell:focus-visible { outline: 2px solid #6fa8ff; outline-offset: 2px; }
  .starcell.active-star { border-color: #e0c36a; background: #211f14; }
  .starnum { color: #6c7686; font-size: .72rem; font-variant-numeric: tabular-nums;
             line-height: 1; height: 1em; }
  .starholder { display: block; width: min(74px, 72%); aspect-ratio: 1 / 1;
                transition: transform .18s ease; }
  .starcell.active-star .starholder { transform: scale(1.16); }
  .starname { font-size: .8rem; font-weight: 600; line-height: 1.12; min-height: 2.1em;
              display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
              overflow: hidden; }
  .starcell.active-star .starname { color: #ffd75f; }
  .starsub { display: flex; align-items: center; gap: .28rem; justify-content: center;
             max-width: 100%; }
  .starsub .strat { color: #6c7686; font-size: .74rem; white-space: nowrap;
                    overflow: hidden; text-overflow: ellipsis; }
  .starsub .strat.none { opacity: .6; }
  @media (prefers-reduced-motion: no-preference) {
    .starcell.active-star .starholder { animation: starbob 2.4s ease-in-out infinite; }
    @keyframes starbob { 0%, 100% { transform: scale(1.16) translateY(0); }
                         50% { transform: scale(1.16) translateY(-3px); } }
  }
```

- [ ] **Step 3: Import `GoldStar` into `stagebanner.js`**

In `src/sm64_events/ui/components/stagebanner.js`, directly after the existing
line `import { Medal } from "./ranks.js";` (line 24), add:

```js
import { GoldStar } from "./goldstar.js";
```

- [ ] **Step 4: Add the flourish flags + replace the `StarRow` function**

In `src/sm64_events/ui/components/stagebanner.js`, replace the **entire current
`StarRow` function** (from `function StarRow({ t, v, stage }) {` through its
closing `}` — currently lines 49–91) with the following. The four
`STAR_*` module constants sit just above it and are the "flip at the
human-audit playtest" switches the spec calls for:

```js
// StarRow look flags — flip during the human-audit playtest to taste. Kept as
// constants (not props) so the call site below stays a single readable line.
const STAR_SHADED = true;    // false = flat single-tone gold
const STAR_EYES = false;     // true  = SM64 sleeping-eyes on idle stars
const STAR_DIM_IDLE = true;  // false = every star equally bright
const STAR_NUMBERS = true;   // false = hide the 1..N labels above the stars

function StarRow({ t, v, stage }) {
  const course = v.catalog.courses.find((c) => c.id === stage.course_id);
  if (!course) return null;

  const tgt = v.target || {};
  const lastStratFor = (i) =>
    v.last_strat_by_star[`${stage.course_id}:${i}`] || "";
  // Rank under that star's ACTIVE strat (server-graded). Changing the strat
  // refreshes the view and swaps the medal automatically — see views.py.
  const rankFor = (i) =>
    (v.rank_by_star || {})[`${stage.course_id}:${i}`];

  async function pick(i) {
    await send("POST", "/api/target", {
      course_id: stage.course_id, star_id: i,
      strat_tag: lastStratFor(i) || null,
    });
    t.refresh();
  }

  return html`<div class="starsec stagebanner">
    <div class="shead"><b>▸ ${course.name}</b>
      <span class="meta">tap a star to practice</span></div>
    <div class="starrow">
      ${course.stars.map((name, i) => {
        const active = tgt.kind !== "segment"
          && tgt.course_id === stage.course_id && tgt.star_id === i;
        const strat = lastStratFor(i);
        const rank = rankFor(i);
        return html`<button key=${`${stage.course_id}:${i}`}
                            class="starcell ${active ? "active-star" : ""}"
                            title=${name} onclick=${() => pick(i)}>
          ${STAR_NUMBERS ? html`<span class="starnum">${i + 1}</span>` : ""}
          <span class="starholder">
            <${GoldStar} size="100%" shaded=${STAR_SHADED}
                         active=${active}
                         dim=${STAR_DIM_IDLE && !active}
                         eyes=${STAR_EYES && !active} />
          </span>
          <span class="starname">${name}</span>
          <span class="starsub">
            ${rank ? html`<${Medal} rank=${rank} size=${14} />` : ""}
            <span class="strat ${strat ? "" : "none"}">${strat || "—"}</span>
          </span>
        </button>`;
      })}
    </div>
  </div>`;
}
```

- [ ] **Step 5: Regression guard — run the Python suite**

Run: `uv run pytest -q`
Expected: PASS (same pass count as before this task — no Python was touched). If anything fails, it is unrelated to this change; stop and investigate.

- [ ] **Step 6: Page-load smoke check (JS parses, no console errors)**

Start the dev server if one is not already running (from the repo root, so
`data/` resolves):

Run: `uv run python -m sm64_events.main`

Open `http://localhost:8065/` in a browser (or use the chrome-devtools MCP per
the `frontend-smoke-test` skill). Confirm:
- The app renders (Practice tab visible) — a syntax/import error in
  `goldstar.js` or `stagebanner.js` would blank the page.
- The browser console shows **no errors**.

(The full gold-star row only appears when a stage banner is active, which
requires standing in a main course — that is the human-audit step, not this
one. This step only proves the new modules load.)

- [ ] **Step 7: Commit**

```bash
git add src/sm64_events/ui/components/goldstar.js src/sm64_events/ui/index.html src/sm64_events/ui/components/stagebanner.js
git commit -m "ui(stagebanner): render main-course StarRow as a line of SM64 gold stars

Replace the .stagebtn card grid with a single non-wrapping row of
self-contained SVG gold stars (new GoldStar primitive), keeping each
star's name, strat, rank medal and active-target state. Pure frontend;
other banner modes untouched. Look flags (shaded/eyes/dim/numbers) are
call-site constants for the human-audit playtest.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 8: Human-audit — live render & feel** (REQUIRED SUB-SKILL: `human-audit`)

Ask the human to run PJ64 + the ROM and enter a main course (e.g. Lethal Lava
Land). Confirm together:
- A single horizontal row of gold stars appears — one per star (7 incl. 100
  Coins), each with its name, rank medal, and strat (or `—`).
- The active/target star is enlarged, gold-framed, and gently bobs; idle stars
  are dimmed.
- Narrowing the window shrinks the stars but the row **never wraps to a second
  line**; long names clamp to two lines.
- Clicking a star still sets it as the practice target (header + pinned section
  update).
- Confirm the look choices (shaded vs flat, eyes on/off, dim, numbers). If the
  human wants a different default, flip the matching `STAR_*` constant in
  `stagebanner.js` and re-commit.

---

### Task 2: Update the module map

**Files:**
- Modify: `CLAUDE.md` (the "Module map" table)

**Interfaces:**
- Consumes: nothing. Documentation only.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Amend the `stagebanner.js` row and add a `goldstar.js` row**

In `CLAUDE.md`, find the module-map table row that begins
`| Stage quick-select banner | \`ui/components/stagebanner.js\` —`. At the end
of that row's description (before the closing `|`), append:

```
 The main-course STAR mode renders as a single non-wrapping line of SM64 gold stars (`ui/components/goldstar.js`), one per star with name + rank Medal + active-strat; look flags (`STAR_SHADED`/`STAR_EYES`/`STAR_DIM_IDLE`/`STAR_NUMBERS`) are call-site constants. Other modes keep the `.stagebtn` look.
```

Then add a new row immediately below it:

```
| Gold star SVG primitive | `ui/components/goldstar.js` — self-contained SVG five-point gold star (`GoldStar({size, shaded, eyes, active, dim})`); no sprite/asset (CSP-safe). Used by stagebanner.js's StarRow; reusable for a future Bowser-"Reds" star |
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(module-map): note SM64 gold-star StarRow + goldstar.js primitive

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Single line of SM64 gold stars, never wraps → Task 1 Steps 1–2 (`.starrow` `flex-nowrap` + `.starholder` `min(74px,72%)`), verified Task 1 Step 8. ✓
- Same info per star (name / strat / rank / active) → Task 1 Step 4 render. ✓
- Full-card layout (info under each star) → Task 1 Step 4. ✓
- Self-contained SVG, no asset/CSP issue → Task 1 Step 1 (`goldstar.js`). ✓
- Scope = StarRow only; other modes untouched → Task 1 Step 4 replaces only `StarRow`; namespaced CSS (Global Constraints). ✓
- Zero backend/view/storage change → Global Constraints + Task 1 Step 5 regression guard. ✓
- Shipped flourish defaults + one-line reversibility → `STAR_*` constants (Task 1 Step 4). ✓
- Parity → all edits under `ui/` (Global Constraints). ✓
- Reusable primitive for later Bowser-Reds → `GoldStar` export (Task 1) + doc row (Task 2). ✓
- Docs (module map) updated → Task 2. ✓

**Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to". All CSS, JS, and commands are complete and literal. ✓

**Type consistency:** `GoldStar({ size, shaded, eyes, active, dim })` defined in Task 1 Step 1 and called with exactly those props in Step 4. `Medal({ rank, size })` used as it already exists in `ranks.js`. `pick(i)`, `lastStratFor(i)`, `rankFor(i)` match the retained handler shape. ✓
