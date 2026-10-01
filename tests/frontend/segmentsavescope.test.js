// @vitest-environment jsdom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/preact";
import { h } from "preact";
import { Builder } from "../../src/sm64_events/ui/components/segments.js";
import { SegmentTimeline } from "../../src/sm64_events/ui/components/segmenttimeline.js";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const trigger = { key: "level_enter", label: "Enter level", params: {}, template: "Enter level" };
const vocab = { triggers: [trigger], guards: [], origins: [],
  match_modes: [{ key: "loose", label: "Loose" }, { key: "strict", label: "Strict" }],
  clock_starts: [{ key: "move", label: "Move" }] };
const shipped = { id: 42, name: "LBLJ", enabled: true,
  start_triggers: [{ type: "level_enter" }], end_triggers: [{ type: "level_enter" }],
  guards: [], waypoints: [], parents: [], match_mode: "strict", clock_start: "move",
  seed_key: "lblj", seed_dirty: false };
const rows = [1, 2].map((id) => ({ id, label: id === 1 ? "Started in lobby" : "Reached stairs",
  frame: id * 30, place: "lobby", place_label: "Lobby", place_level: null }));

function fixture({ canShare = true, delayCapability = false, failSave = false,
    unconfirmedSave = false, failCapabilityOnce = false, saveMessage = null } = {}) {
  const writes = [], unexpected = [];
  const capability = { can_share: canShare, default_scope: canShare ? "shared" : "personal" };
  let finishCapability;
  let capabilityRequests = 0;
  const response = (body, ok = true) => Promise.resolve({ ok, status: ok ? 200 : 409,
    json: async () => body });
  const saveResponse = (path, method, options) => {
    writes.push({ path, method, body: JSON.parse(options.body) });
    if (failSave) return response({ detail: "Shared defaults could not be written" }, false);
    return response({ ok: true, id: 91, message: saveMessage,
      scope: unconfirmedSave ? "personal" : new URL(path, "http://fixture").searchParams.get("scope") });
  };
  vi.stubGlobal("fetch", vi.fn((url, options = {}) => {
    const method = options.method || "GET", path = String(url);
    if (path === "/api/segments/authoring") {
      capabilityRequests += 1;
      if (failCapabilityOnce && capabilityRequests === 1)
        return response({ detail: "Save options unavailable" }, false);
      return delayCapability
        ? new Promise((resolve) => { finishCapability = () => resolve({ ok: true, json: async () => capability }); })
        : response(capability);
    }
    if (path === "/api/library") return response({ groups: [] });
    if (path === "/api/library/adoptions") return response({ by_entity: {} });
    if (path.startsWith("/api/segments/timeline?")) return response({ rows });
    if (path.startsWith("/api/segments/synthesize?")) return response({ name: "LBLJ",
      start_clause: { type: "level_enter" }, end_clause: { type: "level_enter" },
      start_sentence: "Started in lobby", end_sentence: "Reached stairs", picked: [], steps: [] });
    if (path === "/api/segments/backtest") return response({ arms: 1, fires: 1, unclosed: [], attempts: [] });
    if (path === "/api/segments/lint") return response({ warnings: [] });
    if (path === "/api/uilog") return response({ ok: true });
    if (/^\/api\/segments(?:\/\d+)?\?scope=/.test(path)) return saveResponse(path, method, options);
    unexpected.push(`${method} ${path}`);
    return response({ detail: `Unexpected fixture request: ${method} ${path}` }, false);
  }));
  return { writes, unexpected, t: { view: { catalog: { courses: [] } }, feed: [],
    refresh: vi.fn(), setNotice: vi.fn() },
    finishCapability: () => finishCapability(), onSaved: vi.fn(async () => {}) };
}

function editor(f, initial = shipped, extra = {}) {
  return render(h(Builder, { vocab, initial, t: f.t, allDefs: [],
    load: vi.fn(async () => [shipped]), onSaved: f.onSaved, onCancel: vi.fn(), ...extra }));
}

