// src/sm64_events/ui/components/leaderboard.js — draws the [[Rank board]]
// (docs/glossary.md) on the Rank tab: every community runner who has
// practiced something in the tab's own scope, plus the user's own row,
// ordered by MARELO. Computed and served by `library/board.py` at
// `GET /api/leaderboard` (docs/api.md's Leaderboard section) — this module
// only fetches and draws it; it owns no scoring and no second scope control.
//
// Three things to know before changing the reading:
// - a row's COLUMNS are `LeaderboardRow` below, one place;
// - the two sentences above the list are `basisNote`/`omittedNote`, so the
//   wording and the rule it states change together;
// - the user's own row is `you: true, runner: null` (board.py's contract),
//   and `runnerName` is the only place that sentinel becomes text.
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

function runnerName(row) {
  return row.you ? "You" : row.runner;
}

// The basis line is unconditional -- always PB, every entity, whatever the
// Rank tab is showing -- and only APPENDS the reason(s) the two numbers can
// differ. Two independent causes, and a mismatch from either must be read as
// intended rather than discovered: the board ignores the user's rank mode,
// and it ignores his exclusions.
function basisNote(board, hasExcluded) {
  const reasons = [];
  if (board.rank_mode !== "pb") {
    reasons.push(`it's graded on ${MODE_LABEL[board.rank_mode] || board.rank_mode} right now`);
  }
  if (hasExcluded) reasons.push("you've excluded an entity from this scope");
  return "Ranked by PB — every runner's number is their lifetime best time on "
    + "the Ultimate Sheet, graded the same way your own practice PB is."
    + (reasons.length
      ? ` That can differ from what your Rank tab shows — ${reasons.join(", and ")}.`
      : "");
}

// The drop is never silent: a board that hides most of the sheet without a
// count reads as "this is everyone" (his ruling; `board.py` has the numbers).
function omittedNote(omitted) {
  if (omitted === 0) return "Every runner rated on the sheet has practiced something in this scope.";
  const plural = omitted !== 1;
  return `${omitted} more ${plural ? "runners are" : "runner is"} rated on the sheet elsewhere, `
    + `but ${plural ? "haven't" : "hasn't"} practiced anything in this scope yet.`;
}

// One board row. Every OTHER runner's row is a door onto their runner page;
// your own stays inert (no destination -- it is the page you are reading)
// and so carries no role/tabindex/click and no hover affordance. The row is
// a plain DIV, so a clickable one brings its own keyboard path -- the same
// role="button"/tabindex/keydown shape rankpage.js's hover ✎ uses.
function LeaderboardRow({ row, youRowRef, onOpenRunner }) {
  const open = () => onOpenRunner(row.runner);
  const door = row.you ? {} : {
    role: "button", tabindex: "0", title: `View ${row.runner}'s ratings`,
    onclick: open,
    onkeydown: (keyEvent) => {
      if (keyEvent.key !== "Enter" && keyEvent.key !== " ") return;
      keyEvent.preventDefault();
      open();
    },
  };
  return html`<div ref=${row.you ? youRowRef : null}
      class="leaderboard-row ${row.you ? "is-you" : "is-clickable"}" ...${door}>
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
  </div>`;
}

export function Leaderboard({ t, scopeId, onOpenRunner = () => {}, hasExcluded = false }) {
  const [board, setBoard] = useState(null);
  const [error, setError] = useState(null);
  const [query, setQuery] = useState("");
  const youRowRef = useRef(null);

  // Same staleness fix rankpage.js's own `t.mareloRev` comment explains for
  // the rest of the Rank tab: a board left open during play must not go
  // stale. Clear-then-fetch on scope OR mareloRev, mirroring RankPage.
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

  // The user's own row is never filtered out: a search that hid the one row
  // he came here to find would defeat the jump control.
  const needle = query.trim().toLowerCase();
  const rows = needle
    ? board.rows.filter((row) => row.you || (row.runner || "").toLowerCase().includes(needle))
    : board.rows;

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
    <p class="meta leaderboard-basis">${basisNote(board, hasExcluded)}</p>
    <p class="meta leaderboard-omitted">${omittedNote(board.omitted)}</p>
    <div class="leaderboard-body">
      ${rows.map((row) => html`<${LeaderboardRow} key=${row.you ? "you" : row.runner}
          row=${row} youRowRef=${youRowRef} onOpenRunner=${onOpenRunner} />`)}
    </div>
  </div>`;
}
