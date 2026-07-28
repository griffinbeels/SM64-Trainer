// src/sm64_events/ui/components/marelocelebrate.js — the OVERALL rank-up.
//
// User, 2026-07-28: "the rank at the top of the screen (where it says capless
// 3) should be getting the same type of celebration effects as the main rank
// standards below. It should maybe even animate from its current position to
// overlay very big in the center of the screen first, and THEN do the
// celebratory animation, and then animate back to its normal position."
//
// So this file owns the FLIGHT and nothing else. Everything that happens at
// the centre is ui/rankclimb.js walking ui/celebrations.js — the same
// registry, the same beats, the same digit reel and wing grow and tier burst
// the star banners run, amplified by one tunable. "Same type of celebration
// effects" is structural here, not a resemblance: there is no second set of
// effects to keep in step, and adding one is still a single registry entry.
//
// It replaces TWO hand-rolled overlays (celebrate.js's fill->flip->hold
// TierRankUp and the DivisionRankUp top banner), which the file itself had
// flagged as a KNOWN DEVIATION since 2026-07-26.
//
// The thing that flies is the REAL card (components/marelo.js), parked at the
// BEFORE rank with its dropdown suppressed — not a lookalike that would drift
// from it the first time the card changed.
import { h } from "preact";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "preact/hooks";
import htm from "htm";
import { send } from "../api.js";
import { RouteRankCard } from "./marelo.js";
import { rankColor } from "./caps.js";
import { celebrationsEnabled } from "../celebrations.js";
import { tuning } from "../climbtuning.js";
import { mareloTuning } from "../marelotuning.js";
import { prefersReducedMotion } from "../useTween.js";

const html = htm.bind(h);

// A tier crossing is the rare event and must outrank the common one — but as
// an INTENSITY, not as a second component. These are the climb's own knobs,
// multiplied for this surface only.
const AMPLIFIED = ["levelFlashTier", "levelFlashDivision", "shakeAmplitude",
                   "burstOvershoot"];

function celebrationTuning(tune) {
  const climb = { ...tuning() };
  for (const key of AMPLIFIED) climb[key] = climb[key] * tune.tierAmplify;
  return climb;
}

async function ackScope(scopeId, key, onDone) {
  try { await send("POST", "/api/marelo/ack", { scope: scopeId, key }); }
  finally { onDone(); }
}

