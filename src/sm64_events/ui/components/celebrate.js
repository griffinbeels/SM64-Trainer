// src/sm64_events/ui/components/celebrate.js — the rank-up overlay.
// Server decides WHETHER (it owns the watermark); this decides how it looks
// and acks when the user has actually seen it.
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { send } from "../api.js";
import { RANK_NAMES, rankColor } from "./ranks.js";
import { Crest } from "./marelo.js";
const html = htm.bind(h);

const PREF = "sm64.celebrate";
export const celebrationsEnabled = () => localStorage.getItem(PREF) !== "0";
export const setCelebrationsEnabled = (on) =>
  localStorage.setItem(PREF, on ? "1" : "0");

const STEP_MS = 850;

export function RankUpOverlay({ celebration, scopeId, onDone }) {
  // Every tier between old and new, so a multi-tier jump is climbed rather
  // than teleported -- the climb is the reward.
  const [step, setStep] = useState(0);
  const climb = celebration ? RANK_NAMES.slice(
    RANK_NAMES.indexOf(celebration.to.tier),
    RANK_NAMES.indexOf(celebration.from.tier) + 1).reverse() : [];

  // This overlay stays mounted across celebrations (it self-guards on
  // `celebration` being null rather than the caller conditionally mounting
  // it) -- without resetting on a new key, a second rank-up would inherit
  // the previous climb's final step and jump straight to its own end.
  useEffect(() => { setStep(0); }, [celebration && celebration.key]);

  useEffect(() => {
    if (!celebration) return undefined;
    if (step >= climb.length - 1) {
      const doneTimer = setTimeout(finish, 1600);
      return () => clearTimeout(doneTimer);
    }
    const nextStepTimer = setTimeout(() => setStep((previousStep) => previousStep + 1), STEP_MS);
    return () => clearTimeout(nextStepTimer);
  }, [celebration, step]);

  async function finish() {
    try { await send("POST", "/api/marelo/ack", { scope: scopeId, key: celebration.key }); }
    finally { onDone(); }
  }

  if (!celebration || !celebrationsEnabled()) {
    if (celebration) finish();          // acked without showing: pref is off
    return null;
  }
  const tier = climb[step] || celebration.to.tier;
  const last = step >= climb.length - 1;
  return html`<div class="rankup" role="status" style=${`--tier:${rankColor(tier)}`}>
    <div class=${`rankup-card ${last ? "final" : ""}`} onclick=${finish}>
      <span class="meta">RANK UP</span>
      <${Crest} tier=${tier} division=${last ? celebration.to.division : "I"} size=${96} />
      <h2>${tier}${last ? ` ${celebration.to.division}` : ""}</h2>
      <span class="meta">click to dismiss</span>
    </div>
  </div>`;
}
