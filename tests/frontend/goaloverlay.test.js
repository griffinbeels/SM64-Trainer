// The stream overlay's URL settings and counted-goal rules (ui/goaloverlay.js).
// The overlay's only settings channel is its URL (an OBS Browser Source shares
// nothing with the app), so a URL that parses wrongly is an overlay that looks
// wrong on stream with no way for the streamer to see why.
import { expect, test } from "vitest";
import { OVERLAY_DEFAULTS, THEME_BOX, CANVAS_PAD, overlayQuery, overlaySize, overlayUrl,
         parseOverlayConfig, progressMoment, progressView, targetSection }
  from "../../src/sm64_events/ui/goaloverlay.js";

test("an empty query is the defaults", () => {
  expect(parseOverlayConfig("")).toEqual(OVERLAY_DEFAULTS);
  expect(parseOverlayConfig(undefined)).toEqual(OVERLAY_DEFAULTS);
});

test("every key is overridable by a query parameter of its own name", () => {
  const config = parseOverlayConfig(
    "?theme=bar&accent=%2368adf2&scale=1.5&icon=0&name=false&source=off&pips=no"
    + "&celebrate=0&align=right&idle=hide");
  expect(config).toEqual({ theme: "bar", accent: "68adf2", scale: 1.5, icon: false,
    name: false, source: false, pips: false, celebrate: false, align: "right", idle: "hide" });
});

test("a malformed value falls back to its default instead of breaking the overlay", () => {
  const config = parseOverlayConfig("theme=neon&accent=blue&scale=abc&icon=maybe&align=top&idle=x");
  expect(config).toEqual(OVERLAY_DEFAULTS);
});

test("accent accepts 3- and 6-digit hex with or without #", () => {
  expect(parseOverlayConfig("accent=F80").accent).toBe("ff8800");
  expect(parseOverlayConfig("accent=%23ABCDEF").accent).toBe("abcdef");
});

test("scale is clamped to its range and snapped to its step", () => {
  expect(parseOverlayConfig("scale=9").scale).toBe(3);
  expect(parseOverlayConfig("scale=0.1").scale).toBe(0.5);
  expect(parseOverlayConfig("scale=1.23").scale).toBe(1.25);
  expect(overlayQuery(parseOverlayConfig("scale=1.23"))).toBe("scale=1.25");
  expect(parseOverlayConfig("scale=-2").scale).toBe(OVERLAY_DEFAULTS.scale);
});

test("the designer's URL carries only what differs, and parses back to the same config", () => {
  expect(overlayQuery(OVERLAY_DEFAULTS)).toBe("");
  expect(overlayUrl("http://127.0.0.1:8064", OVERLAY_DEFAULTS))
    .toBe("http://127.0.0.1:8064/ui/goal-overlay.html");
  const config = { ...OVERLAY_DEFAULTS, theme: "arcade", pips: false, scale: 2, accent: "ff7b73" };
  const query = overlayQuery(config);
  expect(query).toBe("theme=arcade&accent=ff7b73&scale=2&pips=0");
  expect(parseOverlayConfig(query)).toEqual(config);
});

test("the Browser Source size is the theme's fixed box at the chosen scale", () => {
  const box = THEME_BOX.plate;
  expect(overlaySize(OVERLAY_DEFAULTS)).toEqual({
    width: box.width + 2 * CANVAS_PAD, height: box.height + 2 * CANVAS_PAD });
  const big = overlaySize({ ...OVERLAY_DEFAULTS, theme: "bar", scale: 1.5 });
  expect(big).toEqual({ width: Math.ceil(THEME_BOX.bar.width * 1.5) + 2 * CANVAS_PAD,
    height: Math.ceil(THEME_BOX.bar.height * 1.5) + 2 * CANVAS_PAD });
});

test("the overlay follows the server's target: a star, a segment, or nothing", () => {
  const stars = [{ course_id: 2, star_id: 0 }, { course_id: 2, star_id: 6 }];
  const segments = [{ kind: "segment", segment_id: 9 }];
  expect(targetSection({ target: { course_id: 2, star_id: 6 }, stars, segments })).toBe(stars[1]);
  expect(targetSection({ target: { kind: "segment", segment_id: 9 }, stars, segments }))
    .toBe(segments[0]);
  // target_changed may clear to a star-kind target with no course.
  expect(targetSection({ target: { kind: "star", course_id: null }, stars, segments })).toBe(null);
  expect(targetSection(null)).toBe(null);
});

const goal = (done, extra = {}) => ({
  key: "star:1:0", goal_cs: 1500, count: 10, streak: true,
  progress: { done, needed: 10, best: 4, broken_by: null, window_start: "2026-09-23T18:00:00Z" },
  ...extra,
});

test("a goal you beat once has no progress; a counted goal says streak or total", () => {
  expect(progressView({ key: "k", goal_cs: 100, count: 1 })).toBe(null);
  expect(progressView(null)).toBe(null);
  expect(progressView(goal(3))).toMatchObject({ streak: true, done: 3, needed: 10, best: 4,
    complete: false, dots: true, unit: "in a row" });
  expect(progressView(goal(6, { streak: false }))).toMatchObject({ streak: false, unit: "done" });
  expect(progressView(goal(12))).toMatchObject({ done: 10, complete: true });
});

test("more than twenty successes draw a bar instead of dots", () => {
  const long = goal(5, { count: 25, progress: { done: 5, needed: 25, best: 5 } });
  expect(progressView(long).dots).toBe(false);
  expect(progressView(goal(5, { count: 20, progress: { done: 5, needed: 20 } })).dots).toBe(true);
});

test("each success ticks, the last one completes, and a lost run breaks the streak", () => {
  expect(progressMoment(goal(3), goal(4))).toBe("tick");
  expect(progressMoment(goal(9), goal(10))).toBe("complete");
  expect(progressMoment(goal(4), goal(0))).toBe("break");
  expect(progressMoment(goal(4), goal(4))).toBe(null);
});

test("a total count never breaks: it only ever goes up within a sitting", () => {
  const total = (done) => goal(done, { streak: false });
  expect(progressMoment(total(6), total(7))).toBe("tick");
  expect(progressMoment(total(6), total(0))).toBe(null);
});

test("nothing animates on first load or when the challenge itself changed", () => {
  expect(progressMoment(null, goal(3))).toBe(null);
  expect(progressMoment(goal(3), goal(0, { key: "star:1:1" }))).toBe(null);
  expect(progressMoment(goal(3), goal(0, { goal_cs: 1450 }))).toBe(null);
  expect(progressMoment(goal(3), goal(0, { count: 5 }))).toBe(null);
  const newSitting = goal(0, { progress: { done: 0, needed: 10, best: 0,
    window_start: "2026-09-24T18:00:00Z" } });
  expect(progressMoment(goal(3), newSitting)).toBe(null);
});
