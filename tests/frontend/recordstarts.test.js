// @vitest-environment jsdom
import { afterEach, expect, test, vi } from "vitest";
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/preact";
import { h } from "preact";
import { SegmentTimeline } from "../../src/sm64_events/ui/components/segmenttimeline.js";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

// IDs encode journal order. The alternate samples deliberately bracket the
// primary run, so treating all selected rows as one walk cannot pass.
const samples = [
  [1, "Entered from Castle Grounds"],
  [10, "Usamune warp to Lobby"],
  [20, "Opened the shared door"],
  [30, "Reached the common finish"],
  [40, "Another Castle Grounds entry"],
].map(([id, label]) => ({ id, label, frame: id * 30,
  place: "lobby", place_label: "Lobby", place_level: null }));
const clause = (id) => ({ type: "level_enter", to: id });
const walked = { node: "6:2", label: "Basement",
  clause: { type: "area_enter", level: 6, area: 2 }, sentence: "Crossed Basement" };
const shipped = { id: 42, name: "LBLJ", enabled: true,
  start_triggers: [clause(10)], end_triggers: [clause(30)],
  guards: [], waypoints: [], parents: [], match_mode: "strict", clock_start: "move" };

function synthesize(url) {
  const query = new URL(url, "http://fixture").searchParams;
  const ids = query.get("ids").split(",").filter(Boolean).map(Number).sort((a, b) => a - b);
  const extra = (query.get("start_ids") || "").split(",").filter(Boolean).map(Number);
  const starts = [ids[0], ...extra.filter((id) => id !== ids[0]).sort((a, b) => a - b)];
  const middles = ids.slice(1, -1);
  const picked = middles.filter((id) => !extra.includes(id)).map((id) => ({ id,
    clause: clause(id), sentence: samples.find((row) => row.id === id).label }));
  const sentence = (id) => samples.find((row) => row.id === id).label;
  return { name: "Recorded LBLJ", start_clause: clause(ids[0]), end_clause: clause(ids.at(-1)),
    start_sentence: sentence(ids[0]), end_sentence: sentence(ids.at(-1)),
    start_clauses: starts.map(clause), start_sentences: starts.map(sentence),
    picked, picked_explicit: middles.length > 0, steps: [walked] };
}

const report = (fires = 1) => ({ arms: 1, fires, unclosed: [], attempts: [] });
const response = (body) => ({ ok: true, status: 200, json: async () => body });

function fixture({ holdSynthesis = false, holdBacktest = false } = {}) {
  const calls = { synthesis: [], backtest: [], lint: [], save: [] }, unexpected = [];
  vi.stubGlobal("fetch", vi.fn((url, options = {}) => {
    const path = String(url), method = options.method || "GET";
    const body = options.body ? JSON.parse(options.body) : null;
    if (path === "/api/segments/authoring")
      return Promise.resolve(response({ can_share: false, default_scope: "personal" }));
    if (path.startsWith("/api/segments/timeline?"))
      return Promise.resolve(response({ rows: samples }));
    if (path.startsWith("/api/segments/synthesize?")) {
      const call = { path, body: synthesize(path) };
      calls.synthesis.push(call);
      if (holdSynthesis) return new Promise((resolve) => { call.finish = () => resolve(response(call.body)); });
      return Promise.resolve(response(call.body));
    }
    if (path === "/api/segments/backtest") {
      const call = { body };
      calls.backtest.push(call);
      if (holdBacktest) return new Promise((resolve) => { call.finish = (value = report()) => resolve(response(value)); });
      return Promise.resolve(response(report()));
    }
    if (path === "/api/segments/lint") {
      calls.lint.push({ body });
      return Promise.resolve(response({ warnings: [] }));
    }
    if (/^\/api\/segments(?:\/\d+)?\?scope=personal$/.test(path)) {
      calls.save.push({ path, method, body });
      return Promise.resolve(response({ ok: true, id: 91, scope: "personal" }));
    }
    if (path === "/api/uilog") return Promise.resolve(response({ ok: true }));
    unexpected.push(`${method} ${path}`);
    return Promise.resolve({ ok: false, status: 404, json: async () => ({ detail: `Unexpected ${path}` }) });
  }));
  const props = { t: { view: { catalog: { courses: [] } }, feed: [],
    refresh: vi.fn(), setNotice: vi.fn() }, onSaved: vi.fn(async () => {}), onCancel: vi.fn() };
  return { calls, unexpected, props };
}

const rowButton = (view, id) => view.getByRole("button", { name:
  new RegExp(samples.find((sample) => sample.id === id).label) });
