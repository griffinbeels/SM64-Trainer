// @vitest-environment jsdom
// The Ghost Lab's save-state strip (ui/components/scenestrip.js) against a scripted lab. His words (round 9,
// item 5): "make it really easy (1) to save to a save state (and see with responsive UIUX which save states
// are saved to / active / etc)", and "just a thin strip that shows the screenshot previews, under the video
// player". Nine tiles as GET /api/scene-lab/slots says the game holds them (a picture, else the place's name;
// the version's flag; ⇄ when translated; gold and ▶ for the slot loaded last; an empty slot is its number); a
// click saves the frame on screen into that slot with its picture and reads the slots again; the line under
// them says what a press did or why none can run, and is always there.
import { h } from "preact";
import { useState } from "preact/hooks";
import { render, cleanup, fireEvent, waitFor, within } from "@testing-library/preact";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { SLOTS_POLL, SLOT_NUMBERS, SaveSlots, SaveStrip, stripLine, thumbnail }
  from "../../src/sm64_events/ui/components/scenestrip.js";

const empty = (slot) => ({ slot, state: "empty", level: null, area: null, place: null, version: null, translated: false,
  picture: null, frame: null, mine: false });
const answerOf = (extra = {}) => ({ game: "us", reason: null, loaded: null, stamp: "s1", slots: SLOT_NUMBERS.map(empty), ...extra });
const saved = (slot, fields) => ({ ...empty(slot), state: "saved", level: 9, area: 1, place: "Bob-omb Battlefield",
  version: "us", ...fields });
const PLANTED = answerOf({ loaded: 2, stamp: "s2", slots: [
  saved(1, { picture: "/api/scene-lab/slots/picture/aa", frame: 120, mine: true }),
  saved(2, { picture: "/api/scene-lab/slots/picture/bb", frame: 233, mine: true }),
  saved(3, { level: 24, place: "Whomp's Fortress" }),
  saved(4, { version: "jp", translated: true, picture: "/api/scene-lab/slots/picture/dd", frame: 312, mine: true }),
  ...[5, 6, 7, 8, 9].map(empty)] });
const scene = (extra = {}) => ({ session: 1, attempt: 12, name: "take.sm64scene", growing: false,
  frames: [{ package_frame: 0 }, { package_frame: 2 }, { package_frame: 3 }], ...extra });

let slotsAnswer, slotReads, asked, answers;
const POLL = SLOTS_POLL.ms;

