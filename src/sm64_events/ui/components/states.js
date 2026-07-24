import { h } from "preact";
import htm from "htm";
import { Icon } from "./icons.js";

const html = htm.bind(h);

export function PageState({ kind = "loading", title, message }) {
  const offline = kind === "offline";
  return html`<section class=${`practice-card page-state ${offline ? "is-offline" : "is-loading"}`}
      role=${offline ? "status" : null} aria-live="polite">
    <span class="page-state-icon">
      <${Icon} name=${offline ? "restart" : "updates"} size=${28} />
    </span>
    <div>
      <span class="eyebrow">${offline ? "Connection" : "Loading"}</span>
      <h2>${title || (offline ? "Waiting for the trainer" : "Preparing your workspace")}</h2>
      <p>${message || (offline
        ? "The app will reconnect automatically when the local server is available."
        : "Bringing your latest practice data into view…")}</p>
    </div>
  </section>`;
}

export function InlineState({ kind = "loading", children }) {
  return html`<div class=${`inline-state ${kind}`}>
    <${Icon} name=${kind === "error" ? "close" : "updates"} size=${16} />
    <span>${children}</span>
  </div>`;
}
