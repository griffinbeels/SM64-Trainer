// src/sm64_events/ui/goaloverlay.js — the stream overlay's settings and
// progress rules, import-free so tests/frontend/goaloverlay.test.js drives
// them under node.
//
// Task 0106 (Griffin): streamers type "pb: 1:48.56 / goal: low 1:4x" into OBS
// by hand. The stream overlay (ui/goal-overlay.html) is the page an OBS
// Browser Source points at instead: the current target's PB over its GOAL,
// counting a streak goal's successes and celebrating on top of itself when the
// goal falls. "we should make this as stylish as possible, and perhaps allow
// users to customize it as needed."
//
// Every setting lives in the page URL, because an OBS Browser Source has its
// own browser profile: nothing the app stores reaches it, and a URL is the one
// thing a streamer can paste. One CONFIG object holds the defaults; each key
// is overridable by a query parameter of the same name. The designer
// (components/streamoverlay.js) writes only the keys that differ, so a
// default URL stays short and a future default change reaches old URLs.

export const OVERLAY_DEFAULTS = Object.freeze({
  theme: "plate",       // one of THEMES
  accent: "f0bd32",     // hex without '#': the GOAL line, pips and celebration
  scale: 1,             // multiplies the whole overlay, SCALE_RANGE
  icon: true,           // the target's star or segment art
  name: true,           // the target's name and course
  source: true,         // where the goal came from ("Auto · Gold 2")
  pips: true,           // one dot per success for a counted goal; else N / M only
  celebrate: true,      // play the celebration when a goal falls
  align: "left",        // where the overlay sits in a wider Browser Source
  idle: "show",         // "hide" fades the overlay out while nothing is targeted
});

export const THEMES = Object.freeze({
  plate: { label: "Plate", note: "A dark glass panel. Reads on any game background." },
  minimal: { label: "Minimal", note: "No panel, just outlined type over the game." },
  bar: { label: "Bar", note: "One slim line for the top or bottom edge of the screen." },
  arcade: { label: "Arcade", note: "Big game-font numbers with a chunky outline." },
});
export const ALIGNS = Object.freeze(["left", "center", "right"]);
export const IDLE_MODES = Object.freeze(["show", "hide"]);
export const SCALE_RANGE = Object.freeze({ min: 0.5, max: 3, step: 0.05 });

// The overlay's own box at scale 1, per theme, in CSS pixels. Fixed so an OBS
// crop never jumps: switching star, gaining a streak or celebrating changes
// what is INSIDE the box, never its size. CANVAS_PAD (unscaled; the page's
// own margin) leaves room for the panel's shadow so a tight crop keeps it.
export const THEME_BOX = Object.freeze({
  plate: { width: 420, height: 164 },
  minimal: { width: 420, height: 164 },
  bar: { width: 860, height: 60 },
  arcade: { width: 440, height: 172 },
});
export const CANVAS_PAD = 14;

// Beyond this many a counted goal draws a bar instead of dots: twenty dots
// is the most that still read as individual successes at stream size.
export const MAX_PIPS = 20;

const TRUE_WORDS = new Set(["1", "true", "yes", "on"]);
const FALSE_WORDS = new Set(["0", "false", "no", "off"]);

function parseBool(raw, fallback) {
  const word = String(raw).trim().toLowerCase();
  if (TRUE_WORDS.has(word)) return true;
  if (FALSE_WORDS.has(word)) return false;
  return fallback;
}

