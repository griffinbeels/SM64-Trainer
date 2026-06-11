// src/sm64_events/ui/components/practice.js  (stub — replaced in Task 11)
import { h } from "preact"; import htm from "htm";
const html = htm.bind(h);
export function Practice({ t }) {
  return html`<p class="meta">${t.view ? `${t.view.stars.length} star(s) this session` : "loading…"}</p>`;
}
