// @vitest-environment jsdom
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/preact";
import { h } from "preact";
import { PracticeLogSettings } from "../../src/sm64_events/ui/components/practicelog.js";
import { usePracticeLogPreferences } from "../../src/sm64_events/ui/components/practice.js";

function Settings() {
  const ui = usePracticeLogPreferences(localStorage.getItem("sm64.sort") || "newest");
  return h(PracticeLogSettings, { ui, t: {} });
}

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false })));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

test("defaults, remembered preferences, and close/reopen preserve independent switches", async () => {
  localStorage.setItem("sm64.hideResets", "1");
  const screen = render(h(Settings));
  const gear = screen.getByRole("button", { name: "Practice log settings", exact: true });
  fireEvent.click(gear);
  const resets = screen.getByRole("switch", { name: "Hide resets and deaths" });
  const saved = screen.getByRole("switch", { name: "Hide savestated entries" });
  expect(resets.checked).toBe(true);
  expect(saved.checked).toBe(true);
  await waitFor(() => expect(document.activeElement).toBe(resets));
  fireEvent.click(saved);
  expect(saved.checked).toBe(false);
  expect(resets.checked).toBe(true);
  expect(localStorage.getItem("sm64.hideSavestates")).toBe("0");
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "oldest" } });
  expect(localStorage.getItem("sm64.sort")).toBe("oldest");
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(gear);
  screen.unmount();
  const remounted = render(h(Settings));
  fireEvent.click(remounted.getByRole("button", { name: "Practice log settings", exact: true }));
  expect(remounted.getByRole("switch", { name: "Hide resets and deaths" }).checked).toBe(true);
  expect(remounted.getByRole("switch", { name: "Hide savestated entries" }).checked).toBe(false);
  expect(remounted.getByRole("combobox").value).toBe("oldest");
  fireEvent.pointerDown(document.body);
  await waitFor(() => expect(remounted.queryByRole("dialog")).toBeNull());
});

// His words (2026-11-09): "By default "Hide resets & deaths" should be enabled (if it's not already)".
// A fresh profile hides them; his own stored "0" keeps them shown; the switch still flips and remembers.
test("Hide resets and deaths starts on, and only his own choice turns it off", () => {
  const resetsSwitch = (screen) => {
    fireEvent.click(screen.getByRole("button", { name: "Practice log settings", exact: true }));
    return screen.getByRole("switch", { name: "Hide resets and deaths" });
  };
  const fresh = render(h(Settings));
  const resets = resetsSwitch(fresh);
  expect(resets.checked).toBe(true);
  expect(localStorage.getItem("sm64.hideResets")).toBeNull();          // a default, not a write
  fireEvent.click(resets);
  expect(resets.checked).toBe(false);
  expect(localStorage.getItem("sm64.hideResets")).toBe("0");
  fresh.unmount();
  const chosen = render(h(Settings));
  expect(resetsSwitch(chosen).checked).toBe(false);                      // his "0" stays honored
  chosen.unmount();
  localStorage.setItem("sm64.hideResets", "1");
  const on = render(h(Settings));
  expect(resetsSwitch(on).checked).toBe(true);
});
