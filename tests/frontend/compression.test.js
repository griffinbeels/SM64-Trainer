import { afterEach, beforeEach, expect, test, vi } from "vitest";
import {
  ACTIVE_POLL_MS, FAILURES_THAT_ANSWER, QUIET_POLL_MS, exitCost, finishedWaiting, fmtBytes,
  jobFraction, jobName, jobStatus, listSummary, remainingJobs, watchCompression,
} from "../../src/sm64_events/ui/compression.js";

const job = (stage, extra = {}) => ({
  attempt_id: 1, label: "Whomp's Fortress: Shoot into the Wild Blue", time_text: "11.16",
  stage, fraction: null, from_bytes: 38102345, to_bytes: null, ...extra,
});
const STAGES = ["waiting", "compressing", "checking", "in_use", "done", "kept"];

test("every stage is described in the player's words", () => {
  expect(jobStatus(job("waiting"))).toBe("Waiting its turn");
  expect(jobStatus(job("compressing", { fraction: 0.41 }))).toBe("Compressing 41%");
  expect(jobStatus(job("checking", { fraction: 0.9 }))).toBe("Checking it matches the original");
  expect(jobStatus(job("in_use", { fraction: 1 }))).toBe("Ready — waiting for you to stop watching it");
  expect(jobStatus(job("kept", { fraction: 1 }))).toBe("Kept at full size");
  // A stage this page has never heard of still reads as a sentence.
  expect(jobStatus(job("polishing"))).toBe("Working");
});

test("no status names the technique", () => {
  const technique = /encod|transcod|ffmpeg|codec|hash|sha|fingerprint|proof|prov|bitrate|crf|mux/i;
  for (const stage of STAGES)
    expect(jobStatus(job(stage, { fraction: 0.5, to_bytes: 11639193 }))).not.toMatch(technique);
});

test("a finished job says what it saved, and only once both sizes are known", () => {
  expect(jobStatus(job("done", { fraction: 1, to_bytes: 11639193 }))).toBe("Done — 36.3 MB to 11.1 MB");
  expect(jobStatus(job("done", { fraction: 1 }))).toBe("Done");
  expect(jobStatus(job("done", { fraction: 1, from_bytes: null, to_bytes: 5 }))).toBe("Done");
});

test("sizes are 1024-based like the buffer figures beside them", () => {
  expect(fmtBytes(512 * 1024)).toBe("512 KB");
  expect(fmtBytes(200)).toBe("1 KB");
  expect(fmtBytes(38102345)).toBe("36.3 MB");
  expect(fmtBytes(1.5 * 1024 ** 3)).toBe("1.50 GB");
  for (const missing of [null, undefined, NaN, -1, "12"]) expect(fmtBytes(missing)).toBe("");
});

test("the bar is empty while waiting, clamped while working and full once over", () => {
  expect(jobFraction(job("waiting"))).toBe(0);
  expect(jobFraction(job("compressing", { fraction: 0.41 }))).toBe(0.41);
  expect(jobFraction(job("compressing", { fraction: 7 }))).toBe(1);
  expect(jobFraction(job("checking", { fraction: -2 }))).toBe(0);
  expect(jobFraction(job("compressing", { fraction: "soon" }))).toBe(0);
  for (const stage of ["in_use", "done", "kept"])
    expect(jobFraction(job(stage, { fraction: 0.2 }))).toBe(1);
});

test("a replay with no label still has a name", () => {
  expect(jobName(job("waiting", { label: null }))).toBe("Saved replay");
  expect(jobName(job("waiting"))).toBe("Whomp's Fortress: Shoot into the Wild Blue");
});

test("only unfinished work counts against exiting", () => {
  expect(remainingJobs(STAGES.map((stage) => job(stage)))).toBe(3);
  expect(remainingJobs([])).toBe(0);
  expect(remainingJobs(undefined)).toBe(0);
});

test("the panel's tally counts what is left, then says it is over", () => {
  expect(listSummary(STAGES.map((stage) => job(stage)))).toBe("3 of 6 still to go");
  expect(listSummary([job("done"), job("kept")])).toBe("All finished");
});

test("the cost of exiting is stated truthfully for one, many and an unlisted job", () => {
  expect(exitCost(1)).toBe("If you exit now, that 1 replay stays full size and finishes "
    + "getting smaller the next time you open SM64 Trainer.");
  expect(exitCost(3)).toBe("If you exit now, those 3 replays stay full size and finish "
    + "getting smaller the next time you open SM64 Trainer.");
  // Work is unfinished but none of it is in the capped list: no number, never "0".
  expect(exitCost(0)).toContain("the replays still being worked on stay full size");
  expect(exitCost(0)).not.toMatch(/\d+ replay/);
  for (const count of [0, 1, 3]) expect(exitCost(count)).not.toMatch(/lost|delet|fail/i);
});

