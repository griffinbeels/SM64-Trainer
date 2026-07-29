// src/sm64_events/ui/mareloturn.js — whose turn it is to celebrate.
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
// THE TRAP THIS EXISTS TO AVOID, measured 2026-07-29: gating the overlay on
// `useClimbsRunning()` alone is self-referential and loops forever. The
// overlay renders a RouteRankCard, that card runs `useRankClimb`, and a
// running climb is precisely the signal being gated on — so mounting the
// overlay made the gate true, which unmounted it, which made the gate false,
// which mounted it again. On screen: the card flashing in and out endlessly.
// `ready` is therefore LATCHED per celebration: once it is this celebration's
// turn, nothing the celebration itself does can take the turn away.
import { useEffect, useRef, useState } from "preact/hooks";
import { climbsRunning, useClimbsRunning } from "./rankclimb.js";

// How long to wait for the entity banners to REGISTER before concluding there
// are none. The banners and the marelo payload both arrive from the same
// refresh, and which lands first is not ordered — so "no climb is running" at
// the instant the celebration arrives means "not yet", not "none". Without
// this the overlay fired immediately whenever it won that race, which is the
// original report. Comfortably longer than a render pass, far shorter than
// any climb.
const BANNERS_APPEAR_MS = 450;

/**
 * `{ marelo, ready }` — the payload the header should DISPLAY, and whether
 * the scope celebration may start.
 *
 * With no celebration pending, both pass straight through: `marelo` is the
 * live value and `ready` is true, so nothing about ordinary play changes.
 */
export function useMareloTurn(marelo) {
  const celebration = marelo && marelo.celebration;
  const key = celebration ? celebration.key : null;
  const running = useClimbsRunning();

  // The payload as it was BEFORE this celebration landed — what the header
  // keeps showing while it waits. Captured on the render the key changes,
  // never after, so a later poll cannot overwrite the before-state.
  const heldRef = useRef(marelo);
  const keyRef = useRef(key);
  if (keyRef.current !== key) {
    if (key != null && keyRef.current == null) heldRef.current = marelo;
    keyRef.current = key;
  }
  if (key == null) heldRef.current = marelo;

  const [ready, setReady] = useState(true);
  const sawClimbsRef = useRef(false);

  useEffect(() => {
    if (key == null) { setReady(true); sawClimbsRef.current = false; return undefined; }
    // A fresh celebration always waits its turn again.
    setReady(false);
    sawClimbsRef.current = climbsRunning();
    // If no banner has appeared by the time this fires, there are none to
    // wait for and the turn is ours as soon as nothing is running.
    const timer = setTimeout(() => { sawClimbsRef.current = true; }, BANNERS_APPEAR_MS);
    return () => clearTimeout(timer);
  }, [key]);

  useEffect(() => {
    if (key == null) return undefined;
    if (running) { sawClimbsRef.current = true; return undefined; }
    if (!sawClimbsRef.current) return undefined;
    // LATCHED: `setReady(true)` is never undone for this key, so the
    // overlay's own climb cannot revoke its turn (see the header comment).
    setReady(true);
    return undefined;
  }, [key, running]);

  return {
    marelo: (key != null && !ready) ? heldRef.current : marelo,
    ready: key == null ? true : ready,
  };
}
