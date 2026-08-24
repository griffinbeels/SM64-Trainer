// src/sm64_events/ui/components/scorecard.js — the Rank tab's scorecard: every
// star of the 120-star layout plus two castle movements as a small tile
// printing your gap to a goal, one row per course plus a Secret row, a Sigma
// per row and one Upstairs RTA total (spec 2026-08-23-scorecard-design). The
// community's template spreadsheet as a live, screenshot-able card.
//
// `divisionOptions()`/`fmtGapCs` are exported import-free (no Preact) so
// tests/test_ui_scorecard.py drives them under node -- same pattern as
// entitysection.js/caps.js.
//
// The runner goal's Runners group in the picker is fetched LAZILY, on the
// picker's first open (`GET /api/library/runners`, 448 names) rather than
// on this card's own mount -- the card lives on the Rank tab and mounts
// every time that tab does, so an eager fetch would download the whole
// roster on every visit whether or not anyone ever opens the goal picker.
//
// Both Copy buttons (Task 5) COPY to the clipboard rather than downloading
// -- the desktop WebView2 shell's download behaviour is unverified, and a
// button that does nothing there is the dead-control shape this project
// treats as a bug (`.claude/rules/import.md`). `GET /api/scorecard/export
// .csv` stays reachable by URL from a browser regardless.
//
// A row expands (`ScoreRow`'s own `expanded` state, lifted here so several
// rows can be open at once) into a per-star Star/You/Goal/Δ table
// (`ScoreRowDetail`/`ScoreDetailRow`) -- the Ultimate Sheet template's own
// shape, his own screenshot of it. A star's Goal cell is editable in place;
// edits live ONLY in `pendingOverrides` (entity_key -> goal_cs) until saved,
// recomputed client-side through `applyGoalOverrides` -- no server round
// trip per keystroke -- and saved under a NAME through
// `PUT /api/scorecard/goal {kind:"custom", name, times}`, which both
// creates/overwrites the named goal and makes it the active one in a single
// write. Saved names surface at the TOP of the picker (`goalGroups`'s own
// "Custom" group, scorecardgoal.js) via the payload's own `custom_goals`.
import { h } from "preact";
import { useEffect, useMemo, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { attainableCs, fmtSeconds } from "../format.js";
import { applyGoalOverrides, divisionOptions, fmtGapCs, goalGroups,
         parseGapTime } from "../scorecardgoal.js";
import { capName, divisionDigit } from "./caps.js";
import { entityIconSrc } from "./entityicons.js";
import { SearchSelect } from "./searchselect.js";
import { InlineState } from "./states.js";

const html = htm.bind(h);

// Re-exported at this path too -- ui/scorecardgoal.js's own header comment
// says why the pure logic lives in a separate, genuinely import-free file
// rather than here.
export { applyGoalOverrides, divisionOptions, fmtGapCs, goalGroups, parseGapTime };

function goalToValue(goal) {
  if (!goal) return "";
  if (goal.kind === "division") return `division:${goal.tier}:${goal.division}`;
  if (goal.kind === "runner") return `runner:${goal.runner}`;
  if (goal.kind === "custom") return `custom:${goal.name}`;
  return "";
}

function goalToLabel(goal) {
  if (!goal) return "No goal";
  if (goal.kind === "division") return `${capName(goal.tier)} ${divisionDigit(goal.division)}`;
  if (goal.kind === "runner") return goal.runner;
  if (goal.kind === "custom") return goal.name;
  return "No goal";
}

// Re-selecting an EXISTING custom goal (picked from the dropdown) carries no
// `times` -- the server already has them (`_CUSTOM_KEY`); `times` is added
// separately, only at SAVE time (see `saveCustomGoal` below), which is the
// one call site allowed to turn a picker value into a body carrying data.
function valueToGoal(value) {
  if (!value) return null;
  const [kind, ...rest] = value.split(":");
  if (kind === "division") {
    const [tier, division] = rest;
    return { kind: "division", tier, division };
  }
  if (kind === "runner") return { kind: "runner", runner: rest.join(":") };
  if (kind === "custom") return { kind: "custom", name: rest.join(":") };
  return null;
}

// The picker's group list -- `goalGroups`, imported above -- lives in
// scorecardgoal.js so it stays node-testable the same way divisionOptions()
// already is; this file just calls it with whatever runners it has.

// A tile's own class + printed text. `hasGoal` is the CARD's goal, not the
// tile's own goal_cs -- a tile whose ladder cannot grade the chosen goal
// still reads as "missing a side" (dim), never as "no goal was ever set".
function tileView(tile, hasGoal) {
  if (!hasGoal) {
    return { cls: "", text: tile.you_cs != null ? fmtSeconds(tile.you_cs / 100) : "—" };
  }
  if (tile.you_cs == null || tile.goal_cs == null) return { cls: "dim", text: "—" };
  return { cls: tile.delta_cs <= 0 ? "good" : "bad", text: fmtGapCs(tile.delta_cs) };
}

function tileTitle(tile) {
  const you = tile.you_cs != null ? fmtSeconds(tile.you_cs / 100) : "no time";
  const goal = tile.goal_cs != null ? fmtSeconds(tile.goal_cs / 100) : "no goal";
  const base = `${tile.label} — You ${you} · Goal ${goal}`;
  return tile.folded ? `${base} (not in Σ)` : base;
}

function ScoreTile({ t, tile, hasGoal }) {
  const { cls, text } = tileView(tile, hasGoal);
  const classes = ["score-tile", cls, tile.folded ? "folded" : ""]
    .filter(Boolean).join(" ");
  // The art rides a custom property, never a plain inline background --
  // `.score-tile::before` reads it so the text stays a real, always-on-top
  // sibling element instead of racing the pseudo-element's own paint order
  // (`.claude/rules/ui-core.md`'s "assert the painted value" warning: a
  // `background` set directly on this span would sit BEHIND nothing, since
  // there would be no separate dim layer to hold it at low opacity).
  return html`<span class=${classes} title=${tileTitle(tile)}
      style=${`--tile-art:url(${entityIconSrc(t, tile.key)})`}>
    <span class="score-tile-text">${text}</span>
  </span>`;
}

// The row/foot Sigma. Row and tile gaps are printed differently on purpose:
// a single tile's gap is at most a few seconds (fmtGapCs's decimal
// notation), but a row sums up to ten tiles and can run past a minute, so
// the Sigma prints via fmtSeconds' M'SS"CC notation instead -- the ✓/✗
// glyph alone carries the sign, since fmtSeconds cannot take a negative
// number.
function SumChip({ sum, large = false }) {
  const hasDelta = sum.delta_cs != null;
  const behind = hasDelta && sum.delta_cs > 0;
  const classes = ["score-sum", large ? "score-sum-total" : "",
                    hasDelta ? (behind ? "bad" : "good") : ""]
    .filter(Boolean).join(" ");
  // `counted === 0` still carries `you_cs`/`goal_cs` of 0 (Python `sum([])`),
  // which is not a real 0'00"00 on either side -- printing them unconditionally
  // fabricates a comparison nothing backs, the exact "cannot logically be
  // compared" shape `.claude/rules/acceptance.md` rules against. No comparable
  // tile means an honest sentence about WHY, not two invented zeroes.
  const title = hasDelta
    ? `You ${fmtSeconds(sum.you_cs / 100)} · Goal ${fmtSeconds(sum.goal_cs / 100)}`
    : `${sum.counted}/${sum.total} tiles comparable`;
  return html`<div class=${classes} title=${title}>
    <span class="score-sum-glyph">${hasDelta ? (behind ? "✗" : "✓") : ""}</span>
    <span class="score-sum-value">${hasDelta
      ? fmtSeconds(Math.abs(sum.delta_cs) / 100) : "—"}</span>
    ${sum.counted < sum.total
      ? html`<span class="score-sum-coverage">${sum.counted}/${sum.total}</span>` : ""}
  </div>`;
}

// The expanded breakdown: every tile in the row as Star / You / Goal / Δ (round 5:
// "the YOU column first, *then* the GOAL column"),
// the shape of the community's own Ultimate Sheet template (round 8, his
// screenshot from it), plus the row's own Sigma restated as "Stage RTA
// target" -- the row's collapsed chip already IS that number, named here so
// clicking in for detail also answers "what am I actually chasing on this
// stage" without having to reread the collapsed row above it.
function ScoreRowDetail({ row, onGoalOverride }) {
  return html`<div class="score-row-detail">
    <div class="score-detail-target">
      <span class="meta">Stage RTA target</span>
      <${SumChip} sum=${row.sum} />
    </div>
    <table class="score-detail-table">
      <thead><tr><th>Star</th><th>You</th><th>Goal</th><th>Δ</th></tr></thead>
      <tbody>
        ${row.tiles.map((tile) => html`<${ScoreDetailRow} key=${tile.key}
            tile=${tile} onGoalOverride=${onGoalOverride} />`)}
      </tbody>
    </table>
  </div>`;
}

// One star's editable Goal cell. Click/tap turns it into a text input
// pre-filled with the CURRENT goal in the exact notation it is displayed in
// (`fmtSeconds`) -- his rule is "replace the time entry", so what he types
// over is byte-for-byte what he is looking at, never a different unit or a
// three-box split. Enter or blur commits; Escape cancels with no write.
function ScoreDetailRow({ tile, onGoalOverride }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [invalid, setInvalid] = useState(false);

  function startEdit() {
    setDraft(tile.goal_cs != null ? fmtSeconds(tile.goal_cs / 100) : "");
    setInvalid(false);
    setEditing(true);
  }
  function cancel() { setEditing(false); setInvalid(false); }
  function commit() {
    // An EMPTY blur/Enter is "I changed my mind", not "I typed garbage" --
    // cancelling (rather than flagging it invalid) is the only way out of
    // edit mode a blur on an empty box ever had, and leaving it red and open
    // is the un-dismissable-control shape `.claude/rules/acceptance.md`
    // rules against.
    if (!draft.trim()) { cancel(); return; }
    const parsed = parseGapTime(draft);
    if (parsed === null) { setInvalid(true); return; }
    // The one snap the app already has (format.js::attainableCs, the
    // import field's own door): only 30 of every 100 centisecond values can
    // appear on the timer, so a typed goal rounds onto the displayable set
    // and the cell shows the snapped value the moment the edit lands --
    // never a number nobody typed (round 5).
    onGoalOverride(tile.key, attainableCs(parsed));
    setEditing(false);
  }

  return html`<tr class=${tile.folded ? "score-detail-folded" : ""}>
    <td>${tile.label}${tile.folded
      ? html`<span class="meta">${" "}(not in Σ)</span>` : ""}</td>
    <td>${tile.you_cs != null ? fmtSeconds(tile.you_cs / 100) : "—"}</td>
    <td class="score-detail-goal">
      ${editing
        ? html`<div class="score-detail-editing">
              <input class="score-detail-input ${invalid ? "is-invalid" : ""}"
                  value=${draft} placeholder="0'00&quot;00" autoFocus
                  oninput=${(inputEvent) => { setDraft(inputEvent.target.value); setInvalid(false); }}
                  onkeydown=${(keyEvent) => {
                    if (keyEvent.key === "Enter") commit();
                    if (keyEvent.key === "Escape") cancel();
                  }}
                  onblur=${commit} />
              ${invalid
                ? html`<span class="score-detail-hint">type it like 51"83</span>` : ""}
            </div>`
        : html`<button type="button" class="score-detail-goal-btn" onclick=${startEdit}>
              ${tile.goal_cs != null ? fmtSeconds(tile.goal_cs / 100) : "set a time…"}
            </button>`}
    </td>
    <td class=${tile.delta_cs != null ? (tile.delta_cs <= 0 ? "good" : "bad") : ""}>
      ${tile.delta_cs != null ? fmtGapCs(tile.delta_cs) : "—"}
    </td>
  </tr>`;
}

function ScoreRow({ t, row, hasGoal, expanded, onToggle, onGoalOverride }) {
  const rowKey = row.course_id ?? "secret";
  return html`<div class="score-row ${expanded ? "is-expanded" : ""}">
    <button type="button" class="score-row-label" title=${row.label}
        aria-expanded=${expanded} onclick=${() => onToggle(rowKey)}>
      <span class="score-row-chevron">${expanded ? "▾" : "▸"}</span>
      <span class="score-row-name">${row.label}</span>
    </button>
    <div class="score-row-tiles">
      ${row.tiles.map((tile) => html`<${ScoreTile} key=${tile.key} t=${t}
          tile=${tile} hasGoal=${hasGoal} />`)}
    </div>
    <${SumChip} sum=${row.sum} />
    ${expanded
      ? html`<${ScoreRowDetail} row=${row} onGoalOverride=${onGoalOverride} />` : ""}
  </div>`;
}

// One button, one job: fetch/format the text via `onCopy`, hand it to the
// clipboard, and flash "Copied ✓" for ~1.5s -- the same idiom
// routes.js's plain `Copy JSON` button lacks (it has no feedback at all),
// generalised here since two buttons on one card need to look alike.
// `onCopy` returns the text to copy, or throws/rejects on failure; the
// caller (ScorecardExports) is what turns a rejection into the inline
// error line, so this component never needs to know the shape of a
// server error.
const COPIED_FLASH_MS = 1500;

function CopyButton({ className, label, onCopy, onError }) {
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);

  async function handleClick() {
    if (busy) return;
    setBusy(true);
    try {
      // `routes.js:269`'s own `navigator.clipboard &&` guard, but this
      // button owns an inline error slot (routes.js's plain Copy JSON does
      // not), so a missing clipboard gets a plain sentence there instead of
      // a silent no-op.
      if (!navigator.clipboard) {
        throw new Error("clipboard access is not available here");
      }
      const text = await onCopy();
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), COPIED_FLASH_MS);
    } catch (err) {
      onError(err.message || String(err));
    } finally {
      setBusy(false);
    }
  }

  return html`<button class=${`scorecard-copy-btn ${className}`}
      onclick=${handleClick} disabled=${busy}>
    ${copied ? "Copied ✓" : label}
  </button>`;
}

