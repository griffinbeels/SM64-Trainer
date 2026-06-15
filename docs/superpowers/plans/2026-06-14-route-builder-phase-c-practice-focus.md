# Route Builder — Phase C (Route Practice Focus) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A non-destructive **focus layer** on the Practice tab: pick an active route and the tab shows only that route's members, in route order, with a suggested current/next pointer and per-step/cumulative success — while you retry anything freely.

**Architecture:** Pure frontend, entirely client-side over existing APIs. A `RouteFocus` view added **inside** `practice.js` (so it can reuse the local `StarSection`/`SegmentSection`), driven by the route view (`GET /api/routes/{id}`) for ordering + names + %s, cross-referenced to the live session view (`t.view.stars`/`t.view.segments`) for the current step's full section. "Ignore outside the route" = the focus view hides non-route sections and reads the live `target` for the current-step pointer; background journaling is untouched. No Python changes.

**Tech Stack:** Vendored Preact + htm. Verification: **frontend-smoke-test** (Chrome DevTools MCP) + **human-audit**. No JS unit harness.

**Source spec:** `docs/superpowers/specs/2026-06-14-route-builder-design.md` (§6 Practice focus; §3 "restriction = attention not data", "retry freely"). **Depends on Phases A+B** (merged): `GET /api/routes`, `GET /api/routes/{id}` (steps with `step_rate`/`cumulative`/`candidates[].display`/`kind`), and the existing `POST /api/target`.

**Scope note:** Plan **3 of 5**. Focus/practice ONLY. The full-game run timer (Phase D) and run history (Phase E) are separate. Do NOT build run UI here.

**Convention:** commit messages end with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. Stage explicit paths (`git add -A` is hook-blocked). Verify the branch before each commit. Execute in an isolated worktree off current master.

**Key design decisions (locked):**
- The picker + active-route state live **in `practice.js`** with `localStorage` (`sm64.activeRoute`) — NOT in `store.js`. Rationale: it's a Practice-tab concern; keeping it local avoids churning the shared store (Phase D will touch `store.js` for run WS). Minor deviation from the spec's "store.js" note, intentional.
- When a route is active, `RouteFocus` **replaces** the normal star/segment/unassigned lists and the StageBanner (focus = only the route). The ControlBar (sort / hide-resets) and the stats menu stay.
- Each step renders a **compact header** (number, name(s), CURRENT/NEXT badge, group `N of M` chip, step% + cum%, a "practice" button per candidate). The **current step** (the one whose candidate is the live target; else step 1) additionally renders the **full `StarSection`/`SegmentSection`** inline (attempts, PB, timeline) for the matching session section when it exists. Clicking a candidate sets the target → that section appears → it becomes current. Auto-advance is implicit (the target follows completions); the user can click any step to retry.

---

## File Structure

- **Modify** `src/sm64_events/ui/components/practice.js` — add `getJSON` import; add `RouteFocus` + helpers; add the route picker + active-route state to `Practice`; branch the render to `RouteFocus` when a route is active.
- **Modify** `src/sm64_events/ui/index.html` — CSS for the focus step rows + badges.
- **Modify** `README.md` / `CLAUDE.md` — note Route Practice focus.

No new endpoints, no Python. Full pytest suite stays green (unaffected) as a sanity check.

---

## Task 1: Focus-mode CSS

**Files:**
- Modify: `src/sm64_events/ui/index.html` (inside `<style>`, after the route-builder rules from Phase B)

- [ ] **Step 1: Add the CSS**

Insert after the `.routejson { … }` rule:

```css
  .routefstep { border: 1px solid #2c3140; border-radius: 8px; padding: .4rem .6rem; margin: 0 0 .6rem 0; }
  .routefstep.active-star { border-color: #e0c36a; }
  .routecur { border-color: #4ca; color: #4ca; }
  .routefstep .shead { align-items: center; }
```

- [ ] **Step 2: Commit**

```bash
git add src/sm64_events/ui/index.html
git commit -m "feat(ui): route-focus CSS"
```

---

## Task 2: RouteFocus + picker in practice.js

**Files:**
- Modify: `src/sm64_events/ui/components/practice.js`

- [ ] **Step 1: Add `getJSON` to the api import**

Change the existing import line:

```javascript
import { send } from "../api.js";
```

to:

```javascript
import { getJSON, send } from "../api.js";
```

- [ ] **Step 2: Add the RouteFocus component + helpers**

