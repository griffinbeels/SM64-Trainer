// @vitest-environment jsdom
// One frame of the Ghost Lab's scene into a Usamune slot (ui/components/scenemoment.js) against a scripted lab:
// the build names the scene the way the lab finds it, each step's refusal shows as the lab said it, the write
// carries the watched run's star when there is one, and whyNot says why no press can run before anyone presses.
// The strip that presses it is scenestrip.test.js.
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { OLDER, momentSource, saveMoment, whyNot } from "../../src/sm64_events/ui/components/scenemoment.js";

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
afterEach(() => { vi.unstubAllGlobals(); });

test("the package's own frame goes to the lab, not the frame's place in the list", async () => {
  const said = await saveMoment(scene(), scene().frames[1], 2, 3);
  expect(asked).toEqual([["/api/scene-lab/moment", { attempt: 12, frame: 2 }],
    ["/api/scene-lab/moment/abc/write", { slot: 3 }]]);
  expect(said).toEqual({ bad: false, text: "Frame 2 is in slot 3." });
});

test("the write carries the watched run's star when there is one, and no picture: the lab draws it", async () => {
  await saveMoment(scene(), scene().frames[1], 2, 3, { course_id: 2, star_id: 1 });
  expect(asked[1]).toEqual(["/api/scene-lab/moment/abc/write", { slot: 3, course_id: 2, star_id: 1 }]);
});

test("the lab finds the scene by the watched ghost, the attempt, or the listed package, in that order", async () => {
  expect(momentSource(scene({ ghost: "g0123456789abcdef0123" }))).toEqual({ ghost: "g0123456789abcdef0123" });
  expect(momentSource(scene())).toEqual({ attempt: 12 });
  expect(momentSource(scene({ attempt: undefined }))).toEqual({ package: "take.sm64scene" });
  await saveMoment(scene({ ghost: "g0123456789abcdef0123" }), scene().frames[2], 3, 1);
  expect(asked[0][1]).toEqual({ ghost: "g0123456789abcdef0123", frame: 3 });
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

test("why no press can run: the first frame, a scene still being recorded, a frame with no package frame", () => {
  expect(whyNot(link())).toBe(null);
  expect(whyNot(link({ frame: 0 }))).toBe("This is the first frame. Pick a later frame.");
  expect(whyNot(link({ scene: scene({ growing: true }) }))).toMatch(/still being recorded/);
  expect(whyNot(link({ scene: scene({ frames: [{}, {}] }) }))).toBe("This scene does not say which frame this is.");
});

const CROSSED = "The Trainer changed it from the US version to your JP game.";

test("a save state written into the other version's game names its own version and says so", async () => {
  answers = [{ id: "abc", exact: true, build: "us" },
    { written: true, slot: 3, build: "us", translated: { from: "us", to: "jp", words: CROSSED, removed: 0 } }];
  expect(await saveMoment(scene(), scene().frames[1], 2, 3)).toEqual(
    { bad: false, text: `Frame 2 is in slot 3. ${CROSSED}`, build: "us" });
});

test("a save state of the game's own version names its version and no translation words", async () => {
  answers = [{ id: "abc", exact: true, build: "jp" }, { written: true, slot: 3, build: "jp" }];
  const said = await saveMoment(scene(), scene().frames[1], 2, 3);
  expect(said).toEqual({ bad: false, text: "Frame 2 is in slot 3.", build: "jp" });
});
