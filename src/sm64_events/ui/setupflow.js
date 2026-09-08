// Pure wizard policy. Observations come from /api/setup, never grading settings.
import { EMU, N64 } from "./platform.js";
export const SETUP_TITLES = {
  platform: "Select platform", connect: "Connect Project64",
  install: "Set up Practice Replay", complete: "You're ready!", console: "N64 support",
};
export const EMU_PAGES = ["platform", "connect", "install", "complete"];
export const STEP_ORDER = ["close", "install", "reopen", "rom", "verify", "ready"];
export const ACKNOWLEDGE_MS = 1000;
export const SUBSTEP_MS = 550;
export const RESUME_KEY = "sm64.setupResume.v1";

export function setupStep(setup) {
  return setup?.emu?.verification?.step || "checking";
}

export function canAdvance(page, setup) {
  if (page === "connect") return setup?.emu?.target?.state === "ready";
  if (page === "install") return setup?.emu?.verification?.ready === true;
  return false;
}

export function pagesFor(platform) {
  return platform === N64 ? ["platform", "console"] : EMU_PAGES;
}

export function canReviewForward(page, reached, setup) {
  const pages = pagesFor(reached.platform);
  const next = pages[pages.indexOf(page) + 1];
  if (!next || !reached.pages.includes(next)) return false;
  return page === "platform" || canAdvance(page, setup);
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
