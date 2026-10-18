// @vitest-environment jsdom
import {h} from "preact";
import {act} from "preact/test-utils";
import {render, cleanup, waitFor} from "@testing-library/preact";
import {afterEach, beforeEach, expect, test, vi} from "vitest";

vi.mock("../../src/sm64_events/ui/api.js", () => ({getJSON: vi.fn(), send: vi.fn()}));
const api = await import("../../src/sm64_events/ui/api.js");
const {useUpdates} = await import("../../src/sm64_events/ui/updates.js");
const {UpdatePopup} = await import("../../src/sm64_events/ui/components/update.js");
let updates;
function Probe() {
  updates = useUpdates();
  return h("div", null, h("output", null, String(updates.updateVisible)), h(UpdatePopup, {t:updates}));
}
const offer = extra => ({update_available: true, current: "1.10.0-beta.16", latest: "1.10.0-beta.17", ...extra});
beforeEach(() => { api.getJSON.mockReset(); api.send.mockReset(); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

test("startup checks stay silent, an actual offer opens, Later dismisses and manual check reopens", async () => {
  let answer;
  api.getJSON.mockReturnValue(new Promise(resolve => { answer = resolve; }));
  render(h(Probe));
  expect(updates.updateVisible).toBe(false);
  expect(document.querySelector(".modal")).toBeNull();
  answer(offer());
  await waitFor(() => expect(updates.updateVisible).toBe(true));
  act(() => updates.dismissUpdate());
  expect(updates.updateVisible).toBe(false);
  api.getJSON.mockResolvedValue(offer());
  await act(async () => { await updates.checkUpdates(); });
  expect(updates.updateVisible).toBe(true);
});

test.each(["offline", "current", "skipped"])("%s never traps onboarding", async state => {
  if (state === "offline") api.getJSON.mockRejectedValue(new Error("offline"));
  else api.getJSON.mockResolvedValue(state === "current" ? {update_available: false} : offer({skipped: "1.10.0-beta.17"}));
  render(h(Probe));
  await act(async () => {});
  expect(updates.updateVisible).toBe(false);
  expect(document.querySelector(".modal")).toBeNull();
});

test("stalled checking never opens a modal and a late actual offer remains usable", async () => {
  vi.useFakeTimers();
  let answer;
  api.getJSON.mockReturnValue(new Promise(resolve => { answer = resolve; }));
  render(h(Probe));
  expect(document.querySelector(".modal")).toBeNull();
  await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
  expect(document.querySelector(".modal")).toBeNull();
  await act(async () => { answer(offer()); });
  expect(updates.updateVisible).toBe(true);
});

test("skipping releases onboarding and applying holds the update surface", async () => {
  api.getJSON.mockResolvedValue(offer());
  api.send.mockResolvedValue({});
  render(h(Probe));
  await waitFor(() => expect(updates.updateVisible).toBe(true));
  await act(async () => { await updates.skipUpdate("1.10.0-beta.17"); });
  expect(updates.updateVisible).toBe(false);
  act(() => updates.setUpdateApplying(true));
  expect(updates.updateVisible).toBe(true);
});

test("Skip dismisses immediately even while persistence is stalled", async () => {
  api.getJSON.mockResolvedValue(offer());
  let saved;
  api.send.mockReturnValue(new Promise(resolve => { saved = resolve; }));
  render(h(Probe));
  await waitFor(() => expect(updates.updateVisible).toBe(true));
  act(() => { void updates.skipUpdate("1.10.0-beta.17"); });
  expect(updates.updateVisible).toBe(false);
  await act(async () => { saved({}); });
});
