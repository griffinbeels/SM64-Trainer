// src/sm64_events/ui/components/closewarning.js — the warning shown when the
// app is closed while saved replays are still being made smaller.
// The desktop shell cancels that close and dispatches `sm64-close-requested`
// on the window (it only does so while work is unfinished); this opens, shows
// the jobs moving, and offers Exit anyway / Wait. The copy is the contract: the
// replays are ALREADY saved, so exiting loses nothing and Exit is not styled as
// a destructive action -- it only leaves some replays full size until the app
// next opens. Mounted at app root in app.js beside the update popup.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { send } from "../api.js";
import { exitCost, finishedWaiting, remainingJobs } from "../compression.js";
import { Modal } from "./modal.js";
import { CompressionJobs, useCompression } from "./compressionjobs.js";

const html = htm.bind(h);

export const CLOSE_REQUESTED = "sm64-close-requested";
// After "Wait", how long the last row is left showing Done before the app
// goes: long enough to read, short enough that it still feels like one act.
const FINISHED_HOLD_MS = 1200;
// The departure is over when `close-warning-out` (index.html) says so, not
// when a timer guesses: a timer started at the click unmounted the panel at
// 0.39 opacity on a busy page, which is a fade that ends in a cut. The timer
// is only the net under a page where the animation never runs.
const LEAVE_ANIMATION = "close-warning-out";
const LEAVE_FALLBACK_MS = 600;

const ALREADY_SAVED = "Your replays are already saved. SM64 Trainer makes saved replays "
  + "smaller in the background to save disk space, and it has not finished yet.";

const ALL_FINISHED = "Every saved replay has finished getting smaller. "
  + "Nothing is waiting on SM64 Trainer any more.";

function Lead({ state, mode }) {
  if (!state.loaded) return html`<div class="close-warning-lead"><p>Checking on your saved replays…</p></div>`;
  const remaining = remainingJobs(state.jobs);
  return html`<div class="close-warning-lead">
    ${state.active ? html`<p>${ALREADY_SAVED}</p>
        <p><b>${exitCost(remaining)}</b>${" "}Nothing is lost either way.</p>`
      : html`<p>${ALL_FINISHED}</p>`}
    <${WaitLine} mode=${mode} active=${state.active} />
  </div>`;
}

// ONE line, present before and after Wait is pressed: before, it says what
// Wait will do; after, that it is happening. Inserting it on the press would
// push the list he is watching down a line at the moment he acts on it.
function WaitLine({ mode, active }) {
  if (mode === "ask" && !active) return null;
  const words = mode === "exiting" ? "Closing SM64 Trainer…"
    : mode === "ask" ? "Wait keeps this open, then closes SM64 Trainer by itself when they finish."
    : active ? "SM64 Trainer will close when these finish."
    : "All finished. Closing SM64 Trainer…";
  return html`<p class=${`close-warning-wait ${mode === "ask" ? "" : "is-waiting"}`} role="status">${words}</p>`;
}

function Actions({ mode, finished, exit, wait, keep, error }) {
  const exiting = mode === "exiting";
  return html`
    ${error ? html`<span class="close-warning-error" role="alert">${error}</span>` : null}
    <button class="close-warning-keep" disabled=${exiting} onclick=${keep}>Keep using the app</button>
    <button disabled=${exiting} onclick=${exit}>
      ${exiting ? "Closing…" : finished ? "Exit" : mode === "waiting" ? "Exit now" : "Exit anyway"}</button>
    ${mode === "ask" && !finished ? html`<button class="primary-button" autofocus
        onclick=${wait}>Wait</button>` : null}`;
}

export function CloseWarning() {
  const [open, setOpen] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const [mode, setMode] = useState("ask");       // ask | waiting | exiting
  const [error, setError] = useState(null);
  const body = useRef(null);
  const state = useCompression(open);

  useEffect(() => {
    // A close asked for mid-departure brings the warning straight back.
    const onRequest = () => { setLeaving(false); setOpen(true); };
    window.addEventListener(CLOSE_REQUESTED, onRequest);
    // The shell asks before it cancels a close (desktop/closeguard.py): with
    // no listener mounted -- a page still loading, a crashed render -- nobody
    // would ever show the warning, so it must close instead of hanging open.
    window.__sm64CloseWarning = true;
    return () => {
      window.__sm64CloseWarning = false;
      window.removeEventListener(CLOSE_REQUESTED, onRequest);
    };
  }, []);

  useEffect(() => {
    const panel = leaving && body.current ? body.current.closest(".modal") : null;
    if (!panel) return undefined;
    const gone = () => { setOpen(false); setLeaving(false); };
    const onEnd = (animation) => { if (animation.animationName === LEAVE_ANIMATION) gone(); };
    panel.addEventListener("animationend", onEnd);
    const fallback = setTimeout(gone, LEAVE_FALLBACK_MS);
    return () => { panel.removeEventListener("animationend", onEnd); clearTimeout(fallback); };
  }, [leaving]);

  async function exit() {
    setMode("exiting"); setError(null);
    try {
      await send("POST", "/api/admin/shutdown");
    } catch (failure) {
      console.error(failure); // the request may drop as the server goes down
      setError("SM64 Trainer did not close. Try again.");
      setMode("ask");
    }
  }

  // "Wait" exits by itself once the list says the work is over. Leaving the
  // wait -- Keep using the app, Esc, the backdrop -- changes `mode`, and this
  // effect's cleanup is what cancels the pending exit.
  const finished = finishedWaiting(mode === "waiting", state);
  useEffect(() => {
    if (!finished) return undefined;
    const timer = setTimeout(exit, FINISHED_HOLD_MS);
    return () => clearTimeout(timer);
  }, [finished]);

  // Wait's own button leaves the footer when it is pressed. Focus goes to the
  // dialog itself, never to the next button along: a second press of Space
  // must not land on "Keep using the app" and undo the choice just made.
  function wait() {
    setMode("waiting");
    body.current?.closest(".modal")?.focus();
  }

  function keep() {
    setMode("ask"); setError(null); setLeaving(true);
  }

  if (!open || !state) return null;
  const over = state.loaded && !state.active;
  return html`<${Modal} title=${over ? "Your replays have finished" : "Still making your replays smaller"}
      description=${over ? "Nothing is waiting. You can exit now."
        : "Your replays are safe. You can exit now or let them finish."}
      icon="save" onClose=${mode === "exiting" ? undefined : keep}
      footer=${html`<${Actions} mode=${mode} finished=${over} exit=${exit}
        wait=${wait} keep=${keep} error=${error} />`}>
    <div ref=${body} class=${`close-warning ${leaving ? "is-leaving" : ""}`}>
      <${Lead} state=${state} mode=${mode} />
      <${CompressionJobs} jobs=${state.jobs} />
    </div>
  <//>`;
}
