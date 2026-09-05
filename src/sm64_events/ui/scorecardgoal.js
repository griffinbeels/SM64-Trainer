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

// The goal picker's whole group list: "No goal" first, then whatever named
// goals the player has SAVED (round 8, his own words: "Custom comparisons
// should show up at the top of the goal dropdown selector" -- immediately
// after "No goal", since that IS the top of the list), then every division,
// then whatever sheet runners the caller has fetched (possibly none yet --
// `ui/components/scorecard.js`'s own header comment says why the Runners
// group is fetched LAZILY, on the picker's first open, rather than eagerly
// with everything else).
//
// Unlike Runners, an EMPTY Custom group is omitted entirely rather than
// rendered with nothing under it -- Runners has to stay present as the drop
// target for its own lazy fetch; a saved-goal list either has entries or it
// does not, and a heading over nothing is a defect, not a placeholder.
//
// `runner:<name>`/`custom:<name>` are the SAME encoding `goalToValue`/
// `valueToGoal` (scorecard.js) round-trip a goal through -- one value shape
// per kind, never re-derived at the two ends.
export function goalGroups(runners, customNames) {
  const groups = [{ label: "", options: [{ value: "", label: "No goal" }] }];
  if (customNames && customNames.length) {
    groups.push({ label: "Custom", options: customNames.map(
        (name) => ({ value: `custom:${name}`, label: name })) });
  }
  groups.push({ label: "Divisions", options: divisionOptions() });
  groups.push({ label: "Runners", options: (runners || []).map(
      (name) => ({ value: `runner:${name}`, label: name })) });
  return groups;
}

