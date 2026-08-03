// src/sm64_events/ui/components/cellrow.js — the ONE door every row of
// practice options is drawn through, so the set can never change on screen
// without the exchange (ui/exchange.js) mediating it.
//
// Live report 2026-08-02: "when we invalidate / add / remove cards from the menu
// here, it feels more like a bug / error than intentional… we need a better
// mechanism in place here / process for updating the displayed stars +
// segments." Every selector row used to render its cells into a bare
// `<div class="starrow">`, so each of the three or four set changes a single
// walk through a door produces was its own repaint.
//
// Why a component and not a CSS class: the fade is the easy half. The half that
// answers his "no intermediate" is holding the OLD children mounted while newer
// ones arrive, which only something with state can do. `tests/test_ui_row_
// exchange.py` fails if a row goes back to rendering its own div — a second door
// here would look completely correct and quietly reintroduce the flicker in one
// row while the others stayed smooth.
import { Fragment, h } from "preact";
import { useEffect, useReducer, useRef } from "preact/hooks";
import htm from "htm";

import { IDLE, OUT, identityOf, initialState, nextState, paintsShown, phaseMs,
         rowStyle } from "../exchange.js";
import { selectorTuning } from "../selectortuning.js";
import { prefersReducedMotion } from "../useTween.js";

const html = htm.bind(h);

// Rows pass `${cells.map(...)}` beside `${extras}`, so children arrive as a
// ragged nest of arrays with holes where a conditional cell said null. Flatten
// and drop the holes; KEEP a keyless cell (the Bowser row's Reds cell is one)
// and let its position speak for it — dropping the unkeyed ones was the first
// version of this and it deleted that cell from the row outright.
const cells = (children) => [children].flat(Infinity).filter(Boolean);

// A keyless cell contributes its INDEX, so the set's identity still changes
// when it appears or leaves, and does NOT change when only its props do — which
// is the whole distinction the exchange turns on (a mode toggle inside one cell
// is not a new set of options).
const identity = (list) =>
  identityOf(list.map((child, at) =>
    (child.key == null ? `@${at}` : String(child.key))));

/** ONE row of cells: the identity is the cells themselves.
 *
 *  Use this when the thing that changes is WHICH options are offered inside a
 *  surface that stays put. */
export const CellRow = ({ class: className, children }) =>
  html`<${Exchanged} className=${className}
    identity=${identity(cells(children))}>${children}<//>`;

/** A whole SURFACE swapping for a different one — the empty state becoming a
 *  course's stars, a course row becoming the castle's movements.
 *
 *  Live report 2026-08-02 (second round on this feature): "it doesn't fire in
 *  *all* situations when it should. For example, if there previously were no
 *  options available, but I transition to a stage with options, I would expect
 *  the animation to happen (right now it incorrectly cuts). In all circumstances
 *  where we change this display, it should animate in / out."
 *
 *  Why `CellRow` alone could not cover it: those swaps replace the whole card,
 *  and a row component that unmounts takes its exchange state with it — the
 *  replacement mounts fresh and idle, i.e. it cuts. So the surface-level
 *  exchange lives ABOVE the swap, in `StageBanner`, which never unmounts, and
 *  the identity has to be handed to it rather than read off the children (the
 *  children here are one card, and a card carries no key).
 *
 *  The two nest without double-fading: a surface swap remounts the inner
 *  `CellRow`, which therefore starts idle at full opacity while this one does
 *  the fade; a cell-set change leaves this identity untouched, so the inner one
 *  does it. Each granularity has exactly one owner. */
export const SurfaceExchange = ({ class: className, identity: id, children }) =>
  html`<${Exchanged} className=${className} identity=${id}>${children}<//>`;

// The machine, wearing whichever identity its caller decided on. A real
// component rather than a helper both of the above call: its hooks then belong
// to ONE stable instance per surface, which is exactly the state that has to
// survive the children swapping underneath it.
function Exchanged({ className, identity: id, children }) {
  const current = cells(children);
  const [state, dispatch] = useReducer(nextState, id, initialState);
  // The last children whose identity the machine had adopted. Written during
  // RENDER, not in an effect: an effect runs a frame too late, and that frame
  // is the flash. Safe to write here because it is the very value being
  // painted on this pass — nothing derives a render from it changing.
  const held = useRef(current);
  const reduced = prefersReducedMotion();
  const settled = paintsShown(id, state);
  if (settled) held.current = current;

  useEffect(() => {
    dispatch(reduced ? { type: "snap", id } : { type: "incoming", id });
  }, [id, reduced]);

  // `state.absorbed` is a dependency on purpose: a change arriving mid-fade
  // RE-ARMS this wait, which is how the invisible window stays open until the
  // answers stop arriving.
  useEffect(() => {
    if (state.phase === IDLE) return;
    const ms = phaseMs(state.phase, selectorTuning(), state.absorbed);
    const timer = setTimeout(
      () => dispatch({ type: state.phase === OUT ? "outDone" : "inDone" }),
      ms);
    return () => clearTimeout(timer);
  }, [state.phase, state.shownId, state.absorbed]);

  const { opacity, transitionMs } = rowStyle(state.phase, selectorTuning(),
                                             reduced);
  const shown = settled ? current : held.current;
  // Keyed on what is being PAINTED, which makes adopting a new identity remount
  // everything under it — and that is a correctness rule, not a tidy-up.
  //
  // Live report 2026-08-02: "when swapping between courses, it briefly flashes
  // the previous course's stars and then flashes again… there should not be a
  // flicker… it should only trigger ONE animation." Two courses use the SAME row
  // component, so Preact patched it instead of unmounting it, the inner
  // `CellRow` survived, and it ran its own cell-set exchange while this one was
  // still fading the surface in. Two owners, one event, two flashes. A child
  // that cannot survive an adoption cannot animate across one.
  //
  // Keyed on `state.shownId` and NOT on the incoming id: during the fade the
  // key must not move, or the outgoing content would be torn down and rebuilt
  // mid-fade — the very frame this whole mechanism exists to hide.
  return html`<div class=${className}
    style=${`opacity:${opacity};transition:opacity ${transitionMs}ms linear`}
    ><${Fragment} key=${state.shownId}>${shown}<//></div>`;
}