Insert this block immediately **before** `function ControlBar({ ui }) {` (so `RouteFocus` can reference the already-defined `StarSection` and `SegmentSection`):

```javascript
// --- Route Practice focus (Phase C) ---------------------------------------
// Non-destructive focus layer: when a route is active the Practice tab shows
// ONLY that route's members, in route order. The current-step pointer reads
// the live target; clicking a candidate sets the target (retry anything
// freely). Driven by the route view (GET /api/routes/{id}) for order + names +
// %s, cross-referenced to the session view for the current step's full section.
const fpct = (r) => `${Math.round((r ?? 0) * 100)}%`;

function candIsTarget(c, tgt) {
  return c.kind === "segment"
    ? (tgt.kind === "segment" && tgt.segment_id === c.segment_id)
    : (tgt.kind !== "segment" && tgt.course_id === c.course && tgt.star_id === c.star);
}

async function setTargetCandidate(c, t) {
  if (c.kind === "segment")
    await send("POST", "/api/target", { kind: "segment", segment_id: c.segment_id });
  else
    await send("POST", "/api/target", { course_id: c.course, star_id: c.star });
  t.refresh();
}

function RouteFocus({ rv, t, ui, freshIds }) {
  const v = t.view;
  const tgt = v.target || {};
  // current = first step whose any candidate is the live target; else step 0
  // (the suggested start). next = the following step (badge only — advancing is
  // a suggestion; the target auto-follows completions, the user may click any
  // step to retry).
  let currentIdx = rv.steps.findIndex((s) =>
    s.candidates.some((c) => candIsTarget(c, tgt)));
  if (currentIdx === -1) currentIdx = 0;

  const sectionFor = (c) => c.kind === "segment"
    ? (v.segments || []).find((s) => s.segment_id === c.segment_id)
    : v.stars.find((s) => s.course_id === c.course && s.star_id === c.star);

  return html`<div>
    <div class="meta listhead">route — ${rv.name}</div>
    ${rv.steps.length === 0
      ? html`<p class="meta">This route has no steps yet — add some in the Routes tab.</p>`
      : null}
    ${rv.steps.map((s, i) => {
      const isCurrent = i === currentIdx;
      const badge = isCurrent
        ? html`<span class="chip routecur">▶ CURRENT</span>`
        : i === currentIdx + 1 ? html`<span class="chip">NEXT</span>` : null;
      return html`<div class="routefstep ${isCurrent ? "active-star" : ""}">
        <div class="shead">
          <span class="routenum">${i + 1}.</span>
          ${badge}
          ${s.candidates.length > 1
            ? html`<span class="chip">${s.need} of ${s.candidates.length}</span>` : null}
          ${s.label ? html`<b>${s.label}</b>` : null}
          ${s.candidates.map((c) => html`<button
              class=${candIsTarget(c, tgt) ? "pb-glow" : ""}
              onclick=${() => setTargetCandidate(c, t)}
              title="practice this">${c.display}</button>`)}
          <span style="flex:1"></span>
          <span class="routerate">step ${fpct(s.step_rate)}</span>
          <span class="routecum">cum ${fpct(s.cumulative)}</span>
        </div>
        ${isCurrent ? s.candidates.map((c) => {
          const sec = sectionFor(c);
          if (!sec) return null;   // not the target yet / no history — compact only
          return c.kind === "segment"
            ? html`<${SegmentSection} key=${`seg:${sec.segment_id}`}
                sec=${sec} t=${t} ui=${ui} pinned=${false} freshIds=${freshIds} />`
            : html`<${StarSection} key=${`${sec.course_id}:${sec.star_id}`}
                sec=${sec} t=${t} ui=${ui} pinned=${false} freshIds=${freshIds} />`;
        }) : null}
      </div>`;
    })}
  </div>`;
}
```

- [ ] **Step 3: Add the picker + active-route state to `Practice`**

In `function Practice({ t })`, after the `const freshIds = useFreshAttemptIds(t);` line and before `const v = t.view;`, add:

