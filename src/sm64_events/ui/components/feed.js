// src/sm64_events/ui/components/feed.js  (stub — replaced in Task 11)
import { h } from "preact"; import htm from "htm";
const html = htm.bind(h);
export function Feed({ t }) {
  return html`<ul>${t.feed.map((ev) => html`<li>${ev.type} <span class="meta">#${ev.seq}</span></li>`)}</ul>`;
}
