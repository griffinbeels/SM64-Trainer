// @vitest-environment jsdom
// The Practice log's Ghost button (ui/components/attemptscene.js, lab only) on
// the real AttemptTable, against a scripted lab: which rows draw it, in which
// state, when the table asks, and that it never asks with the lab off. The
// lab's answers follow GET /api/scene-lab/attempt-scenes?ids= (the scene lab's
// API, server/scene_lab_api.py) and are stubbed here only.
import { h } from "preact";
import { render, cleanup, fireEvent } from "@testing-library/preact";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { AttemptTable } from "../../src/sm64_events/ui/components/attemptlog.js";
import { OPEN_IN_VIEWER, SCENE_FINISHING, SCENE_POLL, sceneButton }
  from "../../src/sm64_events/ui/components/attemptscene.js";

const NO_CAPTURE = "Capture was not recording when this attempt was played.";
let asks, answer;

function respond(body, status = 200) {
  return Promise.resolve({ ok: status < 300, status, json: () => Promise.resolve(body) });
}

beforeEach(() => {
  SCENE_POLL.ms = 15;
  asks = [];
  // Per asked id: ready, still cutting, unavailable, or unknown to the lab (left out).
  answer = (id) => ({ 11: { state: "ready", seconds: 15.2 }, 12: { state: "cutting" },
    13: { state: "unavailable", reason: NO_CAPTURE } })[id];
  globalThis.fetch = vi.fn((url) => {
    if (url.startsWith("/api/scene-lab/attempt-scenes")) {
      const ids = new URL(url, "http://trainer").searchParams.get("ids").split(",").map(Number);
      asks.push(ids);
      const scenes = Object.fromEntries(ids.map((id) => [String(id), answer(id, asks.length)])
        .filter(([, scene]) => scene));
      return respond({ scenes });
    }
    return respond({}, 404);
  });
});
afterEach(() => { cleanup(); SCENE_POLL.ms = 1000; vi.restoreAllMocks(); });

async function until(check, turns = 200) {
  for (let turn = 0; ; turn++) {
    try {
      return check();
    } catch (error) {
      if (turn >= turns) throw error;
    }
    await new Promise((resolve) => { setTimeout(resolve, 0); });
  }
}
const wait = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });

const attempt = (id) => ({ id, outcome: "success", igt: 12.76, rta: 13.1, igt_frames: 383, rta_frames: 393,
  strat_tag: "Standard", pb_action: "save", pb_blocked: null, pb_delta_frames: -10, caveat: null,
  other_strat: false, cleared: false, imported: false });
const SEC = { course_id: 1, star_id: 1, strategies: ["Standard"], strategy_groups: null, last_strat: "Standard",
  default_strat: "Standard", pb_by_strat: {} };

let shown = [];
function table(ids, openScene = vi.fn()) {
  shown = ids;
  const rows = ids.map(attempt);
  const t = { clock: "igt", showDust: false, view: { catalog: { courses: [] } }, refresh() {}, setNotice() {},
    sceneLab: openScene && { open: openScene } };
  return h(AttemptTable, { attempts: rows, rows, t, sec: SEC });
}

// A row by its attempt: the table numbers its rows #1.. in the order it was given them.
const rowOf = (page, id) => [...page.container.querySelectorAll(".attempt-table tr")]
  .find((row) => row.querySelector(".attempt-index")?.textContent.trim().endsWith(`#${shown.indexOf(id) + 1}`));
const ghostOf = (page, id) => rowOf(page, id).querySelector("button.attempt-scene");

