// src/sm64_events/ui/components/collapsible.js
//
// Card collapse: one primitive, every card.
//
// The user asked for this on all layouts, not just narrow ones ("we should
// just do this by default on all layouts"), because at 900x1180 the practice
// log is below two tall cards and unreachable without scrolling.
//
// Why this is a plain `height` animation and not the usual grid-0fr trick:
// every card it applies to ALREADY has a hard pixel height, because the user
// streams them in OBS and a card that reflowed mid-run would shift their
// capture. Both ends of the animation are therefore real numbers and `height`
// interpolates between them directly. The OBS contract survives collapse for
// the same reason -- a collapsed card is still a fixed height, just a smaller
// one, and it only changes when the user asks it to.
//
// State is an open-SET inverted: we store the COLLAPSED ids, so "nothing
// stored" means every card is open, which is the right first-run state. Same
// shape as grouplist.js's open-set, opposite polarity, for the same reason --
// the default must need no entry.
import { useCallback, useEffect, useLayoutEffect, useRef, useState }
  from "preact/hooks";
import { h } from "preact";
import htm from "htm";
import { Icon } from "./icons.js";
import { disclosurePlan } from "../disclosure.js";
import { feedTuning } from "../feedtuning.js";

const html = htm.bind(h);

const STORE_KEY = "sm64.cardsCollapsed";

function readCollapsed() {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    return new Set(raw ? JSON.parse(raw) : []);
  } catch {
    return new Set();          // private mode, quota, corrupt JSON — open is safe
  }
}

/**
 * `[collapsed, toggle]` for one card id, persisted across reloads.
 *
 * Reads storage per hook rather than through a shared store: there are five
 * of these on a page, they change only on a click, and a context provider
 * would be more moving parts than the thing it coordinates.
 */
export function useCollapsed(cardId) {
  const [collapsed, setCollapsed] = useState(() => readCollapsed().has(cardId));
  const toggle = useCallback(() => {
    const next = readCollapsed();
    if (next.has(cardId)) next.delete(cardId);
    else next.add(cardId);
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify([...next]));
    } catch {
      /* not persisting is survivable; refusing to collapse is not */
    }
    setCollapsed(next.has(cardId));
  }, [cardId]);
  return [collapsed, toggle];
}

/**
 * The affordance. Lives in the card's own heading, and is the whole heading's
 * business: `aria-expanded` carries the state, so the chevron is decoration
 * and screen readers do not need it described.
 */
export function CollapseToggle({ collapsed, toggle, label }) {
  return html`<button type="button" class="card-collapse"
      aria-expanded=${collapsed ? "false" : "true"}
      title=${`${collapsed ? "Expand" : "Collapse"} ${label}`}
      onclick=${toggle}>
    <${Icon} name="chevron" size=${18} />
  </button>`;
}

/** The class a collapsible card wears; keeps the string in one place. */
export function cardClass(collapsed) {
  return collapsed ? "is-collapsed" : "";
}