const clickRow = (view, id) => fireEvent.click(rowButton(view, id));
const marker = (view, id) => rowButton(view, id).querySelector(".record-mark");
const addStart = (view) => fireEvent.click(view.getByRole("button", { name: "Add another start" }));
const finishAdding = (view) => fireEvent.click(view.getByRole("button", { name: "Done adding starts" }));
const saveButton = (view) => {
  const button = view.container.querySelector(".builder-actions .primary-button");
  expect(button).not.toBeNull();
  return button;
};
async function pickPath(view, interior = false) {
  await view.findByRole("button", { name: /Usamune warp to Lobby/ });
  clickRow(view, 10);
  if (interior) clickRow(view, 20);
  clickRow(view, 30);
}
async function readyToSave(view) {
  await waitFor(() => {
    const button = saveButton(view);
    expect(button.disabled).toBe(false);
    expect(button.textContent.trim()).toMatch(/^(Save|Replace) segment$/);
  });
}

test("the extra-start mode appears after a path pick and cannot reclassify its boundaries", async () => {
  const f = fixture(), view = render(h(SegmentTimeline, f.props));
  await view.findByRole("button", { name: /Usamune warp to Lobby/ });
  expect(view.queryByRole("button", { name: "Add another start" })).toBeNull();
  clickRow(view, 10);
  expect(view.getByRole("button", { name: "Add another start" }).getAttribute("aria-pressed")).toBe("false");
  clickRow(view, 30);
  await readyToSave(view);
  addStart(view);
  expect(view.getByRole("button", { name: "Done adding starts" }).getAttribute("aria-pressed")).toBe("true");
  clickRow(view, 10);
  clickRow(view, 30);
  expect(marker(view, 10).classList.contains("role-start")).toBe(true);
  expect(marker(view, 30).classList.contains("role-finish")).toBe(true);
  expect(view.container.querySelectorAll(".record-mark.role-start")).toHaveLength(1);
  expect(view.container.querySelector(".record-picks").textContent).toMatch(/start|finish/i);
  expect(f.unexpected).toEqual([]);
});

test("alternate samples on both sides of the run do not move Start or Finish", async () => {
  const f = fixture(), view = render(h(SegmentTimeline, f.props));
  await pickPath(view);
  addStart(view);
  clickRow(view, 40);
  clickRow(view, 1);
  await readyToSave(view);
  expect(marker(view, 10).classList.contains("role-start")).toBe(true);
  expect(marker(view, 30).classList.contains("role-finish")).toBe(true);
  expect(marker(view, 1).textContent.trim()).toBe("OR");
  expect(marker(view, 40).textContent.trim()).toBe("OR");
  expect(view.container.querySelectorAll(".record-mark.role-start")).toHaveLength(3);
  expect(view.container.querySelector(".record-review").textContent).toMatch(/or/i);
  fireEvent.click(saveButton(view));
  await waitFor(() => expect(f.calls.save).toHaveLength(1));
  expect(f.calls.save[0].body.start_triggers).toEqual([clause(10), clause(1), clause(40)]);
  expect(f.calls.save[0].body.end_triggers).toEqual([clause(30)]);
  expect(f.calls.save[0].body.waypoints).toEqual([[walked.clause]]);
});

test("promoting the only explicit stop to Start preserves an explicitly empty path", async () => {
  const f = fixture(), view = render(h(SegmentTimeline, f.props));
  await pickPath(view, true);
  await readyToSave(view);
  addStart(view);
  clickRow(view, 20);
  await readyToSave(view);
  expect(marker(view, 20).classList.contains("role-start")).toBe(true);
  expect(view.container.querySelector(".step-picker")).toBeNull();
  const query = new URL(f.calls.synthesis.at(-1).path, "http://fixture").searchParams;
  expect(query.get("ids").split(",").map(Number)).toEqual([10, 20, 30]);
  expect(query.get("start_ids").split(",").map(Number)).toContain(20);
  fireEvent.click(saveButton(view));
  await waitFor(() => expect(f.calls.save).toHaveLength(1));
  expect(f.calls.save[0].body.start_triggers).toEqual([clause(10), clause(20)]);
  expect(f.calls.save[0].body.waypoints).toEqual([]);
});

test("ordinary clicks remove alternate samples without turning them back into stops", async () => {
  const f = fixture(), view = render(h(SegmentTimeline, f.props));
  await pickPath(view, true);
  addStart(view);
  clickRow(view, 20);
  clickRow(view, 40);
  finishAdding(view);
  clickRow(view, 20);
  clickRow(view, 40);
  await readyToSave(view);
  expect(rowButton(view, 20).getAttribute("aria-pressed")).toBe("false");
  expect(rowButton(view, 40).getAttribute("aria-pressed")).toBe("false");
  expect(view.container.querySelectorAll(".record-row.picked")).toHaveLength(2);
  expect(marker(view, 30).classList.contains("role-finish")).toBe(true);
  const query = new URL(f.calls.synthesis.at(-1).path, "http://fixture").searchParams;
  expect(query.get("ids").split(",").map(Number)).toEqual([10, 30]);
  expect(query.get("start_ids") || "").toBe("");
});

