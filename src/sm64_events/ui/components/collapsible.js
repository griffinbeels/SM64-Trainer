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
import { useCallback, useLayoutEffect, useRef, useState } from "preact/hooks";
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
  useLayoutEffect(() => {
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
    // A cancelled `fill` leaves whatever it had already applied, so the inline
    // state is reset before the replacement measures anything -- otherwise the
    // new run's `scrollHeight` is read through the old run's frozen box.
    box.style.height = "";
    box.style.overflow = "";
    inner.style.opacity = "";
    inner.style.transform = "";

    // Each run stamps the direction it belongs to, so a `settle` scheduled by
    // a run that has since been replaced cannot unmount the contents of the
    // one now playing -- the other half of the rapid-toggle bug.
    // UNCONDITIONAL, and this is the whole of the "everything bleeds out of
    // the card" bug (2026-08-05). A previous version skipped the cleanup when
    // the run that scheduled it had already been replaced -- which left the
    // inline `height` frozen at a stale measurement while `overflow` was
    // cleared by the run that replaced it, so the box was SHORTER than its
    // contents and did not clip them. The rows painted straight through the
    // card and over the cards below.
    //
    // Clearing inline state can never be conditional: it is the state that
    // does not belong to any run, and whoever finishes last is right, because
    // the CSS underneath is the truth in every direction. The double-unmount
    // this guard was reaching for is handled where it belongs -- by
    // CANCELLING the previous runs above, which is what stops their `onfinish`
    // firing at all.
    const settle = () => {
      // A FINISHED `fill` KEEPS APPLYING. It stays in `getAnimations()` and
      // goes on holding the height it ended on, so "it finished" is not the
      // same as "it stopped mattering" -- cancelling is what hands the box
      // back to the stylesheet, and without it the next open measures its
      // contents through the previous run's frozen box.
      if (running.current) running.current.forEach((a) => a.cancel());
      box.style.height = "";
      box.style.overflow = "";
      inner.style.opacity = "";
      inner.style.transform = "";
      running.current = null;
      if (!wasOpen.current) setMounted(false);
    };

    if (reducedMotion() || typeof box.animate !== "function") {
      settle();
      return undefined;
    }

    const full = inner.scrollHeight;
    const plan = disclosurePlan(open, full, feedTuning());
    if (plan.durationMs <= 0) { settle(); return undefined; }

    box.style.overflow = "hidden";
    const run = box.animate(
      [{ height: `${plan.from}px` }, { height: `${plan.to}px` }],
      { duration: plan.durationMs, easing: plan.easing, fill: "both" });
    run.onfinish = settle;

    // THE CONTENTS FALL WITH THE EDGE, they are not uncovered by it. Griffin,
    // 2026-08-06, choosing this over clipping a static body: "an animation for
    // all the elements inside the panel. They should animate from top to
    // bottom, as if they're in-sync, falling as the dropdown itself falls
    // downwards. When the dropdown goes back up, it should animate in the
    // inverse direction, keeping pace with the dropdown."
    //
    // Same duration, same easing, no delay -- "keeping pace" is not a
    // similar-looking curve, it is the SAME one, and any difference shows up
    // as the contents sliding against their own container. Offsetting by the
    // full height puts the body's bottom edge on the box's bottom edge at
    // every instant, so the panel reads as one object arriving rather than a
    // window being opened over something already there.
    // THE OFFSET IS ALWAYS NEGATIVE, in both directions, and getting that
    // wrong is what he photographed frame by frame (2026-08-06: "EVERYTHING
    // INCORRECTLY DISAPPEARS... it's as if everything got offset by the entire
    // height of the open panel area by accident?" -- it was, exactly).
    //
    // The contents' BOTTOM edge rides the box's bottom edge, so the offset at
    // any moment is minus the height still to come: `-(full - current)`. Open
    // runs -full -> 0 and close runs 0 -> -full, which is the same rule read
    // in each direction and is what makes the close the inverse of the open
    // rather than its own animation.
    //
    // The previous form was `from - to`, which is -full -> 0 on the way open
    // (right, by luck) and +full -> 0 on the way closed (the contents shoved
    // DOWN by a whole panel, out of the clip, leaving only the tip of a hat
    // visible -- which is precisely what he saw).
    const offset = (height) => `translateY(${-(full - height)}px)`;
    const runs = [run, inner.animate(
      [{ transform: offset(plan.from) }, { transform: offset(plan.to) }],
      { duration: plan.durationMs, easing: plan.easing, fill: "both" })];

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
