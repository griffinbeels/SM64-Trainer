// Pure wizard policy. Observations come from /api/setup, never grading settings.
import { EMU, N64 } from "./platform.js";
export const SETUP_TITLES = {
  platform: "Select platform", connect: "Connect Project64",
  install: "Set up Practice Replay", complete: "You're ready!", console: "N64 support",
};
export const EMU_PAGES = ["platform", "connect", "install", "complete"];
export const ACKNOWLEDGE_MS = 1000;
export const SUBSTEP_MS = 550;
export const RESUME_KEY = "sm64.setupResume.v1";

// The install page shows a few stages, each one thing to do or wait for. The
// server's finer steps only change text inside a stage, so a step flickering
// inside one stage never swaps the card (his report, 2026-10-17: "bouncing
// visually between so many steps").
export const STAGE_OF = {
  close: "close", install: "install", emulator: "game", reopen: "game", rom: "game",
  verify: "check", ready: "check", unavailable: "unavailable", checking: "checking",
};
export const STAGE_ORDER = ["close", "install", "game", "check"];
// Progress order of the steps; a step without a rank has no place in it.
const STEP_RANK = {close: 0, install: 1, emulator: 2, reopen: 2, rom: 3, verify: 4, ready: 5};
// Shown for less than one poll or so, a stage resolved by itself: no acknowledgment.
export const SEEN_MS = 1500;
// An earlier step is shown only after it persists this long.
export const HOLD_MS = 2000;

export function setupStep(setup) {
  return setup?.emu?.verification?.step || "checking";
}

export function stageOf(step) {
  return STAGE_OF[step] || "checking";
}

function ahead(from, to) {
  return !(from in STEP_RANK) || (to in STEP_RANK && STEP_RANK[to] > STEP_RANK[from]);
}

// Only the next stage in order confirms what the user just did ("Usamune
// found"); a jump past it did not happen in front of them.
function acknowledges(from, to) {
  const at = STAGE_ORDER.indexOf(stageOf(from));
  return at >= 0 && STAGE_ORDER.indexOf(stageOf(to)) === at + 1;
}

export function shownStart(step, now) {
  return {step, since: now, ack: null, behind: null};
}

// The step on screen after observing `actual` at `now`. Progress shows at
// once; anything else must persist HOLD_MS. Moving on from a stage the user
// saw to the next one first shows its acknowledgment for SUBSTEP_MS.
export function shownNext(state, actual, now) {
  if (state.ack) {
    if (now < state.ack.until) return state;
    const step = ahead(state.ack.to, actual) ? actual : state.ack.to;
    return shownNext(shownStart(step, now), actual, now);
  }
  if (actual === state.step) return state.behind == null ? state : {...state, behind: null};
  if (ahead(state.step, actual)) {
    if (stageOf(actual) === stageOf(state.step)) return {...state, step: actual, behind: null};
    if (acknowledges(state.step, actual) && now - state.since >= SEEN_MS) {
      return {...state, ack: {to: actual, until: now + SUBSTEP_MS}, behind: null};
    }
    return shownStart(actual, now);
  }
  const behind = state.behind ?? now;
  if (now - behind < HOLD_MS) return state.behind === behind ? state : {...state, behind};
  return stageOf(actual) === stageOf(state.step) ? {...state, step: actual, behind: null} : shownStart(actual, now);
}

// When the shown step can next change without a new observation.
export function shownWake(state) {
  return state.ack ? state.ack.until : state.behind == null ? null : state.behind + HOLD_MS;
}

export function canAdvance(page, setup, step = setupStep(setup)) {
  if (page === "connect") return setup?.emu?.target?.state === "ready";
  if (page === "install") return step === "ready";
  return false;
}

export function pagesFor(platform) {
  return platform === N64 ? ["platform", "console"] : EMU_PAGES;
}

export function canReviewForward(page, reached, setup, step) {
  const pages = pagesFor(reached.platform);
  const next = pages[pages.indexOf(page) + 1];
  if (!next || !reached.pages.includes(next)) return false;
  return page === "platform" || canAdvance(page, setup, step);
}

export function readResume() {
  try {
    const saved = JSON.parse(localStorage.getItem(RESUME_KEY));
    if ([EMU, N64].includes(saved?.platform) && pagesFor(saved.platform).includes(saved.page)) {
      // Completion needs a fresh observation; never restore a celebration from storage.
      return {platform: saved.platform, page: saved.page === "complete" ? "install" : saved.page};
    }
  } catch { /* Missing/corrupt private storage starts at the platform choice. */ }
  return null;
}

export function saveResume(value) {
  try {
    if (value) localStorage.setItem(RESUME_KEY, JSON.stringify(value));
    else localStorage.removeItem(RESUME_KEY);
  } catch { /* Setup also works in browsers without persistent storage. */ }
}
