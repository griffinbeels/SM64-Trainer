// ui/components/streamoverlay.js — the Stream overlay designer (Settings ->
// Streaming -> Stream overlay). Pick a look, watch it in a live preview, copy
// the URL into an OBS Browser Source.
//
// The preview IS the overlay page (an iframe of ui/goal-overlay.html in demo
// mode), not a drawing of it, so what the streamer tunes here is exactly what
// OBS will show. The buttons under it drive that demo through postMessage.
//
// The streamer's last settings stay in this browser's localStorage, stored as
// the same query string the URL carries: one encoding, parsed by the same
// parseOverlayConfig the overlay itself uses.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { Modal } from "./modal.js";
import { Icon } from "./icons.js";
import { OVERLAY_DEFAULTS, OVERLAY_PATH, SCALE_RANGE, THEMES, overlayQuery,
         overlaySize, overlayUrl, parseOverlayConfig } from "../goaloverlay.js";

const html = htm.bind(h);
const STORAGE_KEY = "sm64.streamOverlay";

// The app's own accents (index.html :root), plus white for a neutral look.
const ACCENTS = [
  { hex: "f0bd32", name: "Star gold" }, { hex: "ff7b73", name: "Coral" },
  { hex: "68adf2", name: "Sky blue" }, { hex: "91e5a2", name: "Green" },
  { hex: "b48bf0", name: "Violet" }, { hex: "f5f7fb", name: "White" },
];
const SCENES = [
  { id: "goal", label: "One time" }, { id: "streak", label: "Streak" },
  { id: "count", label: "Count" }, { id: "idle", label: "No star" },
];
const BACKDROPS = [
  { id: "dark", label: "Dark course" }, { id: "bright", label: "Bright course" },
  { id: "checker", label: "Transparent" },
];
const TOGGLES = [
  { key: "icon", label: "Star art" }, { key: "name", label: "Star name" },
  { key: "source", label: "Where the goal comes from" },
  { key: "pips", label: "A dot per success on streak and count goals" },
  { key: "celebrate", label: "Celebrate when the goal falls" },
];

function loadConfig() {
  try { return parseOverlayConfig(localStorage.getItem(STORAGE_KEY) || ""); }
  catch { return { ...OVERLAY_DEFAULTS }; }
}

function saveConfig(config) {
  try { localStorage.setItem(STORAGE_KEY, overlayQuery(config)); } catch { /* private mode */ }
}

async function copyText(text, input) {
  try { await navigator.clipboard.writeText(text); return true; }
  catch {
    // The desktop window's webview may refuse the async clipboard; the
    // selection copy still works there.
    if (!input) return false;
    input.select();
    return document.execCommand("copy");
  }
}

/** Width available to the preview, so a wide overlay (the bar, or a big
 *  scale) shrinks to fit instead of scrolling. */
