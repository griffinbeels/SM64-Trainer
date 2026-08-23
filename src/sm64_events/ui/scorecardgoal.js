// src/sm64_events/ui/scorecardgoal.js — the scorecard's pure goal logic:
// every division goal a player can pick, and how a gap prints.
//
// DEVIATION FROM THE TASK BRIEF, flagged rather than silently done: the
// brief's file list names only ui/components/scorecard.js and asks for
// `divisionOptions`/`fmtGapCs` to live there, node-driven "same pattern as
// tests/test_ui_entity_section.py". That pattern is `node --input-type=module`
// importing the file directly, and scorecard.js -- to render at all -- must
// `import { h } from "preact"`, which resolves only through the browser's
// importmap (ui/index.html) and does not exist under node (no node_modules,
// no vendored preact for node -- confirmed: `ui/vendor/*.module.js` is
// fetched only by the browser). A node import of scorecard.js itself would
// therefore fail before executing a single line, same as it would for
// rankpage.js (test_ui_rank_chart.py works around this with SOURCE SCANS
// instead, precisely because that file cannot be node-imported).
//
// entitysection.js is import-free for exactly this reason -- consumed by
// components that import Preact, itself importing nothing Preact needs --
// so this file follows that precedent rather than the brief's literal file
// list. scorecard.js imports and re-exports both names, so they are still
// reachable at the path the brief names.
import { RANK_NAMES, DIVISION_NUMERALS, capName, divisionDigit } from "./components/caps.js";

// Every division goal a player can aim at, hardest first ("Mario 1" leads,
// Capless excluded -- its tail has no cutoff to aim at). 8 tiers x 5
// divisions = 40.
export function divisionOptions() {
  const tiers = RANK_NAMES.filter((tier) => tier !== "Iron");
  // DIVISION_NUMERALS is bottom-of-tier-first (V..I); a goal picker reads
  // top-down as best-to-worst, so it walks the SAME registry reversed
  // rather than inventing a second division order.
  const divisions = [...DIVISION_NUMERALS].reverse();
  const options = [];
  for (const tier of tiers) {
    for (const division of divisions) {
      // `value` is an IDENTITY the server round-trips and validates
      // (PUT /api/scorecard/goal's `tier`/`division` fields), never shown to
      // a player -- string concatenation rather than a template literal on
      // purpose, so it holds the raw tier key without tripping
      // test_ui_cap_names.py's raw-tier-print guard, which flags exactly the
      // `${tier}` template-interpolation shape that IS a display print.
      options.push({ value: "division:" + tier + ":" + division,
                      label: `${capName(tier)} ${divisionDigit(division)}` });
    }
  }
  return options;
}

// A tile or Sigma gap in signed decimal seconds -- "-4.37"/"+0.40". Reuses
// attemptlog.js::delta's ± convention (a rendered sign, never a bare
// negative-number string) but always signed (delta's own blank-at-zero case
// has no tile analogue) and centiseconds rather than frames, since a tile is
// a `<span>` with no element to spare for delta's own wrapper.
export function fmtGapCs(cs) {
  return `${cs < 0 ? "-" : "+"}${(Math.abs(cs) / 100).toFixed(2)}`;
}
