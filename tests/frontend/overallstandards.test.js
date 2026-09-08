// @vitest-environment jsdom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/preact";
import { h } from "preact";
import { OverallStandards } from "../../src/sm64_events/ui/components/overallstandards.js";
import { SCORE_ANCHORS } from "../../src/sm64_events/ui/timecurve.js";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function payload(version) {
  const offset = version === "jp" ? 100 : 0;
  const nodes = [[600, 100], [700, 99], [900, 95], [1400, 90], [2000, 80],
    [2500, 70], [3000, 60], [4000, 45], [5000, 25], [6500, 10], [8500, 2]]
    .map(([time, score]) => [time + offset, score]);
  const ladder = Object.fromEntries(Object.entries(SCORE_ANCHORS)
    .map(([rank, score]) => [rank, nodes.find((node) => node[1] === score)[0]]));
  return { version, overall_curve: { schema_version: 1, interpolation: "pchip", nodes,
    ladder_cs: ladder, metadata: { population_count: 23, family_count: 4,
      families: [{ provisional: true }] } },
    // A stale display ladder must never become the compiled table's source.
    overall: { Mario: 99999 }, overall_overrides: {}, calibration_revision: "fixture" };
}

function fixture() {
  const states = { us: payload("us"), jp: payload("jp") };
  const requests = [];
  const controls = { failure: null };
  vi.stubGlobal("fetch", vi.fn(async (path, options = {}) => {
    const url = new URL(path, "http://localhost");
    const version = url.searchParams.get("version") || "us";
    const state = states[version];
    if (!options.method) return { ok: true, json: async () => structuredClone(state) };
    requests.push({ path, method: options.method, body: options.body && JSON.parse(options.body) });
    if (controls.failure) return { ok: false, status: 409, json: async () => ({ detail: controls.failure }) };
    if (options.method === "PUT") {
      const rank = decodeURIComponent(url.pathname.split("/").at(-1));
      const seconds = JSON.parse(options.body).seconds;
      state.overall_overrides[rank] = seconds;
      state.overall_curve.ladder_cs[rank] = Math.round(seconds * 100);
      state.overall_curve.nodes.find((node) => node[1] === SCORE_ANCHORS[rank])[0] = Math.round(seconds * 100);
    } else {
      states[version] = payload(version);
    }
    return { ok: true, json: async () => ({ ok: true, calibration_revision: "saved" }) };
  }));
  return { states, requests, controls };
}

async function open(version = "jp", pbCs = 2600) {
  const view = render(h(OverallStandards, { entity: "star:2:4", version, pbCs }));
  fireEvent.click(view.getByRole("button", { name: /Overall Rank Standards/ }));
  await view.findByRole("button", { name: "Edit Overall Mario cutoff" });
  return view;
}

test("the full curve renders 45 capped divisions with community basis and the actual personal marker", async () => {
  fixture();
  const view = await open();
  expect(view.container.querySelectorAll(".library-overall-division")).toHaveLength(45);
  expect(view.container.querySelectorAll(".library-overall-division .rank-icon-slot")).toHaveLength(45);
  expect(view.container.querySelector(".library-overall-band").dataset.tier).toBe("Iron");
  expect(view.container.querySelector(".library-overall-division.is-you").textContent).toContain("Luigi 5");
  expect(view.container.querySelector(".library-overall-division.is-next").textContent).toContain("Luigi 4");
  expect(view.container.textContent).toContain("23 eligible Sheet runners");
  expect(view.container.textContent).toContain("4 strategy families");
  expect(view.container.textContent).toContain("provisional family");
  expect(view.container.textContent).not.toContain("set by");
});

test("an Enter submission commits the last field and PUT/reset remain on the displayed version", async () => {
  const { requests } = fixture();
  const view = await open();
  fireEvent.click(view.getByRole("button", { name: "Edit Overall Mario cutoff" }));
  const centis = view.getByRole("spinbutton", { name: "Overall Mario cutoff centis" });
  centis.focus();
  fireEvent.input(centis, { target: { value: "3" } });
  expect(requests).toHaveLength(0); // Typing/blur never writes a partial cutoff.
  fireEvent.submit(centis.closest("form"));
  await view.findByRole("button", { name: "Reset Overall cutoffs" });
  expect(requests).toEqual([{ path: "/api/ranks/overall/star%3A2%3A4/Mario?version=jp",
    method: "PUT", body: { seconds: 10.03 } }]);
  expect(view.container.querySelector('[data-tier="Mario"] .library-overall-by').textContent).toContain("Fixed cutoff");
  fireEvent.click(view.getByRole("button", { name: "Reset Overall cutoffs" }));
  await waitFor(() => expect(view.queryByRole("button", { name: "Reset Overall cutoffs" })).toBeNull());
  expect(requests[1]).toEqual({ path: "/api/ranks/overall/star%3A2%3A4?version=jp", method: "DELETE" });
});

