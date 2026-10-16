// @vitest-environment jsdom
// Real hook/control, with only the session API and unrelated library UI replaced.
import { h } from "preact";
import { render, cleanup, fireEvent, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

vi.mock("../../src/sm64_events/ui/api.js", () => ({ getJSON: vi.fn(), send: vi.fn() }));
vi.mock("../../src/sm64_events/ui/components/ghostlibrary.js", () => ({ GhostLibraryButton: () => null }));
const api = await import("../../src/sm64_events/ui/api.js");
const { GhostSwitch, GhostSettings, useGhostSession } = await import("../../src/sm64_events/ui/components/ghostswitch.js");

const session = (target = {kind: "star", entity_key: "5-4", strat: "Standard"}) => ({
  enabled: false, target, source: {available: true}, window: {}, limits: {}, nudge_frames: 0,
});
const tracker = extra => ({ feed: [], connected: false, setNotice: vi.fn(), ...extra });
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
};
let observed;
function Probe({t, active = true}) {
  observed = useGhostSession(t, active);
  return h("output", {"data-testid": "availability"}, String(observed.reachable));
}
beforeEach(() => { vi.clearAllMocks(); api.getJSON.mockReset(); api.send.mockReset(); });
afterEach(cleanup);

test.each([
  ["star", "star:5:4", {kind: "star", entity_key: "5-4", strat: "Standard"}],
  ["segment", "segment:130", {kind: "segment", entity_key: "130", strat: "Standard"}],
])("a failed %s read leaves an explanatory control and Retry restores it", async (_kind, key, target) => {
  api.getJSON.mockRejectedValueOnce(new Error("warming up")).mockResolvedValue(session(target));
  const view = render(h(GhostSwitch, {t: tracker(), entityKey: key, active: true}));
  const retry = await view.findByRole("button", {name: "Retry"});
  expect(view.container.querySelectorAll(".ghost-row")).toHaveLength(1);
  expect(view.container.querySelector("[role=switch]").disabled).toBe(true);
  expect(view.container.textContent).toContain("couldn't load");
  fireEvent.click(retry);
  await waitFor(() => expect(view.container.querySelector("[role=switch]").disabled).toBe(false));
  expect(view.queryByRole("button", {name: "Retry"})).toBeNull();
  expect(api.send).not.toHaveBeenCalled();
});

test("a valid broadcast revives a card after its initial read failed", async () => {
  api.getJSON.mockRejectedValue(new Error("initial failure"));
  const t = tracker();
  const view = render(h(Probe, {t}));
  await waitFor(() => expect(observed.reachable).toBe(false));
  view.rerender(h(Probe, {t: {...t, feed: [{type: "ghost_changed", payload: session()}]}}));
  await waitFor(() => expect(observed.reachable).toBe(true));
  expect(observed.session.target.entity_key).toBe("5-4");
});

test.each(["failure", "success"])("late GET %s cannot replace a newer successful broadcast", async (reply) => {
  const pending = deferred();
  api.getJSON.mockReturnValue(pending.promise);
  const t = tracker();
  const view = render(h(Probe, {t}));
  await waitFor(() => expect(api.getJSON).toHaveBeenCalledTimes(1));
  const latest = session();
  view.rerender(h(Probe, {t: {...t, feed: [{type: "ghost_changed", payload: latest}]}}));
  await waitFor(() => expect(observed.session).toBe(latest));
  if (reply === "failure") pending.reject(new Error("obsolete request"));
  else pending.resolve(session({kind: "star", entity_key: "2-2", strat: "Standard"}));
  await new Promise(resolve => { setTimeout(resolve, 20); });
  expect(observed.reachable).toBe(true);
  expect(observed.session).toBe(latest);
});

test("an initial old feed does not suppress the authoritative successful GET", async () => {
  const fresh = session({kind: "star", entity_key: "2-2", strat: "Standard"});
  api.getJSON.mockResolvedValue(fresh);
  render(h(Probe, {t: tracker({feed: [{type: "ghost_changed", payload: session()}]})}));
  await waitFor(() => expect(observed.session).toBe(fresh));
});

test("reconnecting retries only the active card and recovers without reload", async () => {
  api.getJSON.mockRejectedValueOnce(new Error("first read")).mockResolvedValue(session());
  const t = tracker();
  const view = render(h(Probe, {t}));
  await waitFor(() => expect(observed.reachable).toBe(false));
  view.rerender(h(Probe, {t: {...t, connected: true}}));
  await waitFor(() => expect(observed.reachable).toBe(true));
  expect(api.getJSON).toHaveBeenCalledTimes(2);
  view.rerender(h(Probe, {t: {...t, connected: false}, active: false}));
  view.rerender(h(Probe, {t: {...t, connected: true}, active: false}));
  expect(api.getJSON).toHaveBeenCalledTimes(2);
});

test("returning to a failed card restores it after a successful read", async () => {
  api.getJSON.mockRejectedValueOnce(new Error("first read")).mockResolvedValue(session());
  const t = tracker();
  const view = render(h(Probe, {t}));
  await waitFor(() => expect(observed.reachable).toBe(false));
  view.rerender(h(Probe, {t, active: false}));
  view.rerender(h(Probe, {t, active: true}));
  await waitFor(() => expect(observed.reachable).toBe(true));
});

test("a successful write supersedes an older failed read", async () => {
  const pending = deferred();
  api.getJSON.mockReturnValue(pending.promise);
  api.send.mockResolvedValue(session());
  render(h(Probe, {t: tracker()}));
  await waitFor(() => expect(api.getJSON).toHaveBeenCalledTimes(1));
  await observed.write({enabled: false});
  pending.reject(new Error("obsolete request"));
  await new Promise(resolve => { setTimeout(resolve, 20); });
  expect(observed.reachable).toBe(true);
});

test("settings recover through Retry instead of asking the user to close them", async () => {
  api.getJSON.mockRejectedValueOnce(new Error("startup")).mockResolvedValue(session());
  const view = render(h(GhostSettings, {t: tracker(), onClose: vi.fn()}));
  fireEvent.click(await view.findByRole("button", {name: "Retry"}));
  await waitFor(() => expect(view.getByRole("button", {name: "Done"}).disabled).toBe(false));
  expect(api.send).not.toHaveBeenCalled();
});

test("inactive cards make no ghost request or target write", () => {
  const t = tracker({connected: true});
  const view = render(h(GhostSwitch, {t, entityKey: "star:5:4", active: false}));
  expect(view.container.querySelector(".ghost-row")).toBeNull();
  expect(api.getJSON).not.toHaveBeenCalled();
  expect(api.send).not.toHaveBeenCalled();
});
