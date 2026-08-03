// src/sm64_events/ui/exchange.js — the state machine that turns "the set of
// practice options changed, possibly several times in a row" into ONE visible
// event: the old set fades out, a beat passes, the new set comes up.
//
// Live report 2026-08-02: "internally we're doing some shuffling / heartbeats /
// validations, but the user should never see that: they should see their old
// options fade away, and their new options appear, no intermediate."
//
// The load-bearing property is not the fade — it is that an intermediate set is
// UNOBSERVABLE BY CONSTRUCTION rather than merely unlikely. While the old set
// is on its way out, every further change only replaces what will be adopted
// when it lands (`pendingId`); nothing renders in between. So a burst of four
// validations inside the fade window is one exchange, and the number of
// repaints stops depending on how the server happened to sequence its events.
//
// Import-free on purpose: the whole machine is drivable by node, which is how
// the "no intermediate set ever renders" claim is proved rather than asserted
// (tests/test_ui_exchange.py). The component wiring is ui/components/cellrow.js.

export const IDLE = "idle";
export const OUT = "out";
export const IN = "in";

// A set's identity is its ORDERED cell keys. NUL-joined because a key may
// contain anything a segment name can, and the one thing it cannot contain is
// a NUL — a "|" separator would let `["a|b"]` and `["a","b"]` collide, which
// reads as "nothing changed" and skips an exchange that was owed.
export const identityOf = (keys) => keys.join("\u0000");

export const initialState = (id) =>
  ({ phase: IDLE, shownId: id, pendingId: null });

/** The reducer. Pure, total, and never throws: an unknown event returns the
 *  state unchanged, because the alternative is a row wedged invisible by a
 *  typo. */
export function nextState(state, event) {
  if (!event) return state;
  if (event.type === "snap")            // reduced motion, or a first render
    return initialState(event.id);
  if (event.type === "incoming") {
    if (state.phase === OUT)            // absorb: only the DESTINATION moves
      return event.id === state.shownId
        ? { phase: OUT, shownId: state.shownId, pendingId: state.shownId }
        : { ...state, pendingId: event.id };
    if (event.id === state.shownId) return state;   // props changed, set did not
    return { phase: OUT, shownId: state.shownId, pendingId: event.id };
  }
  if (event.type === "outDone") {
    // Nothing to adopt (every change during the fade cancelled back out to
    // what was already shown) — come straight back up rather than swapping to
    // an identical set, so a self-cancelling burst costs one blink, not two.
    const target = state.pendingId === null ? state.shownId : state.pendingId;
    return { phase: IN, shownId: target, pendingId: null };
  }
  if (event.type === "inDone")
    return { phase: IDLE, shownId: state.shownId, pendingId: null };
  return state;
}

/** What the row's own box should look like in this phase. ONE element carries
 *  the fade — never a wrapper per cell, which would break every
 *  `.starrow > .starcell` child-combinator rule in the design system, and never
 *  a per-cell stagger, which he did not ask for: "ALL of cards fade away". */
export function rowStyle(phase, tuning, reducedMotion = false) {
  if (reducedMotion) return { opacity: 1, transitionMs: 0 };
  if (phase === OUT) return { opacity: 0, transitionMs: tuning.outMs };
  if (phase === IN) return { opacity: 1, transitionMs: tuning.inMs };
  return { opacity: 1, transitionMs: 0 };
}

/** How long to wait before the phase after this one. The BEAT is charged to
 *  the out phase, so the row sits empty for `gapMs` with the old cells still
 *  mounted at zero opacity — mounting the new ones early would make the swap
 *  frame the thing he sees. */
export function phaseMs(phase, tuning) {
  if (phase === OUT) return tuning.outMs + tuning.gapMs;
  if (phase === IN) return tuning.inMs;
  return 0;
}

/** The rule the whole module exists for, and the reason it is not a phase test.
 *
 *  A row paints the arriving children ONLY when their identity is the one the
 *  machine currently calls shown; otherwise it repaints the set it already had.
 *  Phrasing it as "during OUT, paint the held snapshot" left a one-frame hole
 *  big enough to be the whole bug: new children arrive, the row renders while
 *  the phase is still IDLE (a reducer cannot be dispatched during render), and
 *  the intermediate set flashes for exactly one frame before the fade starts.
 *  Anchored to `shownId` instead, there is no frame in which an unadopted set
 *  can reach the screen — which is what "no intermediate" has to mean. */
export const paintsShown = (id, state) => id === state.shownId;
