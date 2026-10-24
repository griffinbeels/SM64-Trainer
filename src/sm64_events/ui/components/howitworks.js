// src/sm64_events/ui/components/howitworks.js — About > How it works.
//
// A star's journey from your controller to this page, one stop at a time.
// The words live in STOPS; each stop may name a small interactive visual.
// Keep it fundamental: every sentence here should stay true while the code
// around it changes (no tuning constants, no module names).
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { Icon } from "./icons.js";
import { getJSON } from "../api.js";
import { entityIconSrc } from "./entityicons.js";
import { fmtIgtShort } from "../format.js";
import { STOPS, GRAB_FRAMES, MEMORY_CELLS, ACTION_NAMES, MESSAGE_EXAMPLE } from "../howitworks.js";

const html = htm.bind(h);

const hex = (n, width = 8) => `0x${(n >>> 0).toString(16).toUpperCase().padStart(width, "0")}`;

function MemoryVisual() {
  const [picked, setPicked] = useState(0);
  const cell = MEMORY_CELLS[picked];
  return html`<div class="hiw-memory">
    <div class="hiw-cells" aria-hidden="true">
      ${Array.from({ length: 96 }, (_, i) => {
        const hit = MEMORY_CELLS.findIndex((c) => c.slot === i);
        return html`<span class=${`hiw-cell ${hit >= 0 ? "is-known" : ""} ${hit === picked ? "is-picked" : ""}`}></span>`;
      })}
    </div>
    <div class="hiw-cell-picker" role="group" aria-label="Pick a value">
      ${MEMORY_CELLS.map((c, i) => html`<button type="button" class="hiw-pill"
          aria-pressed=${i === picked} onclick=${() => setPicked(i)}>${c.label}</button>`)}
    </div>
    <div class="hiw-cell-detail">
      <span class="hiw-addr">${hex(cell.address)}</span>
      <span>${cell.meaning}</span>
    </div>
  </div>`;
}

// The live readout: what GET /state says right now. Polled only while this
// stop is on screen, and it says so plainly when there is no game to read.
function LiveVisual() {
  const [snap, setSnap] = useState(undefined);
  useEffect(() => {
    let alive = true;
    const tick = () => getJSON("/state")
      .then((body) => alive && setSnap(body?.snapshot || null))
      .catch(() => alive && setSnap(null));
    tick();
    const id = setInterval(tick, 250);
    return () => { alive = false; clearInterval(id); };
  }, []);
  if (snap === undefined) return html`<div class="hiw-live is-empty">Reading…</div>`;
  if (!snap) {
    return html`<div class="hiw-live is-empty">
      Open Super Mario 64 in Project64 and your own numbers appear here, live.
    </div>`;
  }
  const rows = [
    ["Frame counter", snap.global_timer],
    ["Mario's action", `${hex(snap.mario_action)}${ACTION_NAMES[snap.mario_action] ? ` · ${ACTION_NAMES[snap.mario_action]}` : ""}`],
    ["Stars", snap.num_stars],
    ["Coins", snap.coins],
    ["Level", snap.curr_level],
    ["Usamune timer", fmtIgtShort(snap.igt_overall)],
  ];
  return html`<div class="hiw-live">
    <div class="hiw-live-head"><span class="status-light"></span>Your game, right now</div>
    <dl>${rows.map(([k, v]) => html`<div><dt>${k}</dt><dd>${v}</dd></div>`)}</dl>
  </div>`;
}

// Step through a real midair grab one game frame at a time. The detector
// sees only these numbers; the labels on the right are what it concludes.
function GrabVisual() {
  const [at, setAt] = useState(0);
  const frame = GRAB_FRAMES[at];
  const prev = GRAB_FRAMES[at - 1];
  return html`<div class="hiw-grab">
    <ol class="hiw-frames">
      ${GRAB_FRAMES.map((f, i) => html`<li class=${`${i === at ? "is-at" : ""} ${f.mark ? `is-${f.mark}` : ""}`}>
        <button type="button" onclick=${() => setAt(i)}>
          <span class="hiw-frame-n">frame ${f.frame}</span>
          <span class="hiw-frame-act">${ACTION_NAMES[f.action]}</span>
        </button>
      </li>`)}
    </ol>
    <div class="hiw-verdict" aria-live="polite">
      ${prev ? html`<span class="hiw-compare">${hex(prev.action)} → ${hex(frame.action)}</span>` : html`<span class="hiw-compare">${hex(frame.action)}</span>`}
      <b>${frame.says}</b>
    </div>
    <div class="hiw-stepper">
      <button type="button" disabled=${at === 0} onclick=${() => setAt(at - 1)}>◀ Previous frame</button>
      <button type="button" class="primary-button" disabled=${at === GRAB_FRAMES.length - 1}
        onclick=${() => setAt(at + 1)}>Next frame ▶</button>
    </div>
  </div>`;
}

