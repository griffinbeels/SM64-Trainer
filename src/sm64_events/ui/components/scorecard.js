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
import { h } from "preact";
import { useEffect, useMemo, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { fmtSeconds } from "../format.js";
import { divisionOptions, fmtGapCs, goalGroups } from "../scorecardgoal.js";
import { capName, divisionDigit } from "./caps.js";
import { entityIconSrc } from "./entityicons.js";
import { SearchSelect } from "./searchselect.js";
import { InlineState } from "./states.js";

const html = htm.bind(h);

// Re-exported at this path too -- ui/scorecardgoal.js's own header comment
// says why the pure logic lives in a separate, genuinely import-free file
// rather than here.
export { divisionOptions, fmtGapCs, goalGroups };

function goalToValue(goal) {
  if (!goal) return "";
  if (goal.kind === "division") return `division:${goal.tier}:${goal.division}`;
  if (goal.kind === "runner") return `runner:${goal.runner}`;
  return "";
}

function goalToLabel(goal) {
  if (!goal) return "No goal";
  if (goal.kind === "division") return `${capName(goal.tier)} ${divisionDigit(goal.division)}`;
  if (goal.kind === "runner") return goal.runner;
  return "No goal";
}

function valueToGoal(value) {
  if (!value) return null;
  const [kind, ...rest] = value.split(":");
  if (kind === "division") {
    const [tier, division] = rest;
    return { kind: "division", tier, division };
  }
  if (kind === "runner") return { kind: "runner", runner: rest.join(":") };
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
  return html`<div class=${classes}
      title=${`You ${fmtSeconds(sum.you_cs / 100)} · Goal ${fmtSeconds(sum.goal_cs / 100)}`}>
    <span class="score-sum-glyph">${hasDelta ? (behind ? "✗" : "✓") : ""}</span>
    <span class="score-sum-value">${hasDelta
      ? fmtSeconds(Math.abs(sum.delta_cs) / 100) : "—"}</span>
    ${sum.counted < sum.total
      ? html`<span class="score-sum-coverage">${sum.counted}/${sum.total}</span>` : ""}
  </div>`;
}

function ScoreRow({ t, row, hasGoal }) {
  return html`<div class="score-row">
    <span class="score-row-label" title=${row.label}>${row.label}</span>
    <div class="score-row-tiles">
      ${row.tiles.map((tile) => html`<${ScoreTile} key=${tile.key} t=${t}
          tile=${tile} hasGoal=${hasGoal} />`)}
    </div>
    <${SumChip} sum=${row.sum} />
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

function ScorecardHead({ goal, groups, onOpen, coverage, onGoalChange }) {
  return html`<div class="scorecard-head">
    <h3>Scorecard</h3>
    <${SearchSelect} value=${goalToValue(goal)} valueLabel=${goalToLabel(goal)}
        title="Pick a goal" groups=${groups} onOpen=${onOpen} onChange=${onGoalChange} />
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

  const groups = useMemo(() => goalGroups(runners), [runners]);

  async function onGoalChange(value) {
    try {
      await send("PUT", "/api/scorecard/goal", valueToGoal(value));
      setData(await getJSON("/api/scorecard"));
    } catch (err) { setError(err); }
  }

  return html`<div class="practice-card scorecard-card">
    ${error
      ? html`<${InlineState} kind="error">${error.message}<//>`
      : !data
        ? html`<${InlineState}>Loading your scorecard…<//>`
        : html`<${ScorecardHead} goal=${data.goal} groups=${groups}
              onOpen=${loadRunnersOnce}
              coverage=${data.goal_coverage} onGoalChange=${onGoalChange} />
            <div class="score-rows">
              ${data.rows.map((row) => html`<${ScoreRow} key=${row.course_id ?? "secret"}
                  t=${t} row=${row} hasGoal=${!!data.goal} />`)}
            </div>
            <div class="score-foot">
              <span class="score-foot-label">Upstairs RTA</span>
              <${SumChip} sum=${data.total} large=${true} />
            </div>`}
  </div>`;
}