test("Clear removes both selections and leaves the next run in ordinary mode", async () => {
  const f = fixture(), view = render(h(SegmentTimeline, f.props));
  await pickPath(view);
  addStart(view);
  clickRow(view, 1);
  fireEvent.click(view.getByRole("button", { name: /Clear/ }));
  expect(view.container.querySelectorAll(".record-row.picked")).toHaveLength(0);
  expect(view.queryByRole("button", { name: "Done adding starts" })).toBeNull();
  expect(view.queryByRole("button", { name: "Save segment" })).toBeNull();
  await pickPath(view);
  await readyToSave(view);
  expect(view.container.querySelectorAll(".record-mark.role-start")).toHaveLength(1);
  expect(view.container.querySelectorAll(".record-mark.role-finish")).toHaveLength(1);
  expect(f.calls.save).toEqual([]);
});

test.each([
  ["create", {}, "POST", "/api/segments?scope=personal"],
  ["re-record", { replaces: shipped }, "PUT", "/api/segments/42?scope=personal"],
  ["Library", { prefill: { name: "Library LBLJ", parents: [] } }, "POST", "/api/segments?scope=personal"],
])("%s sends the same multi-start definition to backtest, lint and save", async (_, intent, method, path) => {
  const f = fixture(), view = render(h(SegmentTimeline, { ...f.props, ...intent }));
  await pickPath(view, true);
  await readyToSave(view);
  addStart(view);
  clickRow(view, 1);
  await readyToSave(view);
  expect(saveButton(view).textContent.trim()).toBe(intent.replaces ? "Replace segment" : "Save segment");
  finishAdding(view);
  fireEvent.click(saveButton(view));
  await waitFor(() => expect(f.calls.save).toHaveLength(1));
  const saved = f.calls.save[0];
  expect([saved.method, saved.path]).toEqual([method, path]);
  expect(saved.body.start_triggers).toEqual([clause(10), clause(1)]);
  expect(saved.body.waypoints).toEqual([[clause(20)]]);
  expect(saved.body.end_triggers).toEqual([clause(30)]);
  expect(saved.body.match_mode).toBe("strict");
  expect(saved.body.clock_start).toBe("move");
  expect(f.calls.backtest.at(-1).body.definition).toEqual(saved.body);
  expect(f.calls.lint.at(-1).body.definition).toEqual(saved.body);
  expect(f.calls.backtest.at(-1).body.replaces).toBe(intent.replaces?.id ?? null);
  expect(f.calls.lint.at(-1).body.segment_id).toBe(intent.replaces?.id ?? null);
  if (intent.replaces || intent.prefill) expect(saved.body.name).toBe(intent.replaces?.name ?? intent.prefill.name);
  expect(f.unexpected).toEqual([]);
});

test("an older synthesis arriving last cannot replace a newer alternate-start pick", async () => {
  const f = fixture({ holdSynthesis: true }), view = render(h(SegmentTimeline, f.props));
  await pickPath(view);
  await waitFor(() => expect(f.calls.synthesis).toHaveLength(1));
  addStart(view);
  clickRow(view, 40);
  await waitFor(() => expect(f.calls.synthesis).toHaveLength(2));
  await act(async () => { f.calls.synthesis[1].finish(); });
  await readyToSave(view);
  await act(async () => { f.calls.synthesis[0].finish(); });
  expect(view.container.querySelector(".record-review").textContent).toContain("Another Castle Grounds entry");
  fireEvent.click(saveButton(view));
  await waitFor(() => expect(f.calls.save).toHaveLength(1));
  expect(f.calls.save[0].body.start_triggers).toEqual([clause(10), clause(40)]);
});

test("an obsolete backtest cannot enable Save while the current definition is still testing", async () => {
  const f = fixture({ holdBacktest: true }), view = render(h(SegmentTimeline, f.props));
  await pickPath(view);
  await waitFor(() => expect(f.calls.backtest).toHaveLength(1));
  addStart(view);
  clickRow(view, 40);
  await waitFor(() => expect(f.calls.backtest).toHaveLength(2));
  await act(async () => { f.calls.backtest[0].finish(report(9)); });
  expect(saveButton(view).disabled).toBe(true);
  await act(async () => { f.calls.backtest[1].finish(report(2)); });
  await readyToSave(view);
  expect(view.container.querySelector(".record-review").textContent).toContain("2 fires");
  fireEvent.click(saveButton(view));
  await waitFor(() => expect(f.calls.save).toHaveLength(1));
  expect(f.calls.save[0].body.start_triggers).toEqual([clause(10), clause(40)]);
});
