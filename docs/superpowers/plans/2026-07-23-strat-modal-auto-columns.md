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

- [x] **Step 1: Create the shell**

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

- [x] **Step 2: Migrate update.js onto it**

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

- [x] **Step 3: Regression + browser check**

Run: `uv run pytest -q` → all pass.
Start the dev server (`uv run python -m sm64_events.main` from repo root, background). Open `http://127.0.0.1:8065` via Chrome DevTools MCP: **zero console errors**. Then restart the server with `SM64_UPDATE_FAKE=1` set to render the update popup in dev: it must look exactly as before (title, notes box, buttons), Esc and backdrop-click must NOT dismiss it, and "Later" must dismiss it.

- [x] **Step 4: Commit**

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

- [x] **Step 1: Union the columns in standards.js**

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

- [x] **Step 2: Pass the section list from practice.js**

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

- [x] **Step 3: Regression + browser check**

Run: `uv run pytest -q` → all pass.
Browser (`http://127.0.0.1:8065`): zero console errors. Open the LLL Red-Hot Log Rolling section's "Rank standards" panel: a **logless** column now appears after the seed strats, every cell "—", no × behavior change needed yet, and the active-strat column highlight still works when logless is the active strat. The star's rank banner must still show "no rank standards for this strategy" (empty ladder → no rank) until times exist.

- [x] **Step 4: Commit**

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

- [x] **Step 1: Create the modal component**

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

- [x] **Step 2: Add the CSS**

In `src/sm64_events/ui/index.html`, after the `.btnlink { … }` rule (≈ line 207), insert:

```css
  .stratname { width: 100%; box-sizing: border-box; margin-bottom: .2rem; }
  .stdvid { width: 200px; }
  .modal-error { color: #e08585; font-size: .85em; margin-top: .5rem; }
```

- [x] **Step 3: Wire `+ Strategy` in standards.js**

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

- [x] **Step 4: Regression + browser check**

Run: `uv run pytest -q` → all pass.
Browser: zero console errors. In a star section → Rank standards → Edit → `+ Strategy`: the modal opens with the name field, 8 colored rank rows (Mario→Bronze, NO Iron), blank time + video inputs. Check: empty-name Save shows the inline error; a duplicate name (e.g. `Standard`) shows the inline error; Cancel/Esc/backdrop close with **no new column**; saving a strat with two times filled creates its column showing exactly those two times (others "—"); saving with a video URL makes that rank's time a link in view mode. Type into inputs across a poll tick (~1 s) — text must not be wiped (oninput rule).

- [x] **Step 5: Commit**

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

- [x] **Step 1: Wire the modal into StarSection**

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

- [x] **Step 2: Regression + browser check**

Run: `uv run pytest -q` → all pass.
Browser: zero console errors. In the Active Star section, pick `+ new strat…`: the modal opens (no browser prompt). Cancel → dropdown snaps back to the previous value. Create `testmodal` with a Mario time of `11.5` → dropdown now shows `testmodal` selected, the standards panel shows its column with `11.50` in the Mario row, and the rank banner switches off "no rank standards" once a PB with that strat exists. Clean up: × the `testmodal` column (clears data), set the strat back.

- [x] **Step 3: Commit**

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

- [x] **Step 1: Rewire TargetEditor**

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

- [x] **Step 2: Regression + browser check**

Run: `uv run pytest -q` → all pass.
Browser: zero console errors. Header → edit target → strat select → `+ new strategy…`: modal opens; Cancel → select snaps back. Create `headertest` → the select shows `headertest` selected (the unlisted-value option); "Set target" applies it (header target line shows the strat; it is now registered). The standards panel for that star shows the `headertest` column. Clean up: × the column, reset the target.

- [x] **Step 3: Commit**

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

- [x] **Step 1: Full-suite regression**

Run: `uv run pytest -q` → all pass (zero Python changed; any failure means scope leaked).

- [x] **Step 2: Frontend smoke sweep (frontend-smoke-test skill)**

With the dev server up, walk the whole surface once in the browser via Chrome DevTools MCP, console clean throughout: (1) logless auto-column present+empty; (2) create a strat from each of the three entry points; (3) × on an in-use strat clears data but keeps the column, × on a modal-only never-used strat removes the column; (4) rank banner stays "no rank standards" until a time is entered, then ranks; (5) `SM64_UPDATE_FAKE=1` popup renders and is not Esc-dismissable; (6) Compare/Routes tabs still load (shared-file regression sniff).

- [x] **Step 3: Update the module map**

