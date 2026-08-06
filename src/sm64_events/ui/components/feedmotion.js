// src/sm64_events/ui/components/feedmotion.js
//
// The practice log is a FEED, and this is what makes it move like one.
//
// Griffin, 2026-08-05: "we need to animate (1) the newest practice entry card
// appearing, (2) the other cards moving down... It should feel like the card
// is a physical object entering the space, occupied by the other physical
// objects we're moving."
//
// THE TECHNIQUE IS FLIP, and the reason is the second half of his sentence.
// Animating the arrival alone would leave the cards below it JUMPING to their
// new positions on the same frame -- the arrival would look animated and its
// consequence would not, which is the opposite of "objects being displaced".
// So: read where every card was (First), let the browser lay out the new list
// (Last), put each moved card back where it was with a transform (Invert), and
// release them (Play). Nothing is ever positioned by hand and no layout is
// duplicated -- the DOM is always already correct, and the animation is the
// apology for having got there instantly.
//
// Measured in `useLayoutEffect`, which is the whole reason it is that hook and
// not `useEffect`: the browser must not paint the new layout before the
// inversion is applied, or the jump he is asking us to remove happens first
// and the animation plays after it.
//
// The DURATIONS live in `ui/feedtuning.js` and the plan in
// `ui/disclosure.js::feedPlan`; the active slot is read ONCE per run so a
// tuning change mid-flight cannot retime cards already in motion
// (`.claude/skills/tuning-demo`).
import { useLayoutEffect, useRef } from "preact/hooks";
import { feedPlan } from "../disclosure.js";
import { feedTuning } from "../feedtuning.js";

// Stamped on every animation this module starts, so cancelling its own
// leftovers cannot reach an animation some other component owns.
const FEED_MOTION = "sm64-feed-motion";

const reducedMotion = () => typeof matchMedia === "function"
  && matchMedia("(prefers-reduced-motion: reduce)").matches;

/**
 * Animate a keyed list as a feed.
 *
 * `containerRef` holds the element whose direct children carry
 * `data-feed-key`; `keys` is the order currently rendered. The hook never
 * re-sorts and never reads the store: the order on screen IS the order, which
 * is also what makes "further down the list" mean anything for the stagger.
 *
 * The FIRST run only records positions. A page that has just loaded has not
 * had anything arrive in it, and playing an insertion for every card at mount
 * is the "it triggered without me doing anything" reading he rejects
 * (2026-08-01, the replayed rank-up celebration).
 */
export function useFeedMotion(containerRef, keys) {
  const previous = useRef(null);
  const joined = keys.join("|");

  useLayoutEffect(() => {
    const root = containerRef.current;
    if (!root) return;

    const cards = new Map();
    root.querySelectorAll("[data-feed-key]").forEach((el) => {
      cards.set(el.getAttribute("data-feed-key"), el);
    });
    // MEASURED RELATIVE TO THE LIST, never to the viewport. This is the whole
    // of the "shuffle" he reported (2026-08-05): "as we select different stars
    // / segments... the list appears to shuffle around rather than the new one
    // appearing and bumping everything down... I think maybe it's because of
    // the way the overall containing box expands and contracts too?" -- and
    // that guess was exactly right. A viewport-relative FIRST/LAST pair
    // measures the CONTAINER's own movement too, so a card opening above the
    // list, or the page scrolling, gives EVERY card a non-zero dy and animates
    // all of them at once. Subtracting the list's own top leaves only the
    // displacement inside it, which is the only thing this is about.
    const rootTop = root.getBoundingClientRect().top;
    const now = new Map();
    cards.forEach((el, key) => {
      now.set(key, el.getBoundingClientRect().top - rootTop);
    });

    const before = previous.current;
    previous.current = now;
    if (!before) return;                       // first run: record only
    if (reducedMotion()) return;

    const arrived = [...now.keys()].filter((key) => !before.has(key));
    const shifted = [...now.keys()]
      .filter((key) => before.has(key))
      .map((key) => ({ key, dy: before.get(key) - now.get(key) }))
      .filter(({ dy }) => Math.abs(dy) > 0.5);

    // NOTHING MOVED AND NOTHING ARRIVED is the common case -- every unrelated
    // re-render, of which this page has many (a WebSocket event refetches the
    // view several times a second while a run is live). Bailing here is what
    // keeps this from being an animation that plays constantly.
    if (!arrived.length && !shifted.length) return;

    const plan = feedPlan(shifted, feedTuning());

    // A run already in flight is CANCELLED before its replacement starts.
    // These carry `fill: backwards`, so a burst of arrivals -- which this page
    // gets, since one walk through a door can change the list several times
    // inside a tenth of a second -- would otherwise stack transforms on the
    // same cards and read as exactly the scramble he described.
    cards.forEach((el) => {
      if (typeof el.getAnimations === "function") {
        el.getAnimations().forEach((animation) => {
          if (animation.id === FEED_MOTION) animation.cancel();
        });
      }
    });

    plan.shifts.forEach(({ key, dy, durationMs, easing, delayMs }) => {
      const el = cards.get(key);
      if (!el || typeof el.animate !== "function" || durationMs <= 0) return;
      el.animate(
        [{ transform: `translateY(${dy}px)` }, { transform: "translateY(0)" }],
        { duration: durationMs, delay: delayMs, easing, fill: "backwards",
          id: FEED_MOTION });
    });

    // The arrival plays LAST so it is on top of the stack it is displacing --
    // the same reason a hand entering a space is drawn over what it moves.
    arrived.forEach((key) => {
      const el = cards.get(key);
      if (!el || typeof el.animate !== "function" || plan.enter.durationMs <= 0) return;
      el.animate(
        [{ transform: `translateY(${-plan.enter.risePx}px)` },
         { transform: "translateY(0)" }],
        { duration: plan.enter.durationMs, easing: plan.enter.easing,
          fill: "backwards", id: FEED_MOTION });
      if (plan.enter.fadeMs > 0) {
        el.animate([{ opacity: 0 }, { opacity: 1 }],
                   { duration: plan.enter.fadeMs, easing: plan.enter.easing,
                     fill: "backwards", id: FEED_MOTION });
      }
    });
  }, [joined]);
}
