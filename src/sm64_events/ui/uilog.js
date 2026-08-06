// src/sm64_events/ui/uilog.js
// Records what the practice page actually PAINTED, so a live report about a
// cell that "was just there a couple frames ago" can be read back instead of
// guessed at from a screenshot. Storage, and why this is not a journaled
// event, are in core/uilog.py's docstring — read that first.
//
// IT READS THE DOM ON PURPOSE. Every other option here logs a MODEL of what
// we believe is on screen, and the belief is exactly what is in question
// whenever one of these reports arrives: the two reports that produced this
// module were both "the UI showed something the state says it should not
// have". Reading the rendered tree back can only ever agree with the human's
// eyes. The cost is that a class rename silently empties the log — a SILENT
// FALLBACK, the worst failure an instrument has — so every selector below is
// cross-checked against the components' own source by
// tests/test_ui_log_selectors.py.
//
// It also means there are NO changes in the components being observed. That
// is not incidental: an instrument that requires its subject to cooperate is
// one more thing to keep in step, and the subject here is several banner row
// modes and the practice log's own entity cards.
import { useEffect, useRef } from "preact/hooks";
import { send } from "./api.js";
import { takeMarks } from "./latency.js";

// ---------------------------------------------------------------------------
// The readers. Pure functions of a root element, so the only thing they can
// get wrong is a selector — which is the thing the test pins.
// ---------------------------------------------------------------------------

const text = (el) => (el ? (el.textContent || "").trim() : "");

// A cell's identity as the human sees it: the name under the art, plus
// whether it is the highlighted one. `.starcell` / `.starname` /
// `.active-star` are PracticeCell's own classes, and RedsCell's hand-rolled
// cell (the standing rule-11 exception on that surface) uses the same three,
// so both are covered by one query rather than by knowing which row rendered.
export function readSelector(root) {
  const card = root.querySelector(".stagebanner");
  if (!card) return null;
  const head = card.querySelector(".shead");
  return {
    surface: "selector",
    title: text(head && head.querySelector("b")),
    note: text(head && head.querySelector(".meta")),
    cells: Array.from(card.querySelectorAll(".starcell")).map((cell) => ({
      name: text(cell.querySelector(".starname")),
      active: cell.classList.contains("active-star"),
    })),
  };
}

// EVERY currently-ACTIVE `.log-card` on the page in ONE record, in DOM order
// -- typically zero or one (repointed from the deleted `.objective-card`,
// amendment A8: the Active Target card is gone, and the log's own card now
// carries the SAME per-card facts -- name, live strategy, mid-movement step
// -- that card used to). The LIST is still the observation, not one record
// per card: a card losing its `.log-card-active` highlight is exactly the
// kind of thing a report is about, and one-record-per-card could only
// express that as an absence, which is not something an append-only log can
// write down.
//
// Two fields have been RETIRED here, both for the same reason and both when
// the thing they read stopped being drawn. Reading a class nothing renders is
// exactly the SILENT FALLBACK this module's own header warns about: the field
// reports empty forever, and empty is indistinguishable from "nothing was on
// screen", which is the one answer this log exists to give.
//   * `state` (2026-08-04) -- the Ready/Running word off `.log-card-state`.
//     The gold `.log-card-active` highlight says "this is what you're
//     practicing" on its own.
//   * `step` (2026-08-06) -- the armed row off `.seg-waiting`, deleted from
//     the card by Griffin ("we should just remove the step indicator entirely
//     from the display here"). What the RUN is doing is still answerable, just
//     not from the screen: `armed_detail` is on every section in
//     `/api/session`, and `tools/what_happened.py` reads the journal side.
export function readTargets(root) {
  return {
    surface: "target",
    cards: Array.from(root.querySelectorAll(".log-card.log-card-active")).map((card) => ({
      context: text(card.querySelector(".log-card-context")),
      name: text(card.querySelector(".log-card-name")),
      strat: text(card.querySelector(".log-card-strat-picker select")
                  || card.querySelector(".log-card-strat")),
    })),
  };
}

// The PRACTICE LOG itself — the rows he watches for, and until 2026-08-04 the
// one surface on this page nothing observed. Every latency report so far has
// been about "the entry appearing", and the instrument could see the selector
// and the card headers but not the entry, so the end-to-end gap he asked about
// (*"the timegap between detecting the xcam… and when we actually display
// it"*) could not be computed at all.
//
// The NEWEST rows rather than all of them: a log can hold hundreds and this
// file is appended to on every change, so what is recorded is the head of each
// card's table plus how many rows it has. A new entry moves both.
// It reads `.log-card`, NOT `.objective-card`. The first version read the
// tables INSIDE the objective card, where there are none — the log is its own
// `<section>` two cards below it — so `readLogs` returned a well-formed record
// with an empty row list, every time, and the log filled with everything
// except the one thing it had just been added for. Nothing errored; the
// end-to-end report simply said it had joined nothing (2026-08-04, first live
// run: *"I definitely just did a ton of stars, so… seems like that tool didn't
// work"*). SILENT FALLBACK, through the nesting rather than through a class
// name — which is why `tests/test_ui_log_selectors.py` cannot catch it: both
// classes really are rendered, just not inside one another. The guard for THIS
// is the render test, which now requires a real page to produce a non-empty
// row list.
//
// `.log-card` (practicelog.js) is per-ENTITY here, not per-kind: the page-
// level practice log this branch shipped merges stars and segments into one
// recency-ordered list, one card per practiced thing, so `name` is that
// entity's own display name rather than a fixed "Recent attempts" heading —
// a genuine identity a class-rename report can use, where the card it
// replaced could not.
const LOG_ROWS = 3;

