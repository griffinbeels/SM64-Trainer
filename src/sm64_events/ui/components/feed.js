// src/sm64_events/ui/components/feed.js
import { h } from "preact";
import htm from "htm";
import { Icon } from "./icons.js";

const html = htm.bind(h);

export function Feed({ t }) {
  return html`<div class="workshop-page feed-page">
    <header class="practice-card workshop-hero">
      <div class="workshop-title">
        <span class="workshop-title-icon"><${Icon} name="feed" size=${22} /></span>
        <div>
          <span class="eyebrow">Diagnostics</span>
          <h2>Live feed</h2>
          <p>Inspect the raw game events the trainer is receiving and classifying.</p>
        </div>
      </div>
      <span class=${`feed-connection ${t.connected ? "is-live" : ""}`}>
        <span class="status-light"></span>${t.connected ? "Streaming" : "Offline"}
      </span>
    </header>

    <section class="practice-card feed-card">
      <div class="workshop-card-heading">
        <div>
          <span class="eyebrow">Event log</span>
          <h3>Newest events</h3>
        </div>
        <span class="count-badge">${t.feed.length}</span>
      </div>
      <div class="feed-scroll" role="log" aria-label="Raw game event log">
        ${t.feed.length === 0
          ? html`<div class="workshop-empty feed-empty">
              <span class="workshop-empty-icon"><${Icon} name="feed" size=${32} /></span>
              <h3>Waiting for game events</h3>
              <p>Events will appear here as the emulator changes state.</p>
            </div>`
          : t.feed.map((ev) => {
            if (ev.type === "ws_reconnected") {
              return html`<div class="feed-row feed-reconnected">
                <span class="feed-row-icon"><${Icon} name="restart" size=${16} /></span>
                <div><b>Reconnected</b><span>Events may be missing above this point.</span></div>
              </div>`;
            }
            if (ev.type === "star_collected") {
              const p = ev.payload;
              return html`<div class="feed-row feed-star">
                <span class="feed-row-icon"><${Icon} name="practice" size=${17} /></span>
                <div class="feed-event-main">
                  <div class="feed-event-title">
                    <b>${p.course_name} — ${p.star_name}</b>
                    <strong>${p.igt}</strong>
                    <span class=${`src src-${p.igt_source}`}>${p.igt_source}</span>
                  </div>
                  <span class="feed-event-meta">${p.igt_frames}f · course ${p.course_id}
                    · star ${p.star_id}${p.already_collected ? " · already collected" : ""}
                    · frame ${ev.frame} · #${ev.seq}</span>
                </div>
              </div>`;
            }
            return html`<div class="feed-row">
              <span class="feed-row-icon"><${Icon} name="feed" size=${16} /></span>
              <div class="feed-event-main">
                <div class="feed-event-title"><b>${ev.type}</b></div>
                <code class="feed-payload">${JSON.stringify(ev.payload)}</code>
                <span class="feed-event-meta">frame ${ev.frame} · #${ev.seq}</span>
              </div>
            </div>`;
          })}
      </div>
      <div class="feed-footer">
        <span><${Icon} name="feed" size=${14} /> Updates arrive live over WebSocket.</span>
        <span>Raw payloads are intended for troubleshooting.</span>
      </div>
    </section>
  </div>`;
}