async function fetchCsvText() {
  const response = await fetch("/api/scorecard/export.csv");
  if (response.ok) return response.text();
  let detail = null;
  try { detail = (await response.json()).detail; } catch { /* non-JSON body */ }
  throw new Error(detail || `export.csv: ${response.status}`);
}

// The column door's own 503 ("could not read the sheet: …") is shown
// INLINE beside the buttons, never a toast -- the same "put the reason
// where the click lands" rule `.claude/rules/acceptance.md` states for a
// disabled control applies to a button whose action just failed. The CSV
// door reads the cached snapshot rather than fetching live, so it is far
// less likely to fail, but a network hiccup on the fetch itself still
// lands in the same slot rather than going nowhere.
function ScorecardExports() {
  const [error, setError] = useState(null);

  return html`<div class="scorecard-exports">
    <${CopyButton} className="scorecard-copy-column" label="Copy sheet column"
        onCopy=${async () => (await getJSON("/api/scorecard/column")).lines.join("\n")}
        onError=${setError} />
    <${CopyButton} className="scorecard-copy-csv" label="Copy scorecard CSV"
        onCopy=${fetchCsvText} onError=${setError} />
    ${error ? html`<${InlineState} kind="error">${error}<//>` : ""}
  </div>`;
}

// Appears only while there are unsaved goal edits -- his flow, verbatim:
// "This should automatically adjust my goal time comparison... I should
// then be able to SAVE my custom comparison with a given name." The name
// field defaults to the ACTIVE goal's own name when it is already a custom
// one, so re-editing and re-saving under the SAME name is the natural path
// -- "If I modify a saved comparison, it should just overwrite it" needs no
// separate control, since the store is a plain dict keyed by name and
// saving under an unchanged name IS the overwrite. Discard is a pure local
// reset (nothing has been written to the server yet), matching the
// "abandonable with no side effects" rule every other multi-step control in
// this app follows.
function ScorecardSaveBar({ pendingCount, initialName, busy, error, onSave, onDiscard }) {
  const [name, setName] = useState(initialName);
  useEffect(() => setName(initialName), [initialName]);
  return html`<div class="scorecard-savebar">
    <span class="meta">${pendingCount} custom ${pendingCount === 1 ? "time" : "times"} edited</span>
    <input class="scorecard-savebar-input" value=${name}
        placeholder="Name this goal…"
        oninput=${(inputEvent) => setName(inputEvent.target.value)} />
    <button type="button" class="quiet-button" disabled=${busy || !name.trim()}
        onclick=${() => onSave(name.trim())}>Save goal</button>
    <button type="button" class="quiet-button" disabled=${busy}
        onclick=${onDiscard}>Discard</button>
    ${error ? html`<${InlineState} kind="error">${error}<//>` : ""}
  </div>`;
}

function ScorecardHead({ goal, groups, onOpen, coverage, onGoalChange }) {
  return html`<div class="scorecard-head">
    <h3>Scorecard</h3>
    <${SearchSelect} value=${goalToValue(goal)} valueLabel=${goalToLabel(goal)}
        title="Pick a goal" groups=${groups} onOpen=${onOpen} onChange=${onGoalChange}
        align="right" />
    ${goal && coverage.covered < coverage.tiles
      ? html`<p class="meta scorecard-note">goal covers ${coverage.covered}/${coverage.tiles}</p>`
      : ""}
    <${ScorecardExports} />
  </div>`;
}

export function Scorecard({ t }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  // null = not fetched yet (the Runners group is lazy, see the header
  // comment); [] once fetched even if the sheet somehow named nobody.
  const [runners, setRunners] = useState(null);
  // Which rows are expanded -- a plain Set of `row.course_id ?? "secret"`,
  // several open at once (no accordion constraint asked for).
  const [expandedRows, setExpandedRows] = useState(() => new Set());
  // entity_key -> goal_cs, UNSAVED. Lives here (not per-row) because a save
  // can gather edits made across several rows before he ever presses Save.
  const [pendingOverrides, setPendingOverrides] = useState({});
  const [saveBusy, setSaveBusy] = useState(false);
  const [saveError, setSaveError] = useState(null);

  // Fetches on mount and on t.mareloRev, the Rank tab's own staleness key
  // (RankPage's own useEffect does the same -- an attempt or a PB save must
  // not leave this card showing a stale gap while open during play).
  useEffect(() => {
    let alive = true;
    getJSON("/api/scorecard").then((response) => alive && setData(response))
      .catch((err) => alive && setError(err));
    return () => { alive = false; };
  }, [t.mareloRev]);

  function loadRunnersOnce() {
    if (runners != null) return;
    getJSON("/api/library/runners")
      .then((body) => setRunners(body.runners || []))
      .catch(() => setRunners([]));
  }

  const groups = useMemo(() => goalGroups(runners, data && data.custom_goals),
    [runners, data && data.custom_goals]);

  // The payload actually RENDERED -- the server's own resolved goal with any
  // unsaved edits laid on top, recomputed live (scorecardgoal.js's own
  // header comment on `applyGoalOverrides` has the "no server round trip
  // per keystroke" reasoning). A no-op object identity when there is
  // nothing pending, so this costs nothing on every other render.
  const displayData = useMemo(
    () => (data ? applyGoalOverrides(data, pendingOverrides) : null),
    [data, pendingOverrides]);
  // Editing even ONE star without a base goal set still means "I am
  // comparing against something now" -- coloring should not wait for a
  // saved goal to exist.
  const hasGoal = !!(data && data.goal) || Object.keys(pendingOverrides).length > 0;

  function toggleRow(rowKey) {
    setExpandedRows((current) => {
      const next = new Set(current);
      if (next.has(rowKey)) next.delete(rowKey); else next.add(rowKey);
      return next;
    });
  }

  function handleGoalOverride(entityKey, goalCs) {
    setPendingOverrides((current) => ({ ...current, [entityKey]: goalCs }));
  }

  function discardOverrides() {
    setPendingOverrides({});
    setSaveError(null);
  }

  async function onGoalChange(value) {
    // Picking a different base goal makes any unsaved edit ambiguous (it
    // was relative to whatever was active a moment ago) -- discard rather
    // than silently carry it onto a goal it was never made against.
    setPendingOverrides({});
    setSaveError(null);
    try {
      await send("PUT", "/api/scorecard/goal", valueToGoal(value));
      setData(await getJSON("/api/scorecard"));
    } catch (err) { setError(err); }
  }

  async function saveCustomGoal(name) {
    if (!name || !displayData) return;
    setSaveBusy(true);
    setSaveError(null);
    try {
      // The FULL currently-displayed times, not just the touched keys -- a
      // save carries every star's existing target forward, so the ones he
      // never opened keep whatever the base goal already gave them.
      const times = {};
      for (const row of displayData.rows) {
        for (const tile of row.tiles) {
          if (tile.goal_cs != null) times[tile.key] = tile.goal_cs;
        }
      }
      await send("PUT", "/api/scorecard/goal", { kind: "custom", name, times });
      setPendingOverrides({});
      setData(await getJSON("/api/scorecard"));
    } catch (err) {
      setSaveError(err.message || String(err));
    } finally {
      setSaveBusy(false);
    }
  }

  const pendingCount = Object.keys(pendingOverrides).length;

  return html`<div class="practice-card scorecard-card">
    ${error
      ? html`<${InlineState} kind="error">${error.message}<//>`
      : !data
        ? html`<${InlineState}>Loading your scorecard…<//>`
        : html`<${ScorecardHead} goal=${data.goal} groups=${groups}
              onOpen=${loadRunnersOnce}
              coverage=${data.goal_coverage} onGoalChange=${onGoalChange} />
            ${pendingCount > 0
              ? html`<${ScorecardSaveBar} pendingCount=${pendingCount}
                    initialName=${data.goal && data.goal.kind === "custom" ? data.goal.name : ""}
                    busy=${saveBusy} error=${saveError}
                    onSave=${saveCustomGoal} onDiscard=${discardOverrides} />`
              : ""}
            <div class="score-rows">
              ${displayData.rows.map((row) => html`<${ScoreRow} key=${row.course_id ?? "secret"}
                  t=${t} row=${row} hasGoal=${hasGoal}
                  expanded=${expandedRows.has(row.course_id ?? "secret")}
                  onToggle=${toggleRow} onGoalOverride=${handleGoalOverride} />`)}
            </div>
            <div class="score-foot">
              <span class="score-foot-label">Upstairs RTA</span>
              <${SumChip} sum=${displayData.total} large=${true} />
            </div>`}
  </div>`;
}
