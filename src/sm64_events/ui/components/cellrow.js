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
import { h } from "preact";
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

export function CellRow({ class: className, children }) {
  const current = cells(children);
  const id = identity(current);
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

  useEffect(() => {
    if (state.phase === IDLE) return;
    const ms = phaseMs(state.phase, selectorTuning());
    const timer = setTimeout(
      () => dispatch({ type: state.phase === OUT ? "outDone" : "inDone" }),
      ms);
    return () => clearTimeout(timer);
  }, [state.phase, state.shownId]);

  const { opacity, transitionMs } = rowStyle(state.phase, selectorTuning(),
                                             reduced);
  const shown = settled ? current : held.current;
  return html`<div class=${className}
    style=${`opacity:${opacity};transition:opacity ${transitionMs}ms linear`}
    >${shown}</div>`;
}
