// @vitest-environment jsdom
import { afterEach, expect, test, vi } from "vitest";
import { watchReplayFocus } from "../../src/sm64_events/ui/replayfocus.js";

const stops = [];
afterEach(() => {
  stops.splice(0).forEach(stop => stop());
  document.body.replaceChildren(); vi.restoreAllMocks();
});
function opening() {
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  const root = document.createElement("div"); root.className = "attempt-drawer";
  root.innerHTML = '<div class="attempt-drawer-inputs"></div><video tabindex="0"></video>';
  document.body.append(root);
  stops.push(watchReplayFocus(root));
  return root;
}
function ready(root) {
  const lanes = document.createElement("div"); lanes.className = "input-lanes"; lanes.tabIndex = 0;
  root.querySelector(".attempt-drawer-inputs").append(lanes);
  const video = root.querySelector("video"); Object.defineProperty(video, "readyState", {value:2});
  video.dispatchEvent(new Event("loadeddata"));
  return lanes;
}
test("the latest opening wins even when the older request finishes last", () => {
  const first = opening(), second = opening();
  const expected = ready(second);
  ready(first);
  expect(document.activeElement).toBe(expected);
});
test.each(["blur", "keydown", "visibilitychange"])("%s cancels delayed focus permanently", event => {
  const root = opening();
  (event === "visibilitychange" ? document : window).dispatchEvent(new Event(event));
  const lanes = ready(root);
  expect(document.activeElement).not.toBe(lanes);
});
test("an explicit download click inside the loading replay retains its opening intent", () => {
  const root = opening(), button = document.createElement("button"); root.append(button);
  button.dispatchEvent(new Event("pointerdown", {bubbles:true})); button.focus();
  expect(document.activeElement).toBe(button);
  const lanes = ready(root);
  expect(document.activeElement).toBe(lanes);
});

test.each(["is-empty", "is-error"])("playable video owns focus when inputs are %s", state => {
  const root = opening(), video = root.querySelector("video");
  root.querySelector(".attempt-drawer-inputs").innerHTML = `<div class="input-timeline ${state}">No input lanes</div>`;
  Object.defineProperty(video, "readyState", {value:2});
  video.dispatchEvent(new Event("loadeddata"));
  expect(document.activeElement).toBe(video);
});