export function normalizeAccent(raw) {
  const hex = String(raw ?? "").trim().replace(/^#/, "").toLowerCase();
  if (/^[0-9a-f]{6}$/.test(hex)) return hex;
  if (/^[0-9a-f]{3}$/.test(hex)) return hex.split("").map((c) => c + c).join("");
  return null;
}

export function clampScale(raw) {
  const value = Number(raw);
  if (!Number.isFinite(value) || value <= 0) return null;
  const clamped = Math.min(SCALE_RANGE.max, Math.max(SCALE_RANGE.min, value));
  // toFixed: 25 * 0.05 is 1.2500000000000002, which would land in the URL.
  return Number((Math.round(clamped / SCALE_RANGE.step) * SCALE_RANGE.step).toFixed(2));
}

const oneOf = (allowed) => (raw, fallback) => (allowed.includes(raw) ? raw : fallback);
// How each non-boolean key reads its raw query value; booleans share parseBool.
const PARSERS = {
  theme: oneOf(Object.keys(THEMES)),
  align: oneOf(ALIGNS),
  idle: oneOf(IDLE_MODES),
  accent: (raw, fallback) => normalizeAccent(raw) || fallback,
  scale: (raw, fallback) => clampScale(raw) ?? fallback,
};

/** The overlay's settings from a query string ("?theme=bar&scale=1.5" or a
 *  URLSearchParams). Unknown keys are ignored and a malformed value falls
 *  back to its default, so a hand-edited URL can never blank the overlay. */
export function parseOverlayConfig(search) {
  const params = search instanceof URLSearchParams ? search : new URLSearchParams(search || "");
  const config = { ...OVERLAY_DEFAULTS };
  for (const [key, fallback] of Object.entries(OVERLAY_DEFAULTS)) {
    if (!params.has(key)) continue;
    const parse = PARSERS[key] || parseBool;
    config[key] = parse(params.get(key), fallback);
  }
  return config;
}

/** The query string for a config: only the keys that differ from the
 *  defaults, booleans as 1/0. Round-trips through parseOverlayConfig. */
export function overlayQuery(config) {
  const params = new URLSearchParams();
  for (const [key, fallback] of Object.entries(OVERLAY_DEFAULTS)) {
    const value = config[key];
    if (value === undefined || value === fallback) continue;
    params.set(key, typeof fallback === "boolean" ? (value ? "1" : "0") : String(value));
  }
  return params.toString();
}

export const OVERLAY_PATH = "/ui/goal-overlay.html";

export function overlayUrl(origin, config) {
  const query = overlayQuery(config);
  return `${origin}${OVERLAY_PATH}${query ? `?${query}` : ""}`;
}

/** The Browser Source size that fits the overlay exactly at its scale. */
export function overlaySize(config) {
  const box = THEME_BOX[config.theme] || THEME_BOX[OVERLAY_DEFAULTS.theme];
  const scale = config.scale || 1;
  return { width: Math.ceil(box.width * scale) + 2 * CANVAS_PAD,
           height: Math.ceil(box.height * scale) + 2 * CANVAS_PAD };
}

/** The target's practice section in a session view, or null when nothing is
 *  targeted. The overlay follows the SERVER's target and nothing else: the
 *  practice page additionally hides a target you cannot run where you stand,
 *  but on stream a plate that blanks every time you walk through the castle
 *  would be noise, so the overlay keeps showing the star you are on. */
export function targetSection(view) {
  const target = view && view.target;
  if (!target) return null;
  if (target.kind === "segment") {
    return (view.segments || []).find((sec) => sec.segment_id === target.segment_id) || null;
  }
  if (target.course_id == null) return null;
  return (view.stars || []).find((sec) => sec.course_id === target.course_id
    && sec.star_id === target.star_id) || null;
}

/** A counted goal's progress as the overlay draws it, or null for a goal you
 *  beat once. "sub 1:55 10x streak" is ten in a row; "sub 1:55 10x" is ten in
 *  total. Both count this sitting only (the server's `window_start`). */
export function progressView(goal) {
  if (!goal || !(goal.count > 1)) return null;
  const progress = goal.progress || {};
  const needed = Math.max(1, progress.needed ?? goal.count);
  const done = Math.max(0, Math.min(progress.done ?? 0, needed));
  return {
    streak: !!goal.streak,
    done,
    needed,
    best: Math.max(done, progress.best ?? 0),
    complete: done >= needed,
    dots: needed <= MAX_PIPS,
    unit: goal.streak ? "in a row" : "done",
  };
}

// Two goals describe the same challenge when nothing about WHAT counts moved:
// a different entry, threshold, count, kind or sitting is a new challenge,
// and its progress starting over is not a streak breaking.
function sameChallenge(before, after) {
  return before.key === after.key && before.goal_cs === after.goal_cs
    && before.count === after.count && !!before.streak === !!after.streak
    && ((before.progress || {}).window_start ?? null)
      === ((after.progress || {}).window_start ?? null);
}

/** What just happened to a counted goal between two refetches, for the
 *  overlay's small animations: "tick" (one more success), "complete" (the
 *  last one), "break" (a streak lost its run), or null. Null on first load
 *  and whenever the challenge itself changed, so nothing animates that the
 *  player did not just do. */
export function progressMoment(before, after) {
  if (!before || !after || !sameChallenge(before, after)) return null;
  const was = progressView(before);
  const now = progressView(after);
  if (!was || !now) return null;
  if (now.done > was.done) return now.complete && !was.complete ? "complete" : "tick";
  if (now.streak && now.done < was.done) return "break";
  return null;
}