In `CLAUDE.md`'s module map, add two rows after the "Rank UI" row and amend the Rank UI row's standards.js text:

```markdown
| Shared modal shell | `ui/components/modal.js` — `Modal({title,onClose,footer,children})`: backdrop+panel extracted from the update popup; onClose optional (absent = not dismissable — the update popup relies on that) |
| Strategy-creation modal | `ui/components/stratmodal.js` — name + full rank ladder (time + optional example video per rank) on the Modal shell; Save rides the existing ranks endpoints (create→PUT thresholds→PUT videos, idempotent re-Save); opened from the practice strat dropdown, the standards table's + Strategy, and the header target picker |
```

and in the Rank UI row, change "collapsible editable table" to "collapsible editable table; columns = store ∪ section strategies (custom strats get empty fillable columns; × clears data, column persists while the strat is in use)".

- [ ] **Step 4: Human audit (human-audit skill)**

Pause for the user: modal look/feel at all three entry points, the logless column, × semantics. This plan is done only after their sign-off.

- [x] **Step 5: Commit**

```bash
git add CLAUDE.md docs/superpowers/plans/2026-07-23-strat-modal-auto-columns.md
git commit -m "docs: module-map rows for the modal shell + strategy modal"
```

---

# Addendum tasks: full delete for custom strategies (spec addendum 2026-07-23)

> These tasks END the pure-frontend constraint: Tasks 7-10 are Python with
> true red/green TDD (`uv run pytest` gates). The no-JS-test-runner rule and
> the oninput rule still bind Task 11. Entity keys are `star:{c}:{s}` /
> `segment:{id}` (ranks/standards.py `entity_key`). The tombstone lives in
> ui_state KV `deleted_strats`: `{entity_key: [names]}`.

### Task 7: `RankStandards.seeded_strategies`

**Files:**
- Modify: `src/sm64_events/ranks/standards.py` (after `user_videos`, ≈ line 130)
- Test: `tests/test_ranks_standards.py` (uses the existing `_seed(tmp_path)` helper)

**Interfaces:**
- Produces: `seeded_strategies(ek) -> list[str]` — strategy names the bundled
  seed defines for `ek`; `[]` when there is no seed or the entity is absent.
  Task 8's seeded-protection check and Task 10's GET field consume this.

- [x] **Step 1: Write the failing tests**

Append to `tests/test_ranks_standards.py`:

```python
def test_seeded_strategies_lists_seed_strats_only(tmp_path):
    s = RankStandards(tmp_path / "rs.json", seed_path=_seed(tmp_path)); s.load()
    ek = next(iter(s.to_json()["entities"]))
    seed_strats = s.seeded_strategies(ek)
    assert seed_strats == s.strategies(ek)          # fresh install: store == seed
    s.create_strategy(ek, "customx")
    assert "customx" in s.strategies(ek)
    assert "customx" not in s.seeded_strategies(ek)  # custom never seeded


def test_seeded_strategies_without_seed_is_empty(tmp_path):
    s = RankStandards(tmp_path / "rs.json", seed_path=None); s.load()
    assert s.seeded_strategies("star:1:0") == []
```

- [x] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_ranks_standards.py -q`
Expected: 2 FAIL with `AttributeError: ... 'seeded_strategies'`

- [x] **Step 3: Implement**

In `standards.py`, after `user_videos` (≈ line 130):

```python
    def seeded_strategies(self, ek) -> list:
        """Strategy names the bundled community seed defines for this entity —
        THE custom-vs-default distinction (the same one _reconcile uses).
        Seeded strats are community data: protected from full deletion."""
        seed = self._read_valid(self.seed_path)
        if seed is None:
            return []
        return list(seed["entities"].get(ek, {}).get("strategies", {}).keys())
```

- [x] **Step 4: Run to verify green**

Run: `uv run pytest tests/test_ranks_standards.py -q` → all pass.

- [x] **Step 5: Commit**

```bash
git add src/sm64_events/ranks/standards.py tests/test_ranks_standards.py
git commit -m "feat(ranks): seeded_strategies — the custom-vs-default distinction

