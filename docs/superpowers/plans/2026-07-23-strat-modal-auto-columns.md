# Strategy Modal + Auto Rank-Table Columns Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every strategy a star/segment knows gets a rank-standards column (empty until times are entered), and all three strategy-creation entry points open one proper modal (name + full ladder times + optional per-rank example videos) built on a reusable `Modal` shell.

**Architecture:** Pure-frontend. Extract the update popup's backdrop/panel pattern into `ui/components/modal.js`; build `ui/components/stratmodal.js` on it; standards-table columns become the client-side union of the store's strategies with the section's known-strategies list (`sec.strategies`, already computed by views.py). All writes ride EXISTING ranks endpoints — zero Python changes.

**Tech Stack:** Preact 10 + `htm` (vendored, no build step), plain CSS in `ui/index.html`, Python/uv only to run the server and the regression suite.

**Spec:** `docs/superpowers/specs/2026-07-23-strat-modal-auto-columns-design.md`

> **Shell note (Windows / PowerShell-primary machine):** run the shell snippets below with the **Bash tool** (Git Bash, POSIX). Do **not** paste multi-line `git commit -m "…"` into PowerShell 5.1 — embedded quotes/newlines break native-arg quoting (use `git commit -F <file>` if PowerShell is unavoidable).

## Global Constraints

- **Pure frontend.** No change to any file under `server/`, `tracking/`, `storage/`, `ranks/`, `memory/`, `core/`, `detectors/`, or `desktop/`. `uv run pytest -q` must stay green (regression guard only — no new Python).
- **Browser ↔ GUI parity** (domain rule 10): all changes live in `ui/`, appearing identically in the browser tab and the desktop window.
- **No JS unit-test runner exists** in this repo — do NOT add Jest/Vitest (YAGNI, not the codebase pattern). The UI verification gate per task is: `uv run pytest -q` green + the page loads with **zero console errors** (proves the modules parse and mount) + the task's specific browser checks. Dev server: `uv run python -m sm64_events.main` from repo root → `http://127.0.0.1:8065` (port 8065 from source; the Chrome DevTools MCP tools drive the checks).
- **Update popup behavior is frozen.** Its migration onto the shell must not add dismissal: today Esc/backdrop-click do nothing on it (only its buttons act) — pass NO `onClose` from update.js so that stays true.
- **Controlled inputs use `oninput`, not `onchange`.** The page re-renders on every poll tick; `onchange` values in flight get wiped (see the point-of-use comment in segments.js and commit f042419). Every new input in this plan follows that rule.
- **Iron never gets a row/threshold** — it is the implicit floor everywhere (`classify.py`); the modal's ladder is `RANK_NAMES` minus Iron.

## File Structure

| File | Responsibility |
|---|---|
| `src/sm64_events/ui/components/modal.js` (new) | THE shared modal shell: backdrop + panel + optional Esc/backdrop dismissal + footer slot. Stateless. |
| `src/sm64_events/ui/components/stratmodal.js` (new) | The strategy-creation modal: name + ladder (time + video per rank), Save/Cancel, inline errors. |
| `src/sm64_events/ui/components/update.js` (modify) | Migrates onto `Modal` (children-only use; no footer prop, no onClose). |
| `src/sm64_events/ui/components/standards.js` (modify) | Union columns via new `strategies` prop; × tooltip; `+ Strategy` opens StratModal. |
| `src/sm64_events/ui/components/practice.js` (modify) | Passes `sec.strategies` to both StandardsPanel call sites; StarSection `__new` opens StratModal. |
| `src/sm64_events/ui/components/header.js` (modify) | TargetEditor `+ new strategy…` opens StratModal, replacing the inline input; select renders an unlisted current value. |
| `src/sm64_events/ui/index.html` (modify) | Small CSS additions for the strat modal (`.stratname`, `.stdvid`, `.modal-error`). |

---

### Task 1: `Modal` shell + update.js migration

One deliverable: the shell has no standalone UI until something renders it, and migrating the update popup both proves the shell and removes the duplicated markup. Zero visual/behavioral change to the popup.