async function pickRecording(view) {
  fireEvent.click(await view.findByRole("button", { name: /Started in lobby/ }));
  fireEvent.click(await view.findByRole("button", { name: /Reached stairs/ }));
}

test("the editor waits for capability and saves the full edit for everyone", async () => {
  const f = fixture({ delayCapability: true }), view = editor(f);
  await view.findByText("Checking save options…");
  expect(view.queryByRole("button", { name: "Save segment" })).toBeNull();
  expect(view.queryByRole("button", { name: "Save for everyone" })).toBeNull();
  f.finishCapability();
  const button = await view.findByRole("button", { name: "Save for everyone" });
  expect(button.className).toContain("primary-button");
  fireEvent.input(view.getByPlaceholderText("e.g. Lobby to BitDW"), { target: { value: "Corrected LBLJ" } });
  fireEvent.click(button);
  await waitFor(() => expect(f.onSaved).toHaveBeenCalledWith(42));
  expect(f.writes).toHaveLength(1);
  expect(f.writes[0]).toMatchObject({ method: "PUT", path: "/api/segments/42?scope=shared",
    body: { name: "Corrected LBLJ", waypoints: [], match_mode: "strict", clock_start: "move" } });
  expect(f.writes[0].body).not.toHaveProperty("seed_key");
  expect(f.t.setNotice).toHaveBeenCalledWith("Saved for the next release");
  expect(f.unexpected).toEqual([]);
});

test("a maintainer can save a personal copy and its warning describes personal update protection", async () => {
  const f = fixture(), view = editor(f);
  fireEvent.click(await view.findByRole("button", { name: "Save only for me" }));
  await waitFor(() => expect(f.writes).toHaveLength(1));
  expect(f.writes[0].path).toBe("/api/segments/42?scope=personal");
  await view.findByText(/This is your personal copy/);
  expect(f.t.setNotice).toHaveBeenCalledWith("Saved only for me");
});

test("a committed shared save preserves the server's refresh warning in the visible receipt", async () => {
  const message = "Saved for the next release. Refresh failed; reload the page or restart the test server.";
  const f = fixture({ saveMessage: message }), view = editor(f);
  fireEvent.click(await view.findByRole("button", { name: "Save for everyone" }));
  await view.findByText(message);
  expect(f.t.setNotice).toHaveBeenCalledWith(message);
  expect(f.onSaved).toHaveBeenCalledWith(42);
});

test("released editor offers only personal Save segment", async () => {
  const f = fixture({ canShare: false }), view = editor(f);
  fireEvent.click(await view.findByRole("button", { name: "Save segment" }));
  await waitFor(() => expect(f.writes).toHaveLength(1));
  expect(f.writes[0].path).toBe("/api/segments/42?scope=personal");
  expect(view.queryByRole("button", { name: "Save for everyone" })).toBeNull();
  expect(view.queryByRole("button", { name: "Save only for me" })).toBeNull();
});

test("failed capability has an actionable retry and cannot silently select a personal scope", async () => {
  const f = fixture({ failCapabilityOnce: true }), view = editor(f);
  await view.findByText(/Save options unavailable/);
  expect(view.queryByRole("button", { name: "Save segment" })).toBeNull();
  expect(f.writes).toEqual([]);
  fireEvent.click(view.getByRole("button", { name: "Retry save options" }));
  expect(await view.findByRole("button", { name: "Save for everyone" })).toBeTruthy();
});

test.each([{ failSave: true }, { unconfirmedSave: true }])("failed or unconfirmed shared editor save gives no success receipt: %j", async (mode) => {
  const f = fixture(mode), view = editor(f);
  fireEvent.click(await view.findByRole("button", { name: "Save for everyone" }));
  await view.findByText(mode.failSave ? /Shared defaults could not be written/ : /did not confirm/);
  expect(f.onSaved).not.toHaveBeenCalled();
  expect(f.t.setNotice).not.toHaveBeenCalled();
  expect(view.queryByText("Saved for the next release")).toBeNull();
});