test("a row draws the Ghost button left of ▷ in its scene's state, and none for an attempt the lab does not know", async () => {
  const page = render(table([11, 12, 13, 14]));
  await until(() => expect(ghostOf(page, 11)).toBeTruthy());
  const ready = ghostOf(page, 11), slots = ready.parentElement;
  expect(slots.firstElementChild).toBe(ready);
  expect(ready.nextElementSibling.getAttribute("aria-label")).toBe("View replay");
  expect([ready.disabled, ready.title, ready.getAttribute("aria-label")]).toEqual([false, OPEN_IN_VIEWER, OPEN_IN_VIEWER]);
  expect(ready.querySelector("svg")).toBeTruthy();
  expect([ghostOf(page, 12).disabled, ghostOf(page, 12).title]).toEqual([true, SCENE_FINISHING]);
  expect([ghostOf(page, 13).disabled, ghostOf(page, 13).title]).toEqual([true, NO_CAPTURE]);
  expect(ghostOf(page, 13).getAttribute("aria-label")).toBe(OPEN_IN_VIEWER);
  expect(ghostOf(page, 14)).toBeNull();
  expect(rowOf(page, 14).querySelector(".attempt-action-slots").firstElementChild.getAttribute("aria-label"))
    .toBe("View replay");
  expect(asks).toEqual([[11, 12, 13, 14]]);
});

test("a cutting scene turns its button on by itself, and the table stops asking once nothing is cutting", async () => {
  answer = (id, ask) => (id === 12 ? { state: ask < 3 ? "cutting" : "ready", seconds: 9.4 } : undefined);
  const page = render(table([12, 14]));
  await until(() => expect(ghostOf(page, 12)?.title).toBe(SCENE_FINISHING));
  await until(() => expect(ghostOf(page, 12).disabled).toBe(false));
  expect(ghostOf(page, 12).title).toBe(OPEN_IN_VIEWER);
  expect(asks.length).toBe(3);
  await wait(SCENE_POLL.ms * 6);
  expect(asks.length).toBe(3);                    // nothing cutting: no more asking
});

test("without a cutting row the table asks once per change of the rows it shows, never on a re-render", async () => {
  answer = (id) => (id === 11 ? { state: "ready" } : undefined);
  const page = render(table([14, 11]));
  await until(() => expect(ghostOf(page, 11)).toBeTruthy());
  page.rerender(table([11, 14]));                 // the same rows, re-sorted, new objects
  await wait(SCENE_POLL.ms * 4);
  expect(asks).toEqual([[11, 14]]);
  page.rerender(table([11, 13, 14]));             // a new attempt joined the page
  await until(() => expect(asks).toEqual([[11, 14], [11, 13, 14]]));
  expect(fetch.mock.calls.at(-1)[0]).toBe("/api/scene-lab/attempt-scenes?ids=11,13,14");
});

test("with the lab off nothing is asked and no row draws the button", async () => {
  const page = render(table([11, 12, 13], null));
  await wait(SCENE_POLL.ms * 4);
  expect(asks).toEqual([]);
  expect(page.container.querySelector(".attempt-scene")).toBeNull();
  expect(page.container.querySelectorAll(".attempt-action-slots").length).toBe(3);
});

test("a lab that cannot answer draws no buttons and no error", async () => {
  globalThis.fetch = vi.fn(() => respond({ detail: "Not Found" }, 404));
  const page = render(table([11, 12]));
  await until(() => expect(fetch).toHaveBeenCalled());
  await wait(SCENE_POLL.ms * 4);
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(page.container.querySelector(".attempt-scene")).toBeNull();
});

test("clicking a ready Ghost button hands its attempt to the viewer's door; a waiting one does nothing", async () => {
  const openScene = vi.fn();
  const page = render(table([11, 12, 13, 14], openScene));
  await until(() => expect(ghostOf(page, 11)).toBeTruthy());
  fireEvent.click(ghostOf(page, 12));
  fireEvent.click(ghostOf(page, 13));
  expect(openScene).not.toHaveBeenCalled();
  fireEvent.click(ghostOf(page, 11));
  expect(openScene).toHaveBeenCalledWith(11);
});

test("an answer the page does not know is a disabled button that says why", () => {
  expect(sceneButton(undefined)).toBeNull();
  expect(sceneButton({ state: "ready" })).toEqual({ disabled: false, title: OPEN_IN_VIEWER });
  expect(sceneButton({ state: "cutting" })).toEqual({ disabled: true, title: SCENE_FINISHING });
  expect(sceneButton({ state: "gone", reason: NO_CAPTURE })).toEqual({ disabled: true, title: NO_CAPTURE });
  expect(sceneButton({ state: "unavailable" }).disabled).toBe(true);
});
