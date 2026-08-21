// src/sm64_events/ui/components/leaderboard.js — draws the [[Rank board]]
// (docs/glossary.md) on the Rank tab: every community runner who has
// practiced something in the tab's own scope, plus the user's own row,
// ordered by MARELO. Computed and served by `library/board.py` at
// `GET /api/leaderboard` (docs/api.md's Leaderboard section) — this module
// only fetches and draws it; it owns no scoring and no second scope control.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON } from "../api.js";
import { RankIcon } from "./rankicon.js";
import { fmtPoints, fmtScore } from "./marelo.js";
import { RANK_MODE_OPTIONS } from "./ranks.js";
import { InlineState } from "./states.js";

const html = htm.bind(h);

// ranks.js keeps its own id->label lookup private (MODE_LABEL); this derives
// its own copy from the SAME exported registry rather than hand-copying the
// six pairs, so the two can never name a mode differently.
const MODE_LABEL = Object.fromEntries(RANK_MODE_OPTIONS);

// Rows carry `you: true, runner: null` for the user's own row (board.py's
// contract) — this is the only place in the file that turns that into text,
// so a rename of the sentinel only breaks one line, not several.
function runnerName(row) {
  return row.you ? "You" : row.runner;
}

export function Leaderboard({ t, scopeId, onOpenRunner = () => {}, hasExcluded = false }) {
  const [board, setBoard] = useState(null);
  const [error, setError] = useState(null);
  const [query, setQuery] = useState("");
  const youRowRef = useRef(null);

  // Same staleness fix rankpage.js's own `t.mareloRev` comment explains for
  // the rest of the Rank tab: a board left open during play must not go
  // stale. One door onto that rule, not a second mechanism — this effect's
  // shape (clear-then-fetch on scope OR mareloRev) mirrors RankPage's.
  useEffect(() => {
    if (!scopeId) return undefined;
    let alive = true;
    setError(null);
    setBoard(null);
    setQuery("");
    getJSON(`/api/leaderboard?scope=${encodeURIComponent(scopeId)}`)
      .then((response) => alive && setBoard(response))
      .catch((requestError) => alive && setError(requestError));
    return () => { alive = false; };
  }, [scopeId, t.mareloRev]);

  if (error) return html`<${InlineState} kind="error">${error.status === 404
    ? "This scope is gone — pick another from the list above."
    : error.message}<//>`;
  if (!board) return html`<${InlineState}>Loading the leaderboard…<//>`;

  const needle = query.trim().toLowerCase();
  // The user's own row is never filtered out — every row places (the
  // product rule this surface exists to honour), and a search that hid the
  // one row he came here to find would defeat the jump control below.
  const rows = needle
    ? board.rows.filter((row) => row.you
        || (row.runner || "").toLowerCase().includes(needle))
    : board.rows;
  const modeLabel = MODE_LABEL[board.rank_mode] || board.rank_mode;

  function jumpToYou() {
    if (youRowRef.current) youRowRef.current.scrollIntoView({ block: "center" });
  }

  return html`<div class="leaderboard">
    <div class="leaderboard-head">
      <h3>Leaderboard</h3>
      <div class="leaderboard-find">
        <input type="search" class="leaderboard-find-input" value=${query}
          placeholder="Find a runner…" aria-label="Filter the leaderboard by runner"
          oninput=${(event) => setQuery(event.target.value)} />
        ${query && html`<button type="button" class="leaderboard-find-clear"
            title="Clear the filter" aria-label="Clear the filter"
            onclick=${() => setQuery("")}>✕</button>`}
      </div>
      <button type="button" class="chip leaderboard-jump" onclick=${jumpToYou}>
        Jump to you</button>
    </div>
    <!-- The basis line is unconditional (always PB, every entity, whatever
         rank_mode/exclusions the Rank tab is showing) -- the note only
         APPENDS the reason(s) for a mismatch, it never replaces the
         always-true one, per the contract: the user must read a mismatch as
         intended rather than discover it for himself. TWO independent
         causes (fix wave, final review, M2): the board's own scope
         resolution always scores every entity, ignoring exclusions -- that
         was already tested, but the note used to fire only on a rank_mode
         mismatch, so an EXCLUDED-only mismatch (pb mode, an entity turned
         off) showed two different numbers one card apart with no
         explanation at all. -->
    <p class="meta leaderboard-basis">Ranked by PB — every runner's number is
      their lifetime best time on the Ultimate Sheet, graded the same way
      your own practice PB is.${(() => {
        const reasons = [];
        if (board.rank_mode !== "pb") reasons.push(`it's graded on ${modeLabel} right now`);
        if (hasExcluded) reasons.push("you've excluded an entity from this scope");
        return reasons.length
          ? ` That can differ from what your Rank tab shows — ${reasons.join(", and ")}.`
          : "";
      })()}</p>
    <p class="meta leaderboard-omitted">${board.omitted > 0
      ? `${board.omitted} more ${board.omitted === 1 ? "runner is" : "runners are"} `
        + `rated on the sheet elsewhere, but ${board.omitted === 1 ? "hasn't" : "haven't"} `
        + "practiced anything in this scope yet."
      : "Every runner rated on the sheet has practiced something in this scope."}</p>
    <div class="leaderboard-body">
      ${/* Task 5's own door: every OTHER runner's row opens their
           runnerpage.js page. Your own row stays inert — it has no
           `runner` name to open (board.py's `you: true, runner: null`
           contract) and it is the page you are already reading, so it
           gets no role/tabindex/click at all (still no pointer cursor,
           still no hover affordance -- the CSS comment this class carried
           since Task 4). `.leaderboard-row` is a plain DIV, so a clickable
           row carries its own keyboard path -- the same
           role="button"/tabindex="0"/keydown shape rankpage.js's own
           hover ✎ already uses for a clickable non-button element. */""}
      ${rows.map((row) => html`<div key=${row.you ? "you" : row.runner}
          ref=${row.you ? youRowRef : null}
          class="leaderboard-row ${row.you ? "is-you" : "is-clickable"}"
          role=${row.you ? null : "button"} tabindex=${row.you ? null : "0"}
          title=${row.you ? null : `View ${row.runner}'s ratings`}
          onclick=${row.you ? null : () => onOpenRunner(row.runner)}
          onkeydown=${row.you ? null : (keyEvent) => {
            if (keyEvent.key !== "Enter" && keyEvent.key !== " ") return;
            keyEvent.preventDefault();
            onOpenRunner(row.runner);
          }}>
        <span class="leaderboard-pos">${row.position}</span>
        <span class="rank-icon-slot leaderboard-icon">
          ${row.tier
            ? html`<${RankIcon} tier=${row.tier} division=${row.division} size=${26} />`
            : "–"}
        </span>
        <span class="leaderboard-name">${runnerName(row)}</span>
        <span class="meta leaderboard-points">${fmtPoints(row.marelo)} pts</span>
        <span class="meta leaderboard-mastery">${fmtScore(row.mastery)} mastery</span>
        <span class="meta leaderboard-coverage">${row.practiced}/${row.n}</span>
      </div>`)}
    </div>
  </div>`;
}