```javascript
  const [routes, setRoutes] = useState([]);
  const [activeRouteId, setActiveRouteId] = useState(() => {
    const s = localStorage.getItem("sm64.activeRoute");
    return s ? Number(s) : null;
  });
  const [routeView, setRouteView] = useState(null);
  useEffect(() => { getJSON("/api/routes").then(setRoutes).catch(() => {}); }, []);
  // Refetch the resolved route view on selection change AND on every session
  // view update, so per-step/cumulative % stay live as attempts land. A 404
  // (route deleted) clears it → the tab falls back to normal practice.
  useEffect(() => {
    if (activeRouteId == null) { setRouteView(null); return; }
    getJSON(`/api/routes/${activeRouteId}`).then(setRouteView).catch(() => setRouteView(null));
  }, [activeRouteId, t.view]);
  const pickRoute = (id) => {
    if (id == null) localStorage.removeItem("sm64.activeRoute");
    else localStorage.setItem("sm64.activeRoute", String(id));
    setActiveRouteId(id);
  };
```

- [ ] **Step 4: Render the picker and branch the body**

In the returned JSX of `Practice`, replace the existing tail starting at `<${StageBanner} t=${t} />` through the end of the `return` with:

```javascript
    <div class="bar">
      <label class="meta">Focus route${" "}
        <select value=${activeRouteId ?? ""}
            onchange=${(e) => pickRoute(e.target.value ? Number(e.target.value) : null)}>
          <option value="">— none (all practice) —</option>
          ${routes.map((r) => html`<option value=${r.id}>${r.name}</option>`)}
        </select></label>
      ${routeView ? html`<span class="meta">focused — non-route stars/segments hidden;
        history still records</span>` : null}
    </div>
    ${routeView
      ? html`<${RouteFocus} rv=${routeView} t=${t} ui=${ui} freshIds=${freshIds} />`
      : html`<div>
        <${StageBanner} t=${t} />
        ${pinnedSegs.map((sec) => html`<${SegmentSection} key=${`seg:${sec.segment_id}`} sec=${sec} t=${t} ui=${ui} pinned=${true} freshIds=${freshIds} />`)}
        ${activeStar && html`<${StarSection} key=${`${activeStar.course_id}:${activeStar.star_id}`} sec=${activeStar} t=${t} ui=${ui} pinned=${true} freshIds=${freshIds} />`}
        ${v.stars.length === 0 && segs.length === 0 && v.unassigned.length === 0
          ? html`<p class="meta">No attempts this session yet — grab a star.</p>` : ""}
        ${restSegs.length > 0 && html`<div class="meta listhead">segments — recent activity first</div>`}
        ${restSegs.map((sec) => html`<${SegmentSection} key=${`seg:${sec.segment_id}`} sec=${sec} t=${t} ui=${ui} pinned=${false} freshIds=${freshIds} />`)}
        ${restStars.length > 0 && html`<div class="meta listhead">stars — recent activity first</div>`}
        ${restStars.map((sec) => html`<${StarSection} key=${`${sec.course_id}:${sec.star_id}`} sec=${sec} t=${t} ui=${ui} pinned=${false} freshIds=${freshIds} />`)}
        ${v.unassigned.length > 0 && html`<div class="starsec">
          <div class="shead"><b>No target</b>
            <span class="meta">failures before any star was grabbed or set</span></div>
          <${AttemptTable} attempts=${v.unassigned} rows=${unassignedRows} t=${t} freshIds=${freshIds} />
          <${HideToggle} hidden=${unassignedHidden}
                         showHidden=${showUnassignedHidden}
                         setShowHidden=${setShowUnassignedHidden} />
        </div>`}
      </div>`}`;