**Files:**
- Create: `src/sm64_events/ui/components/modal.js`
- Modify: `src/sm64_events/ui/components/update.js` (imports ≈ lines 6–10; the `return html` block, lines 76–107)

**Interfaces:**
- Consumes: existing `.modal-backdrop` / `.modal` / `.modal-actions` CSS in `ui/index.html` (unchanged).
- Produces: `Modal({ title, onClose, footer, children })` exported from `ui/components/modal.js`. `onClose` optional — when absent, Esc and backdrop-click do nothing (the update popup relies on this). `footer` optional — renders inside `.modal-actions`. Tasks 2–5 consume this exact signature.

- [ ] **Step 1: Create the shell**

Create `src/sm64_events/ui/components/modal.js` with exactly:

```js
// src/sm64_events/ui/components/modal.js — THE shared modal shell (backdrop +
// panel), extracted from the update popup so every modal keeps one look and
// one dismissal contract. Stateless: callers own visibility (render it or
// don't) and pass onClose for dismissal. onClose is OPTIONAL — when absent,
// Esc/backdrop-click do nothing (the update popup must not be dismissable
// that way). Clicks inside the panel never dismiss (stopPropagation).
import { h } from "preact";
import { useEffect } from "preact/hooks";
import htm from "htm";

const html = htm.bind(h);

export function Modal({ title, onClose, footer, children }) {
  useEffect(() => {
    if (!onClose) return undefined;
    const onKey = (keyEvent) => { if (keyEvent.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  return html`<div class="modal-backdrop" onclick=${() => onClose && onClose()}>
    <div class="modal" onclick=${(clickEvent) => clickEvent.stopPropagation()}>
      ${title ? html`<h2>${title}</h2>` : null}
      ${children}
      ${footer ? html`<div class="modal-actions">${footer}</div>` : null}
    </div>
  </div>`;
}
```

- [ ] **Step 2: Migrate update.js onto it**

In `src/sm64_events/ui/components/update.js`, add the import after the `htm` import (line 8):

```js
import { Modal } from "./modal.js";
```

Replace the entire `return html` block (lines 76–107) with:

```js
  return html`<${Modal} title=${`Update available — v${st.latest}`}>
    <div class="meta">You're on v${st.current}.</div>
    <div class="update-notes"
         dangerouslySetInnerHTML=${{ __html: renderNotes(st.notes) }}></div>
    <p><a href=${st.html_url} target="_blank">View this release on GitHub →</a></p>
    ${applying
      ? (st.state === "error"
        ? html`
          <div class="meta">Update failed — your current version is unchanged.</div>
          <div class="modal-actions">
            <button onclick=${onClose}>Close</button>
            <a class="btnlink" href=${st.html_url}
               target="_blank">Download from GitHub</a>
          </div>`
        : html`
          <div class="meta">Installing… the app will restart automatically.</div>
          <div class="progress"><div class="progress-bar"
               style=${{ width: pct + "%" }}></div></div>`)
      : html`
        <div class="modal-actions">
          ${st.writable
            ? html`<button onclick=${() => t.applyUpdate()}>Update now</button>`
            : html`<a class="btnlink" href=${st.html_url}
                      target="_blank">Download from GitHub</a>`}
          <button onclick=${onSkip}>Skip this version</button>
          <button onclick=${onLater}>Later</button>
        </div>`}
  <//>`;
```

Note: NO `onClose` prop is passed — Esc/backdrop must stay inert on this popup. The state-dependent `.modal-actions` blocks stay as children (the `footer` prop is a convenience, not mandatory). `<//>` is htm's close-component tag.

- [ ] **Step 3: Regression + browser check**

Run: `uv run pytest -q` → all pass.
Start the dev server (`uv run python -m sm64_events.main` from repo root, background). Open `http://127.0.0.1:8065` via Chrome DevTools MCP: **zero console errors**. Then restart the server with `SM64_UPDATE_FAKE=1` set to render the update popup in dev: it must look exactly as before (title, notes box, buttons), Esc and backdrop-click must NOT dismiss it, and "Later" must dismiss it.

- [ ] **Step 4: Commit**