test("waiting only ends on an answer that is safe to act on", () => {
  const idle = { loaded: true, answered: true, active: false, jobs: [] };
  expect(finishedWaiting(true, idle)).toBe(true);
  expect(finishedWaiting(true, { ...idle, active: true })).toBe(false);
  expect(finishedWaiting(true, { ...idle, answered: false })).toBe(false);
  expect(finishedWaiting(true, null)).toBe(false);
  expect(finishedWaiting(false, idle)).toBe(false);
});

let answer, requests;
beforeEach(() => {
  vi.useFakeTimers();
  requests = 0;
  answer = () => ({ active: true, jobs: [job("compressing", { fraction: 0.2 })] });
  vi.stubGlobal("fetch", vi.fn(async () => {
    requests += 1;
    const body = answer();
    if (body instanceof Error) throw body;
    return { ok: true, status: 200, json: async () => body };
  }));
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

test("nothing is asked until a surface watches, and nothing after the last one leaves", async () => {
  expect(requests).toBe(0);
  await vi.advanceTimersByTimeAsync(QUIET_POLL_MS * 3);
  expect(requests).toBe(0);
  const seen = [];
  const stop = watchCompression((state) => seen.push(state));
  expect(seen[0].loaded).toBe(false);
  await vi.advanceTimersByTimeAsync(0);
  expect(requests).toBe(1);
  expect(seen.at(-1)).toMatchObject({ loaded: true, active: true });
  stop();
  await vi.advanceTimersByTimeAsync(QUIET_POLL_MS * 3);
  expect(requests).toBe(1);
});

test("it looks twice a second while work moves and slowly once it stops", async () => {
  const stop = watchCompression(() => {});
  await vi.advanceTimersByTimeAsync(0);
  await vi.advanceTimersByTimeAsync(ACTIVE_POLL_MS * 4);
  expect(requests).toBe(5);
  answer = () => ({ active: false, jobs: [job("done", { fraction: 1 })] });
  await vi.advanceTimersByTimeAsync(ACTIVE_POLL_MS);
  const idleFrom = requests;
  await vi.advanceTimersByTimeAsync(QUIET_POLL_MS - 1);
  expect(requests).toBe(idleFrom);
  await vi.advanceTimersByTimeAsync(1);
  expect(requests).toBe(idleFrom + 1);
  stop();
});

test("a second surface asks again at once and is never handed an idle list that may be stale", async () => {
  answer = () => ({ active: false, jobs: [] });
  const stopPanel = watchCompression(() => {});
  await vi.advanceTimersByTimeAsync(0);
  expect(requests).toBe(1);
  answer = () => ({ active: true, jobs: [job("compressing", { fraction: 0.6 })] });
  const seen = [];
  const stopWarning = watchCompression((state) => seen.push(state));
  expect(seen[0].loaded).toBe(false);
  await vi.advanceTimersByTimeAsync(0);
  expect(requests).toBe(2);
  expect(seen.at(-1)).toMatchObject({ loaded: true, active: true });
  stopWarning(); stopPanel();
});

test("with replay off the route is not there, and that reads as an idle list at once", async () => {
  answer = () => new Error("/api/replay/compression: 404");
  const seen = [];
  const stop = watchCompression((state) => seen.push(state));
  await vi.advanceTimersByTimeAsync(0);
  expect(requests).toBe(1);
  expect(seen.at(-1)).toEqual({ active: false, jobs: [], loaded: true, answered: false });
  stop();
});

test("a blip keeps the list on screen and cannot release a Wait; failures that persist do", async () => {
  const seen = [];
  const stop = watchCompression((state) => seen.push(state));
  await vi.advanceTimersByTimeAsync(0);
  const moving = seen.at(-1);
  expect(moving).toMatchObject({ active: true, answered: true });
  answer = () => new Error("server blinked");
  for (let look = 1; look < FAILURES_THAT_ANSWER; look += 1) {
    await vi.advanceTimersByTimeAsync(ACTIVE_POLL_MS);
    expect(seen.at(-1)).toBe(moving);
    expect(finishedWaiting(true, seen.at(-1))).toBe(false);
  }
  await vi.advanceTimersByTimeAsync(ACTIVE_POLL_MS);
  expect(seen.at(-1)).toEqual({ active: false, jobs: [], loaded: true, answered: true });
  expect(finishedWaiting(true, seen.at(-1))).toBe(true);
  // One good look and the count starts again.
  answer = () => ({ active: true, jobs: [job("checking", { fraction: 0.8 })] });
  await vi.advanceTimersByTimeAsync(ACTIVE_POLL_MS);
  expect(seen.at(-1)).toMatchObject({ active: true, answered: true });
  answer = () => new Error("server blinked");
  await vi.advanceTimersByTimeAsync(ACTIVE_POLL_MS);
  expect(seen.at(-1)).toMatchObject({ active: true });
  stop();
});