test("editor autosave uses the primary scope and retries a created id after a finishing failure", async () => {
  const f = fixture(), apiRef = { current: null };
  f.onSaved.mockRejectedValueOnce(new Error("Refresh failed"));
  const view = editor(f, null, { apiRef });
  await view.findByRole("button", { name: "Save for everyone" });
  expect(await apiRef.current.save()).toBe(false);
  await view.findByText(/Refresh failed/);
  expect(await apiRef.current.save()).toBe(true);
  expect(f.writes.map((write) => [write.method, write.path])).toEqual([
    ["POST", "/api/segments?scope=shared"], ["PUT", "/api/segments/91?scope=shared"]]);
});

test.each([null, shipped, { prefill: { name: "Library movement", parents: [] } }])("record/create, re-record and Library record share the same primary save scope: %j", async (intent) => {
  const f = fixture(), props = intent?.prefill ? intent : { replaces: intent };
  const view = render(h(SegmentTimeline, { t: f.t, onSaved: f.onSaved, onCancel: vi.fn(), ...props }));
  expect(view.queryByRole("button", { name: "Save for everyone" })).toBeNull();
  await pickRecording(view);
  const button = await view.findByRole("button", { name: "Save for everyone" });
  await waitFor(() => expect(button.disabled).toBe(false));
  fireEvent.click(button);
  await waitFor(() => expect(f.onSaved).toHaveBeenCalled());
  expect(f.writes[0]).toMatchObject({ method: intent === shipped ? "PUT" : "POST",
    path: intent === shipped ? "/api/segments/42?scope=shared" : "/api/segments?scope=shared",
    body: { match_mode: "strict", clock_start: "move", parents: [] } });
  expect(f.t.setNotice).toHaveBeenCalledWith("Saved for the next release");
  expect(f.unexpected).toEqual([]);
});

test("recorder supports personal saves and preserves the created id on a callback retry", async () => {
  const f = fixture();
  f.onSaved.mockRejectedValueOnce(new Error("Library link failed"));
  const view = render(h(SegmentTimeline, { t: f.t, onSaved: f.onSaved, onCancel: vi.fn() }));
  await pickRecording(view);
  const button = await view.findByRole("button", { name: "Save only for me" });
  await waitFor(() => expect(button.disabled).toBe(false));
  fireEvent.click(button);
  await view.findByText(/Library link failed/);
  expect(f.t.setNotice).not.toHaveBeenCalled();
  fireEvent.click(view.getByRole("button", { name: "Save only for me" }));
  await waitFor(() => expect(f.onSaved).toHaveBeenCalledTimes(2));
  expect(f.writes.map((write) => [write.method, write.path])).toEqual([
    ["POST", "/api/segments?scope=personal"], ["PUT", "/api/segments/91?scope=personal"]]);
});

test("released recorder saves personally", async () => {
  const f = fixture({ canShare: false });
  const view = render(h(SegmentTimeline, { t: f.t, onSaved: f.onSaved, onCancel: vi.fn() }));
  await pickRecording(view);
  const button = await view.findByRole("button", { name: "Save segment" });
  await waitFor(() => expect(button.disabled).toBe(false));
  fireEvent.click(button);
  await waitFor(() => expect(f.writes).toHaveLength(1));
  expect(f.writes[0].path).toBe("/api/segments?scope=personal");
  expect(view.queryByRole("button", { name: "Save for everyone" })).toBeNull();
});

test("a rejected recorder shared save leaves the recording available and reports no success", async () => {
  const f = fixture({ failSave: true });
  const view = render(h(SegmentTimeline, { t: f.t, onSaved: f.onSaved, onCancel: vi.fn() }));
  await pickRecording(view);
  const button = await view.findByRole("button", { name: "Save for everyone" });
  await waitFor(() => expect(button.disabled).toBe(false));
  fireEvent.click(button);
  await view.findByText(/Shared defaults could not be written/);
  expect(f.onSaved).not.toHaveBeenCalled();
  expect(f.t.setNotice).not.toHaveBeenCalled();
  expect(view.queryByText("Saved for the next release")).toBeNull();
  expect(view.getByRole("button", { name: "Save for everyone" }).disabled).toBe(false);
});
