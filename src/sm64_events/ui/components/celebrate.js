// src/sm64_events/ui/components/celebrate.js — celebration intensity, matched
// to what happened (spec task F3, 2026-07-25). Server decides WHETHER (it
// owns the watermarks); this decides HOW it looks and acks once the user has
// actually seen it, never on arrival — a client that fetches and never
// renders a celebration must not silently swallow it.
//
// Four treatments, tiered so the rare event outranks the common one (a
// full-screen overlay every few minutes of grinding one star would stop
// meaning anything):
//   scope tier-up      -> TierRankUp    (the grand one: fill -> flip -> hold)
//   scope division-up  -> DivisionRankUp (a compact top-banner, not full-screen)
//   entity tier-up     -> EntityCelebration's toast (on the active-target card)
//   entity division-up -> EntityCelebration's inline pop (same card, quieter)
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
import { Hat } from "./hat.js";
const html = htm.bind(h);

const PREF = "sm64.celebrate";
export const celebrationsEnabled = () => localStorage.getItem(PREF) !== "0";
export const setCelebrationsEnabled = (on) =>
  localStorage.setItem(PREF, on ? "1" : "0");

async function ackScope(scopeId, key, onDone) {
  try { await send("POST", "/api/marelo/ack", { scope: scopeId, key }); }
  finally { onDone(); }
}

async function ackEntity(entityKey, key, onDone) {
  try { await send("POST", "/api/marelo/ack", { entity: entityKey, key }); }
  finally { onDone(); }
}

const isTierUp = (celebration) => celebration.from.tier !== celebration.to.tier;

// entity_celebrations is a LIST (more than one entity can cross a tier
// between two /api/marelo views) -- this picks the one entry for the ONE
// entity a given card renders. Exported so practice.js's call sites don't
// need to know the list shape.
export function entityCelebrationFor(marelo, entityKey) {
  const celebrations = marelo && marelo.entity_celebrations;
  return (celebrations && celebrations.find((c) => c.entity === entityKey)) || null;
}

// -- Scope: the grand one (tier-up) ------------------------------------

// Beat durations. Kept in lockstep with index.html's rankup-fill/rankup-flip
// keyframe durations (1.2s / .6s) -- a mismatch there would desync the CSS
// animation from the phase that's actually showing.
const FILL_MS = 1200;   // beat 1: the division bar visibly reaches the crossing point
const FLIP_MS = 600;    // beat 2: one crest-flip per tier gained
const HOLD_MS = 2700;   // beat 3: long enough to actually read, still dismissible

// The three-beat climb the live report asked for: the bar fills to the
// crossing point (so the BEFORE state is actually seen, not skipped), the
// crest turns over once per tier gained (a multi-tier jump is climbed, not
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

  return html`<div class="rankup" role="status" style=${`--tier:${rankColor(shownTier)}`}>
    <div class=${`rankup-card ${phase === "hold" ? "final" : ""}`} onclick=${finish}>
      <span class="meta">RANK UP</span>
      <span key=${`${phase}:${flipStep}`} class=${phase === "flip" ? "rankup-crest-flip" : ""}>
        <${Hat} tier=${shownTier} division=${shownDivision} size=${96} />
      </span>
      ${phase === "fill" && html`<div class="rankup-fill-track"><i></i></div>`}
      <h2>${shownTier}${phase !== "flip" ? ` ${shownDivision}` : ""}</h2>
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
      <${Hat} tier=${tier} division=${celebration.to.division} size=${40} />
      <span class="rankup-medium-text">
        <b>${celebration.from.division} → ${celebration.to.division}</b>
        <i>${tier} · click to dismiss</i>
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

// -- Entity: pop (division-up) or toast (tier-up), on the active card ---

const ENTITY_POP_MS = 1100;
const ENTITY_TOAST_MS = 3600;

/**
 * Wraps a practice card's `.rank-slot` (the RankBanner row). `celebration`
 * is the entry from `entity_celebrations` for THIS card's entity, or `null`
 * -- practice.js passes `null` whenever this card is not the active target
 * (user decision 2026-07-25: the rank-up belongs where the rank already
 * lives, not a detached corner), so an entity that ranks up off-screen stays
 * pending and celebrates whenever its card next becomes the active target,
 * exactly the same "ack on dismissal, never on arrival" contract the scope
 * overlay already honours.
 *
 * Division-up gets a quiet inline pop (a glow pulse on the existing rank
 * display, no extra copy) since it is the frequent event; tier-up gets a
 * small toast sized to the rank-slot's own footprint so it can never grow
 * past `.objective-card`'s hard fixed height -- it just overlays inside the
 * existing box, the same constraint the shipped card layout already lives by.
 */
export function EntityCelebration({ celebration, entityKey, onDone, children }) {
  const relevant = !!(celebration && celebration.entity === entityKey);
  const tierUp = relevant && isTierUp(celebration);

  async function finish() { await ackEntity(entityKey, celebration.key, onDone); }

  useEffect(() => {
    if (!relevant) return undefined;
    if (!celebrationsEnabled()) { finish(); return undefined; }  // pref off: ack without showing
    const timer = setTimeout(finish, tierUp ? ENTITY_TOAST_MS : ENTITY_POP_MS);
    return () => clearTimeout(timer);
  }, [relevant && celebration.key]);

  const showing = relevant && celebrationsEnabled();
  return html`<div class="rank-slot-wrap ${showing && !tierUp ? "rank-slot-pop" : ""}">
    ${children}
    ${showing && tierUp && html`<div class="entity-rankup-toast" role="status"
        style=${`--tier:${rankColor(celebration.to.tier)}`} onclick=${finish}>
      <${Hat} tier=${celebration.to.tier} division=${celebration.to.division} size=${30} />
      <span class="entity-rankup-text">
        <b>${celebration.to.tier} ${celebration.to.division}</b>
        <i>tier up · tap to dismiss</i>
      </span>
    </div>`}
  </div>`;
}
