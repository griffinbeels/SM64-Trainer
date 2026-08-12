// src/sm64_events/ui/rankdisplaymode.js -- which rank a practice-log card
// shows when its strategy and overall ladders differ.
//
// This is a display preference, not tracker state: it lives in localStorage
// per entity so choosing Overall on one star/segment neither changes the
// active strategy nor changes another card. The pure read/write functions
// keep malformed or stale storage from breaking the Practice page and make
// the persistence rule directly testable under node.

export const RANK_DISPLAY_MODE_KEY = "sm64.practiceRankModes";

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
  return storedModes(storage)[entity] || "strategy";
}

export function writeRankDisplayMode(entity, mode,
                                     storage = globalThis.localStorage) {
  if (!storage || !entity || !VALID_MODES.has(mode)) return false;
  try {
    storage.setItem(RANK_DISPLAY_MODE_KEY, JSON.stringify({
      ...storedModes(storage),
      [entity]: mode,
    }));
    return true;
  } catch {
    return false;
  }
}

// A shared ladder has no strategy-vs-overall distinction. It always presents
// Overall, whatever preference the card remembers for a future strategy whose
// ladder differs again.
export function effectiveRankDisplayMode(preferred, hasSeparateRank) {
  return hasSeparateRank && preferred === "strategy" ? "strategy" : "overall";
}