test("a rejected cutoff keeps its draft and shows the server explanation beside that editor", async () => {
  const { controls, requests } = fixture();
  controls.failure = "Leave enough game frames between the neighboring cutoffs.";
  const view = await open("us");
  fireEvent.click(view.getByRole("button", { name: "Edit Overall Mario cutoff" }));
  const seconds = view.getByRole("spinbutton", { name: "Overall Mario cutoff seconds" });
  fireEvent.input(seconds, { target: { value: "14" } });
  fireEvent.blur(seconds);
  fireEvent.click(view.getByRole("button", { name: "Save cutoff" }));
  const alert = await view.findByRole("alert");
  expect(alert.closest("form")).not.toBeNull();
  expect(alert.textContent).toBe(controls.failure);
  expect(view.getByRole("spinbutton", { name: "Overall Mario cutoff seconds" }).value).toBe("14");
  fireEvent.click(view.getByRole("button", { name: "Cancel" }));
  expect(view.queryByRole("spinbutton")).toBeNull();
  expect(requests).toHaveLength(1);
});

test("switching versions drops a dirty cutoff draft and ignores a late previous-version fetch", async () => {
  const states = { us: payload("us"), jp: payload("jp") };
  let finishUS;
  vi.stubGlobal("fetch", vi.fn((path) => {
    if (path.includes("version=us")) return new Promise((resolve) => { finishUS = resolve; });
    return Promise.resolve({ ok: true, json: async () => states.jp });
  }));
  const props = { entity: "star:2:4", version: "us" };
  const view = render(h(OverallStandards, props));
  fireEvent.click(view.getByRole("button", { name: /Overall Rank Standards/ }));
  await waitFor(() => expect(finishUS).toBeTypeOf("function"));
  view.rerender(h(OverallStandards, { ...props, version: "jp" }));
  await view.findByRole("button", { name: "Edit Overall Mario cutoff" });
  finishUS({ ok: true, json: async () => states.us });
  fireEvent.click(view.getByRole("button", { name: "Edit Overall Mario cutoff" }));
  const seconds = view.getByRole("spinbutton", { name: "Overall Mario cutoff seconds" });
  expect(seconds.value).toBe("10");
  fireEvent.input(seconds, { target: { value: "33" } });
  fireEvent.blur(seconds);
  view.rerender(h(OverallStandards, { ...props, entity: "segment:7", version: "jp" }));
  await view.findByRole("button", { name: "Edit Overall Mario cutoff" });
  expect(view.queryByRole("spinbutton")).toBeNull();
  fireEvent.click(view.getByRole("button", { name: "Edit Overall Mario cutoff" }));
  expect(view.getByRole("spinbutton", { name: "Overall Mario cutoff seconds" }).value).toBe("10");
});

test("legacy fallback and unsupported compiled data are explicitly visible", async () => {
  const { states } = fixture();
  states.jp.overall_curve.interpolation = "legacy";
  states.jp.overall_curve.nodes = [];
  states.jp.overall_curve.metadata.anchor_adjustments = {
    fallback_reason: "not enough frames", unreachable_divisions: [{ tier: "Mario", division: "II" }],
  };
  const view = await open();
  expect(view.container.textContent).toContain("Legacy standards");
  expect(view.container.textContent).toContain("1 division cannot be reached");
  states.jp.overall_curve.schema_version = 99;
  view.rerender(h(OverallStandards, { entity: "star:2:4", version: "jp", standardsRevision: 1 }));
  expect((await view.findByRole("alert")).textContent).toContain("could not be read");
  expect(view.container.querySelectorAll(".library-overall-division")).toHaveLength(0);
});