```bash
git add src/sm64_events/ui/components/modal.js src/sm64_events/ui/components/update.js
git commit -m "feat(ui): extract shared Modal shell from the update popup

Stateless backdrop+panel with optional Esc/backdrop dismissal; the
update popup passes no onClose so its frozen no-dismiss behavior is
unchanged. Groundwork for the strategy-creation modal."
```

---

### Task 2: Auto-columns — standards table shows every known strategy

Independent of Task 1. The table's columns become the union of the store's strategies (community order first) and the section's known-strategies list; the existing "logless" gets its empty column retroactively.

**Files:**
- Modify: `src/sm64_events/ui/components/standards.js` (signature line 16; `strats` derivation line 58; cell lookup line 81; × button title line 76)
- Modify: `src/sm64_events/ui/components/practice.js` (StandardsPanel call sites, lines 443–444 and 530–531)

**Interfaces:**
- Consumes: `sec.strategies` (array of strat names — views.py's registered+observed+standards union, present on BOTH star and segment sections).
- Produces: `StandardsPanel({ entity, activeStrat, strategies, onChanged })` — the new optional `strategies` prop. Task 3 modifies this same file and assumes this signature.

- [ ] **Step 1: Union the columns in standards.js**

Change the signature (line 16) to:

```js
export function StandardsPanel({ entity, activeStrat, strategies, onChanged }) {
```

Replace the `strats` derivation (line 58, `const strats = data ? Object.keys(data.strategies) : [];`) with:

```js
  // Columns = store strategies (community order first) + every other strat
  // this section knows (registered / used on attempts — sec.strategies from
  // views.py). A known strat with no store entry renders an empty column, so
  // custom strats are fillable the moment they exist. Object.hasOwn (not
  // `in`): a strat named e.g. "constructor" must not vanish via the proto
  // chain.
  const strats = data
    ? [...Object.keys(data.strategies),
       ...(strategies || []).filter((s) => !Object.hasOwn(data.strategies, s))]
    : [];
```

In the cell renderer (line 81), guard the missing store entry — replace

```js
            const v = data.strategies[s][rank];
```

with

```js
            const v = (data.strategies[s] || {})[rank];
```

On the × button (line 76), change the tooltip from `title="remove strategy"` to reflect the clear-data semantics:

```js
title="clear this strategy's standards"
```

- [ ] **Step 2: Pass the section list from practice.js**

Both call sites gain the prop. Lines 443–444 (StarSection):

```js
    <${StandardsPanel} entity=${`star:${sec.course_id}:${sec.star_id}`}
        activeStrat=${sec.last_strat} strategies=${sec.strategies} onChanged=${t.refresh} />
```

Lines 530–531 (SegmentSection):

```js
    <${StandardsPanel} entity=${`segment:${sec.segment_id}`}
        activeStrat=${sec.last_strat} strategies=${sec.strategies} onChanged=${t.refresh} />
```

- [ ] **Step 3: Regression + browser check**

Run: `uv run pytest -q` → all pass.
Browser (`http://127.0.0.1:8065`): zero console errors. Open the LLL Red-Hot Log Rolling section's "Rank standards" panel: a **logless** column now appears after the seed strats, every cell "—", no × behavior change needed yet, and the active-strat column highlight still works when logless is the active strat. The star's rank banner must still show "no rank standards for this strategy" (empty ladder → no rank) until times exist.

- [ ] **Step 4: Commit**

```bash
git add src/sm64_events/ui/components/standards.js src/sm64_events/ui/components/practice.js
git commit -m "feat(ui): standards table auto-columns for every known strategy

Columns = store strategies + the section's registered/observed strats
(client-side union of sec.strategies — zero server changes). Custom
strats like a user-added 'logless' get an empty, fillable column
retroactively; empty ladders still award no rank. The x button keeps
its DELETE but the tooltip now says clear — an in-use strat's column
persists by design (user-confirmed semantics)."
```

---

### Task 3: `StratModal` + standards-table wiring

The strategy-creation modal itself, wired into its first entry point (`+ Strategy`). Depends on Task 1 (Modal) and Task 2 (union `strats` in scope at the wiring site).

**Files:**
- Create: `src/sm64_events/ui/components/stratmodal.js`
- Modify: `src/sm64_events/ui/components/standards.js` (import block; replace `addStrat` lines 26–31; the `+ Strategy` button line 68; render the modal before the closing `</div>` of the panel)
- Modify: `src/sm64_events/ui/index.html` (CSS: after the `.btnlink` rule ≈ line 207)

**Interfaces:**
- Consumes: `Modal({ title, onClose, footer, children })` from Task 1; `RANK_NAMES`, `rankColor` from `./ranks.js`; `send(method, path, body)` from `../api.js` (throws on non-2xx); existing endpoints `POST /api/ranks/standards/{entity}` `{strategy}`, `PUT …/{entity}/{strat}/{rank}` `{seconds}`, `PUT …/{entity}/{strat}/{rank}/video` `{url}`.
- Produces: `StratModal({ entity, existing, onSaved, onClose })` from `ui/components/stratmodal.js` — `entity` is `star:{c}:{s}` or `segment:{id}`; `existing` is an array of strat names for duplicate rejection; `onSaved(name)` fires after all writes succeed; `onClose` on Cancel/Esc/backdrop. Tasks 4–5 consume this exact signature.

- [ ] **Step 1: Create the modal component**

Create `src/sm64_events/ui/components/stratmodal.js` with exactly:

```js
// src/sm64_events/ui/components/stratmodal.js — the strategy-creation modal:
// name + the full rank ladder (blank time + optional example-video URL per
// rank), Save/Cancel. Writes ride the EXISTING ranks endpoints in order
// (create → PUT each filled threshold → PUT each filled video), so a partial
// failure leaves a valid strat and re-Save is idempotent (create no-ops,
// PUTs overwrite). Callers own what happens after save (set active / reload)
// via onSaved; Cancel/Esc/backdrop write nothing.
import { h } from "preact";
import { useState } from "preact/hooks";
import htm from "htm";
import { send } from "../api.js";
import { Modal } from "./modal.js";
import { RANK_NAMES, rankColor } from "./ranks.js";

const html = htm.bind(h);
const enc = encodeURIComponent;
// Iron is the implicit floor everywhere (classify.py) — never a ladder row.
const LADDER_RANKS = RANK_NAMES.filter((rank) => rank !== "Iron");

export function StratModal({ entity, existing, onSaved, onClose }) {
  const [name, setName] = useState("");
  const [times, setTimes] = useState({});     // rank -> raw input string
  const [videos, setVideos] = useState({});   // rank -> raw input string
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);

  async function save() {
    const strat = name.trim();
    if (!strat) { setError("Strategy name required."); return; }
    if ((existing || []).includes(strat)) {
      setError(`"${strat}" already exists here.`); return;
    }
    setSaving(true); setError(null);
    try {
      await send("POST", `/api/ranks/standards/${enc(entity)}`, { strategy: strat });
      for (const rank of LADDER_RANKS) {
        const rawTime = (times[rank] || "").trim();
        if (rawTime !== "") {
          const seconds = parseFloat(rawTime);
          if (!isNaN(seconds)) {
            await send("PUT",
              `/api/ranks/standards/${enc(entity)}/${enc(strat)}/${enc(rank)}`,
              { seconds });
          }
        }
        const url = (videos[rank] || "").trim();
        if (url) {
          await send("PUT",
            `/api/ranks/standards/${enc(entity)}/${enc(strat)}/${enc(rank)}/video`,
            { url });
        }
      }
      onSaved(strat);
    } catch (requestError) {
      // Keep the modal open: the strat may exist with partial data — re-Save
      // is safe (idempotent), and the auto-column union means nothing created
      // here can end up invisible.
      setError(String(requestError));
      setSaving(false);
    }
  }

  return html`<${Modal} title="New strategy" onClose=${saving ? null : onClose}
      footer=${html`
        <button onclick=${save} disabled=${saving}>${saving ? "Saving…" : "Save"}</button>
        <button onclick=${onClose} disabled=${saving}>Cancel</button>`}>
    <input class="stratname" placeholder="strategy name" value=${name}
           oninput=${(inputEvent) => setName(inputEvent.target.value)} />
    <div class="meta" style="margin:.4rem 0 .2rem">
      Rank standards — optional; leave blank and no rank is awarded until times are entered.
    </div>
    <table class="stdtable">
      <thead><tr><th>Rank</th><th>Time (s)</th><th>Example video (optional)</th></tr></thead>
      <tbody>
      ${LADDER_RANKS.map((rank) => html`<tr>
        <td style=${`background:${rankColor(rank)};color:#111;font-weight:700`}>${rank}</td>
        <td><input class="stdinp" placeholder="—" value=${times[rank] || ""}
              oninput=${(inputEvent) =>
                setTimes({ ...times, [rank]: inputEvent.target.value })} /></td>
        <td><input class="stdvid" placeholder="https://…" value=${videos[rank] || ""}
              oninput=${(inputEvent) =>
                setVideos({ ...videos, [rank]: inputEvent.target.value })} /></td>
      </tr>`)}
      </tbody>
    </table>
    ${error ? html`<div class="modal-error">${error}</div>` : null}
  <//>`;
}
```

- [ ] **Step 2: Add the CSS**

In `src/sm64_events/ui/index.html`, after the `.btnlink { … }` rule (≈ line 207), insert:

```css
  .stratname { width: 100%; box-sizing: border-box; margin-bottom: .2rem; }
  .stdvid { width: 200px; }
  .modal-error { color: #e08585; font-size: .85em; margin-top: .5rem; }
```

- [ ] **Step 3: Wire `+ Strategy` in standards.js**

Add the import after the `ranks.js` import (line 12):

```js
import { StratModal } from "./stratmodal.js";
```

Add modal state next to the other `useState` calls (after line 19):

```js
  const [showAdd, setShowAdd] = useState(false);
```

Delete the `addStrat` function entirely (lines 26–31). Change the `+ Strategy` button (line 68) to:

```js
        ${editing ? html`<button class="meta" onclick=${() => setShowAdd(true)}>+ Strategy</button>` : null}
```

Render the modal at the end of the panel — change the component's final lines from

```js
    </div>` : null}
  </div>`;
```

to

```js
    </div>` : null}
    ${showAdd ? html`<${StratModal} entity=${entity} existing=${strats}
        onSaved=${async () => { setShowAdd(false); await load(); onChanged && onChanged(); }}
        onClose=${() => setShowAdd(false)} />` : null}
  </div>`;
```

(`strats` is Task 2's union list — the correct duplicate-rejection set; it is only non-empty when the panel is open with data, which is the only time `showAdd` is reachable.)

- [ ] **Step 4: Regression + browser check**

Run: `uv run pytest -q` → all pass.
Browser: zero console errors. In a star section → Rank standards → Edit → `+ Strategy`: the modal opens with the name field, 8 colored rank rows (Mario→Bronze, NO Iron), blank time + video inputs. Check: empty-name Save shows the inline error; a duplicate name (e.g. `Standard`) shows the inline error; Cancel/Esc/backdrop close with **no new column**; saving a strat with two times filled creates its column showing exactly those two times (others "—"); saving with a video URL makes that rank's time a link in view mode. Type into inputs across a poll tick (~1 s) — text must not be wiped (oninput rule).

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/ui/components/stratmodal.js src/sm64_events/ui/components/standards.js src/sm64_events/ui/index.html
git commit -m "feat(ui): strategy-creation modal, wired into the standards table

Name + full rank ladder (time + optional example video per rank) on the
shared Modal shell; Save rides the existing ranks endpoints in order so
partial failure is re-Save-safe (create no-ops, PUTs overwrite). First
entry point: the standards table's + Strategy button."
```

---

### Task 4: Practice-tab dropdown opens the modal

Replaces the `window.prompt` in StarSection's `setStrat`. Depends on Task 3.

**Files:**
- Modify: `src/sm64_events/ui/components/practice.js` (import block ≈ line 12; StarSection state ≈ line 351; `setStrat` lines 364–383; render block — add the modal next to the `<select>`'s parent `.shead` close, after line 419)

**Interfaces:**
- Consumes: `StratModal({ entity, existing, onSaved, onClose })` from Task 3; existing `setStrat`, `stratNonce`, `sec.strategies`, `POST /api/strat`.
- Produces: nothing new for later tasks.

- [ ] **Step 1: Wire the modal into StarSection**

Add the import after the StandardsPanel import (line 12):

```js
import { StratModal } from "./stratmodal.js";
```

Add state after the `stratNonce` line (351):

```js
  const [showStratModal, setShowStratModal] = useState(false);
```

Replace the `__new` branch at the top of `setStrat` (lines 365–368) — delete

```js
    if (v === "__new") {
      v = (window.prompt("New strategy name:") || "").trim();
      if (!v) { setStratNonce((n) => n + 1); return; }   // cancelled: snap back
    }
```

and insert

```js
    if (v === "__new") { setShowStratModal(true); return; }
```

(The select's DOM briefly shows "+ new strat…" while the modal is up; both exits below re-sync it.)

After the `.shead` closing `</div>` (line 419), add the modal render:

```js
    ${showStratModal ? html`<${StratModal}
        entity=${`star:${sec.course_id}:${sec.star_id}`} existing=${sec.strategies}
        onSaved=${(stratName) => { setShowStratModal(false); setStrat(stratName); }}
        onClose=${() => { setShowStratModal(false); setStratNonce((n) => n + 1); }} />` : null}
```

`onSaved` reuses `setStrat` (the saved name is never `"__new"`), which POSTs `/api/strat` — registering and activating the strat — then refreshes; `onClose` uses the established `stratNonce` snap-back so the dropdown returns to the server's truth.

- [ ] **Step 2: Regression + browser check**

Run: `uv run pytest -q` → all pass.
Browser: zero console errors. In the Active Star section, pick `+ new strat…`: the modal opens (no browser prompt). Cancel → dropdown snaps back to the previous value. Create `testmodal` with a Mario time of `11.5` → dropdown now shows `testmodal` selected, the standards panel shows its column with `11.50` in the Mario row, and the rank banner switches off "no rank standards" once a PB with that strat exists. Clean up: × the `testmodal` column (clears data), set the strat back.

- [ ] **Step 3: Commit**

```bash
git add src/sm64_events/ui/components/practice.js
git commit -m "feat(ui): practice-tab '+ new strat' opens the strategy modal

Replaces window.prompt; save re-enters setStrat so POST /api/strat
still registers+activates and refreshes, cancel rides the existing
stratNonce snap-back."
```

---

### Task 5: Header target picker opens the modal

Replaces TargetEditor's `adding` inline-input state. Depends on Task 3.

**Files:**
- Modify: `src/sm64_events/ui/components/header.js` (import block; TargetEditor lines 136–190)

**Interfaces:**
- Consumes: `StratModal({ entity, existing, onSaved, onClose })` from Task 3; `v.strategies` (the view's REGISTERED-strategies KV — note it does NOT include a just-created strat until Set target registers it).
- Produces: nothing new for later tasks.

- [ ] **Step 1: Rewire TargetEditor**

Add the import next to header.js's other component imports:

```js
import { StratModal } from "./stratmodal.js";
```

In `TargetEditor`, replace the `adding`/`newStrat` state (lines 144–145):

```js
  const [adding, setAdding] = useState(false);
  const [newStrat, setNewStrat] = useState("");
```

with

```js
  const [showStratModal, setShowStratModal] = useState(false);
  // Remounts the select after a cancelled "+ new strategy…" pick — same
  // phantom-value pathology and fix as practice.js's stratNonce.
  const [stratNonce, setStratNonce] = useState(0);
```

In `pickStar` (line 150), replace `setAdding(false);` with `setShowStratModal(false);`.

In `apply()` (line 154), replace

```js
    const chosen = adding ? newStrat.trim() : strat;
```

with

```js
    const chosen = strat;
```

Replace the whole strat-row block (lines 176–186, the `${adding ? … : …}` ternary) with:

```js
      <select key=${`hstrat-${stratNonce}`} value=${strat}
              onchange=${(changeEvent) => changeEvent.target.value === "__new__"
                ? setShowStratModal(true) : setStrat(changeEvent.target.value)}>
        <option value="">(no strategy)</option>
        ${options.map((s) => html`<option value=${s}>${s}</option>`)}
        ${strat && !options.includes(strat)
          ? html`<option value=${strat}>${strat}</option>` : null}
        <option value="__new__">+ new strategy…</option>
      </select>
```

(The unlisted-value `<option>` is REQUIRED: `options` is the registered-strategies KV, which won't contain a modal-created strat until "Set target" registers it — without this the select would display blank after save.)

After the closing `</div>` of that row (line 188), before the popover's final `</div>`, add:

```js
    ${showStratModal ? html`<${StratModal}
        entity=${`star:${Number(course)}:${Number(star)}`} existing=${options}
        onSaved=${(stratName) => { setShowStratModal(false); setStrat(stratName); }}
        onClose=${() => { setShowStratModal(false); setStratNonce((n) => n + 1); }} />` : null}
```

- [ ] **Step 2: Regression + browser check**

Run: `uv run pytest -q` → all pass.
Browser: zero console errors. Header → edit target → strat select → `+ new strategy…`: modal opens; Cancel → select snaps back. Create `headertest` → the select shows `headertest` selected (the unlisted-value option); "Set target" applies it (header target line shows the strat; it is now registered). The standards panel for that star shows the `headertest` column. Clean up: × the column, reset the target.

- [ ] **Step 3: Commit**

```bash
git add src/sm64_events/ui/components/header.js
git commit -m "feat(ui): header target picker uses the strategy modal

Replaces the adding/newStrat inline input; the select renders its
current value as an option when absent from the registered list (a
modal-created strat isn't registered until Set target), so the picker
can't display blank after save."
```

---

### Task 6: Full smoke sweep + docs

**Files:**
- Modify: `CLAUDE.md` (module map)
- Modify: `docs/superpowers/plans/2026-07-23-strat-modal-auto-columns.md` (tick checkboxes)

**Interfaces:** none — verification + documentation only.

- [ ] **Step 1: Full-suite regression**

Run: `uv run pytest -q` → all pass (zero Python changed; any failure means scope leaked).

- [ ] **Step 2: Frontend smoke sweep (frontend-smoke-test skill)**

With the dev server up, walk the whole surface once in the browser via Chrome DevTools MCP, console clean throughout: (1) logless auto-column present+empty; (2) create a strat from each of the three entry points; (3) × on an in-use strat clears data but keeps the column, × on a modal-only never-used strat removes the column; (4) rank banner stays "no rank standards" until a time is entered, then ranks; (5) `SM64_UPDATE_FAKE=1` popup renders and is not Esc-dismissable; (6) Compare/Routes tabs still load (shared-file regression sniff).

- [ ] **Step 3: Update the module map**

In `CLAUDE.md`'s module map, add two rows after the "Rank UI" row and amend the Rank UI row's standards.js text:

```markdown
| Shared modal shell | `ui/components/modal.js` — `Modal({title,onClose,footer,children})`: backdrop+panel extracted from the update popup; onClose optional (absent = not dismissable — the update popup relies on that) |
| Strategy-creation modal | `ui/components/stratmodal.js` — name + full rank ladder (time + optional example video per rank) on the Modal shell; Save rides the existing ranks endpoints (create→PUT thresholds→PUT videos, idempotent re-Save); opened from the practice strat dropdown, the standards table's + Strategy, and the header target picker |
```

and in the Rank UI row, change "collapsible editable table" to "collapsible editable table; columns = store ∪ section strategies (custom strats get empty fillable columns; × clears data, column persists while the strat is in use)".

- [ ] **Step 4: Human audit (human-audit skill)**

Pause for the user: modal look/feel at all three entry points, the logless column, × semantics. This plan is done only after their sign-off.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md docs/superpowers/plans/2026-07-23-strat-modal-auto-columns.md
git commit -m "docs: module-map rows for the modal shell + strategy modal"
```