```

> This keeps the exact existing normal-practice markup inside the `: html\`<div>…</div>\`` branch; only the wrapping `<div>` and the `routeView ?` conditional are new. Do not change the `pinnedSegs`/`restSegs`/`restStars`/`unassignedRows` computations above — they're still used by the normal branch.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/ui/components/practice.js
git commit -m "feat(ui): Route Practice focus mode (route-ordered, current/next, retry-free)"
```

---

## Task 3: Frontend smoke test + human-audit (the gate)

- [ ] **Step 1: Python sanity gate**

Run: `uv run pytest -q`
Expected: PASS, same count as before (Phase C is JS/HTML only).

- [ ] **Step 2: Start the server** (manage-server skill, or `uvicorn sm64_events.main:app --port 8065 --timeout-graceful-shutdown 3` from the worktree if 8064 is occupied). Pre-seed at least one route with 2–3 steps (via the Routes tab or `POST /api/routes`) so focus mode has something to show.

- [ ] **Step 3: frontend-smoke-test skill** — drive this script, console clean at each step:
  1. Practice tab → a **"Focus route"** picker appears with "— none (all practice) —" plus the route(s).
  2. Pick a route → the normal star/segment lists + StageBanner are **replaced** by the route's steps in order; a "focused — non-route … hidden" note shows.
  3. Each step shows its number, name(s), step% + cum%, and a "practice" button per candidate; a group step shows the `N of M` chip.
  4. The first step shows **▶ CURRENT**; the second shows **NEXT**.
  5. Click a candidate "practice" button on step 2 → the target moves there (the candidate button gets the gold glow), step 2 becomes **▶ CURRENT** and renders the full section (attempts/PB/timeline) inline; step 1 collapses to compact.
  6. Pick "— none —" → the normal practice view returns; reload the page → the focus selection **persists** (localStorage) and the route reopens focused.
  7. Delete the focused route in the Routes tab, return to Practice → it falls back to normal practice without error (404 handled).

- [ ] **Step 4: Fix any issues, then commit** (if needed)

```bash
git add src/sm64_events/ui/components/practice.js src/sm64_events/ui/index.html
git commit -m "fix(ui): route-focus smoke-test fixes"
```

- [ ] **Step 5: human-audit skill** — summarize, point the user at Practice → Focus route on the live page, wait for confirmation before wrapping up.

---

## Task 4: Docs

**Files:** `README.md`, `CLAUDE.md`

- [ ] **Step 1: README** — add under the Routes/Practice overview:

```markdown
- **Route Practice focus** — pick an active route on the Practice tab to show
  only that route's stars/segments, in route order, with a suggested
  current/next step and live cumulative success. Retry any step freely; your
  full history keeps recording in the background.
```

- [ ] **Step 2: CLAUDE.md** — update the `ui/components/practice.js`-related coverage by adding a row:

```
| Route Practice focus | `ui/components/practice.js` (RouteFocus) — active-route picker (localStorage `sm64.activeRoute`) replaces the normal lists with the route's members in order; current step = live target (else step 1) and renders the full Star/SegmentSection inline; click a candidate to set target/retry. Route order + %s from `GET /api/routes/{id}` |
```

- [ ] **Step 3: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: Route Practice focus in README + module map (Phase C)"
```

---

## Self-Review (completed during planning)

**Spec coverage (§6 practice focus):**
- Active-route picker filtering/reordering to route members → Task 2 (picker + `RouteFocus` replaces normal lists).
- Soft current/next pointer from the live target; auto-advance is a suggestion → Task 2 (`currentIdx` from target; NEXT is badge-only).
- Click a step to set target / retry freely → Task 2 (`setTargetCandidate`).
- "Ignore outside the route" = attention not data → Task 2 (focus view hides non-route sections; no server change; journaling untouched).
- Groups are freely-practiceable clusters (strict K-of-N only in run mode) → Task 2 (each candidate gets a practice button; no mark-off enforcement).
- Per-step + cumulative % in context → Task 2 (from the route view, refetched on `t.view` change).
- Persistence of the focus selection → Task 2 (`localStorage`).
- Graceful fallback when the route is deleted → Task 2 (404 → `setRouteView(null)`).
- Deferred (out of scope): run mode, run history, cross-client active-route sync.

**Consistency:** reuses the existing `StarSection`/`SegmentSection` (no duplication); `candIsTarget` matches the server's target shape (`kind`/`course_id`/`star_id`/`segment_id`); route-view candidate shape (`kind`, `course`, `star`, `segment_id`, `display`) matches `build_route_view` (Phase A); the normal-practice branch preserves the exact existing markup and its precomputed `pinnedSegs`/`restSegs`/`restStars`/`unassignedRows`.

**Placeholder scan:** none — full component code, exact CSS, exact insertion points.

**Risk note (flag during code review):** Task 2 Step 4 replaces a large JSX tail; the implementer must keep the normal-branch markup byte-identical to today's (only wrap it + add the conditional). The spec-reviewer/code-reviewer should diff the normal-practice branch against the pre-Phase-C version to confirm nothing in the existing layout changed.

---

## Subsequent phases (separate plan files)

- **Phase D** — Run mode: `runs` table (migration v8), `RunTracker` in `tracking/runs.py` wired into `projection.replay()`, run lifecycle (F1 start + configurable 1.36s offset, forgiving splits, abort/restart), PB/gold, run view, Focus mode, click-to-hide.
- **Phase E** — Run history (finished/aborted filter) + progression graph.