export function readLogs(root) {
  return {
    surface: "log",
    logs: Array.from(root.querySelectorAll(".log-card")).map((card) => {
      const rows = Array.from(card.querySelectorAll(".attempt-table tr"));
      return {
        name: text(card.querySelector(".log-card-name")),
        rows: rows.slice(0, LOG_ROWS)
          .map((row) => text(row.querySelector(".attempt-result"))),
        total: rows.length,
      };
    }),
  };
}

// ---------------------------------------------------------------------------
// One ordered channel, shared by every surface.
// ---------------------------------------------------------------------------
// Serialised deliberately. Posts are fired from a render effect, so a level
// load produces a burst; letting them race would scramble the ORDER, and
// order is the entire question a "it lingered, then went away" report asks.
// Nothing is dropped or debounced for the same reason — a state that lasted
// three frames is the evidence, not noise.
const queue = [];
let inFlight = false;

// A POST THAT NEVER SETTLES MUST NOT WEDGE THE CHANNEL (2026-08-02). The
// serialised queue had no timeout, so one request left hanging — a server
// restarted mid-flight is the ordinary way to get one — left `inFlight` true
// for the rest of the page's life and the log simply stopped, silently, at
// 23:02. That is the SILENT-INSTRUMENT failure this module's own header calls
// the worst one it has, arriving through the delivery path instead of the
// reader. Measured: 701 records, then nothing, while the page kept working.
//
// So every post is bounded and the slot is released unconditionally. A
// dropped observation is an acceptable loss; a dead channel is not, because
// its silence reads as "nothing was on screen".
const POST_TIMEOUT_MS = 5000;

function flush() {
  if (inFlight || !queue.length) return;
  inFlight = true;
  const body = queue.shift();
  let released = false;
  const release = () => {
    if (released) return;
    released = true;
    inFlight = false;
    flush();
  };
  setTimeout(release, POST_TIMEOUT_MS);
  send("POST", "/api/uilog", body)
    .catch(() => {})            // an instrument may never break its subject
    .then(release, release);
}

export function postObservation(body, marks) {
  // A bounded backlog: if the server is gone the queue must not grow without
  // limit for the rest of the session.
  if (queue.length > 200) queue.shift();
  // `client_utc` IS the paint time — this is called from a render effect, so
  // it runs after Preact has committed. `marks`, when present, names the
  // WebSocket event that caused this paint and when each stage between them
  // happened (ui/latency.js).
  queue.push({ ...body, client_utc: new Date().toISOString(),
               ...(marks ? { marks } : {}) });
  flush();
}

// ---------------------------------------------------------------------------
// The hook. ONE call site (components/practice.js), which is what keeps this
// from becoming four observers that each see part of the page.
// ---------------------------------------------------------------------------
// No dependency array: it runs after EVERY render and compares the snapshot
// it just read against the last one it sent, so a change is recorded whatever
// caused it — a WebSocket event, a click, or a re-render nobody asked for.
// Deduping on the rendered snapshot rather than on props is the point: two
// different states that paint identically are not a change the human saw.
export function useUiLog(rootRef) {
  const sent = useRef(new Map());
  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    // Claimed UNCONDITIONALLY by the first render pass after the fetch, and
    // shared by whichever surfaces changed in it — they were painted by the
    // same commit, so charging the latency to one and not the others would
    // make the number depend on which surface happened to be compared first.
    //
    // Unconditionally is the load-bearing word. Taking them only when
    // something changed leaves an unclaimed set sitting in latency.js until
    // some LATER, unrelated paint picks it up, and the render stage then
    // silently absorbs the gap in between: the first live run of
    // `tools/star_to_screen.py` reported a 17-SECOND render for a grab, which
    // was really "nothing visible changed for 17 seconds, then something did".
    // A render that painted nothing has no paint time, and dropping the marks
    // is the honest answer to that.
    const marks = takeMarks();
    [readSelector(root), readTargets(root), readLogs(root)].forEach((snap) => {
      if (!snap) return;
      const key = JSON.stringify(snap);
      if (sent.current.get(snap.surface) === key) return;
      sent.current.set(snap.surface, key);
      postObservation(snap, marks);
    });
  });
}
