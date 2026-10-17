// Setup motion owns page swaps and disclosure geometry. Both directions move;
// outgoing content remains paintable but inert, and only one old view is kept.
import { h } from "preact";
import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { shownNext, shownStart, shownWake, stageOf } from "../setupflow.js";
import { Icon } from "./icons.js";

const html = htm.bind(h);

export function SetupSwap({ identity, direction = 1, children, className = "" }) {
  const previous = useRef({ identity, children });
  const current = useRef(null);
  const [outgoing, setOutgoing] = useState(null);
  const [height, setHeight] = useState(null);
  useLayoutEffect(() => {
    if (previous.current.identity !== identity) setOutgoing(previous.current);
    previous.current = { identity, children };
  });
  useEffect(() => {
    const timer = setTimeout(() => setOutgoing(null), 320);
    return () => clearTimeout(timer);
  }, [identity]);
  useLayoutEffect(() => {
    const node = current.current;
    if (!node) return;
    const measure = () => setHeight(node.getBoundingClientRect().height);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [identity]);
  return html`<div class=${`setup-swap ${className}`} data-direction=${direction < 0 ? "back" : "forward"}
      style=${height == null ? undefined : { height: `${height}px` }}>
    ${outgoing && html`<div class="setup-swap-out" aria-hidden="true" inert key=${`out-${outgoing.identity}`}>
      ${outgoing.children}
    </div>`}
    <div class="setup-swap-in" key=${identity} ref=${current}>${children}</div>
  </div>`;
}

export function SetupDisclosure({ label, children }) {
  const [open, setOpen] = useState(false);
  return html`<div class=${`setup-help ${open ? "is-open" : ""}`}>
    <button class="setup-help-toggle" type="button" aria-label=${label} aria-expanded=${open}
        onclick=${() => setOpen(!open)}>${label}<span class="setup-disclosure-symbol" aria-hidden="true">
          <${Icon} name=${open ? "minus" : "plus"} size=${18}/></span></button>
    <div class="setup-help-reveal" aria-hidden=${!open} inert=${!open}>
      <div><div class="setup-help-content">${children}</div></div>
    </div>
  </div>`;
}

// The install page's shown step (setupflow.js `shownNext`), derived during
// render so progress paints with the observation that proved it. Off the
// install page it follows the server, so arriving never acknowledges a stage
// nobody saw.
export function useShownStep(actual, active) {
  const machine = useRef(null);
  const [tick, wake] = useState(0);
  const now = performance.now();
  machine.current = active && machine.current
    ? shownNext(machine.current, actual, now) : shownStart(actual, now);
  const state = machine.current;
  const at = active ? shownWake(state) : null;
  useEffect(() => {
    if (at == null) return;
    const timer = setTimeout(() => wake(tick => tick + 1), Math.max(0, at - performance.now()));
    return () => clearTimeout(timer);
  }, [at, tick]);  // a timer that fires a little early schedules the rest
  return { step: state.step, stage: stageOf(state.step), acknowledging: !!state.ack };
}

// Whether the wizard is on screen, so a confirmation is never spent unseen.
// Visibility, not focus: he plays in Project64 with the trainer beside it, so
// the trainer rarely has focus, and waiting for focus left the wizard sitting
// on "Setup checked" (his report, 2026-09-17). A minimized or background tab
// reads hidden and still waits.
export function useSetupAttention() {
  const active = () => document.visibilityState !== "hidden";
  const [attentive, setAttentive] = useState(active);
  useEffect(() => {
    const refresh = () => setAttentive(active());
    document.addEventListener("visibilitychange", refresh);
    return () => document.removeEventListener("visibilitychange", refresh);
  }, []);
  return attentive;
}
