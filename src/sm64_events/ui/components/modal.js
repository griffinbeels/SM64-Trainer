// src/sm64_events/ui/components/modal.js — THE shared modal shell (backdrop +
// panel), extracted from the update popup so every modal keeps one look and
// one dismissal contract. Stateless: callers own visibility (render it or
// don't) and pass onClose for dismissal. onClose is OPTIONAL — when absent,
// Esc/backdrop-click do nothing (a caller mid-flow with no safe dismissal,
// e.g. an active install, omits it deliberately). Clicks inside the panel
// never dismiss (stopPropagation).
import { h } from "preact";
import { useEffect, useRef } from "preact/hooks";
import htm from "htm";
import { Icon } from "./icons.js";

const html = htm.bind(h);
let nextModalId = 1;

export function Modal({ title, description, icon = "practice", size = "medium",
                        onClose, footer, children }) {
  const panelRef = useRef(null);
  const titleId = useRef(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  if (titleId.current === null) titleId.current = `modal-title-${nextModalId++}`;
  useEffect(() => {
    const before = document.activeElement;
    const panel = panelRef.current;
    const focusable = () => panel
      ? [...panel.querySelectorAll(
        'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href]')]
      : [];
    if (panel && !panel.contains(document.activeElement))
      (panel.querySelector("[autofocus]") || focusable()[0] || panel).focus();
    const onKey = (keyEvent) => {
      if (keyEvent.key === "Escape") {
        keyEvent.preventDefault();
        keyEvent.stopPropagation();
        if (closeRef.current) closeRef.current();
        return;
      }
      if (keyEvent.key !== "Tab" || !panel) return;
      const items = focusable();
      if (!items.length) { keyEvent.preventDefault(); return; }
      const first = items[0], last = items[items.length - 1];
      if (keyEvent.shiftKey && document.activeElement === first) {
        keyEvent.preventDefault(); last.focus();
      } else if (!keyEvent.shiftKey && document.activeElement === last) {
        keyEvent.preventDefault(); first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      if (before && before.focus) before.focus();
    };
  }, []);
  return html`<div class="modal-backdrop" onclick=${() => onClose && onClose()}>
    <section ref=${panelRef} class=${`modal modal-${size}`} role="dialog"
        aria-modal="true" aria-labelledby=${title ? titleId.current : null}
        tabindex="-1" onclick=${(clickEvent) => clickEvent.stopPropagation()}>
      <header class="modal-header">
        <span class="modal-icon"><${Icon} name=${icon} size=${21} /></span>
        <div class="modal-heading">
          ${title ? html`<h2 id=${titleId.current}>${title}</h2>` : null}
          ${description ? html`<p>${description}</p>` : null}
        </div>
        ${onClose ? html`<button class="icon-button modal-close" aria-label="Close dialog"
            title="Close" onclick=${onClose}><${Icon} name="close" /></button>` : null}
      </header>
      <div class="modal-body">${children}</div>
      ${footer ? html`<div class="modal-actions">${footer}</div>` : null}
    </section>
  </div>`;
}
