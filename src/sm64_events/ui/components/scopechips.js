// src/sm64_events/ui/components/scopechips.js — the scope chip row, on its own.
// The Rank tab, the runner page and the account screen all draw it; the
// account screen imports only this, never the whole Rank page module (a
// leaderboard change must not reach the community pages; round 50).
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { getJSON } from "../api.js";
import { fmtPoints } from "./marelo.js";
import { RankIcon } from "./rankicon.js";

const html = htm.bind(h);

// The scope chip row (task D.1): every season's tier at a glance, op.gg
// style, so comparing a 16 Star rank against a 120 Star rank no longer costs
// a dropdown round-trip each way. Fetches `/api/marelo/summary` -- which
// calls `_score_scope`, never `_build_marelo` (server/ranks_api.py), so
// reading this row can never seed, sync or lower a celebration watermark
// for a scope the user has not actually opened. `refreshKey` mirrors the
// tab's own staleness fix (t.mareloRev): the chips must not go stale while
// the tab is left open during play, same reason the breakdown re-fetches
// on every REFRESH_ON event.
//
// `source` (runnerpage.js's own addition): the endpoint to read chips FROM,
// defaulting to the user's own `/api/marelo/summary`. The runner page passes
// `/api/leaderboard/runner/{name}/summary` instead -- the identical chip
// shape (board.py::runner_summary's own docstring), so this is a data-source
// swap and nothing about the row itself changes.
// `chips` PROVIDED means the caller owns the fetch and this draws what it
// is handed (round 13): RankPage holds its whole body behind one spinner
// until every section can be drawn in its final position, and it can only
// know when the chips have landed if it fetches them itself. RunnerPage
// passes nothing and keeps fetching its own, unchanged.
export function ScopeChips({ activeScopeId, onPick, refreshKey,
                             source = "/api/marelo/summary", chips: given = null }) {
  const [fetched, setFetched] = useState(null);
  useEffect(() => {
    if (given) return undefined;
    let alive = true;
    getJSON(source).then((response) => alive && setFetched(response.chips))
      .catch(() => alive && setFetched([]));
    return () => { alive = false; };
  }, [refreshKey, source, given]);
  const chips = given || fetched;
  if (!chips || !chips.length) return null;
  return html`<div class="scope-chip-row">
    ${chips.map((chip) => html`<button type="button" key=${chip.scope_id}
        class="scope-chip ${chip.scope_id === activeScopeId ? "is-selected" : ""}"
        onclick=${() => onPick(chip.scope_id)}>
      <span class="rank-icon-slot scope-chip-icon">
        <${RankIcon} tier=${chip.tier} division=${chip.division} size=${30} />
      </span>
      <span class="scope-chip-text">
        <b>${chip.label}</b>
        <span class="meta">${fmtPoints(chip.marelo)} pts</span>
      </span>
    </button>`)}
  </div>`;
}
