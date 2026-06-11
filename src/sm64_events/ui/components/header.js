// src/sm64_events/ui/components/header.js  (stub — replaced in Task 11)
import { h } from "preact"; import htm from "htm";
const html = htm.bind(h);
export function Header({ t }) {
  return html`<div class="bar">
    <span class="dot ${t.connected ? "ok" : "bad"}">${t.connected ? "live" : "offline"}</span>
  </div>`;
}
