// src/sm64_events/ui/components/celebrate.js — celebration intensity, matched
// to what happened (spec task F3, 2026-07-25). Server decides WHETHER (it
// owns the watermarks); this decides HOW it looks and acks once the user has
// actually seen it, never on arrival — a client that fetches and never
// renders a celebration must not silently swallow it.
//
// Two treatments, both for the SCOPE rank (the aggregate MARELO rating),
// tiered so the rare event outranks the common one — a full-screen overlay
// every few minutes of grinding one star would stop meaning anything:
//   scope tier-up      -> TierRankUp    (the grand one: fill -> flip -> hold)
//   scope division-up  -> DivisionRankUp (a compact top-banner, not full-screen)
//
// The two ENTITY treatments that used to live here (a glow pop for a
// division-up, a small toast for a tier-up, both on the active-target card)
// were deleted with task 0012, 2026-07-26. The user's report was that they
// "kinda just… appear", and the answer was not a better toast: the rank-up
// is now performed by the rank BANNER itself climbing (ui/rankclimb.js), on
// the very bar the rank already lives on. A toast beside a climbing bar would
// be two things celebrating one event. That also retired the server's
// per-entity celebration watermarks — the climb is live-only by decision, so
// nothing needs to hold an unseen rank-up for later.
//
// KNOWN DEVIATION, not an oversight: the two overlays below still run their
// own hand-rolled fill -> flip -> hold machine rather than the climb engine.
// They are a full-screen takeover for a different (aggregate) rank, not an
// in-place bar, and folding them onto ui/celebrations.js is a follow-up that
// was deliberately kept out of task 0012's branch.
//
// `celebration_delta` (ranks/scopes.py) only ever fires on a RISE, so a
// non-null celebration's `from.tier !== to.tier` is exactly "at least one
// tier boundary was crossed" -- the one bit both the scope and entity paths
// branch on below.
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { send } from "../api.js";
import { RANK_NAMES, rankColor } from "./ranks.js";
import { capName, divisionDigit, wingTiers } from "./caps.js";
import { RankIcon } from "./rankicon.js";
import { prefersReducedMotion } from "../useTween.js";
const html = htm.bind(h);

const PREF = "sm64.celebrate";
export const celebrationsEnabled = () => localStorage.getItem(PREF) !== "0";
export const setCelebrationsEnabled = (on) =>
  localStorage.setItem(PREF, on ? "1" : "0");

async function ackScope(scopeId, key, onDone) {
  try { await send("POST", "/api/marelo/ack", { scope: scopeId, key }); }
  finally { onDone(); }
}

const isTierUp = (celebration) => celebration.from.tier !== celebration.to.tier;

// -- Scope: the grand one (tier-up) ------------------------------------

// Beat durations. Kept in lockstep with index.html's rankup-fill/rankup-flip
// keyframe durations (1.2s / .6s) -- a mismatch there would desync the CSS
// animation from the phase that's actually showing.
const FILL_MS = 1200;   // beat 1: the division bar visibly reaches the crossing point
const FLIP_MS = 600;    // beat 2: one cap-flip per tier gained
const HOLD_MS = 2700;   // beat 3: long enough to actually read, still dismissible