// "1'21\"32" / "23\"00" (no minutes, matching fmtSeconds' own display
// convention -- what he SEES is exactly what he can type back) -> displayed
// centiseconds, or null when unparseable. Unlike timeline.js's own
// `parseTimeInput` (which converts to FRAMES, because a marker sits on a
// real 30fps grab), a goal time is centisecond-precision standard data --
// the same unit `you_cs`/`goal_cs` already carry -- so there is no frame
// quantization to round through here.
export function parseGapTime(text) {
  const trimmed = String(text ?? "").trim();
  if (trimmed === "") return null;
  const match = trimmed.match(/^(?:(\d+)')?(\d{1,2})"(\d{1,2})$/);
  if (!match) return null;
  const minutes = match[1] ? Number(match[1]) : 0;
  const seconds = Number(match[2]);
  const centisText = match[3];
  const centis = centisText.length === 1 ? Number(centisText) * 10 : Number(centisText);
  return (minutes * 60 + seconds) * 100 + centis;
}

// A tile's own delta, recomputed from its (possibly overridden) goal --
// mirrors `ranks/scorecard.py::_tile`'s one-line rule exactly (a display
// centisecond gap, you minus goal, present only when both sides are).
function _recomputeTile(tile, overrides) {
  if (!(tile.key in overrides)) return tile;
  const goal_cs = overrides[tile.key];
  const delta_cs = tile.you_cs != null ? tile.you_cs - goal_cs : null;
  // (Round 23 removed the caps -- there is no server-graded rank on a tile
  // to invalidate any more; the shape is exactly `_tile()`'s.)
  return { ...tile, goal_cs, delta_cs };
}

// A row's (or the card's) Sigma over a tile LIST -- mirrors
// `ranks/scorecard.py::_sum_tiles` exactly: each side sums its OWN lines
// (round 29: "the Goal Stage Sum ... should be summed still. Not a ---"),
// the delta only the lines both sides have, `counted` is that shared
// set's size and `total` the tile count regardless. A side with no line
// is null, never a 0 that would print as a real 0'00"00.
function _recomputeSum(tiles) {
  const sumOf = (values) => (values.length
    ? values.reduce((total, value) => total + value, 0) : null);
  const you_cs = sumOf(tiles.filter((tile) => tile.you_cs != null)
    .map((tile) => tile.you_cs));
  const goal_cs = sumOf(tiles.filter((tile) => tile.goal_cs != null)
    .map((tile) => tile.goal_cs));
  const shared = tiles.filter((tile) =>
    tile.you_cs != null && tile.goal_cs != null);
  const delta_cs = sumOf(shared.map((tile) => tile.you_cs - tile.goal_cs));
  return { you_cs, goal_cs, delta_cs, counted: shared.length, total: tiles.length };
}

// Live client-side recompute for an UNSAVED goal edit -- his own words,
// "This should automatically adjust my goal time comparison + my stage rta
// comparison" as he types, with no server round trip per keystroke. Applies
// `overrides` (entity_key -> goal_cs) on top of the server's own resolved
// payload and re-derives every touched tile's delta plus every row's (and
// the card's) Sigma and goal_coverage, using the SAME arithmetic the server
// builder uses -- `_recomputeTile`/`_recomputeSum` above are that
// arithmetic's one JS copy, a real decision (this has to run on every
// keystroke, so it cannot be a server fetch) rather than an oversight,
// mirrored line-for-line against `ranks/scorecard.py`.
export function applyGoalOverrides(payload, overrides) {
  if (!overrides || Object.keys(overrides).length === 0) return payload;
  const rows = payload.rows.map((row) => {
    const tiles = row.tiles.map((tile) => _recomputeTile(tile, overrides));
    return { ...row, tiles, sum: _recomputeSum(tiles) };
  });
  const allTiles = rows.flatMap((row) => row.tiles);
  const goal_coverage = { covered: allTiles.filter((tile) => tile.goal_cs != null).length,
                          tiles: allTiles.length };
  return { ...payload, rows, total: _recomputeSum(allTiles), goal_coverage };
}

// A tile or Sigma gap in signed decimal seconds -- "-4.37"/"+0.40". Reuses
// attemptlog.js::delta's ± convention (a rendered sign, never a bare
// negative-number string) but always signed (delta's own blank-at-zero case
// has no tile analogue) and centiseconds rather than frames, since a tile is
// a `<span>` with no element to spare for delta's own wrapper.
export function fmtGapCs(cs) {
  return `${cs < 0 ? "-" : "+"}${(Math.abs(cs) / 100).toFixed(2)}`;
}

// -- where a card sits in the grid -------------------------------------------

// Round 20's placement, his words as geometry: "We should fill columns top
// to bottom, then left to right... [BOB] [BBH] [DDD] [THI] / [WF] [HMC] [SL]
// [TTC] / [JRB] [LLL] [WDW] [RR] / [CCM] [SSL] [TTM] [Secrets + Bowser
// combined into a single card]". The payload already arrives in COURSE
// order with the specials cards last (ranks/scorecard.py; two of them
// again since round 21), so the whole job here is to chunk it
// COLUMN-MAJOR: read down a column and you are reading the course list in
// order, which is how the sheet he grades himself against is laid out.
//
// Round 21 split the specials into two cards again (Secret, Bowser), so
// there are 17 cards for a 16-slot grid. Round 32 (2026-09-05) settled
// where the odd one goes, at every width, in his words against three
// screenshots: "What if the Bowser card is centered between the two
// columns here when the page is narrower?"; "when medium width, what if
// it's centered between all 4 columns? (centered under SSL + TTM)"; and of
// the old five-track full-monitor shape, "we actually should just keep the
// same layout as the medium width... that looks much nicer." So: the
// COURSE cards keep his grid exactly, chunked column-major on the column
// count; Secret closes the last course column; and the LAST special
// (Bowser) leaves the stacks for a centred row of its own beneath them,
// one card wide, whenever there is more than one column. A single column
// is a single stack and keeps everything in it. The five-track shape is
// retired with `columnCountFor`'s floors below.
export function cardLayout(rows, columnCount = 4) {
  if (!rows || !rows.length) return { columns: [], centred: [] };
  const courses = rows.filter((row) => row.course_id != null);
  const specials = rows.filter((row) => row.course_id == null);
  const perColumn = Math.ceil(courses.length / Math.max(1, columnCount)) || 1;
  const columns = [];
  for (let start = 0; start < courses.length; start += perColumn)
    columns.push({ rows: courses.slice(start, start + perColumn) });
  // Only a card that would otherwise hang alone off the last column is
  // centred: a scope with one special (a route holding only Secret) keeps
  // it in the stack, since there is no odd card to balance.
  const centred = columnCount > 1 && specials.length > 1 ? [specials[specials.length - 1]] : [];
  const stacked = specials.slice(0, specials.length - centred.length);
  if (stacked.length) {
    if (!columns.length) columns.push({ rows: [...stacked] });
    else columns[columns.length - 1].rows.push(...stacked);
  }
  return { columns, centred };
}

// How many columns that measured pane gets. The COMPONENT picks, not a
// container query, for round 12's reason restated: column-major chunking
// and the rendered track count must be the same number, and CSS cannot tell
// the chunker anything. Reading down a column is only course order if the
// stack it drew is the stack it chunked.
//
// Round 32's one rule for the floors: "Every single name should be on a
// SINGLE row, rather than occupying two rows." MEASURED on the real
// template (2026-09-05, viewport walked down in 20px steps): a card wraps
// no star-row name at 417px and wider, and "Red Coins on the Floating Isle
// + 100c" (the widest) wraps at 412px. Four cards plus three 10px gaps need
// a 1698px pane; two cards plus one gap need 844px. So four columns above
// 1700, two above 850 (the supported minimum window, whose pane is
// narrower), else one -- the count steps DOWN before a name would wrap,
// never the name's font or its words. Round 21's five-track step is
// retired (his call: keep the four-column layout at every wider pane).
// `tests/test_ui_scorecard.py::test_every_name_sits_on_one_line_at_every_column_count`
// re-measures this on the real template just above each floor.
export const CARD_COLUMN_FLOORS = [[1700, 4], [850, 2]];

export function columnCountFor(paneWidth) {
  for (const [floor, count] of CARD_COLUMN_FLOORS)
    if (paneWidth > floor) return count;
  return 1;
}
