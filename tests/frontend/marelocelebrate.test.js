// @vitest-environment jsdom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/preact";
import { h } from "preact";
import { MareloCelebration, RankUpCelebration } from "../../src/sm64_events/ui/components/marelocelebrate.js";
import { setCelebrationsEnabled } from "../../src/sm64_events/ui/celebrations.js";

afterEach(() => {
  cleanup(); vi.unstubAllGlobals(); localStorage.clear(); document.body.innerHTML = "";
});

function fixture() {
  const requests = [];
  vi.stubGlobal("fetch", vi.fn(async (url, options) => {
    requests.push({ url, body: JSON.parse(options.body) });
    return { ok: true, json: async () => ({ ok: true }) };
  }));
  vi.stubGlobal("matchMedia", () => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const celebration = { from: { tier: "Diamond", division: "II" },
    to: { tier: "Master", division: "I" }, key: 34, calibration_revision: "earned-before-refresh" };
  return { requests, props: { celebration, scopeId: "overall", routes: [], onDone: vi.fn(),
    marelo: { scope_id: "overall", tier: "Diamond", division: "II", marelo: 76,
      division_progress: .5, calibration_revision: "new-community-revision", celebration } } };
}

function expectedAck() {
  return { url: "/api/marelo/ack", body: { scope: "overall", key: 34,
    calibration_revision: "earned-before-refresh" } };
}

test("auto-dismiss acknowledges the celebration revision rather than the current rating", async () => {
  const { requests, props } = fixture();
  setCelebrationsEnabled(false);
  render(h(RankUpCelebration, props));
  await waitFor(() => expect(props.onDone).toHaveBeenCalled());
  expect(requests.length).toBeGreaterThan(0);
  expect(requests.every((request) => JSON.stringify(request) === JSON.stringify(expectedAck()))).toBe(true);
});

test("returning the visible celebration sends that same original revision", async () => {
  const { requests, props } = fixture();
  const slot = document.createElement("div");
  slot.className = "marelo-slot";
  document.body.append(slot);
  const view = render(h(MareloCelebration, props));
  const card = view.container.querySelector(".marelo-celebrate-card");
  expect(card).not.toBeNull();
  fireEvent.click(card);
  await waitFor(() => expect(props.onDone).toHaveBeenCalled(), { timeout: 2500 });
  expect(requests).toEqual([expectedAck()]);
});
