// @vitest-environment jsdom
// The Ghost Lab's save-state strip (ui/components/scenestrip.js) against a scripted lab. His words (round 9,
// item 5): "make it really easy (1) to save to a save state (and see with responsive UIUX which save states
// are saved to / active / etc)", and "just a thin strip that shows the screenshot previews, under the video
// player". Nine tiles as GET /api/scene-lab/slot-sets names them (a picture, else the place's name; the
// version's flag; ⇄ when translated; gold and ▶ for the slot loaded last; an empty slot is its number); a
// click saves the frame on screen into that slot and reads the slots again; the line under them says what a
// press did or why none can run, and is always there.
// Star save-state sets (item 12), his words: "for every single star, we should be able to have custom save
// states that override the default stage-level save states": a ★ marks a slot from the star's own set, a
// plain tile one from the course's, hover names the course save under a star's save, a saved tile's menu
// moves it between the two sets or out of its set, the line's right end names the course and star, and a
// press sends the watched run's star.
import { h } from "preact";
import { useState } from "preact/hooks";
import { render, cleanup, fireEvent, waitFor, within } from "@testing-library/preact";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { NO_GAME, SLOTS_POLL, SLOT_NUMBERS, SaveSlots, SaveStrip, fromSlotSets, stripLine }
  from "../../src/sm64_events/ui/components/scenestrip.js";

const scene = (extra = {}) => ({ session: 1, attempt: 12, name: "take.sm64scene", growing: false,
  frames: [{ package_frame: 0 }, { package_frame: 2 }, { package_frame: 3 }], ...extra });

// GET /slot-sets's shape (the lead's frozen contract, round 9 item 12; slotlibrary/keeper.py's view).
const noSlot = (slot) => ({ slot, set: null, state: null, translated: null, under: null, picture: null, loaded_last: false });
const kept = (id, place, build = "us") => ({ id, name: `${place} ${id}`, level: 24, area: 1, place, build, source: "lab",
  first_seen_utc: "2026-11-08T12:00:00Z" });
const picture = (id) => `/api/scene-lab/slot-sets/pictures/${id}`;
const WHOMP = { course_id: 2, course: "Whomp's Fortress", star_id: 1, star: "Shoot into the Wild Blue", entity_key: "star:2:1" };
const setsOf = (extra = {}) => ({ context: WHOMP, game: { build: "us" }, slots: SLOT_NUMBERS.map(noSlot), ...extra });
// Four course saves: two with pictures, one saved in the game itself (no picture: its place), one from the
// other version (⇄), and slot 2 loaded last.
const PLANTED = setsOf({ slots: [
  { ...noSlot(1), set: "course", state: kept("a1", "Bob-omb Battlefield"), translated: false, picture: picture("a1") },
  { ...noSlot(2), set: "course", state: kept("a2", "Bob-omb Battlefield"), translated: false, picture: picture("a2"),
    loaded_last: true },
  { ...noSlot(3), set: "course", state: kept("a3", "Whomp's Fortress"), translated: false },
  { ...noSlot(4), set: "course", state: kept("a4", "Bob-omb Battlefield", "jp"), translated: true, picture: picture("a4") },
  ...[5, 6, 7, 8, 9].map(noSlot)] });
const SETS = setsOf({ slots: [
  { ...noSlot(1), set: "star", state: kept("s1", "Whomp's Fortress"), translated: false, under: kept("c1", "Whomp's Fortress"),
    picture: picture("s1"), loaded_last: true },
  { ...noSlot(2), set: "course", state: kept("c2", "Whomp's Fortress"), translated: false },
  { ...noSlot(3), set: "star", state: kept("s3", "Whomp's Fortress", "jp"), translated: true, picture: picture("s3") },
  { ...noSlot(4), state: kept("g4", "Bob-omb Battlefield"), translated: false },
  ...[5, 6, 7, 8, 9].map(noSlot)] });

let setsAnswer, setReads, asked, answers;
const POLL = SLOTS_POLL.ms;