beforeEach(() => {
  slotsAnswer = answerOf();
  slotReads = 0;
  asked = [];
  answers = [{ id: "abc", exact: true }, { written: true, slot: 3 }];
  vi.stubGlobal("fetch", vi.fn(async (url, options = {}) => {
    if (url === "/api/local-access") return new Response("{}", { status: 404 });
    const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
    if (url === "/api/scene-lab/slots") {
      slotReads += 1;
      return json(slotsAnswer);
    }
    asked.push([url, JSON.parse(options.body)]);
    const answer = answers.shift();
    return answer?.status ? json({ detail: answer.detail }, answer.status) : json(answer);
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); SLOTS_POLL.ms = POLL; });

// The page's link as far as the strip reads it; the strip draws again whenever its slots say so.
const makeLink = (extra = {}) => ({ scene: scene(), frame: 1, playing: false, canvas: () => null,
  slots: new SaveSlots({ changed: () => {} }), ...extra });
function Strip({ link }) {
  const [, setRevision] = useState(0);
  link.slots.changed = () => setRevision((revision) => revision + 1);
  return h(SaveStrip, { link });
}
const tile = (container, slot) => container.querySelector(`.ghost-lab-slot[data-slot="${slot}"]`);
const line = (container) => container.querySelector(".ghost-lab-strip-line");
const pictureCanvas = () => {
  const drawn = vi.fn();
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(() => ({ drawImage: drawn }));
  vi.spyOn(HTMLCanvasElement.prototype, "toDataURL").mockImplementation(() => "data:image/jpeg;base64,PIC");
  return drawn;
};

test("the tiles show the nine slots as the game holds them: picture or place, flag, ⇄, the loaded mark", async () => {
  slotsAnswer = PLANTED;
  const { container } = render(h(Strip, { link: makeLink() }));
  await waitFor(() => expect(tile(container, 1).querySelector("img.ghost-lab-slot-picture")).not.toBeNull());
  expect(container.querySelectorAll(".ghost-lab-slot")).toHaveLength(9);
  expect(tile(container, 1).querySelector("img.ghost-lab-slot-picture").getAttribute("src")).toBe("/api/scene-lab/slots/picture/aa");
  expect(tile(container, 1).querySelector("kbd").textContent).toBe("1");
  expect(tile(container, 1).querySelector("img.region-flag").getAttribute("alt")).toBe("US");
  expect(tile(container, 2).classList.contains("is-loaded")).toBe(true);
  expect(tile(container, 2).querySelector(".ghost-lab-slot-loaded").textContent).toBe("▶");
  expect([1, 3, 4, 5].map((slot) => tile(container, slot).classList.contains("is-loaded"))).toEqual([false, false, false, false]);
  expect(tile(container, 3).querySelector("img.ghost-lab-slot-picture")).toBeNull();
  expect(tile(container, 3).querySelector(".ghost-lab-slot-place").textContent).toBe("Whomp's Fortress");
  expect(tile(container, 4).querySelector("img.region-flag").getAttribute("alt")).toBe("JP");
  expect(tile(container, 4).querySelector(".ghost-lab-slot-crossed").textContent).toBe("⇄");
  expect(tile(container, 1).querySelector(".ghost-lab-slot-crossed")).toBeNull();
  const five = tile(container, 5);
  expect(five.classList.contains("is-empty")).toBe(true);
  expect(five.querySelector("button").textContent.trim()).toBe("5");      // an empty slot is its number alone
  expect(five.querySelector("img, .ghost-lab-slot-place, .ghost-lab-slot-marks")).toBeNull();
});

test("each tile names what is in it and what a press does", async () => {
  slotsAnswer = PLANTED;
  const { container } = render(h(Strip, { link: makeLink() }));
  await waitFor(() => expect(tile(container, 4).querySelector("img")).not.toBeNull());
  const named = (slot) => tile(container, slot).querySelector("button").getAttribute("aria-label");
  expect(named(2)).toBe("Slot 2, Bob-omb Battlefield, US, loaded last. Save this frame here (2).");
  expect(named(4)).toBe("Slot 4, Bob-omb Battlefield, JP, changed to work in this version. Save this frame here (4).");
  expect(named(5)).toBe("Slot 5, empty. Save this frame here (5).");
  expect(within(container).getByRole("button", { name: /^Slot 3, Whomp's Fortress, US\./ })).toBeTruthy();
});

test("without a game every tile is its number and the line says why", async () => {
  slotsAnswer = answerOf({ game: null, reason: "Project64 is not running a game.",
    slots: SLOT_NUMBERS.map((slot) => ({ ...empty(slot), state: "unknown" })) });
  const { container } = render(h(Strip, { link: makeLink() }));
  await waitFor(() => expect(line(container).textContent).toBe("Project64 is not running a game."));
  expect([...container.querySelectorAll(".ghost-lab-slot")].every((one) => one.classList.contains("is-unknown"))).toBe(true);
  expect(line(container).classList.contains("is-bad")).toBe(false);
});

test("a click saves the frame on screen with its picture, says so, and reads the slots again", async () => {
  const drawn = pictureCanvas();
  const shown = { width: 640, height: 480 };
  const link = makeLink({ canvas: () => shown });
  const { container } = render(h(Strip, { link }));
  await waitFor(() => expect(slotReads).toBe(1));
  fireEvent.click(tile(container, 3).querySelector("button"));
  await waitFor(() => expect(line(container).textContent).toBe("Saving frame 2 in slot 3..."));
  expect(tile(container, 3).classList.contains("is-busy")).toBe(true);
  expect([...container.querySelectorAll(".ghost-lab-slot button")].every((button) => button.disabled)).toBe(true);
  await waitFor(() => expect(line(container).textContent).toBe("Frame 2 is in slot 3."));
  expect(asked).toEqual([["/api/scene-lab/moment", { attempt: 12, frame: 2 }],
    ["/api/scene-lab/moment/abc/write", { slot: 3, picture: "data:image/jpeg;base64,PIC" }]]);
  expect(drawn).toHaveBeenCalledWith(shown, 0, 0, 160, 120);             // a 160-wide picture at the frame's shape
  await waitFor(() => expect(slotReads).toBe(2));                        // the tile shows what was written
  expect([...container.querySelectorAll(".ghost-lab-slot button")].some((button) => button.disabled)).toBe(false);
});

test("a frame that cannot be saved: every tile waits, the line says why, and a key press asks nothing", async () => {
  const link = makeLink({ frame: 0 });
  const { container } = render(h(Strip, { link }));
  await waitFor(() => expect(line(container).textContent).toBe("This is the first frame. Pick a later frame."));
  expect([...container.querySelectorAll(".ghost-lab-slot button")].every((button) => button.disabled)).toBe(true);
  await link.slots.press(link, 1);
  expect(asked).toEqual([]);
});

test("a press while one is on its way does nothing", async () => {
  const link = makeLink();
  render(h(Strip, { link }));
  const first = link.slots.press(link, 1);
  await link.slots.press(link, 2);
  await first;
  expect(asked.map(([url]) => url)).toEqual(["/api/scene-lab/moment", "/api/scene-lab/moment/abc/write"]);
  expect(asked[1][1].slot).toBe(1);
});

test("the game's refusal shows on the line, marked bad, and a written state's own version shows its flag", async () => {
  answers = [{ id: "abc", exact: true }, { refused: "place", reason: "The game is in Whomp's Fortress. Go there, then press again." }];
  const link = makeLink();
  const { container } = render(h(Strip, { link }));
  fireEvent.click(tile(container, 1).querySelector("button"));
  await waitFor(() => expect(line(container).textContent).toBe("The game is in Whomp's Fortress. Go there, then press again."));
  expect(line(container).classList.contains("is-bad")).toBe(true);
  expect(line(container).getAttribute("title")).toBe("The game is in Whomp's Fortress. Go there, then press again.");
  const crossed = "The Trainer changed it from the US version to your JP game.";
  answers = [{ id: "abc", exact: true, build: "us" },
    { written: true, slot: 2, build: "us", translated: { from: "us", to: "jp", words: crossed, removed: 0 } }];
  fireEvent.click(tile(container, 2).querySelector("button"));
  await waitFor(() => expect(line(container).textContent).toBe(`Frame 2 is in slot 2. ${crossed}`));
  expect(line(container).querySelector("img.region-flag").getAttribute("alt")).toBe("US");
  expect(line(container).classList.contains("is-bad")).toBe(false);
});

test("a server error shows on the line", async () => {
  answers = [{ status: 409, detail: "This scene is gone." }];
  const { container } = render(h(Strip, { link: makeLink() }));
  fireEvent.click(tile(container, 1).querySelector("button"));
  await waitFor(() => expect(line(container).textContent).toMatch(/scene is gone/));
  expect(line(container).classList.contains("is-bad")).toBe(true);
});

test("an answer belongs to its frame: stepping away takes it down, Play keeps it up, a new scene forgets it", () => {
  const slots = new SaveSlots({ changed: () => {} });
  slots.said = { bad: true, text: "Pick another frame.", at: 1, slot: 1 };
  expect(stripLine(makeLink({ slots }), slots).text).toBe("Pick another frame.");
  expect(stripLine(makeLink({ slots, frame: 2, playing: true }), slots).text).toBe("Pick another frame.");
  expect(stripLine(makeLink({ slots, frame: 2 }), slots)).toEqual({ bad: false, text: "" });
  slots.answer = answerOf({ reason: "The running game is not Usamune." });
  expect(stripLine(makeLink({ slots, frame: 2 }), slots).text).toBe("The running game is not Usamune.");
  expect(stripLine(makeLink({ slots, frame: 0 }), slots).text).toBe("This is the first frame. Pick a later frame.");
  slots.busy = { slot: 4, shown: 9 };
  expect(stripLine(makeLink({ slots }), slots).text).toBe("Saving frame 9 in slot 4...");
  slots.busy = null;
  slots.forget();
  expect(stripLine(makeLink({ slots }), slots).text).toBe("The running game is not Usamune.");
});

test("a new stamp draws, the same stamp draws nothing new", async () => {
  const changed = vi.fn();
  const slots = new SaveSlots({ changed });
  await slots.read();
  expect(changed).toHaveBeenCalledTimes(1);
  await slots.read();
  expect(changed).toHaveBeenCalledTimes(1);
  slotsAnswer = { ...answerOf(), stamp: "s2" };
  await slots.read();
  expect(changed).toHaveBeenCalledTimes(2);
  expect(slots.answer.stamp).toBe("s2");
});

test("the strip asks every SLOTS_POLL.ms while it shows, and stops when it leaves", async () => {
  SLOTS_POLL.ms = 15;
  const { unmount } = render(h(Strip, { link: makeLink() }));
  await waitFor(() => expect(slotReads).toBeGreaterThanOrEqual(3));
  unmount();
  const after = slotReads;
  await new Promise((resolve) => { setTimeout(resolve, 80); });
  expect(slotReads).toBeLessThanOrEqual(after + 1);                      // one ask may have been on its way
});

test("a picture of the frame is 160 wide at its shape, and none without a canvas or a 2D context", () => {
  expect(thumbnail(null)).toBeNull();
  expect(thumbnail({ width: 0, height: 0 })).toBeNull();
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(() => null);
  expect(thumbnail({ width: 640, height: 480 })).toBeNull();
  vi.restoreAllMocks();
  const drawn = pictureCanvas();
  expect(thumbnail({ width: 320, height: 240 })).toBe("data:image/jpeg;base64,PIC");
  expect(drawn.mock.calls[0].slice(1)).toEqual([0, 0, 160, 120]);
});