Reads the bundled seed (same source _reconcile trusts) so the delete
path can refuse community strats without a stored flag."
```

---

### Task 8: `purge_strategy` service command + tombstone lifecycle

**Files:**
- Modify: `src/sm64_events/tracking/service.py` (`_register_strategy` ≈ line 250; the rank-command block ≈ lines 441-463; add an import of `entity_key` from `sm64_events.ranks.standards` if not present)
- Test: `tests/test_tracker_service.py` (harness: `make(tmp_path)`, `ev`, `star`; add a ranks-bearing variant below)

**Interfaces:**
- Consumes: `seeded_strategies(ek)` (Task 7); existing `set_strat`, `_register_strategy`, `create_rank_strategy`, `_rank_standards_changed`.
- Produces: `async purge_strategy(ek: str, strat: str)` — raises ValueError on seeded strats; `_clear_tombstone(db, ek, strat)` called by BOTH create paths. Task 9 reads the `deleted_strats` KV shape `{ek: [names]}`; Task 10 exposes the command over REST.

- [x] **Step 1: Write the failing tests**

Append to `tests/test_tracker_service.py` (add `import json` and the `RankStandards` import at the top with the other imports):

```python
from sm64_events.ranks.standards import RankStandards


def make_with_ranks(tmp_path):
    seed = {"version": 1, "entities": {
        "star:7:2": {"clock": "igt", "strategies": {"Standard": {"Mario": 11.76}}}}}
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(json.dumps(seed))
    ranks = RankStandards(tmp_path / "rs.json", seed_path=seed_path)
    ranks.load()
    db = Database(tmp_path / "t.db")
    svc = TrackerService(db, Broadcaster(), ranks=ranks)
    asyncio.run(svc.start())
    return db, svc


def test_purge_strategy_removes_custom_everywhere(tmp_path):
    db, svc = make_with_ranks(tmp_path)
    asyncio.run(svc.set_strat(7, 2, "logless"))            # registers + activates
    asyncio.run(svc.create_rank_strategy("star:7:2", "logless"))
    asyncio.run(svc.purge_strategy("star:7:2", "logless"))
    assert "logless" not in svc.ranks.strategies("star:7:2")
    assert "logless" not in db.get_state("strategies", {}).get("7:2", [])
    assert "logless" in db.get_state("deleted_strats", {}).get("star:7:2", [])
    assert svc.strat_by_star.get((7, 2)) is None           # strat_set null published


