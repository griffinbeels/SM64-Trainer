// src/sm64_events/ui/components/update.js — auto-update popup.
// Presentational: reads update status + actions from the store (t). The header
// "Check for updates" button forces a re-check; the auto-check on load and the
// install/progress live in store.js so both stay in sync.
// Notes are GitHub-release markdown rendered by a tiny safe pass (escape first).
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";

const html = htm.bind(h);

function esc(s) {
  return (s || "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
function inline(s) {
  return s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/\[(.+?)\]\((https?:\/\/[^\s)]+)\)/g,
             '<a href="$2" target="_blank">$1</a>')
    .replace(/(^|[^"(>])(https?:\/\/[^\s<]+)/g,
             '$1<a href="$2" target="_blank">$2</a>');
}
// Block renderer: GitHub joins soft-wrapped lines (a single newline = a space)
// into one paragraph/bullet; a BLANK line separates blocks. The old per-line
// renderer broke continuation lines out of their bullet and added stray <br>s.
function renderNotes(md) {
  const lines = esc(md).split(/\r?\n/);
  let out = "", inList = false, buf = null;   // buf: {type:'li'|'p'|'h', text}
  const flush = () => {
    if (!buf) return;
    if (buf.type === "li") {
      if (!inList) { out += "<ul>"; inList = true; }
      out += "<li>" + inline(buf.text) + "</li>";
    } else {
      if (inList) { out += "</ul>"; inList = false; }
      out += buf.type === "h"
        ? "<b>" + inline(buf.text) + "</b>"
        : "<p>" + inline(buf.text) + "</p>";
    }
    buf = null;
  };
  for (const raw of lines) {
    const ln = raw.replace(/\s+$/, "");
    if (ln.trim() === "") { flush(); continue; }            // blank line ends a block
    const li = ln.match(/^\s*[-*]\s+(.*)$/);
    const hd = ln.match(/^\s*#{1,6}\s+(.*)$/);
    if (li) { flush(); buf = { type: "li", text: li[1] }; }
    else if (hd) { flush(); buf = { type: "h", text: hd[1] }; }
    else if (buf && (buf.type === "li" || buf.type === "p")) {
      buf.text += " " + ln.trim();                          // soft-wrap continuation
    } else { flush(); buf = { type: "p", text: ln.trim() }; }
  }
  flush();
  if (inList) out += "</ul>";
  return out;
}

export function UpdatePopup({ t }) {
  const st = t.update;
  const applying = t.updateApplying;
  const [dismissed, setDismissed] = useState(false);
  // A manual "Check for updates" re-opens the modal even after Skip/Later.
  useEffect(() => { if (t.updateForced) setDismissed(false); }, [t.updateForced]);

  if (!st || !st.update_available) return null;
  const blockedBySkip = !t.updateForced && st.skipped && st.skipped === st.latest;
  if (!applying && (dismissed || blockedBySkip)) return null;

  const onSkip = () => { t.skipUpdate(st.latest); setDismissed(true); };
  const onLater = () => { setDismissed(true); t.setUpdateForced(false); };
  const onClose = () => { t.setUpdateApplying(false); setDismissed(true); };
  const pct = Math.round((st.progress || 0) * 100);

  return html`
    <div class="modal-backdrop">
      <div class="modal">
        <h2>Update available — v${st.latest}</h2>
        <div class="meta">You're on v${st.current}.</div>
        <div class="update-notes"
             dangerouslySetInnerHTML=${{ __html: renderNotes(st.notes) }}></div>
        <p><a href=${st.html_url} target="_blank">View this release on GitHub →</a></p>
        ${applying
          ? (st.state === "error"
            ? html`
              <div class="meta">Update failed — your current version is unchanged.</div>
              <div class="modal-actions">
                <button onclick=${onClose}>Close</button>
                <a class="btnlink" href=${st.html_url}
                   target="_blank">Download from GitHub</a>
              </div>`
            : html`
              <div class="meta">Installing… the app will restart automatically.</div>
              <div class="progress"><div class="progress-bar"
                   style=${{ width: pct + "%" }}></div></div>`)
          : html`
            <div class="modal-actions">
              ${st.writable
                ? html`<button onclick=${() => t.applyUpdate()}>Update now</button>`
                : html`<a class="btnlink" href=${st.html_url}
                          target="_blank">Download from GitHub</a>`}
              <button onclick=${onSkip}>Skip this version</button>
              <button onclick=${onLater}>Later</button>
            </div>`}
      </div>
    </div>`;
}
