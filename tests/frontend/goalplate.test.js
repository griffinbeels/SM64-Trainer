// @vitest-environment jsdom
// The goal plate's celebration (ui/components/goalplate.js), which the stream
// overlay shares: a finished run celebrates only against the goal the plate
// was showing when that run ENDED.
//
// Round 13 (Griffin, 2026-09-28), his goal on Shining Atop the Pyramid read
// 06"96, yet a 07"33 played "GOAL! 07"33" over a struck-through 07"53: "we
// need to make sure that ONLY THE CURRENT ACTIVE GOAL is able to be triggered
// (old goals should no longer be active). Just what's displayed on screen for
// the user's current goal." The times below are his live rows (attempts 5353,
// 5386, 5482, 5528; session 28).
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { act, cleanup, render } from "@testing-library/preact";
import { h } from "preact";
import { GOAL_CELEBRATION_MS, GoalPlate } from "../../src/sm64_events/ui/components/goalplate.js";

beforeEach(() => { vi.useFakeTimers(); localStorage.clear(); });
afterEach(() => { cleanup(); vi.useRealTimers(); localStorage.clear(); });

const KINDS = [["star:8:1", "igt"], ["segment:14", "rta"]];

function goalOf(key, clock, cs, resolvedUtc, source) {
  return { key, clock, you_cs: null, goal_cs: cs, pinned: source.kind !== "automatic",
           count: 1, streak: false, progress: null, achieved: false, source,
           resolved_utc: resolvedUtc };
}
const AUTO = { kind: "automatic", tier: "Metal", division: "II", label: "Metal II" };
const typed = (label) => ({ kind: "time", label });

const run = (id, frames, endedUtc) => ({ id, outcome: "success", cleared: false,
  igt_frames: frames, rta_frames: frames, ended_utc: endedUtc });

function mount(key) {
  const view = render(plate(key, null, []));
  return {
    show: (goal, attempts) => view.rerender(plate(key, goal, attempts)),
    party: () => {
      const box = view.container.querySelector(".goal-plate-party");
      return box && { time: box.querySelector(".goal-plate-party-time").textContent,
                      beaten: box.querySelector("s").textContent };
    },
  };
}
function plate(key, goal, attempts) {
  return h(GoalPlate, { goalKey: key, goal, attempts, t: { marelo: null },
    pbTag: h("span", { class: "pbtag" }, "—"), onChanged: () => {} });
}
const finishParty = () => act(() => { vi.advanceTimersByTime(GOAL_CELEBRATION_MS + 50); });

test.each(KINDS)("%s: the PB that moves an automatic goal celebrates against the goal it beat, "
    + "and a run between the old goal and the new one does not", (key, clock) => {
  const plateView = mount(key);
  const before = [run(5353, 227, "2026-09-28T21:14:12.95Z")];
  plateView.show(goalOf(key, clock, 753, "2026-09-28T21:14:13.20Z", AUTO), before);
  // The 07"00 PB ends at 21:15:16.91. Its refetched goal (06"96, resolved
  // after the PB saved) lands BEFORE its row: the two fetches race.
  plateView.show(goalOf(key, clock, 696, "2026-09-28T21:15:17.05Z", AUTO), before);
  const withPb = [...before, run(5386, 210, "2026-09-28T21:15:16.910705Z")];
  plateView.show(goalOf(key, clock, 696, "2026-09-28T21:15:17.05Z", AUTO), withPb);
  expect(plateView.party()).toEqual({ time: `07"00 07"53`, beaten: `07"53` });
  finishParty();
  expect(plateView.party()).toBeNull();
  // 07"33: under the retired 07"53, over the 06"96 on screen. Nothing.
  const slower = [...withPb, run(5482, 220, "2026-09-28T21:18:18.48Z")];
  plateView.show(goalOf(key, clock, 696, "2026-09-28T21:18:18.70Z", AUTO), slower);
  expect(plateView.party()).toBeNull();
  // 06"96 meets the goal on screen.
  const met = [...slower, run(5528, 209, "2026-09-28T21:20:50.010127Z")];
  plateView.show(goalOf(key, clock, 696, "2026-09-28T21:20:50.20Z", AUTO), met);
  expect(plateView.party()).toEqual({ time: `06"96 06"96`, beaten: `06"96` });
});

test.each(KINDS)("%s: the row landing before its goal refetch is judged against the goal it beat",
    (key, clock) => {
  const plateView = mount(key);
  const before = [run(5353, 227, "2026-09-28T21:14:12.95Z")];
  plateView.show(goalOf(key, clock, 753, "2026-09-28T21:14:13.20Z", AUTO), before);
  const withPb = [...before, run(5386, 210, "2026-09-28T21:15:16.910705Z")];
  plateView.show(goalOf(key, clock, 753, "2026-09-28T21:14:13.20Z", AUTO), withPb);
  expect(plateView.party()).toEqual({ time: `07"00 07"53`, beaten: `07"53` });
  plateView.show(goalOf(key, clock, 696, "2026-09-28T21:15:17.05Z", AUTO), withPb);
  finishParty();
  expect(plateView.party()).toBeNull();
});

test.each(KINDS)("%s: a goal picked by hand retires the one it replaced", (key, clock) => {
  const plateView = mount(key);
  const before = [run(5353, 227, "2026-09-28T21:14:12.95Z")];
  plateView.show(goalOf(key, clock, 753, "2026-09-28T21:14:13.20Z", typed(`07"53`)), before);
  // He types 06"96 between runs; then runs 07"33.
  plateView.show(goalOf(key, clock, 696, "2026-09-28T21:16:00.00Z", typed(`06"96`)), before);
  const slower = [...before, run(5482, 220, "2026-09-28T21:18:18.48Z")];
  plateView.show(goalOf(key, clock, 696, "2026-09-28T21:16:00.00Z", typed(`06"96`)), slower);
  expect(plateView.party()).toBeNull();
});

test("a run that ended before the plate showed any goal is history, not news", () => {
  const plateView = mount("star:8:1");
  plateView.show(goalOf("star:8:1", "igt", 753, "2026-09-28T21:14:13.20Z", AUTO), []);
  plateView.show(goalOf("star:8:1", "igt", 753, "2026-09-28T21:14:13.20Z", AUTO),
    [run(4000, 200, "2026-09-27T20:00:00Z")]);
  expect(plateView.party()).toBeNull();
});