def test_purge_refuses_seeded_strategy(tmp_path):
    db, svc = make_with_ranks(tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(svc.purge_strategy("star:7:2", "Standard"))
    assert "Standard" in svc.ranks.strategies("star:7:2")  # untouched


def test_recreate_after_purge_clears_tombstone(tmp_path):
    db, svc = make_with_ranks(tmp_path)
    asyncio.run(svc.set_strat(7, 2, "logless"))
    asyncio.run(svc.purge_strategy("star:7:2", "logless"))
    asyncio.run(svc.set_strat(7, 2, "logless"))            # register path
    assert "logless" not in db.get_state("deleted_strats", {}).get("star:7:2", [])
    asyncio.run(svc.purge_strategy("star:7:2", "logless"))
    asyncio.run(svc.create_rank_strategy("star:7:2", "logless"))   # ranks path
    assert "logless" not in db.get_state("deleted_strats", {}).get("star:7:2", [])


def test_purge_segment_strategy_tombstones(tmp_path):
    db, svc = make_with_ranks(tmp_path)
    asyncio.run(svc.create_rank_strategy("segment:3", "fast"))
    asyncio.run(svc.purge_strategy("segment:3", "fast"))
    assert "fast" not in svc.ranks.strategies("segment:3")
    assert "fast" in db.get_state("deleted_strats", {}).get("segment:3", [])
```

- [x] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_tracker_service.py -q -k "purge or recreate"`
Expected: FAIL with `AttributeError: ... 'purge_strategy'`

- [x] **Step 3: Implement**

In `service.py`. Helper next to `_register_strategy`:

```python
    def _clear_tombstone(self, db: Database, ek: str, strat: str) -> None:
        """Re-creating a strat un-deletes it: the tombstone (see
        purge_strategy) must not outlive the name's next creation, or the
        new strat would be invisible in every dropdown."""
        tombs = db.get_state("deleted_strats", {})
        if strat in tombs.get(ek, []):
            tombs[ek] = [s for s in tombs[ek] if s != strat]
            if not tombs[ek]:
                tombs.pop(ek)
            db.set_state("deleted_strats", tombs)
```

At the END of `_register_strategy` (unconditionally — registration is creation):

```python
        self._clear_tombstone(db, entity_key(course_id, star_id), strat_tag)
```

In `create_rank_strategy`, after the `create_strategy` call:

```python
        if self.db is not None:
            self._clear_tombstone(self.db, ek, strat)
```

New command in the rank-command block:

```python
    async def purge_strategy(self, ek: str, strat: str) -> None:
        """Fully delete a CUSTOM strategy: standards data + registration +
        a tombstone hiding attempt-observed occurrences (attempts are
        journal-derived and must not be rewritten), and a journaled
        strat_set null when it was the star's active strat. Seeded
        (community) strats are protected. Re-creating the name clears the
        tombstone — see _clear_tombstone."""
        ranks = self._require_ranks()
        db = self._require_db()
        if strat in ranks.seeded_strategies(ek):
            raise ValueError(f"{strat!r} is a community default and can't be deleted")
        ranks.delete_strategy(ek, strat)
        if ek.startswith("star:"):
            _, course_s, star_s = ek.split(":")
            course_id, star_id = int(course_s), int(star_s)
            strategies = db.get_state("strategies", {})
            key = f"{course_id}:{star_id}"
            if strat in strategies.get(key, []):
                strategies[key] = [s for s in strategies[key] if s != strat]
                db.set_state("strategies", strategies)
            if self.strat_by_star.get((course_id, star_id)) == strat:
                await self.set_strat(course_id, star_id, None)
        tombs = db.get_state("deleted_strats", {})
        if strat not in tombs.get(ek, []):
            tombs[ek] = tombs.get(ek, []) + [strat]
            db.set_state("deleted_strats", tombs)
        await self._rank_standards_changed()
```

Note the ORDER: the tombstone is written AFTER the `set_strat(None)` call —
`set_strat` with a null tag never registers, so it cannot re-clear the
tombstone, but keep the order anyway so a future register-on-null change
fails the recreate test instead of silently breaking delete.

- [x] **Step 4: Run to green**

Run: `uv run pytest tests/test_tracker_service.py -q` → all pass.

- [x] **Step 5: Commit**

```bash
git add src/sm64_events/tracking/service.py tests/test_tracker_service.py
git commit -m "feat(tracking): purge_strategy — full delete for custom strats

Standards + registration removed directly; attempt-observed occurrences
are journal-derived so a deleted_strats tombstone hides them instead of
rewriting history. Active-strat clears ride the existing journaled
strat_set path; both create paths clear the tombstone so re-creating a
name is the undo."
```

---

### Task 9: views filter + mask tombstoned strats

**Files:**
- Modify: `src/sm64_events/tracking/views.py` (`_strategies_for` ≈ line 161, `_seg_strategies` ≈ line 178, `build_session_view` KV reads ≈ line 390 and the star/segment section dicts + top-level maps)
- Test: `tests/test_views.py` (harness: `make(tmp_path)`, `ev`, `star`, `build_session_view`)

**Interfaces:**
- Consumes: the `deleted_strats` KV `{ek: [names]}` (Task 8).
- Produces: sections whose `strategies` lists and every active-strat read exclude tombstoned names. The masked read sites: star section `last_strat`, segment section `last_strat`, top-level `last_strat_by_star`, `rank_by_star` grading, `_candidate_rank` (route medals), and `view["target"]["strat_tag"]`.

- [x] **Step 1: Write the failing test**

Append to `tests/test_views.py`:

```python
def test_deleted_strat_hidden_and_masked(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.set_strat(2, 2, "oldstrat"))
    asyncio.run(svc.publish(star(1350)))                   # attempt tagged oldstrat
    db.set_state("strategies", {})                         # not registered anymore
    view = build_session_view(db, svc, clock="igt")
    [sec] = view["stars"]
    assert "oldstrat" in sec["strategies"]                 # sanity: observed source
    assert sec["last_strat"] == "oldstrat"
    db.set_state("deleted_strats", {"star:2:2": ["oldstrat"]})
    view = build_session_view(db, svc, clock="igt")
    [sec] = view["stars"]
    assert "oldstrat" not in sec["strategies"]             # observed hidden
    assert sec["last_strat"] is None                       # ghost masked
    assert view["last_strat_by_star"].get("2:2") is None
    assert (view["target"].get("strat_tag") or None) is None
```

- [x] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_views.py -q -k deleted_strat`
Expected: FAIL on `"oldstrat" not in sec["strategies"]` (hidden not implemented)

- [x] **Step 3: Implement**

Add `deleted=()` params and final filters:

```python
def _strategies_for(registered: dict, attempts, course_id: int, star_id: int,
                    ranks=None, deleted=()) -> list[str]:
    ...existing body...
    return [s for s in out if s not in deleted]


def _seg_strategies(history, seg_id: int, ranks=None, deleted=()) -> list:
    ...existing body...
    return [s for s in out if s not in deleted]
```

In `build_session_view`, read the KV once next to the other state reads:

```python
    deleted_strats = db.get_state("deleted_strats", {})
```

and a local masking helper:

```python
    def masked(strat, ek):
        """A tombstoned (fully deleted) strat must never surface as an
        active/last strat — the dropdowns no longer offer it."""
        return None if strat and strat in deleted_strats.get(ek, []) else strat
```

Then: star sections pass `deleted=deleted_strats.get(f"star:{course_id}:{star_id}", [])`
to `_strategies_for` and wrap `last_strat` in `masked(..., ek)`; segment
sections likewise with `f"segment:{seg_id}"`; `last_strat_by_star` filters
each value through `masked`; the `rank_by_star` grading and
`_candidate_rank` treat a masked strat as None (unranked); `view["target"]`'s
`strat_tag` is wrapped in `masked` for the target's entity. Keep each mask
at the point the value is read. (`_candidate_rank` lives outside
`build_session_view` — pass the deleted-KV or re-read it there; keep it
one read per view build where practical.)

- [x] **Step 4: Run to green + full suite**

Run: `uv run pytest tests/test_views.py -q` then `uv run pytest -q` → all pass.

- [x] **Step 5: Commit**

```bash
git add src/sm64_events/tracking/views.py tests/test_views.py
git commit -m "feat(views): hide tombstoned strats from unions and mask ghost reads

The observed-on-attempts union source can't be deleted (journal-derived),
so the view layer is where deleted_strats takes effect; every active-strat
read masks a tombstoned name to None so no ghost reaches a dropdown,
medal, or target line."
```

---

### Task 10: REST surface — `?purge=true` + `seeded` in GET

**Files:**
- Modify: `src/sm64_events/server/ranks_api.py` (GET ≈ line 33; DELETE ≈ line 78)
- Test: `tests/test_ranks_api.py` (reuse the file's existing client/ranks harness)

**Interfaces:**
- Consumes: `purge_strategy` (Task 8), `seeded_strategies` (Task 7).
- Produces: `DELETE /api/ranks/standards/{entity}/{strategy}?purge=true` (409 on seeded via the existing ValueError mapping); GET response gains `"seeded": [names]`. Task 11 consumes both.

- [x] **Step 1: Write the failing tests**

Append to `tests/test_ranks_api.py`. IMPORTANT: read the file's existing
harness first and adapt construction — the helper below is referred to as
`make(tmp_path)` returning `(client, svc)` with a seeded RankStandards; use
whatever the file actually provides (it already builds
`TrackerService(db, b, ranks=ranks)`), and pick the entity/strat from the
harness's own seed rather than hardcoding:

```python
def test_get_lists_seeded_strategies(tmp_path):
    client, svc = make(tmp_path)
    ek = next(iter(svc.ranks.to_json()["entities"]))
    body = client.get(f"/api/ranks/standards?entity={ek}").json()
    assert body["seeded"] == svc.ranks.seeded_strategies(ek)


def test_delete_purge_true_fully_deletes_custom(tmp_path):
    client, svc = make(tmp_path)
    ek = next(iter(svc.ranks.to_json()["entities"]))
    client.post(f"/api/ranks/standards/{ek}", json={"strategy": "customx"})
    r = client.delete(f"/api/ranks/standards/{ek}/customx?purge=true")
    assert r.status_code == 200
    assert "customx" not in svc.ranks.strategies(ek)
    assert "customx" in svc.db.get_state("deleted_strats", {}).get(ek, [])


def test_delete_purge_true_refuses_seeded(tmp_path):
    client, svc = make(tmp_path)
    ek = next(iter(svc.ranks.to_json()["entities"]))
    seeded = svc.ranks.seeded_strategies(ek)[0]
    r = client.delete(f"/api/ranks/standards/{ek}/{seeded}?purge=true")
    assert r.status_code == 409
    assert seeded in svc.ranks.strategies(ek)


def test_delete_without_purge_keeps_clear_semantics(tmp_path):
    client, svc = make(tmp_path)
    ek = next(iter(svc.ranks.to_json()["entities"]))
    client.post(f"/api/ranks/standards/{ek}", json={"strategy": "customy"})
    r = client.delete(f"/api/ranks/standards/{ek}/customy")
    assert r.status_code == 200
    assert "customy" not in svc.db.get_state("deleted_strats", {}).get(ek, [])
```

If the harness's ranks store has no seed file, extend the harness (or add a
seeded variant) so `seeded_strategies` is non-empty — the 409 test needs a
real seeded strat.

- [x] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_ranks_api.py -q -k "seeded or purge"`
Expected: FAIL (`seeded` KeyError; purge param ignored → no tombstone)

- [x] **Step 3: Implement**

GET gains one field:

```python
                "seeded": service.ranks.seeded_strategies(entity),
```

DELETE gains the param and dispatch:

```python
    @router.delete("/ranks/standards/{entity}/{strategy}")
    async def delete_strategy(entity: str, strategy: str, purge: bool = False):
        try:
            if purge:
                await service.purge_strategy(entity, strategy)
            else:
                await service.delete_rank_strategy(entity, strategy)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}
```

- [x] **Step 4: Run to green + full suite**

Run: `uv run pytest tests/test_ranks_api.py -q` then `uv run pytest -q` → all pass.

- [x] **Step 5: Commit**

```bash
git add src/sm64_events/server/ranks_api.py tests/test_ranks_api.py
git commit -m "feat(api): DELETE ?purge=true full-deletes a custom strat; GET lists seeded

409 on seeded rides the existing ValueError mapping; the seeded list is
what lets the UI pick clear-vs-delete per column."
```

---

### Task 11: dual-meaning × in the standards table

**Files:**
- Modify: `src/sm64_events/ui/components/standards.js` (`delStrat` ≈ line 28; the × button title in the header row)

**Interfaces:**
- Consumes: GET `seeded` field + `?purge=true` (Task 10).
- Produces: seeded strat × = clear-data (existing confirm/behavior); custom strat × = full delete with its own confirm.

- [x] **Step 1: Implement**

Add next to the other per-strat accessors:

```js
  const isSeeded = (s) => (data.seeded || []).includes(s);
```

Replace `delStrat` with:

```js
  async function delStrat(s) {
    // Dual-meaning x (user-picked): seeded strats are community data —
    // clear-only; custom strats fully delete (tombstone hides attempt-
    // observed occurrences server-side; re-creating the name restores).
    const msg = isSeeded(s)
      ? `Clear rank standards for "${s}"? (The column stays while the strategy is in use.)`
      : `Delete strategy "${s}"?\nRemoves it from all dropdowns and clears its rank `
        + `standards. Past attempts keep their recorded times; re-creating the same `
        + `name restores them.`;
    if (!window.confirm(msg)) return;
    const qs = isSeeded(s) ? "" : "?purge=true";
    await send("DELETE", `/api/ranks/standards/${enc(entity)}/${enc(s)}${qs}`);
    await load(); onChanged && onChanged();
  }
```

Change the × button's title to:

```js
title=${isSeeded(s) ? "clear this strategy's standards" : "delete this strategy"}
```

- [x] **Step 2: Verify**

`node --check` on standards.js; `uv run pytest -q` (regression). Browser
(dev server in the worktree, :8065): create a custom strat via the modal,
give it a time, set it active on the star → × it → confirm text is the
DELETE variant → column gone AND the practice dropdown no longer lists it
AND the section shows no ghost active strat; re-create the same name →
the old data's column returns. On a seeded strat, × shows the CLEAR
variant and the column survives (store data cleared — restore it after
via "Reset to community defaults"). Console clean.

- [x] **Step 3: Commit**

```bash
git add src/sm64_events/ui/components/standards.js
git commit -m "feat(ui): dual-meaning x — clear seeded strats, delete custom ones"
```

---

### Task 12: addendum docs + consolidated verification

**Files:**
- Modify: `CLAUDE.md` (Rank UI row + rank service/API rows mention purge/tombstone)
- Modify: `docs/api.md` if it documents the ranks DELETE (check; update with `purge` + `seeded`)
- Modify: this plan file (tick addendum checkboxes)

- [x] **Step 1: Full suite** — `uv run pytest -q` → all pass (count > 1077: new tests landed).
- [x] **Step 2: Browser spot-sweep** — the Task 11 scenario end-to-end once more on a fresh server start, plus one seeded-clear + reset-to-defaults; console clean.
- [x] **Step 3: Docs** — CLAUDE.md: amend the Rank UI row's standards.js text ("× clears data" → "× clears seeded / DELETES custom via ?purge=true (tombstone; recreate = undo)"), and the Rank REST surface row to name `purge` + `seeded`; update docs/api.md ranks section if present.
- [x] **Step 4: Commit** — CLAUDE.md + docs/api.md + this plan file.