// ---------------------------------------------------------------------------
// THE OTHER SHAPE OF OPENING, and it lives here so there is one door for
// "how things open and close" rather than two modules with the same name in
// different words.
//
// Everything above collapses a card that ALREADY HAS A HARD PIXEL HEIGHT (the
// OBS contract, see this file's own header), so CSS interpolates it directly
// and no measurement is involved. `Disclose` is for the other kind: a region
// whose open height is whatever its contents happen to need -- a practice-log
// card's body, the Rank standards dropdown. Griffin asked for exactly this
// generality (2026-08-05): "I think we may need a general way to animate drop
// downs opening like this with best practices?"
//
// What makes it correct rather than merely animated:
//
//   * It animates a MEASURED height, never `auto`. `height: auto` has never
//     been interpolable and neither is `display` -- the two failure modes
//     `.claude/rules/acceptance.md` records as "lengthening the duration does
//     nothing".
//   * It GETS OUT OF THE WAY when it lands. A permanent inline height would
//     freeze the box at whatever it measured, so contents that grow later (a
//     new attempt row) would clip; `overflow: hidden` is cleared for the same
//     reason, or a focus ring or a native dropdown popup would be cut off by a
//     box that has stopped moving.
//   * It does NOT animate on mount. A card that arrives already open must not
//     play an open -- the feed's own arrival is what says it appeared, and two
//     animations for one event is the "several things happening near each
//     other" reading he has rejected before.
//   * `prefers-reduced-motion: reduce` snaps, the same rule `ui/useTween.js`
//     applies to every number on screen.
//
// The DURATIONS are not decided here: `ui/disclosure.js::disclosurePlan` owns
// that, off `ui/feedtuning.js`, and the active slot is read ONCE per run so a
// tuning change mid-flight cannot retime an animation already playing
// (`.claude/skills/tuning-demo`).
export function Disclose({ open, className = "", children }) {
  const boxRef = useRef(null);
  const innerRef = useRef(null);
  const wasOpen = useRef(open);
  const running = useRef(null);
  // Contents stay mounted for the whole CLOSING run and are dropped after --
  // unmounting them on the first frame leaves nothing to measure, which is the
  // same "no interpolable value" trap wearing a different coat.
  const [mounted, setMounted] = useState(open);

  useLayoutEffect(() => { if (open) setMounted(true); }, [open]);

  // GATED ON `mounted`, not only on `open`, and that ordering is the whole
  // correctness of the open direction. `setMounted(true)` above happens in a
  // LAYOUT effect, so on the commit where `open` first turns true the contents
  // are not in the DOM yet -- an animator running here would measure an empty
  // box, animate 0 -> 0, and produce a real Web Animation that moves nothing.
  // Found by driving the page (tests/test_tunefeed_page_plays.py): the box
  // reported one running animation and a height of zero at every sample.
  useEffect(() => {
    if (open && !mounted) return;                      // contents not in yet
    if (open === wasOpen.current) return;              // never on mount
    wasOpen.current = open;
    const box = boxRef.current;
    const inner = innerRef.current;
    if (!box || !inner) { if (!open) setMounted(false); return undefined; }

    // CANCEL BOTH, not just the box. The inner opacity run also carries
    // `fill: both`, so a toggle that arrives mid-flight used to leave the
    // previous fade holding the contents at its own frozen opacity while a new
    // one started on top of it -- two fills fighting over one element, which
    // is the flash he reported: "the text inside briefly FLASHES / renders
    // super weird... it's as if it starts overlapping with everything else"
    // (2026-08-05). Anything with `fill` MUST be cancelled by whoever replaces
    // it; there is no such thing as a stale animation that quietly stops
    // mattering.
    if (running.current) {
      running.current.forEach((animation) => animation.cancel());
      running.current = null;
    }

    // Each run stamps the direction it belongs to, so a `settle` scheduled by
    // a run that has since been replaced cannot unmount the contents of the
    // one now playing -- the other half of the rapid-toggle bug.
    const era = open;
    const settle = () => {
      if (wasOpen.current !== era) return;
      box.style.height = "";
      box.style.overflow = "";
      inner.style.opacity = "";
      running.current = null;
      if (!open) setMounted(false);
    };

    if (reducedMotion() || typeof box.animate !== "function") {
      settle();
      return undefined;
    }

    const plan = disclosurePlan(open, inner.scrollHeight, feedTuning());
    if (plan.durationMs <= 0) { settle(); return undefined; }

    box.style.overflow = "hidden";
    const run = box.animate(
      [{ height: `${plan.from}px` }, { height: `${plan.to}px` }],
      { duration: plan.durationMs, easing: plan.easing, fill: "both" });
    run.onfinish = settle;
    const runs = [run];

    if (plan.contentFadeMs > 0) {
      runs.push(inner.animate([{ opacity: 0 }, { opacity: 1 }], {
        duration: plan.contentFadeMs, delay: plan.contentDelayMs,
        easing: plan.easing, fill: "both",
      }));
    }
    running.current = runs;
    return () => {
      if (running.current) running.current.forEach((a) => a.cancel());
      running.current = null;
    };
  }, [open, mounted]);

  return html`<div class=${`disclose ${className}`} ref=${boxRef}>
    <div class="disclose-inner" ref=${innerRef}>${mounted ? children : null}</div>
  </div>`;
}

const reducedMotion = () => typeof matchMedia === "function"
  && matchMedia("(prefers-reduced-motion: reduce)").matches;
