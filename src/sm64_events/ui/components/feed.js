// src/sm64_events/ui/components/feed.js
import { h } from "preact";
import htm from "htm";

const html = htm.bind(h);

export function Feed({ t }) {
  return html`<ul>
    ${t.feed.map((ev) => {
      if (ev.type === "ws_reconnected") {
        return html`<li class="meta">— reconnected (events may be missing above) —</li>`;
      }
      if (ev.type === "star_collected") {
        const p = ev.payload;
        return html`<li>
          <span class="star">⭐ ${p.course_name} — ${p.star_name}</span>
          ${" "}<b>${p.igt}</b>
          <span class="src src-${p.igt_source}">${p.igt_source}</span>
          <span class="meta"> ${p.igt_frames}f · course ${p.course_id} star ${p.star_id}${p.already_collected ? " (already collected)" : ""} · frame ${ev.frame} · #${ev.seq}</span>
        </li>`;
      }
      return html`<li>${ev.type}
        <span class="meta"> ${JSON.stringify(ev.payload)} · frame ${ev.frame} · #${ev.seq}</span></li>`;
    })}
  </ul>`;
}
