// src/sm64_events/ui/mareloturn.js — whose turn it is to celebrate, wired up.
//
// User, 2026-07-29: "Strategy THEN star THEN marelo... While it's in the
// header waiting it shouldn't change its rank / animate ANY progress until
// everything is done. Then the absolute second the previous step finishes,
// the MARELO standard should do its animation. But it should be as if nothing
// has changed before then."
//
// TWO things have to wait, not one, and that is why this is a hook rather
// than a condition at the overlay's mount:
//
//   * the OVERLAY must not fly while the entity banners are climbing, and
//   * the HEADER CARD must not quietly climb to the new rank behind them —
//     it reads `t.marelo` directly, so the moment a new payload lands it
//     starts its own climb, which is "it still animated while the rank
//     standards were animating". Gating only the overlay left that half.
//
// Both read ONE hook, so they cannot disagree about whose turn it is: the
// card is handed the OLD payload until the turn comes, and the overlay is
// told `ready` at the same instant. When the turn arrives the card is still
// showing the before-state, which is exactly what the flight wants to lift.
//
// The DECISION lives in ui/mareloturnstate.js, which is import-free and
// node-tested — read its header for the two transitions that shipped broken
// and why a hook is the wrong place to keep them.
import { useEffect, useRef, useState } from "preact/hooks";
import { useClimbsRunning } from "./rankclimb.js";
import { NO_TURN, advanceTurn, displayed } from "./mareloturnstate.js";

// How long to wait for the entity banners to REGISTER before concluding there
// are none. The banners and the marelo payload both arrive from the same
// refresh, and which lands first is not ordered — so "no climb is running" at
// the instant the celebration arrives means "not yet", not "none". Comfortably
// longer than a render pass, far shorter than any climb.
const BANNERS_APPEAR_MS = 450;

/**
 * `{ marelo, ready }` — the payload the header should DISPLAY, and whether
 * the scope celebration may start.
 *
 * With no celebration pending, both pass straight through: `marelo` is the
 * live value and `ready` is true, so nothing about ordinary play changes.
 */
export function useMareloTurn(marelo) {
  const running = useClimbsRunning();
  const key = marelo && marelo.celebration ? marelo.celebration.key : null;
  // STATE, not a ref. The grace window is the only thing that can hand over
  // the turn when no banner ever appears — every tab but Practice — and a ref
  // it writes cannot wake the render that reads it. As a ref this left the
  // header card frozen on the before-state indefinitely.
  const [graced, setGraced] = useState(false);
  const stateRef = useRef(NO_TURN);
  // Advanced during render, because every input is either this render's own
  // props or a subscription that already re-renders on change — so what the
  // header draws is a pure function of what is on screen this frame.
  stateRef.current = advanceTurn(stateRef.current, { marelo, running, graced });

  useEffect(() => {
    setGraced(false);
    if (key == null) return undefined;
    const timer = setTimeout(() => setGraced(true), BANNERS_APPEAR_MS);
    return () => clearTimeout(timer);
  }, [key]);

  return { marelo: displayed(stateRef.current, marelo),
           ready: stateRef.current.ready,
           unprompted: stateRef.current.unprompted };
}
