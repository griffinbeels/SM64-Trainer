// src/sm64_events/ui/mareloturnstate.js — whose turn it is to celebrate, as
// arithmetic. Import-free on purpose, the same convention as ui/climbplan.js
// and ui/components/caps.js, so node can execute it directly
// (tests/test_ui_marelo_turn.py).
//
// WHY THIS IS A MODULE AND NOT JUST THE HOOK. Every bug this logic has had was
// a STATE TRANSITION — the frame a celebration lands on, the frame the last
// banner stops climbing, the frame the overlay's own card starts climbing —
// and a transition is precisely what a hook makes unreachable from a test. It
// shipped broken twice with green source-scan tests both times:
//
//   1. gating the overlay on "is anything climbing" LOOPED FOREVER, because
//      the overlay renders a card that climbs, so mounting it made the gate
//      true, which unmounted it (2026-07-29);
//   2. the header card was handed the payload that DELIVERED the celebration
//      as its "before" state. That payload already carries the new rank, so
//      the hold was a no-op and the card climbed alongside the entity banners
//      — the exact symptom the hold was written to fix, still shipping
//      (reported again 2026-08-01, "it seems like this bug is still present").
//
// Both are one-line facts about a transition table, and both are now tests.

/** Before any payload has been seen. */
export const NO_TURN = { key: null, held: null, waited: false, ready: true,
                         unprompted: false };

/**
 * Advance the turn by one render.
 *
 *   `marelo`   the payload as it stands NOW (may carry `.celebration`)
 *   `running`  is ANY rank on screen mid-climb (rankclimb.js::climbsRunning)
 *   `graced`   has the "wait for banners to appear" window elapsed for the
 *              celebration currently pending
 *
 * Returns `{key, held, waited, ready}` — feed it back in next render.
 */
export function advanceTurn(state, { marelo, running, graced }) {
  const key = marelo && marelo.celebration ? marelo.celebration.key : null;
  const fresh = key !== state.key;
  // The before-state is the last payload seen with NOTHING pending. Capturing
  // it on the transition instead — which is what shipped — captures the very
  // payload the hold exists to withhold, since the poll that delivers a
  // celebration already carries the rank it is celebrating.
  const held = key == null ? marelo : state.held;
  // "Nothing is running" at the instant a celebration lands means NOT YET, not
  // none: the banners and the marelo payload come from the same refresh with
  // no ordering between them. `graced` is the caller's timer saying the
  // banners had their chance — without it the overlay fired immediately
  // whenever it won that race, which was the original report.
  const waited = key == null ? false
    : fresh ? !!running
    : (state.waited || !!running || !!graced);
  const ready = key == null ? true
    : fresh ? false
    // LATCHED. Once it is this celebration's turn, nothing the celebration
    // ITSELF does may take the turn away: the overlay renders a RouteRankCard
    // and that card climbs, so a plain `!running` test un-readies the moment
    // it becomes ready and the card flashes in and out forever.
    : (state.ready || (waited && !running));
  // A celebration nobody in THIS session triggered. The server holds a scope
  // rank-up until it is acked, so one earned before the app was closed — or
  // one whose ack never landed — greets the next page load unprompted:
  //
  //   "when I opened the page for the first time in my session, the MARELO
  //    display / animation triggered. This should NEVER be triggered outside
  //    of updating a PB… it feels like a bug (because I didn't trigger it)"
  //    (user, 2026-08-01)
  //
  // `held == null` at the moment a key first appears says exactly that and
  // nothing else: `held` is set on every render with nothing pending, so it is
  // null only while this client has never yet seen a quiet payload. The caller
  // acks these WITHOUT showing them, so the watermark still advances and the
  // same rank-up cannot ambush the load after that either.
  const unprompted = key == null ? false
    : fresh ? state.held == null
    : state.unprompted;
  return { key, held, waited, ready, unprompted };
}

/**
 * The payload a surface may DISPLAY this render — the held before-state while
 * a celebration is still waiting its turn, the live one otherwise.
 *
 * `held` is null only when the very first payload this hook ever saw already
 * carried a celebration (a page load with one pending). There is no
 * before-state to show then, and nothing to hold it back FROM: a fresh mount
 * snaps rather than climbing.
 */
export function displayed(state, marelo) {
  return (state.key != null && !state.ready && state.held) ? state.held : marelo;
}
