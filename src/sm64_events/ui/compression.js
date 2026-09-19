// src/sm64_events/ui/compression.js — the page's view of the background jobs
// that make saved replays smaller (`GET /api/replay/compression`).
// TWO halves, one owner each: the WORDS a job is described in (pure, node-
// tested in tests/frontend/compression.test.js) and the ONE poller both
// surfaces share -- the close warning and the recording-dot panel can be open
// together, and two pollers would be two requests for one answer.
//
// Nothing here polls unless a surface is watching. An app with the panel shut
// and no close pending makes no compression requests at all: the desktop shell
// decides for itself whether a close needs the warning, and the panel asks when
// it opens.
import { pollJSON } from "./pollstate.js";

export const COMPRESSION_URL = "/api/replay/compression";
// Twice a second while something is moving (the bar glides between polls, see
// `.compression-meter` in index.html); a slow look while a surface is open on
// an idle list, so a save made meanwhile still turns up within a few seconds.
export const ACTIVE_POLL_MS = 500;
export const QUIET_POLL_MS = 3000;

// A job in one of these stages is unfinished work. `in_use` is NOT one: that
// replay is already proven and swaps when the app closes, so exiting costs it
// nothing (the contract's own definition of `active`).
const WORKING = new Set(["waiting", "compressing", "checking"]);

// 1024-based, like the buffer figures beside it in the panel.
export function fmtBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes < 1024 ** 2) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}

export function jobName(job) {
  return (job && job.label) || "Saved replay";
}

// 0..1 for the bar. A waiting job has no fraction yet and draws an empty lane;
// a finished one is full whatever the server rounded to.
export function jobFraction(job) {
  if (!WORKING.has(job.stage)) return 1;
  const fraction = Number(job.fraction);
  if (job.fraction == null || !Number.isFinite(fraction)) return 0;
  return Math.min(1, Math.max(0, fraction));
}

function sizeChange(job) {
  const from = fmtBytes(job.from_bytes), to = fmtBytes(job.to_bytes);
  return from && to ? ` — ${from} to ${to}` : "";
}

// What a job is doing, in the player's words. Never the technique's name: he
// reads "checking it matches the original", not the name of the proof.
export function jobStatus(job) {
  switch (job.stage) {
    case "waiting": return "Waiting its turn";
    case "compressing": return `Compressing ${Math.round(jobFraction(job) * 100)}%`;
    case "checking": return "Checking it matches the original";
    case "in_use": return "Ready — waiting for you to stop watching it";
    case "done": return `Done${sizeChange(job)}`;
    case "kept": return "Kept at full size";
    default: return "Working";
  }
}

export function isWorking(job) {
  return WORKING.has(job.stage);
}

export function remainingJobs(jobs) {
  return (jobs || []).filter(isWorking).length;
}

// The panel's one-line tally above its (scrolling) list.
export function listSummary(jobs) {
  const remaining = remainingJobs(jobs);
  return remaining ? `${remaining} of ${jobs.length} still to go` : "All finished";
}

// The truthful cost of exiting now: nothing is lost, N stay big until next time.
// The list is capped, so work can be unfinished with none of it listed; the
// sentence then says so without a number rather than claiming zero.
export function exitCost(remaining) {
  const stay = remaining <= 0 ? "the replays still being worked on stay"
    : remaining === 1 ? "that 1 replay stays" : `those ${remaining} replays stay`;
  const finish = remaining === 1 ? "finishes" : "finish";
  return `If you exit now, ${stay} full size and ${finish} getting smaller `
    + "the next time you open SM64 Trainer.";
}

// `loaded`: there is something to draw. `answered`: it is safe to ACT on --
// it came from the server, or from failures that have persisted.
const EMPTY = Object.freeze({ active: false, jobs: [], loaded: false, answered: false });
// How many looks in a row must fail before "no answer" is taken as the answer.
export const FAILURES_THAT_ANSWER = 3;

function snapshot(body) {
  return {
    active: Boolean(body && body.active),
    jobs: Array.isArray(body && body.jobs) ? body.jobs : [],
    loaded: true, answered: true,
  };
}

// The auto-exit after "Wait" may only follow an answer saying the work is over
// that is safe to act on: one failed look in the middle of a job must not
// close the app under it.
export function finishedWaiting(waiting, state) {
  return Boolean(waiting && state && state.answered && !state.active);
}

const listeners = new Set();
let latest = EMPTY, stopPolling = null, failures = 0;

function tell(state) {
  latest = state;
  listeners.forEach((listener) => listener(latest));
}

function publish(body) {
  failures = 0;
  tell(snapshot(body));
}

// A look that fails -- a 404 because replay is off and its routes are not
// mounted, a server mid-restart -- is an idle list, shown without a word of
// complaint: there is nothing he could do about it and nothing is wrong with
// his replays. The one distinction kept is between DRAWING idle and ACTING on
// it. A list already on screen survives a blip or two; once the failures
// persist, idle becomes the answer, and a pending Wait is released by it.
function failed() {
  failures += 1;
  const persisted = failures >= FAILURES_THAT_ANSWER;
  if (latest.loaded && !persisted) return;
  tell({ active: false, jobs: [], loaded: true, answered: persisted });
}

// Subscribe a surface. The last one to leave stops the poll and forgets the
// list, so a surface reopened later never flashes a list from the last time
// anyone looked. EVERY arrival asks again at once: the panel may have been
// sitting on an idle list for up to QUIET_POLL_MS when the close warning
// opens, and a warning that says "nothing is waiting" and then corrects
// itself is the one thing it must not do. For the same reason a newcomer is
// only handed the shared list while it is moving (at most ACTIVE_POLL_MS old).
export function watchCompression(listener) {
  listeners.add(listener);
  listener(latest.active ? latest : EMPTY);
  if (stopPolling) stopPolling();
  stopPolling = pollJSON(COMPRESSION_URL, publish, {
    intervalMs: (state) => (state && state.active ? ACTIVE_POLL_MS : QUIET_POLL_MS),
    timeoutMs: 5000, onError: failed,
  });
  return () => {
    listeners.delete(listener);
    if (listeners.size) return;
    if (stopPolling) stopPolling();
    stopPolling = null;
    latest = EMPTY;
    failures = 0;
  };
}
