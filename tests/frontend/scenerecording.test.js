// @vitest-environment jsdom
// The Scene viewer's recording sign and switch (ui/components/scenerecording.js) against a scripted lab:
// it says whether the lab records his play now, one press stops or starts it through the lab's switch,
// and a server that cannot switch shows nothing.
import { h } from "preact";
import { render, cleanup, fireEvent, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { RECORDING_WORDS, RecordingSwitch, recordingView } from "../../src/sm64_events/ui/components/scenerecording.js";

const live = (extra = {}) => ({ recording: true, on: true, switch: true, reason: null, ...extra });
let probe, asked;

beforeEach(() => {
  asked = [];
  probe = live();
  vi.stubGlobal("fetch", vi.fn(async (url, options = {}) => {
    if (url === "/api/local-access") return new Response("{}", { status: 404 });
    if (options.method === "POST") {
      const body = JSON.parse(options.body);
      asked.push([url, body]);
      probe = live({ on: body.on, recording: false, reason: body.on ? "No scene recording is running." : "off" });
      return new Response(JSON.stringify({ live: probe }), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    return new Response(JSON.stringify({ lab: "scene-viewer", live: probe }),
      { status: 200, headers: { "Content-Type": "application/json" } });
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

test("each answer has its words and its one action", () => {
  expect(recordingView(live())).toMatchObject({ state: "running", words: RECORDING_WORDS.running, action: "Stop recording" });
  expect(recordingView(live({ recording: false, reason: "Project64 closed the game." }))).toMatchObject(
    { state: "waiting", words: RECORDING_WORDS.waiting, detail: "Project64 closed the game.", action: "Stop recording" });
  expect(recordingView(live({ on: false, recording: false }))).toMatchObject(
    { state: "off", words: RECORDING_WORDS.off, action: "Start recording" });
  expect(recordingView(live({ switch: false }))).toBe(null);
  expect(recordingView(null)).toBe(null);
});

test("the sign says it records, and one press stops it and then starts it again", async () => {
  const { container, getByRole } = render(h(RecordingSwitch, {}));
  await waitFor(() => expect(container.textContent).toContain(RECORDING_WORDS.running));
  expect(container.querySelector(".scene-recording.is-running")).not.toBe(null);
  fireEvent.click(getByRole("button", { name: "Stop recording" }));
  await waitFor(() => expect(container.textContent).toContain(RECORDING_WORDS.off));
  expect(asked).toEqual([["/api/scene-lab/recording", { on: false }]]);
  fireEvent.click(getByRole("button", { name: "Start recording" }));
  await waitFor(() => expect(container.textContent).toContain(RECORDING_WORDS.waiting));
  expect(asked.at(-1)).toEqual(["/api/scene-lab/recording", { on: true }]);
});

test("a server that cannot switch the recording shows no row", async () => {
  probe = live({ switch: false, recording: false, on: false, reason: "This server does not run the live scene recording." });
  const { container } = render(h(RecordingSwitch, {}));
  await new Promise((resolve) => { setTimeout(resolve, 50); });
  expect(container.querySelector(".scene-recording")).toBe(null);
});
