// src/sm64_events/ui/latency.js — where the time between the game and the
// screen actually goes.
//
// Live report 2026-08-04, after two rounds of detector-side fixes: *"It's
// still slow, and you did not succeed… Do we have instrumentation for
// comparing the timegap between detecting the xcam / final time, and when we
// actually display it in the frontend?"* We did not. Everything measured until
// now stopped at the server: `published_after` says how long the DETECTOR
// held a row, and `data/ui_log.jsonl` says what the page eventually painted,
// but nothing joined them — so every hypothesis about the remaining delay was
// aimed by reading code, which is how two rounds went to a real 1.5 s tail
// that was not the thing he was feeling.
//
// The join is EXACT rather than fuzzy: every broadcast event carries the
// journal `seq` it was written with, so a paint can name the event that caused
// it and `tools/star_to_screen.py` can put the two rows side by side with no
// matching heuristic in between.
//
// Four stages, and each one is a different suspect:
//
//   journal wall time -> ws           the broadcast reaching the browser
//   ws               -> fetch start   the coalescer's own window
//   fetch start      -> fetch done    building and shipping the whole view
//   fetch done       -> paint         Preact rendering it
//
// Import-free so tests/test_ui_latency.py can drive it in node, and a module
// rather than state inside store.js because the two ends live in different
// files: store.js knows when the event arrived and when the fetch returned,
// uilog.js knows when the paint happened, and neither can see the other.

// The trigger currently in flight. ONE at a time, deliberately: a burst is
// coalesced into a single refresh (coalesce.js), so the interesting question
// is when the FIRST event of that burst arrived — that is the moment the
// player's action became knowable, and everything after it is our latency.
let pending = null;
// The last completed set of marks, waiting to be attached to the next paint.
let ready = null;

export function noteEvent(type, seq, frame, now) {
  if (pending) return;            // the burst already has an owner
  pending = { ws_type: type, ws_seq: seq ?? null, ws_frame: frame ?? null,
              ws_utc: now };
}

export function noteFetchStart(now) {
  if (!pending || pending.fetch_start_utc) return;
  pending.fetch_start_utc = now;
}

export function noteFetchDone(now) {
  if (!pending) return;
  pending.fetch_done_utc = now;
  ready = pending;
  pending = null;
}

// Consumed by the next observation that reports a CHANGE. Returns null when a
// paint had no trigger of ours behind it (a click, a resize, a re-render
// nobody asked for) — those are still logged, just without a latency claim,
// because attributing them to a stale event is how an instrument invents a
// number nobody can act on.
export function takeMarks() {
  const marks = ready;
  ready = null;
  return marks;
}

// Test seam only: no page ever needs this, and a stuck `pending` from a fetch
// that never returned would otherwise poison every later measurement.
export function resetMarks() {
  pending = null;
  ready = null;
}