function useWidth(ref) {
  const [width, setWidth] = useState(640);
  useEffect(() => {
    if (!ref.current || typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  return width;
}

function Preview({ config, scene, setScene }) {
  const [backdrop, setBackdrop] = useState("dark");
  const frame = useRef(null);
  const stage = useRef(null);
  const size = overlaySize(config);
  const fit = Math.min(1, (useWidth(stage) - 24) / size.width);
  const query = overlayQuery(config);
  // The frame loads ONCE; later settings and examples arrive as messages, so
  // dragging the size slider never reloads (and blanks) the preview.
  const [src] = useState(() => `${OVERLAY_PATH}?demo=${scene}${query ? `&${query}` : ""}`);
  const send = (action, value) => frame.current && frame.current.contentWindow
    && frame.current.contentWindow.postMessage({ type: "goal-overlay-demo", action, value },
      location.origin);
  useEffect(() => { send("config", query); }, [query]);
  useEffect(() => { send("scene", scene); }, [scene]);
  const counted = scene === "streak" || scene === "count";
  return html`<div class="sov-preview">
    <div class="sov-preview-bar">
      <div class="sov-segmented" role="group" aria-label="Preview example">
        ${SCENES.map((s) => html`<button type="button" aria-pressed=${scene === s.id}
          onclick=${() => setScene(s.id)}>${s.label}</button>`)}
      </div>
      <div class="sov-segmented" role="group" aria-label="Preview background">
        ${BACKDROPS.map((b) => html`<button type="button" aria-pressed=${backdrop === b.id}
          onclick=${() => setBackdrop(b.id)}>${b.label}</button>`)}
      </div>
    </div>
    <div ref=${stage} class=${`sov-stage is-${backdrop}`}
        style=${`height:${Math.ceil(size.height * fit) + 24}px`}>
      <div style=${`width:${size.width * fit}px;height:${size.height * fit}px`}>
        <iframe ref=${frame} src=${src} title="Stream overlay preview"
          width=${size.width} height=${size.height}
          style=${`transform:scale(${fit});transform-origin:0 0`}></iframe>
      </div>
    </div>
    <div class="sov-actions">
      <button type="button" onclick=${() => send("celebrate")} disabled=${scene === "idle"}>
        <${Icon} name="play" />Play celebration</button>
      <button type="button" onclick=${() => send("tick")} disabled=${!counted}
        title=${counted ? "" : "Pick the Streak or Count example first"}>
        <${Icon} name="plus" />Count a success</button>
      <button type="button" onclick=${() => send("break")} disabled=${scene !== "streak"}
        title=${scene === "streak" ? "" : "Pick the Streak example first"}>
        <${Icon} name="restart" />Break the streak</button>
    </div>
  </div>`;
}

function Look({ config, set }) {
  return html`<fieldset class="sov-group">
    <legend>Look</legend>
    <div class="sov-themes">
      ${Object.entries(THEMES).map(([id, theme]) => html`<label
          class=${`sov-theme ${config.theme === id ? "is-on" : ""}`}>
        <input type="radio" name="sov-theme" value=${id} checked=${config.theme === id}
          onchange=${() => set({ theme: id })} />
        <strong>${theme.label}</strong><span>${theme.note}</span>
      </label>`)}
    </div>
    <div class="sov-field">
      <span>Accent</span>
      <div class="sov-swatches">
        ${ACCENTS.map((a) => html`<button type="button" class="sov-swatch"
          style=${`--c:#${a.hex}`} aria-label=${a.name} title=${a.name}
          aria-pressed=${config.accent === a.hex} onclick=${() => set({ accent: a.hex })}></button>`)}
        <input type="color" value=${`#${config.accent}`} aria-label="Any colour"
          title="Any colour" oninput=${(e) => set({ accent: e.target.value.slice(1) })} />
      </div>
    </div>
    <label class="sov-field">
      <span>Size</span>
      <input type="range" min=${SCALE_RANGE.min} max=${SCALE_RANGE.max} step=${SCALE_RANGE.step}
        value=${config.scale} oninput=${(e) => set({ scale: Number(e.target.value) })} />
      <output>${Math.round(config.scale * 100)}%</output>
    </label>
  </fieldset>`;
}

function Shows({ config, set }) {
  return html`<fieldset class="sov-group">
    <legend>Show</legend>
    ${TOGGLES.map((t) => html`<label class="sov-check">
      <input type="checkbox" checked=${config[t.key]}
        onchange=${(e) => set({ [t.key]: e.target.checked })} />${t.label}</label>`)}
    <label class="sov-check">
      <input type="checkbox" checked=${config.idle === "hide"}
        onchange=${(e) => set({ idle: e.target.checked ? "hide" : "show" })} />
      Hide it while no star is targeted</label>
    <div class="sov-field">
      <span>Position in a wider source</span>
      <div class="sov-segmented" role="group" aria-label="Position">
        ${["left", "center", "right"].map((a) => html`<button type="button"
          aria-pressed=${config.align === a} onclick=${() => set({ align: a })}>
          ${a[0].toUpperCase() + a.slice(1)}</button>`)}
      </div>
    </div>
  </fieldset>`;
}

function Link({ config }) {
  const [copied, setCopied] = useState(null);
  const input = useRef(null);
  const url = overlayUrl(location.origin, config);
  const size = overlaySize(config);
  async function copy() {
    setCopied(await copyText(url, input.current) ? "Copied" : "Select the address and copy it");
    setTimeout(() => setCopied(null), 2200);
  }
  return html`<div class="sov-link">
    <div class="sov-url">
      <input ref=${input} readonly value=${url} aria-label="Overlay address"
        onfocus=${(e) => e.target.select()} />
      <button type="button" class="primary-button" onclick=${copy}>
        <${Icon} name=${copied === "Copied" ? "check" : "copy"} />${copied === "Copied" ? "Copied" : "Copy URL"}</button>
    </div>
    <p class="sov-help">In OBS: <b>Sources → + → Browser</b>. Paste this URL, then set
      width <b>${size.width}</b> and height <b>${size.height}</b>. The overlay follows the star
      you are practicing while the trainer is running.</p>
    ${copied && copied !== "Copied" ? html`<p class="sov-help is-warn">${copied}</p>` : null}
  </div>`;
}

export function StreamOverlayDesigner({ onClose }) {
  const [config, setConfig] = useState(loadConfig);
  const [scene, setScene] = useState("goal");
  const set = (patch) => setConfig((current) => {
    const next = { ...current, ...patch };
    saveConfig(next);
    return next;
  });
  return html`<${Modal} title="Stream overlay" icon="target" size="large" onClose=${onClose}
      description="Your PB and goal for the star you are practicing, on stream.">
    <div class="sov">
      <${Preview} config=${config} scene=${scene} setScene=${setScene} />
      <${Link} config=${config} />
      <div class="sov-settings">
        <${Look} config=${config} set=${set} />
        <${Shows} config=${config} set=${set} />
      </div>
      <button type="button" class="quiet-button sov-reset"
        onclick=${() => { setConfig({ ...OVERLAY_DEFAULTS }); saveConfig(OVERLAY_DEFAULTS); }}>
        Reset to the default look</button>
    </div>
  <//>`;
}
