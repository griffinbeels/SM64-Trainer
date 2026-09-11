// @vitest-environment jsdom
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { act, cleanup, render } from "@testing-library/preact";
import { h } from "preact";
import { practiceSuccessSnapshot, usePracticeScroll } from "../../src/sm64_events/ui/practicescroll.js";
import { isCelebrating, useHeldWhileCelebrating, useRankClimb } from "../../src/sm64_events/ui/rankclimb.js";

beforeEach(() => {
  vi.stubGlobal("scrollTo", vi.fn());
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false })));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const attempt = (id, extra = {}) => ({ id, journal_id: id, outcome: "success",
  ended_utc: new Date(1700000000000 + id * 1000).toISOString(), ...extra });
const star = (id, attempts = []) => ({ course_id: 2, star_id: id, attempts });
const segment = (id, attempts = []) => ({ kind: "segment", segment_id: id, attempts });
const view = (stars, segments = [], extra = {}) => ({ session: { id: 1 },
  scope: "session", clock: "igt", stars, segments, ...extra });

function Probe({ value, activeKey = "star:2:1" }) {
  usePracticeScroll(practiceSuccessSnapshot(value), activeKey);
  return null;
}
function mount(value, activeKey) {
  const component = render(h(Probe, { value, activeKey }));
  return (next, key = activeKey) => component.rerender(h(Probe, { value: next, activeKey: key }));
}

test("selection and repeats keep position; the first success after moving on scrolls once", () => {
  const first = star(1, [attempt(1)]);
  const update = mount(view([first]));
  update(view([first, star(2)]), "star:2:2");
  update(view([first, star(2, [attempt(2, { outcome: "reset" })])]), "star:2:2");
  expect(scrollTo).not.toHaveBeenCalled();
  update(view([first, star(2, [attempt(3)])]), "star:2:2");
  expect(scrollTo).toHaveBeenCalledExactlyOnceWith({ top: 0, behavior: "smooth" });
  update(view([first, star(2, [attempt(3), attempt(4)])]), "star:2:2");
  update(view([first, star(2, [attempt(3), attempt(4)])]), "star:2:2");
  expect(scrollTo).toHaveBeenCalledTimes(1);
});

test("segment start IDs can be older than seen star IDs; completion time chooses the batch winner", () => {
  const first = star(1, [attempt(100)]);
  const update = mount(view([first]));
  const latest = attempt(2, { ended_utc: attempt(300).ended_utc });
  update(view([first, star(2, [attempt(200)])], [segment(6, [latest])]));
  expect(scrollTo).toHaveBeenCalledTimes(1);
  update(view([first], [segment(6, [latest, attempt(3, { ended_utc: attempt(400).ended_utc })])]));
  expect(scrollTo).toHaveBeenCalledTimes(1);
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: true })));
  update(view([first], [segment(5, [attempt(4, { ended_utc: attempt(500).ended_utc })])]));
  expect(scrollTo).toHaveBeenLastCalledWith({ top: 0, behavior: "instant" });
  expect(scrollTo).toHaveBeenCalledTimes(2);
});

test.each([
  { outcome: "reset" }, { cleared: true }, { imported: true }, { journal_id: null },
])("excluded entries stay seen if later restored or reclassified: %j", extra => {
  const first = star(1, [attempt(1)]);
  const update = mount(view([first]));
  update(view([first, star(2, [attempt(2, extra)])]));
  update(view([first, star(2, [attempt(2)])]));
  expect(scrollTo).not.toHaveBeenCalled();
  update(view([first, star(2, [attempt(2), attempt(3)])]));
  expect(scrollTo).toHaveBeenCalledTimes(1);
});

test("reassigning an unassigned row or restoring a removed row is not a new completion", () => {
  const first = star(1, [attempt(1)]);
  const update = mount(view([first], [], { unassigned: [attempt(2)] }));
  update(view([first, star(2, [attempt(2)])]));
  update(view([first]));
  update(view([first, star(2, [attempt(2)])]));
  expect(scrollTo).not.toHaveBeenCalled();
});

test.each([{ session: { id: 2 } }, { scope: "lifetime" }, { clock: "rta" }])(
  "loading another context establishes a baseline: %j", context => {
    const update = mount(view([star(1, [attempt(1)])]));
    update(null);
    const history = star(2, [attempt(2)]);
    update(view([history], [], context));
    update(view([star(2, [attempt(2), attempt(3)])], [], context));
    expect(scrollTo).not.toHaveBeenCalled();
    update(view([history, star(1, [attempt(4)])], [], context));
    expect(scrollTo).toHaveBeenCalledTimes(1);
  },
);

test("an empty session uses the initial active entity until the first completion", () => {
  const update = mount(view([]), "star:2:1");
  update(view([star(2, [attempt(1)])]), "star:2:2");
  expect(scrollTo).toHaveBeenCalledTimes(1);
});

test("a success arriving during a rank celebration waits for the held snapshot to release", () => {
  // Exercise the real hold and climb, stopping its animation clock until unmount.
  vi.stubGlobal("requestAnimationFrame", vi.fn(() => 1));
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
  function Climb({ division }) { useRankClimb({ tier: "Bronze", division, fill: 0.5 }); return null; }
  function HeldProbe({ value, division }) {
    const held = useHeldWhileCelebrating(practiceSuccessSnapshot(value));
    usePracticeScroll(held, "star:2:1");
    return division ? h(Climb, { division }) : null;
  }
  const initial = view([star(1, [attempt(1)])]);
  const component = render(h(HeldProbe, { value: initial, division: "II" }));
  component.rerender(h(HeldProbe, { value: initial, division: "I" }));
  expect(isCelebrating()).toBe(true);
  const changed = view([star(2, [attempt(2)])]);
  component.rerender(h(HeldProbe, { value: changed, division: "I" }));
  expect(scrollTo).not.toHaveBeenCalled();
  act(() => component.rerender(h(HeldProbe, { value: changed, division: null })));
  expect(isCelebrating()).toBe(false);
  expect(scrollTo).toHaveBeenCalledExactlyOnceWith({ top: 0, behavior: "smooth" });
});
