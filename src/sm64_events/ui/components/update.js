// src/sm64_events/ui/components/update.js — auto-update popup.
// Presentational: reads update status + actions from the store (t). The header
// "Check for updates" button forces a re-check; the auto-check on load and the
// install/progress live in store.js so both stay in sync.
// Notes are GitHub-release markdown rendered by a tiny safe pass (escape first).
import { h } from "preact";
import htm from "htm";
import { Modal } from "./modal.js";
import { Icon } from "./icons.js";

const html = htm.bind(h);

function esc(s) {
  return (s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
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
  let out = "", inList = false, buf = null;   // buf: {type:'li'|'p'|'h'|'hr', text}
  const flush = () => {
    if (!buf) return;
    if (buf.type === "li") {
      if (!inList) { out += "<ul>"; inList = true; }
      out += "<li>" + inline(buf.text) + "</li>";
    } else {
      if (inList) { out += "</ul>"; inList = false; }
      out += buf.type === "hr" ? "<hr>"
        : buf.type === "h" ? "<b>" + inline(buf.text) + "</b>"
        : "<p>" + inline(buf.text) + "</p>";
    }
    buf = null;
  };
  for (const raw of lines) {
    const ln = raw.replace(/\s+$/, "");
    if (ln.trim() === "") { flush(); continue; }            // blank line ends a block
    // Checked before `li`: '***' is ambiguous with a bullet and '---' must
    // not be mistaken for one.
    const hr = ln.match(/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/);
    const li = ln.match(/^\s*[-*]\s+(.*)$/);
    const hd = ln.match(/^\s*#{1,6}\s+(.*)$/);
    if (hr) { flush(); buf = { type: "hr" }; }
    else if (li) { flush(); buf = { type: "li", text: li[1] }; }
    else if (hd) { flush(); buf = { type: "h", text: hd[1] }; }
    else if (buf && (buf.type === "li" || buf.type === "p")) {
      buf.text += " " + ln.trim();                          // soft-wrap continuation
    } else { flush(); buf = { type: "p", text: ln.trim() }; }
  }
  flush();
  if (inList) out += "</ul>";
  return out;
}

// One block per missed version, newest first. `releases` comes from
// status(); the `fallback` single-body path covers a status payload without
// it (older server mid-update, or a hand-rolled response).
function NotesStack({ releases, fallback }) {
  const rows = (releases && releases.length)
    ? releases
    : [{ version: "", date: "", notes: fallback }];
  return html`<div class="update-notes">
    ${rows.map((r, i) => html`
      <div class=${i ? "update-rel sep" : "update-rel"}>
        ${r.version
          ? html`<div class="update-ver">v${r.version}${
              r.date ? html`<span class="update-date">${r.date}</span>` : ""}
            </div>`
          : ""}
        <div dangerouslySetInnerHTML=${{ __html: renderNotes(r.notes || "") }}></div>
      </div>`)}
  </div>`;
}

function UpdateFailure({ st, t, onClose }) {
  const attempt = st.attempt || 1;
  const unchanged = attempt > 1
    ? `Tried ${attempt} times. Your current version is unchanged.`
    : "Your current version is unchanged.";
  const detail = st.files_changed
    ? "App files were installed, but the app could not restart. Open the installer to repair it."
    : unchanged;
  return html`<div class="update-error">
    <${Icon} name="close" size=${18} />
    <span><b>${st.repair ? "Repair failed" : "Update failed"}</b>${detail}</span>
  </div>
  ${st.error && html`<p role="alert">${st.error}</p>`}
  <div class="modal-actions">
    ${!st.files_changed && html`<button class="primary-button" onclick=${() => t.applyUpdate()}>
      <${Icon} name="updates" size=${16} /> Try again
    </button>`}
    <button onclick=${onClose}>Close</button>
    <a class="btnlink" href=${st.html_url} target="_blank">Download from GitHub</a>
  </div>`;
}

function UpdateProgress({ st }) {
  const pct = Math.round((st.progress || 0) * 100);
  const retrying = (st.attempt || 1) > 1
    ? `Connection interrupted — retrying (attempt ${st.attempt} of ${st.attempts || 1}).`
    : "";
  return html`<div class="update-installing">
    <b>Installing update…</b><span>The app will restart automatically when it is ready.</span>
    ${retrying && html`<span class="update-retrying">${retrying}</span>`}
  </div>
  <div class="progress" role="progressbar" aria-label="Update progress"
      aria-valuemin="0" aria-valuemax="100" aria-valuenow=${pct}>
    <div class="progress-bar" style=${{ width: pct + "%" }}></div>
  </div>`;
}

function UpdateOffer({ st, t, onSkip, onLater }) {
  return html`${st.download_bytes != null && html`<div class="update-size">
    <${Icon} name="download" size=${15} />${(st.download_bytes / 1048576).toFixed(1)} MB download
  </div>`}
  <div class="modal-actions">
    ${st.writable ? html`<button class="primary-button" onclick=${() => t.applyUpdate()}>
      <${Icon} name="updates" size=${16} /> ${st.repair ? "Repair now" : "Update now"}
    </button>` : html`<a class="btnlink" href=${st.html_url} target="_blank">Download from GitHub</a>`}
    ${!st.repair && html`<button onclick=${onSkip}>Skip this version</button>`}
    <button onclick=${onLater}>Later</button>
  </div>`;
}

function UpdateActions({ applying, st, t, onClose, onSkip, onLater }) {
  if (!applying) return html`<${UpdateOffer} st=${st} t=${t} onSkip=${onSkip} onLater=${onLater} />`;
  return st.state === "error"
    ? html`<${UpdateFailure} st=${st} t=${t} onClose=${onClose} />`
    : html`<${UpdateProgress} st=${st} />`;
}

export function UpdatePopup({ t }) {
  const st = t.update;
  const applying = t.updateApplying;
  if (t.updateStartupPending && !t.updateVisible) return html`<${Modal}
      title="Checking for updates…" icon="updates">
    <p role="status">Checking for a newer version before sign-in.</p>
  <//>`;
  if (!t.updateVisible) return null;

  const onSkip = () => t.skipUpdate(st.latest);
  const onLater = t.dismissUpdate;
  const onClose = () => { t.setUpdateApplying(false); t.dismissUpdate(); };
  const missed = (st.releases || []).length;
  // Backdrop click / Esc mirror whichever dismiss action is visible: "Later"
  // on the normal offer, "Close" on a failed install, and NOTHING while an
  // install is actively in flight (no button is shown there either — closing
  // mid-install would hide a restart about to happen under the user).
  const onBackdropDismiss = applying
    ? (st.state === "error" ? onClose : undefined)
    : onLater;

  return html`<${Modal} title=${`${st.repair ? "Repair installation" : "Update available"} · v${st.latest}`}
      description=${st.repair ? "Replace missing or damaged app files. Your practice data stays in place." : undefined}
      icon="updates" size="large" onClose=${onBackdropDismiss}>
    <div class="update-summary">
      <span><small>Installed</small><b>v${st.current}</b></span>
      <${Icon} name="arrowDown" size=${17} />
      <span><small>Available</small><b>v${st.latest}</b></span>
      ${missed > 1 ? html`<em>${missed} versions</em>` : null}
    </div>
    <${NotesStack} releases=${st.releases} fallback=${st.notes} />
    <p class="update-release-link"><a href=${st.html_url} target="_blank">
      View release details on GitHub ↗</a></p>
    <${UpdateActions} applying=${applying} st=${st} t=${t}
      onClose=${onClose} onSkip=${onSkip} onLater=${onLater} />
  <//>`;
}