// The three-beat climb the live report asked for: the bar fills to the
// crossing point (so the BEFORE state is actually seen, not skipped), the
// cap turns over once per tier gained (a multi-tier jump is climbed, not
// teleported -- the climb is the reward), then a long dismissible hold.
// Roughly 4-5s total for a one-tier jump, longer for a bigger one.
function TierRankUp({ celebration, scopeId, onDone }) {
  const climb = RANK_NAMES.slice(
    RANK_NAMES.indexOf(celebration.to.tier),
    RANK_NAMES.indexOf(celebration.from.tier) + 1).reverse();
  const [phase, setPhase] = useState("fill");   // "fill" | "flip" | "hold"
  const [flipStep, setFlipStep] = useState(1);  // index into climb; 0 is the FROM tier, shown during "fill"

  async function finish() { await ackScope(scopeId, celebration.key, onDone); }

  // A fresh celebration always restarts at the fill beat -- keyed on
  // celebration.key (a stable primitive), not the celebration object
  // itself, which is a FRESH identity on every /api/marelo refetch
  // (test_ui_celebrate.py) -- an object-identity dependency would restart
  // the whole sequence on every poll during the hold.
  useEffect(() => { setPhase("fill"); setFlipStep(1); }, [celebration && celebration.key]);

  useEffect(() => {
    if (phase !== "fill") return undefined;
    const flipTimer = setTimeout(() => setPhase("flip"), FILL_MS);
    return () => clearTimeout(flipTimer);
  }, [celebration && celebration.key, phase]);

  useEffect(() => {
    if (phase !== "flip") return undefined;
    if (flipStep >= climb.length - 1) {
      const holdTimer = setTimeout(() => setPhase("hold"), FLIP_MS);
      return () => clearTimeout(holdTimer);
    }
    const nextStepTimer = setTimeout(() => setFlipStep((previousStep) => previousStep + 1), FLIP_MS);
    return () => clearTimeout(nextStepTimer);
  }, [celebration && celebration.key, phase, flipStep]);

  useEffect(() => {
    if (phase !== "hold") return undefined;
    const dismissTimer = setTimeout(finish, HOLD_MS);
    return () => clearTimeout(dismissTimer);
  }, [celebration && celebration.key, phase]);

  // "fill" shows the REAL before-state (celebration.from) -- the live
  // complaint was never seeing one. "flip" walks the intermediate tiers,
  // each shown at "V" (the bottom numeral -- just crossed in, no real
  // division of its own). "hold" shows the REAL after-state.
  const shownTier = phase === "fill" ? celebration.from.tier
    : phase === "flip" ? climb[flipStep] : celebration.to.tier;
  const shownDivision = phase === "fill" ? celebration.from.division
    : phase === "hold" ? celebration.to.division : "V";
  const caption = phase === "fill" ? "before" : phase === "flip" ? "climbing…" : "click to dismiss";

  // The wing FOLD (task 10, addendum, 2026-07-25): the wing policy itself
  // is unchanged -- division V (every "flip" tick) wears none -- but the
  // very FIRST flip tick (flipStep === 1) is also the exact instant the
  // fill beat's wings would otherwise just vanish. For that one tick only,
  // render the OUTGOING division's wing count (still on the tier the climb
  // is now leaving) with the fold animation instead of letting `division`
  // alone drop them straight to zero; `foldWings` decouples the wing COUNT
  // from the glyph digit shown (still "V"/no real division, per the comment
  // above). Nothing to fold if the FROM division was already wingless.
  // Reduced motion skips the tick's fold treatment entirely (the fold
  // NEVER renders in that case) rather than showing motionless wings that
  // are neither flapping nor folding -- same "jump straight to the end
  // state" contract useTween already honours elsewhere.
  const fromWings = wingTiers(celebration.from.tier, celebration.from.division);
  const foldWings = phase === "flip" && flipStep === 1 && fromWings > 0 && !prefersReducedMotion()
    ? fromWings : 0;

  return html`<div class="rankup" role="status" style=${`--tier:${rankColor(shownTier)}`}>
    <div class=${`rankup-card ${phase === "hold" ? "final" : ""}`} onclick=${finish}>
      <span class="meta">RANK UP</span>
      <span key=${`${phase}:${flipStep}`} class=${phase === "flip" ? "rankup-cap-flip" : ""}>
        <${RankIcon} tier=${shownTier} division=${shownDivision} size=${96} flap=${true} foldWings=${foldWings} />
      </span>
      ${phase === "fill" && html`<div class="rankup-fill-track"><i></i></div>`}
      <h2>${capName(shownTier)}${phase !== "flip" ? ` ${divisionDigit(shownDivision)}` : ""}</h2>
      <span class="meta">${caption}</span>
    </div>
  </div>`;
}

// -- Scope: the medium one (division-up) --------------------------------

const MEDIUM_MS = 2400;

// A compact top-banner, deliberately NOT the full centered takeover: a
// division-up is common enough during ordinary play that giving it the
// grand treatment would drown out the rare tier-up it's tiered against.
function DivisionRankUp({ celebration, scopeId, onDone }) {
  async function finish() { await ackScope(scopeId, celebration.key, onDone); }

  useEffect(() => {
    const timer = setTimeout(finish, MEDIUM_MS);
    return () => clearTimeout(timer);
  }, [celebration && celebration.key]);

  const tier = celebration.to.tier;
  return html`<div class="rankup-medium" role="status" style=${`--tier:${rankColor(tier)}`}>
    <div class="rankup-medium-card" onclick=${finish}>
      <span class="rank-icon-slot rankup-medium-icon">
        <${RankIcon} tier=${tier} division=${celebration.to.division} size=${40} flap=${true} />
      </span>
      <span class="rankup-medium-text">
        <b>${divisionDigit(celebration.from.division)} → ${divisionDigit(celebration.to.division)}</b>
        <i>${capName(tier)} · click to dismiss</i>
      </span>
    </div>
  </div>`;
}

// Mounted at the app ROOT (app.js), not inside the Rank tab, so a rank-up
// earned while on Practice still celebrates (rule 10 parity). Fixed-position
// wrappers only (both TierRankUp's `.rankup` and DivisionRankUp's
// `.rankup-medium`) with pointer-events OFF except on the card itself -- the
// user plays with this on screen, and an overlay that swallows a click is a
// real bug this codebase already shipped and fixed once.
export function RankUpOverlay({ celebration, scopeId, onDone }) {
  if (!celebration || !celebrationsEnabled()) {
    if (celebration) ackScope(scopeId, celebration.key, onDone);  // pref off: ack without showing
    return null;
  }
  return isTierUp(celebration)
    ? html`<${TierRankUp} celebration=${celebration} scopeId=${scopeId} onDone=${onDone} />`
    : html`<${DivisionRankUp} celebration=${celebration} scopeId=${scopeId} onDone=${onDone} />`;
}