function GameVisual({ t }) {
  return html`<figure class="hiw-window">
    <figcaption><span></span><span></span><span></span>Project64 1.6 · Usamune</figcaption>
    <img src=${entityIconSrc(t, "course:1")} alt="Bob-omb Battlefield" />
  </figure>`;
}

// Drag the frame count: the time is frames ÷ 30, so most hundredths can
// never appear (format.js: "Only 30 of every 100 centisecond values").
function FramesVisual() {
  const [frames, setFrames] = useState(343);
  return html`<div class="hiw-frames-demo">
    <div class="hiw-big-time">${fmtIgtShort(frames)}</div>
    <label class="hiw-slider">
      <span>${frames} frames</span>
      <input type="range" min="300" max="360" value=${frames}
        oninput=${(e) => setFrames(Number(e.currentTarget.value))} />
    </label>
    <p>Drag it: the hundredths jump in steps of 3 or 4, because the clock counts whole frames. A time like 11"44 can never happen.</p>
  </div>`;
}

function MessageVisual() {
  return html`<div class="hiw-message">
    <span class="hiw-caption">What this page receives for a star (trimmed)</span>
    <pre>${JSON.stringify(MESSAGE_EXAMPLE, null, 2)}</pre>
  </div>`;
}

function ReplayVisual() {
  return html`<div class="hiw-road" aria-label="The picture's two roads">
    <span class="hiw-node">Project64 draws a frame</span>
    <span class="hiw-arrow" aria-hidden="true">↓</span>
    <span class="hiw-node is-gold">The trainer's graphics plugin</span>
    <div class="hiw-split">
      <div><span class="hiw-arrow" aria-hidden="true">↙</span><span class="hiw-node">Your screen, as always</span></div>
      <div><span class="hiw-arrow" aria-hidden="true">↘</span><span class="hiw-node">A stamped copy → your graphics card encodes it → a replay</span></div>
    </div>
  </div>`;
}

function JournalVisual() {
  const steps = ["The journal: every event, in order", "Attempts, rebuilt from it",
    "Your personal best and rank", "This page asks again and redraws"];
  return html`<div class="hiw-road" aria-label="From the journal to the page">
    ${steps.map((step, i) => html`${i ? html`<span class="hiw-arrow" aria-hidden="true">↓</span>` : ""}
      <span class=${`hiw-node ${i === steps.length - 1 ? "is-gold" : ""}`}>${step}</span>`)}
  </div>`;
}

const VISUALS = { journal: JournalVisual, game: GameVisual, memory: MemoryVisual, live: LiveVisual,
  grab: GrabVisual, frames: FramesVisual, message: MessageVisual, replay: ReplayVisual };

export function HowItWorks({ t, showCredits }) {
  const [at, setAt] = useState(0);
  const stop = STOPS[at];
  const Visual = VISUALS[stop.visual];
  return html`<section class="practice-card hiw">
    <ol class="hiw-rail" aria-label="Steps">
      ${STOPS.map((s, i) => html`<li class=${`${i === at ? "is-at" : ""} ${i < at ? "is-past" : ""}`}>
        <button type="button" aria-current=${i === at ? "step" : null} onclick=${() => setAt(i)}>
          <span class="hiw-dot">${i < at ? html`<${Icon} name="check" size=${13} />` : i + 1}</span>
          <span class="hiw-rail-label">${s.short}</span>
        </button>
      </li>`)}
    </ol>
    <div class="hiw-stage">
      <div class="hiw-copy">
        <h3>${stop.title}</h3>
        ${stop.body.map((p) => html`<p>${p}</p>`)}
        ${stop.term && html`<p class="hiw-term"><b>${stop.term[0]}</b>${" "}${stop.term[1]}</p>`}
        ${stop.builtOn && html`<p class="hiw-built">Built on${" "}
          ${stop.builtOn.map((name, i) => html`${i ? ", " : ""}<button type="button"
              class="link-button" onclick=${showCredits}>${name}</button>`)}</p>`}
      </div>
      ${Visual && html`<div class="hiw-visual"><${Visual} t=${t} /></div>`}
    </div>
    <div class="hiw-nav">
      <button type="button" disabled=${at === 0} onclick=${() => setAt(at - 1)}>Back</button>
      ${at < STOPS.length - 1
        ? html`<button type="button" class="primary-button" onclick=${() => setAt(at + 1)}>Next: ${STOPS[at + 1].short}</button>`
        : html`<button type="button" class="primary-button" onclick=${showCredits}>See who made it possible</button>`}
    </div>
  </section>`;
}
