// The practice log's one open card when the TARGET moves (ui/components/
// practicelog.js: nextActivation + autoOpenKey; ui/target.js records his pick).
//
// Round 13 (Griffin, 2026-09-28): "Even if we autoselect the next star/segment
// (e.g., auto selecting Bowser 1 correctly), it shouldn't immediately expand
// the practice log, because we don't have an entry yet." The game moving the
// target keeps the card he was reading open until the new target's first row;
// his own pick still opens its card at once.
import { afterEach, expect, test, vi } from "vitest";
import { PICK_LANDS_MS, autoOpenKey, nextActivation }
  from "../../src/sm64_events/ui/components/practicelog.js";
import { lastTargetPick, requestTarget } from "../../src/sm64_events/ui/target.js";

afterEach(() => { vi.unstubAllGlobals(); });

const star = (course, id, attempts) => ({ course_id: course, star_id: id,
  attempts: Array.from({ length: attempts }, (_, n) => ({ id: course * 100 + id * 10 + n })) });
const REDS = star(16, 0, 3);          // the run he just finished
const BOWSER = star(17, 0, 2);        // older rows from earlier in the session
const KEYS = { reds: "star:16:0", bowser: "star:17:0" };

// One render of the log's decision, the way PracticeLog makes it.
function render(prev, { sections, activeKey, played, pick = null, now }) {
  const activation = nextActivation(prev, activeKey, pick, now,
    autoOpenKey(sections, null, played));
  return { activation, open: autoOpenKey(sections, activeKey, played, [], activation.leads) };
}

test("the game moving the target keeps the card he was reading open until the new one's first row", () => {
  const sections = [BOWSER, REDS];
  let step = render(null, { sections, activeKey: KEYS.reds, played: [KEYS.reds, KEYS.bowser], now: 0 });
  expect(step.open).toBe(KEYS.reds);
  // He walks into Bowser 1: the target moves by itself. Bowser has older rows.
  step = render(step.activation, { sections, activeKey: KEYS.bowser,
    played: [KEYS.reds, KEYS.bowser], now: 10_000 });
  expect(step.open).toBe(KEYS.reds);
  // Bowser's first row of this visit makes it the newest thing played.
  step = render(step.activation, { sections, activeKey: KEYS.bowser,
    played: [KEYS.bowser, KEYS.reds], now: 20_000 });
  expect(step.open).toBe(KEYS.bowser);
  // It keeps the slot from then on, even if a row lands elsewhere.
  step = render(step.activation, { sections, activeKey: KEYS.bowser,
    played: [KEYS.reds, KEYS.bowser], now: 30_000 });
  expect(step.open).toBe(KEYS.bowser);
});

test("his pick opens its card at once, older rows and all", () => {
  const sections = [BOWSER, REDS];
  let step = render(null, { sections, activeKey: KEYS.reds, played: [KEYS.reds, KEYS.bowser], now: 0 });
  const pick = { key: KEYS.bowser, at: 10_000 };
  step = render(step.activation, { sections, activeKey: KEYS.reds,
    played: [KEYS.reds, KEYS.bowser], pick, now: 10_010 });
  expect(step.open).toBe(KEYS.reds);            // not landed yet
  step = render(step.activation, { sections, activeKey: KEYS.bowser,
    played: [KEYS.reds, KEYS.bowser], pick, now: 10_400 });
  expect(step.open).toBe(KEYS.bowser);
});

test("picking the target the game already chose opens it", () => {
  const sections = [BOWSER, REDS];
  let step = render(null, { sections, activeKey: KEYS.reds, played: [KEYS.reds, KEYS.bowser], now: 0 });
  step = render(step.activation, { sections, activeKey: KEYS.bowser,
    played: [KEYS.reds, KEYS.bowser], now: 10_000 });
  expect(step.open).toBe(KEYS.reds);
  step = render(step.activation, { sections, activeKey: KEYS.bowser,
    played: [KEYS.reds, KEYS.bowser], pick: { key: KEYS.bowser, at: 12_000 }, now: 12_300 });
  expect(step.open).toBe(KEYS.bowser);
});

test("an old pick does not make the game's later return to that target his pick", () => {
  const pick = { key: KEYS.bowser, at: 0 };
  const picked = nextActivation({ key: KEYS.reds, since: -1, leads: true }, KEYS.bowser, pick, 200);
  expect(picked.leads).toBe(true);
  const away = nextActivation(picked, KEYS.reds, pick, 60_000);
  const back = nextActivation(away, KEYS.bowser, pick, 120_000);
  expect(back.leads).toBe(false);
  expect(nextActivation(picked, KEYS.bowser, pick, PICK_LANDS_MS + 1).leads).toBe(true);
});

test("a pick that lands too late, or names another target, is not his pick of this one", () => {
  const prev = { key: KEYS.reds, since: 0, leads: true };
  expect(nextActivation(prev, KEYS.bowser, { key: KEYS.bowser, at: 0 }, PICK_LANDS_MS + 1).leads)
    .toBe(false);
  expect(nextActivation(prev, KEYS.bowser, { key: "star:9:9", at: 100 }, 200).leads).toBe(false);
});

test("the target leaving for nowhere and coming back is the same activation", () => {
  const prev = { key: KEYS.bowser, since: 0, leads: false };
  expect(nextActivation(prev, null, null, 5_000)).toBe(prev);
  expect(nextActivation(prev, KEYS.bowser, null, 6_000)).toBe(prev);
});

test("the target the page opens on leads, so it opens as it always has", () => {
  expect(nextActivation(null, KEYS.bowser, null, 0).leads).toBe(true);
  const sections = [BOWSER, REDS];
  expect(render(null, { sections, activeKey: KEYS.bowser,
    played: [KEYS.reds, KEYS.bowser], now: 0 }).open).toBe(KEYS.bowser);
});

test("a target the game chose with no rows stays closed and the last card stays open", () => {
  const fresh = star(18, 0, 0);
  const sections = [fresh, REDS];
  let step = render(null, { sections, activeKey: KEYS.reds, played: [KEYS.reds], now: 0 });
  step = render(step.activation, { sections, activeKey: "star:18:0", played: [KEYS.reds], now: 5_000 });
  expect(step.open).toBe(KEYS.reds);
});

const fakeStore = () => ({ refresh: vi.fn(), setNotice: vi.fn() });

test("the target door records an accepted pick, never a fill or a refusal", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) })));
  expect(await requestTarget(fakeStore(), { course_id: 17, star_id: 0, strat_tag: null })).toBe(true);
  expect(lastTargetPick().key).toBe(KEYS.bowser);
  await requestTarget(fakeStore(), { kind: "segment", segment_id: 67 });
  const picked = lastTargetPick();
  expect(picked.key).toBe("segment:67");
  await requestTarget(fakeStore(), { course_id: 17, star_id: 0 }, { quiet: true });
  await requestTarget(fakeStore(), { course_id: 17, star_id: 0 }, { auto: true });
  expect(lastTargetPick()).toBe(picked);
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 409,
    json: async () => ({ detail: "you can only practice what you are standing in" }),
    text: async () => "you can only practice what you are standing in" })));
  expect(await requestTarget(fakeStore(), { course_id: 1, star_id: 0 })).toBe(false);
  expect(lastTargetPick()).toBe(picked);
});
