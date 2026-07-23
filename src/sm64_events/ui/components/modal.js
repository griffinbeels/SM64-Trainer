// src/sm64_events/ui/components/modal.js — THE shared modal shell (backdrop +
// panel), extracted from the update popup so every modal keeps one look and
// one dismissal contract. Stateless: callers own visibility (render it or
// don't) and pass onClose for dismissal. onClose is OPTIONAL — when absent,
// Esc/backdrop-click do nothing (the update popup must not be dismissable
// that way). Clicks inside the panel never dismiss (stopPropagation).
import { h } from "preact";
import { useEffect } from "preact/hooks";
import htm from "htm";

const html = htm.bind(h);

export function Modal({ title, onClose, footer, children }) {
  useEffect(() => {
    if (!onClose) return undefined;
    const onKey = (keyEvent) => { if (keyEvent.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  return html`<div class="modal-backdrop" onclick=${() => onClose && onClose()}>
    <div class="modal" onclick=${(clickEvent) => clickEvent.stopPropagation()}>
      ${title ? html`<h2>${title}</h2>` : null}
      ${children}
      ${footer ? html`<div class="modal-actions">${footer}</div>` : null}
    </div>
  </div>`;
}
