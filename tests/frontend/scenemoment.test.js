// @vitest-environment jsdom
// The Scene viewer's Save states row (ui/components/scenemoment.js) against a scripted lab: one press makes the
// frame on screen and writes it to that slot, each step's refusal shows as the lab said it, and the row says why it
// cannot run before anyone presses.
import { h } from "preact";
import { render, cleanup, fireEvent, waitFor, within } from "@testing-library/preact";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { HINT, OLDER, SaveStates, saveMoment, whyNot } from "../../src/sm64_events/ui/components/scenemoment.js";

const scene = (extra = {}) => ({ session: 1, attempt: 12, name: "take.sm64scene", growing: false,
  frames: [{ package_frame: 0 }, { package_frame: 2 }, { package_frame: 3 }], ...extra });
const link = (extra = {}) => ({ scene: scene(), frame: 1, ...extra });
let asked, answers;

beforeEach(() => {
  asked = [];
  answers = [{ id: "abc", exact: true }, { written: true, slot: 3 }];
  vi.stubGlobal("fetch", vi.fn(async (url, options = {}) => {
    if (url === "/api/local-access") return new Response("{}", { status: 404 });
    asked.push([url, JSON.parse(options.body)]);
    const answer = answers.shift();
    return new Response(JSON.stringify(answer), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

test("the package's own frame goes to the lab, not the frame's place in the list", async () => {
  const said = await saveMoment(scene(), scene().frames[1], 2, 3);
  expect(asked).toEqual([["/api/scene-lab/moment", { attempt: 12, frame: 2 }],
    ["/api/scene-lab/moment/abc/write", { slot: 3 }]]);
  expect(said).toEqual({ bad: false, text: "Frame 2 is in slot 3." });
});

test("a listed package is named by its name", async () => {
  await saveMoment(scene({ attempt: undefined }), scene().frames[2], 3, 1);
  expect(asked[0][1]).toEqual({ package: "take.sm64scene", frame: 3 });
});

test("a build's refusal stops before the write", async () => {
  answers = [{ refused: "never-reached", reason: "Usamune cannot save on this frame." }];
  expect(await saveMoment(scene(), scene().frames[1], 2, 3)).toEqual({ bad: true, text: "Usamune cannot save on this frame." });
  expect(asked).toHaveLength(1);
});

test("the live check's refusal shows as the lab said it", async () => {
  answers = [{ id: "abc", exact: true }, { refused: "mario", reason: "Leave the level, enter it again, then press again." }];
  expect((await saveMoment(scene(), scene().frames[1], 2, 3)).text).toBe("Leave the level, enter it again, then press again.");
});

test("an older recording says what may not be exact", async () => {
  answers = [{ id: "abc", exact: false }, { written: true, slot: 3 }];
  expect((await saveMoment(scene(), scene().frames[1], 2, 3)).text).toBe(`Frame 2 is in slot 3. ${OLDER}`);
});

test("the row says why it cannot run, and every number waits", () => {
  expect(whyNot(link())).toBe(null);
  expect(whyNot(link({ frame: 0 }))).toBe("This is the first frame. Pick a later frame.");
  expect(whyNot(link({ scene: scene({ growing: true }) }))).toMatch(/still being recorded/);
  const { container } = render(h(SaveStates, { link: link({ frame: 0 }) }));
  expect(container.textContent).toContain("This is the first frame.");
  const buttons = [...container.querySelectorAll("button")];
  expect(buttons).toHaveLength(9);
  expect(buttons.every((button) => button.disabled)).toBe(true);
});

test("one press: nine numbers, the message, and the frame on screen when pressed", async () => {
  const { container } = render(h(SaveStates, { link: link() }));
  expect(within(container).getByRole("status").textContent).toBe(HINT);
  fireEvent.click(within(container).getByRole("button", { name: "Put this frame in slot 3" }));
  expect(within(container).getByRole("status").textContent).toBe("Putting frame 2 in slot 3...");
  expect([...container.querySelectorAll("button")].every((button) => button.disabled)).toBe(true);
  await waitFor(() => expect(within(container).getByRole("status").textContent).toBe("Frame 2 is in slot 3."));
  expect([...container.querySelectorAll("button")].some((button) => button.disabled)).toBe(false);
});

test("an answer leaves when the frame does, and stays through Play", async () => {
  answers = [{ refused: "x", reason: "Pick another frame." }];
  const { container, rerender } = render(h(SaveStates, { link: link() }));
  fireEvent.click(within(container).getByRole("button", { name: "Put this frame in slot 1" }));
  await waitFor(() => expect(container.querySelector(".is-bad")?.textContent).toBe("Pick another frame."));
  rerender(h(SaveStates, { link: link({ frame: 2, playing: true }) }));
  expect(container.querySelector(".is-bad")?.textContent).toBe("Pick another frame.");
  rerender(h(SaveStates, { link: link({ frame: 2 }) }));
  expect(container.querySelector(".is-bad")).toBe(null);
  expect(within(container).getByRole("status").textContent).toBe(HINT);
});

test("a server error shows in place", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url) => new Response(JSON.stringify(url === "/api/local-access" ? {} : { detail: "This scene is gone." }),
    { status: url === "/api/local-access" ? 404 : 409, headers: { "Content-Type": "application/json" } })));
  const { container } = render(h(SaveStates, { link: link() }));
  fireEvent.click(within(container).getByRole("button", { name: "Put this frame in slot 1" }));
  await waitFor(() => expect(container.querySelector(".is-bad")?.textContent).toMatch(/scene is gone/));
});
