// src/sm64_events/ui/components/update.js — auto-update popup.
// Self-contained: polls /api/update/status, shows notes + Update/Skip/Later.
// Notes are GitHub-release markdown rendered by a tiny safe pass (escape first).
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";

const html = htm.bind(h);

function esc(s) {
  return (s || "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
function inline(s) {
  return s
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/\[(.+?)\]\((https?:\/\/[^\s)]+)\)/g,
             '<a href="$2" target="_blank">$1</a>')
    .replace(/(^|[^"(>])(https?:\/\/[^\s<]+)/g,
             '$1<a href="$2" target="_blank">$2</a>');
}
function renderNotes(md) {
  const lines = esc(md).split(/\r?\n/);
  let out = "", inList = false;
  for (const ln of lines) {
    const li = ln.match(/^\s*[-*]\s+(.*)$/);
    if (li) { if (!inList) { out += "<ul>"; inList = true; }
              out += "<li>" + inline(li[1]) + "</li>"; continue; }
    if (inList) { out += "</ul>"; inList = false; }
    const hd = ln.match(/^\s*#{1,6}\s+(.*)$/);
    if (hd) { out += "<b>" + inline(hd[1]) + "</b><br>"; continue; }
    out += ln.trim() === "" ? "<br>" : inline(ln) + "<br>";
  }
  if (inList) out += "</ul>";
  return out;
}

export function UpdatePopup() {
  const [st, setSt] = useState(null);
  const [dismissed, setDismissed] = useState(false);
  const [applying, setApplying] = useState(false);

  const refresh = (force) =>
    getJSON("/api/update/status" + (force ? "?force=1" : ""))
      .then(setSt).catch(() => {});

  useEffect(() => { refresh(false); }, []);
  useEffect(() => {
    if (!applying) return;
    const id = setInterval(() => refresh(false), 700);
    return () => clearInterval(id);
  }, [applying]);

  if (!st || !st.update_available || dismissed) return null;
  if (!applying && st.skipped && st.skipped === st.latest) return null;

  const onUpdate = async () => {
    setApplying(true);
    try { await send("POST", "/api/update/apply"); } catch (e) { /* poll shows error */ }
  };
  const onSkip = async () => {
    try { await send("POST", "/api/update/skip", { version: st.latest }); }
    catch (e) { /* ignore */ }
    setDismissed(true);
  };
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
          ? html`
            <div class="meta">${st.state === "error"
              ? "Update failed — keeping the current version. Try again or download from GitHub."
              : "Installing… the app will restart automatically."}</div>
            <div class="progress"><div class="progress-bar"
                 style=${{ width: pct + "%" }}></div></div>`
          : html`
            <div class="modal-actions">
              ${st.writable
                ? html`<button onclick=${onUpdate}>Update now</button>`
                : html`<a class="btnlink" href=${st.html_url}
                          target="_blank">Download from GitHub</a>`}
              <button onclick=${onSkip}>Skip this version</button>
              <button onclick=${() => setDismissed(true)}>Later</button>
            </div>`}
      </div>
    </div>`;
}