beforeEach(() => {
  setsAnswer = setsOf();
  setReads = 0;
  asked = [];
  answers = [{ id: "abc", exact: true }, { written: true, slot: 3 }];
  vi.stubGlobal("fetch", vi.fn(async (url, options = {}) => {
    if (url === "/api/local-access") return new Response("{}", { status: 404 });
    const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
    if (url === "/api/scene-lab/slot-sets" && !options.method) {
      setReads += 1;
      return json(setsAnswer);
    }
    asked.push([url, options.body ? JSON.parse(options.body) : null]);
    const answer = answers.shift();
    return answer?.status ? json({ detail: answer.detail }, answer.status) : json(answer);
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); SLOTS_POLL.ms = POLL; });

// The page's link as far as the strip reads it; the strip draws again whenever its slots say so.
const makeLink = (extra = {}) => ({ scene: scene(), frame: 1, playing: false,
  slots: new SaveSlots({ changed: () => {} }), ...extra });
function Strip({ link }) {
  const [, setRevision] = useState(0);
  link.slots.changed = () => setRevision((revision) => revision + 1);
  return h(SaveStrip, { link });
}
const tile = (container, slot) => container.querySelector(`.ghost-lab-slot[data-slot="${slot}"]`);
const line = (container) => container.querySelector(".ghost-lab-strip-line");

test("the tiles show the nine slots: picture or place, flag, ⇄, the loaded mark", async () => {
  setsAnswer = PLANTED;
  const { container } = render(h(Strip, { link: makeLink() }));
  await waitFor(() => expect(tile(container, 1).querySelector("img.ghost-lab-slot-picture")).not.toBeNull());
  expect(container.querySelectorAll(".ghost-lab-slot")).toHaveLength(9);
  expect(tile(container, 1).querySelector("img.ghost-lab-slot-picture").getAttribute("src")).toBe(picture("a1"));
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

test("a picture that does not load leaves the place's name, as a tile with none shows it", async () => {
  setsAnswer = PLANTED;
  const { container } = render(h(Strip, { link: makeLink() }));
  const picture = (slot) => tile(container, slot).querySelector("img.ghost-lab-slot-picture");
  await waitFor(() => expect(picture(1)).not.toBeNull());
  fireEvent.error(picture(1));                                            // gone, or not a picture
  await waitFor(() => expect(picture(1)).toBeNull());
  expect(tile(container, 1).querySelector(".ghost-lab-slot-place").textContent).toBe("Bob-omb Battlefield");
  expect(picture(2)).not.toBeNull();                                      // only that tile's
});

test("each tile names what is in it, its set and what a press does", async () => {
  setsAnswer = PLANTED;
  const { container } = render(h(Strip, { link: makeLink() }));
  await waitFor(() => expect(tile(container, 4).querySelector("img")).not.toBeNull());
  const named = (slot) => tile(container, slot).querySelector("button").getAttribute("aria-label");
  expect(named(2)).toBe("Slot 2, Bob-omb Battlefield, US, all stars in this course, loaded last. Save this frame here (2).");
  expect(named(4)).toBe("Slot 4, Bob-omb Battlefield, JP, changed to work in this version, all stars in this course. "
    + "Save this frame here (4).");
  expect(named(5)).toBe("Slot 5, empty. Save this frame here (5).");
  expect(within(container).getByRole("button", { name: /^Slot 3, Whomp's Fortress, US,/ })).toBeTruthy();
});

test("a click saves the frame on screen, says so, and reads the slots again", async () => {
  const link = makeLink();
  const { container } = render(h(Strip, { link }));
  await waitFor(() => expect(setReads).toBe(1));
  fireEvent.click(tile(container, 3).querySelector("button"));
  await waitFor(() => expect(line(container).textContent).toBe("Saving frame 2 in slot 3..."));
  expect(tile(container, 3).classList.contains("is-busy")).toBe(true);
  expect([...container.querySelectorAll(".ghost-lab-slot button")].every((button) => button.disabled)).toBe(true);
  await waitFor(() => expect(line(container).textContent).toBe("Frame 2 is in slot 3."));
  // No picture rides on the write: the lab draws the frame's picture itself.
  expect(asked).toEqual([["/api/scene-lab/moment", { attempt: 12, frame: 2 }],
    ["/api/scene-lab/moment/abc/write", { slot: 3 }]]);
  await waitFor(() => expect(setReads).toBe(2));                         // the tile shows what was written
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
  slots.answer = fromSlotSets({ context: null, game: null, slots: SLOT_NUMBERS.map(noSlot) });
  expect(stripLine(makeLink({ slots, frame: 2 }), slots).text).toBe(NO_GAME);
  expect(stripLine(makeLink({ slots, frame: 0 }), slots).text).toBe("This is the first frame. Pick a later frame.");
  slots.busy = { slot: 4, shown: 9 };
  expect(stripLine(makeLink({ slots }), slots).text).toBe("Saving frame 9 in slot 4...");
  slots.busy = null;
  slots.forget();
  expect(stripLine(makeLink({ slots }), slots).text).toBe(NO_GAME);
});

test("a new answer draws, the same answer draws nothing new", async () => {
  const changed = vi.fn();
  const slots = new SaveSlots({ changed });
  await slots.read();
  expect(changed).toHaveBeenCalledTimes(1);
  await slots.read();
  expect(changed).toHaveBeenCalledTimes(1);
  setsAnswer = SETS;
  await slots.read();
  expect(changed).toHaveBeenCalledTimes(2);
  expect(slots.answer.slots[0].set).toBe("star");
});

test("the strip asks every SLOTS_POLL.ms while it shows, and stops when it leaves", async () => {
  SLOTS_POLL.ms = 15;
  const { unmount } = render(h(Strip, { link: makeLink() }));
  await waitFor(() => expect(setReads).toBeGreaterThanOrEqual(3));
  unmount();
  const after = setReads;
  await new Promise((resolve) => { setTimeout(resolve, 80); });
  expect(setReads).toBeLessThanOrEqual(after + 1);                        // one ask may have been on its way
});

// ---- star save-state sets (round 9, item 12) ----

const menuOf = (container, slot) => tile(container, slot).querySelector(".ghost-lab-slot-more");
const context = (container) => container.querySelector(".ghost-lab-strip-context");
const openMenu = async (container, slot) => {
  fireEvent.click(menuOf(container, slot));
  await waitFor(() => expect(tile(container, slot).querySelector("[role=menu]")).not.toBeNull());
  return within(tile(container, slot).querySelector("[role=menu]"));
};

test("★ marks the star's own saves, the course's are plain, and hover names the save under one", async () => {
  setsAnswer = SETS;
  const { container } = render(h(Strip, { link: makeLink() }));
  await waitFor(() => expect(tile(container, 1).querySelector(".ghost-lab-slot-star")).not.toBeNull());
  expect(tile(container, 1).querySelector(".ghost-lab-slot-star").textContent).toBe("★");
  expect(tile(container, 1).classList.contains("is-set-star")).toBe(true);
  expect(tile(container, 1).classList.contains("is-loaded")).toBe(true);
  expect(tile(container, 1).querySelector("button").getAttribute("title")).toBe("Slot 1, Whomp's Fortress, US, only this "
    + "star, loaded last. Under it is the course save from Whomp's Fortress. Save this frame here (1).");
  expect(tile(container, 2).querySelector(".ghost-lab-slot-star")).toBeNull();            // the course's: plain
  expect(tile(container, 2).classList.contains("is-set-course")).toBe(true);
  expect(tile(container, 2).querySelector(".ghost-lab-slot-place").textContent).toBe("Whomp's Fortress");
  expect(tile(container, 2).querySelector("button").getAttribute("aria-label"))
    .toBe("Slot 2, Whomp's Fortress, US, all stars in this course. Save this frame here (2).");
  expect(tile(container, 3).querySelector(".ghost-lab-slot-star")).not.toBeNull();
  expect(tile(container, 3).querySelector(".ghost-lab-slot-crossed").textContent).toBe("⇄");
  expect(tile(container, 3).querySelector("img.region-flag").getAttribute("alt")).toBe("JP");
  expect(tile(container, 4).querySelector(".ghost-lab-slot-star")).toBeNull();            // in no set
  expect(tile(container, 5).classList.contains("is-empty")).toBe(true);
  expect(tile(container, 5).querySelector("button").textContent.trim()).toBe("5");
  expect(container.querySelectorAll(".ghost-lab-slot-more")).toHaveLength(4);              // saved tiles only
  expect(tile(container, 5).querySelector(".ghost-lab-slot-more")).toBeNull();
  expect(context(container).textContent).toBe("Whomp's Fortress · ★ Shoot into the Wild Blue");
  expect(line(container).textContent).toBe("");                                          // the context is beside the line
});

test("a tile's menu moves a slot between the star's set and the course's, and takes it out of its set", async () => {
  setsAnswer = SETS;
  const moved = setsOf({ slots: SETS.slots.map((one) => (one.slot === 1 ? { ...one, set: "course", under: null } : one)) });
  answers = [moved];
  const { container } = render(h(Strip, { link: makeLink() }));
  await waitFor(() => expect(menuOf(container, 1)).not.toBeNull());
  let menu = await openMenu(container, 1);
  expect(menuOf(container, 1).getAttribute("aria-expanded")).toBe("true");
  expect(menu.getByRole("menuitemradio", { name: "Only this star" }).getAttribute("aria-checked")).toBe("true");
  expect(menu.getByRole("menuitemradio", { name: "All stars in this course" }).getAttribute("aria-checked")).toBe("false");
  setsAnswer = moved;                                       // the lab's next answer agrees with its move
  fireEvent.click(menu.getByRole("menuitemradio", { name: "All stars in this course" }));
  await waitFor(() => expect(tile(container, 1).querySelector(".ghost-lab-slot-star")).toBeNull());
  expect(asked).toEqual([["/api/scene-lab/slot-sets/1/move", { to: "course" }]]);
  expect(tile(container, 1).querySelector("[role=menu]")).toBeNull();                   // a choice closes the menu
  const removed = setsOf({ slots: moved.slots.map((one) => (one.slot === 2 ? { ...one, set: null } : one)) });
  answers = [removed];
  setsAnswer = removed;
  menu = await openMenu(container, 2);
  fireEvent.click(menu.getByRole("menuitem", { name: "Remove from set" }));
  await waitFor(() => expect(tile(container, 2).classList.contains("is-set-course")).toBe(false));
  expect(asked[1]).toEqual(["/api/scene-lab/slot-sets/2?set=course", null]);
  expect(tile(container, 2).classList.contains("is-saved")).toBe(true);               // the game keeps the slot
  menu = await openMenu(container, 4);                                                  // a slot in no set
  expect(menu.getByRole("menuitem", { name: "Remove from set" }).disabled).toBe(true);
  expect(menu.getByRole("menuitem", { name: "Remove from set" }).getAttribute("title")).toBe("It is in no set.");
  expect(asked).toHaveLength(2);
});

test("choosing the set a slot is already in asks nothing", async () => {
  setsAnswer = SETS;
  const { container } = render(h(Strip, { link: makeLink() }));
  await waitFor(() => expect(menuOf(container, 2)).not.toBeNull());
  const menu = await openMenu(container, 2);
  fireEvent.click(menu.getByRole("menuitemradio", { name: "All stars in this course" }));
  await waitFor(() => expect(tile(container, 2).querySelector("[role=menu]")).toBeNull());
  expect(asked).toEqual([]);
});

test("without a star the menu says why it cannot use one, and a refusal shows on the line", async () => {
  setsAnswer = { ...SETS, context: { ...WHOMP, star_id: null, star: null, entity_key: null } };
  answers = [{ status: 409, detail: "Pick a star in the game first." }];
  const { container } = render(h(Strip, { link: makeLink() }));
  await waitFor(() => expect(context(container)?.textContent).toBe("Whomp's Fortress"));
  const menu = await openMenu(container, 1);
  const star = menu.getByRole("menuitemradio", { name: "Only this star" });
  expect([star.disabled, star.getAttribute("title")]).toEqual([true, "Pick a star first."]);
  fireEvent.click(menu.getByRole("menuitemradio", { name: "All stars in this course" }));
  await waitFor(() => expect(line(container).textContent).toBe("Pick a star in the game first."));
  expect(line(container).classList.contains("is-bad")).toBe(true);
});

test("a press sends the watched run's star with the write, and no star for a segment", async () => {
  setsAnswer = SETS;
  answers = [{ id: "abc", exact: true }, { written: true, slot: 4, set: "star" }];
  const link = makeLink({ stageTarget: { course_id: 2, star_id: 1, strat: null } });
  const { container } = render(h(Strip, { link }));
  await waitFor(() => expect(menuOf(container, 4)).not.toBeNull());
  fireEvent.click(tile(container, 4).querySelector("button"));
  await waitFor(() => expect(asked).toHaveLength(2));
  expect(asked[1]).toEqual(["/api/scene-lab/moment/abc/write", { slot: 4, course_id: 2, star_id: 1 }]);
  await waitFor(() => expect(line(container).textContent).toBe("Frame 2 is in slot 4."));
  answers = [{ id: "abd", exact: true }, { written: true, slot: 5 }];
  link.stageTarget = { segment_id: 12, strat: null };
  fireEvent.click(tile(container, 5).querySelector("button"));
  await waitFor(() => expect(asked).toHaveLength(4));
  expect(asked[3]).toEqual(["/api/scene-lab/moment/abd/write", { slot: 5 }]);
});

test("with no game, every slot is unknown, none has a menu, and the line says why", async () => {
  setsAnswer = { context: null, game: null, slots: SLOT_NUMBERS.map(noSlot) };
  const { container } = render(h(Strip, { link: makeLink() }));
  await waitFor(() => expect(line(container).textContent).toBe(NO_GAME));
  expect([...container.querySelectorAll(".ghost-lab-slot")].every((one) => one.classList.contains("is-unknown"))).toBe(true);
  expect(container.querySelector(".ghost-lab-slot-more, .ghost-lab-strip-context")).toBeNull();
  expect(line(container).classList.contains("is-bad")).toBe(false);
});

test("a slot_sets_changed event in the store's feed draws at once, before the next ask", async () => {
  setsAnswer = setsOf();
  const link = makeLink({ feed: [] });
  const { container, rerender } = render(h(Strip, { link }));
  await waitFor(() => expect(context(container)).not.toBeNull());
  expect(container.querySelector(".ghost-lab-slot-star")).toBeNull();
  const reads = setReads;
  link.feed = [{ type: "slot_sets_changed", payload: SETS }, { type: "other" }];
  rerender(h(Strip, { link }));
  await waitFor(() => expect(tile(container, 1).querySelector(".ghost-lab-slot-star")).not.toBeNull());
  expect(setReads).toBe(reads);
});