// "out" -> "climb" -> "hold" -> "back". The card is at the BEFORE rank until
// `climb`, which is what gives the level-up something to animate FROM (user,
// 2026-07-25: "I didn't see us animate from BEFORE -> AFTER obviously").
export function MareloCelebration({ celebration, scopeId, marelo, routes,
                                    activeRouteId, onDone }) {
  const tune = mareloTuning();
  const isTierUp = celebration && celebration.from.tier !== celebration.to.tier;
  // ONE named difference between a division-up and a tier-up.
  const scale = isTierUp ? 1 : tune.divisionScale;
  const [phase, setPhase] = useState("out");
  const [origin, setOrigin] = useState(null);
  // Whether the card is VISUALLY at the centre. Kept apart from `phase`
  // (2026-07-28): a CSS `transition` only has something to interpolate FROM
  // once the element has already been PAINTED with a prior value, and this
  // element does not exist in the DOM at all until `origin` is measured on
  // mount -- so if its very first paint were already the lifted transform
  // (as an earlier draft had it, deriving `lifted` straight from `phase`),
  // there would be no "before" for the browser to animate from and the card
  // would simply materialise at the centre with no flight (measured: 100% of
  // the travel landed in the first sampled frame). `lifted` starts false,
  // paints at the origin for one real frame, THEN flips true -- the same
  // double-rAF trick ui/tune.js's own play() uses for exactly this reason.
  const [lifted, setLifted] = useState(false);
  const cardRef = useRef(null);

  // `tuning()`/`mareloTuning()` both return a STABLE object reference across
  // ordinary re-renders (only SAVE / the inspector's Play button ever
  // reassigns them) — but `celebrationTuning` builds a brand-new object every
  // call via spread, and this component re-renders on every phase change AND
  // on every unrelated store update (the app root re-renders on any /api/
  // marelo poll while a celebration is showing). Without memoizing, the
  // freshly-built object's IDENTITY would change on every one of those
  // renders, and since it rides straight through to useRankClimb's effect
  // dependency array (rankclimb.js's `tuneOverride`), that would restart the
  // whole climb on every unrelated re-render instead of letting it run.
  const climbTune = useMemo(() => celebrationTuning(tune), [tune]);

  // Measure the LIVE header card before the clone is placed — this is the
  // FIRST half of a FLIP, and it must run before paint or the clone appears
  // at the centre for one frame and then jumps home.
  useLayoutEffect(() => {
    const slot = document.querySelector(".marelo-slot");
    if (!slot) { setOrigin(null); return undefined; }
    const box = slot.getBoundingClientRect();
    setOrigin({ left: box.left, top: box.top,
                width: box.width, height: box.height });
    setLifted(false);
    // The header card is hidden for the duration rather than left showing a
    // second copy of itself. It also must not climb: the overlay IS its
    // climb, and two things celebrating one event is the mistake the deleted
    // entity toasts made.
    slot.classList.add("is-celebrating");
    // Double rAF: the FIRST callback runs after the browser has committed the
    // at-origin paint (`lifted` is still false at that point); only the
    // SECOND flips it, which is what guarantees a real prior value for the
    // CSS transition to leave FROM rather than mounting already-arrived.
    let raf = requestAnimationFrame(() => {
      raf = requestAnimationFrame(() => setLifted(true));
    });
    return () => {
      cancelAnimationFrame(raf);
      slot.classList.remove("is-celebrating");
    };
  }, [celebration && celebration.key]);

  // The beats. Each one schedules the next; the whole sequence restarts on a
  // fresh celebration.key (a stable primitive — the celebration OBJECT is a
  // new identity on every /api/marelo refetch, so keying on it would restart
  // the sequence on every poll).
  useEffect(() => {
    if (phase !== "out") return undefined;
    const timer = setTimeout(() => setPhase("climb"),
                             tune.flyOutMs * scale + tune.beforeHoldMs);
    return () => clearTimeout(timer);
  }, [celebration && celebration.key, phase]);

  useEffect(() => {
    if (phase !== "climb") return undefined;
    const timer = setTimeout(() => setPhase("hold"), tune.holdMs * scale);
    return () => clearTimeout(timer);
  }, [celebration && celebration.key, phase]);

  useEffect(() => {
    if (phase !== "hold") return undefined;
    const timer = setTimeout(() => setPhase("back"), 0);
    return () => clearTimeout(timer);
  }, [celebration && celebration.key, phase]);

  useEffect(() => {
    if (phase !== "back") return undefined;
    // No double-rAF needed here: the card already exists in the DOM with a
    // real painted "lifted" value from the flight out, so this is an
    // ordinary value change on an EXISTING element and the CSS transition
    // has a genuine prior state to interpolate from.
    setLifted(false);
    const timer = setTimeout(() => ackScope(scopeId, celebration.key, onDone),
                             tune.flyBackMs * scale);
    return () => clearTimeout(timer);
  }, [celebration && celebration.key, phase]);

  if (!celebration || origin == null) return null;

  const atCentre = phase === "climb" || phase === "hold";
  const shown = atCentre ? celebration.to : celebration.from;
  // The card is handed a rank, so the climb inside it runs from the one it was
  // showing to the one it is given. `identity` never changes across the
  // sequence — a change would make it SNAP, which is exactly what we do not
  // want here (ui/rankclimb.js's identity gate).
  const rank = { tier: shown.tier, division: shown.division,
                 fill: atCentre ? 1 : 0 };

  // The FLIP's last half. One transform on one element: no layout, and the
  // header's four-column grid cannot reflow behind it (the OBS rule).
  const centreLeft = window.innerWidth / 2 - origin.width / 2;
  const centreTop = window.innerHeight / 2 - origin.height / 2;
  const transform = lifted
    ? `translate(${(centreLeft - origin.left).toFixed(1)}px,`
      + ` ${(centreTop - origin.top).toFixed(1)}px)`
      + ` scale(${(1 + (tune.centreScale - 1) * scale).toFixed(3)})`
    : "translate(0px, 0px) scale(1)";
  const flightMs = (phase === "back" ? tune.flyBackMs : tune.flyOutMs) * scale;

  // Shared custom properties live on the WRAPPER, never the card: the
  // backdrop (`.marelo-celebrate`) reads --fly-ms/--fly-ease/--backdrop-*/
  // --climb-color too, and a CSS custom property only flows DOWN the DOM
  // tree -- declaring them on the card alone (as an earlier draft did) would
  // leave the backdrop reading nothing but its own hardcoded fallbacks,
  // which is exactly the "the background gradient disappears" bug the climb
  // colour comment below is guarding against. The card still sees every one
  // of these through ordinary inheritance, so nothing is declared twice.
  const wrapperStyle = [
    // ONE transition declaration on the card reads --fly-ms/--fly-ease; set
    // here so its duration can be tuned. The curve's y1 is 0 and its y2 is 1
    // by construction, which is what makes it start from rest and come to
    // rest rather than hop and stop dead.
    `--fly-ms:${prefersReducedMotion() ? 0 : flightMs.toFixed(0)}ms`,
    `--fly-ease:cubic-bezier(${tune.flyEaseIn},0,${tune.flyEaseOut},1)`,
    `--backdrop-alpha:${tune.backdropOpacity}`,
    `--backdrop-tint:${(tune.backdropTint * 100).toFixed(1)}%`,
    `--shake-px:${tune.shakePx}px`,
    // The backdrop reads the CLIMB's own colour variable, so it cross-fades
    // with the tier instead of vanishing: "when ranking up, the background
    // gradient disappears? this is wrong ... all the colors should animate
    // from the original coloring to the new coloring" (user, 2026-07-27).
    `--climb-color:${rankColor(shown.tier)}`,
  ].join(";");
  const cardStyle = [
    `left:${origin.left}px`, `top:${origin.top}px`,
    `width:${origin.width}px`, `height:${origin.height}px`,
    `transform:${transform}`,
  ].join(";");

  return html`<div class=${`marelo-celebrate${lifted ? " is-lifted" : ""}`}
      role="status" style=${wrapperStyle}>
    <div ref=${cardRef} class="marelo-celebrate-card" style=${cardStyle}
        onclick=${() => setPhase("back")}>
      <${RouteRankCard} marelo=${marelo} routes=${routes}
        activeRouteId=${activeRouteId} interactive=${false}
        rank=${rank} identity="marelo-celebration"
        tune=${climbTune} />
    </div>
  </div>`;
}

// Mounted at the app ROOT (app.js), not inside the Rank tab, so a rank-up
// earned while on Practice still celebrates (rule 10 parity). With the
// "Celebrate rank-ups" pref off the celebration is acked WITHOUT being shown,
// so the watermark does not re-fire on the next load.
export function RankUpCelebration(props) {
  if (!props.celebration) return null;
  if (!celebrationsEnabled()) {
    ackScope(props.scopeId, props.celebration.key, props.onDone);
    return null;
  }
  return html`<${MareloCelebration} ...${props} />`;
}
