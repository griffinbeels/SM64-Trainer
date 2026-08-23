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
// Export buttons and the runner goal are later tasks (5 and 6): this file
// renders nothing for the former and, for a runner goal already persisted
// server-side with no resolver yet, the "arrives with the leaderboard merge"
// note the controller asked for rather than a blank or misleading coverage
// line.
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { fmtSeconds } from "../format.js";
import { divisionOptions, fmtGapCs } from "../scorecardgoal.js";
import { capName, divisionDigit } from "./caps.js";
import { entityIconSrc } from "./entityicons.js";
import { SearchSelect } from "./searchselect.js";
import { InlineState } from "./states.js";

const html = htm.bind(h);

// Re-exported at this path too -- ui/scorecardgoal.js's own header comment
// says why the pure logic lives in a separate, genuinely import-free file
// rather than here.
export { divisionOptions, fmtGapCs };

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

// "No goal" first, then every division. The Runners group is Task 6's --
// PUT /api/scorecard/goal already accepts a runner goal (goal_pending
// below), this picker just cannot mint one yet. Built once at module load:
// divisionOptions() is deterministic and import-free.
const GOAL_GROUPS = [
  { label: "", options: [{ value: "", label: "No goal" }] },
  { label: "Divisions", options: divisionOptions() },
];

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

function ScorecardHead({ goal, goalPending, coverage, onGoalChange }) {
  return html`<div class="scorecard-head">
    <h3>Scorecard</h3>
    <${SearchSelect} value=${goalToValue(goal)} valueLabel=${goalToLabel(goal)}
        title="Pick a goal" groups=${GOAL_GROUPS} onChange=${onGoalChange} />
    ${goalPending
      ? html`<p class="meta scorecard-note">runner goals arrive with the leaderboard merge</p>`
      : goal && coverage.covered < coverage.tiles
        ? html`<p class="meta scorecard-note">goal covers ${coverage.covered}/${coverage.tiles}</p>`
        : ""}
  </div>`;
}

export function Scorecard({ t }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  // Fetches on mount and on t.mareloRev, the Rank tab's own staleness key
  // (RankPage's own useEffect does the same -- an attempt or a PB save must
  // not leave this card showing a stale gap while open during play).
  useEffect(() => {
    let alive = true;
    getJSON("/api/scorecard").then((response) => alive && setData(response))
      .catch((err) => alive && setError(err));
    return () => { alive = false; };
  }, [t.mareloRev]);

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
        : html`<${ScorecardHead} goal=${data.goal} goalPending=${!!data.goal_pending}
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
