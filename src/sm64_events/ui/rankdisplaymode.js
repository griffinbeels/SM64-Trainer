// src/sm64_events/ui/rankdisplaymode.js -- which rank a practice-log card
// shows.
//
// This view preference lives in localStorage per entity. It never narrows the
// ghosts (every lineup races all strategies, ghostswitch.js GHOST_SCOPE);
// choosing Overall neither changes the practice strategy nor changes another card. The pure read/write functions
// keep malformed or stale storage from breaking the Practice page and make
// the persistence rule directly testable under node.

export const RANK_DISPLAY_MODE_KEY = "sm64.practiceRankModes";

const listeners = new Set();
/** Hear every change of any card's choice; returns the way to stop listening. */
export function onRankDisplayModeChange(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

const VALID_MODES = new Set(["strategy", "overall"]);

function storedModes(storage) {
  if (!storage) return {};
  try {
    const parsed = JSON.parse(storage.getItem(RANK_DISPLAY_MODE_KEY) || "{}");
    if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") return {};
    return Object.fromEntries(Object.entries(parsed)
      .filter(([, mode]) => VALID_MODES.has(mode)));
  } catch {
    return {};
  }
}

export function readRankDisplayMode(entity, storage = globalThis.localStorage) {
  // Overall unless the player picked Strategy for this entity (his ask,
  // 2026-10-29: "by default, we should show the user's rank on the 'overall'
  // tab ... because otherwise it's very confusing what their rank is").
  return storedModes(storage)[entity] || "overall";
}

export function writeRankDisplayMode(entity, mode,
                                     storage = globalThis.localStorage) {
  if (!storage || !entity || !VALID_MODES.has(mode)) return false;
  try {
    storage.setItem(RANK_DISPLAY_MODE_KEY, JSON.stringify({
      ...storedModes(storage),
      [entity]: mode,
    }));
    for (const listener of listeners) listener(entity, mode);
    return true;
  } catch {
    return false;
  }
}

// THE rank a star or segment cell draws, off the same choice its practice-log
// card shows (his ask, 2026-11-03: "Need to stay in sync wherever this is
// displayed"). No Overall answer means the two share nothing to choose between.
export function cellRank(entity, strategyRank, overallRank,
                         storage = globalThis.localStorage) {
  return readRankDisplayMode(entity, storage) === "overall" && overallRank
    ? overallRank : strategyRank;
}

// The target picker's rank map, each entry graded the way its card shows it.
export function pickerRanks(ranks, storage = globalThis.localStorage) {
  return Object.fromEntries(Object.entries(ranks || {}).map(([entity, entry]) =>
    [entity, { ...entry, ...cellRank(entity, entry, entry.overall, storage) }]));
}

// A shared ladder has no strategy-vs-overall distinction. It always presents
// Overall, whatever preference the card remembers for a future strategy whose
// ladder differs again.
export function effectiveRankDisplayMode(preferred, hasSeparateRank) {
  return hasSeparateRank && preferred === "strategy" ? "strategy" : "overall";
}
