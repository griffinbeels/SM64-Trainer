// The setup check: your Project64 setup beside the tested setup.
//
// The server owns every judgment (core/setup_profile.py): each item's status,
// importance, order and fix sentence arrive in GET /api/setup/check. This file
// only draws them, in three places: Settings → Check my setup, the ghost
// notice on the practice card, and the setup wizard's completion notes.
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { getJSON } from "../api.js";
import { Modal } from "./modal.js";

const html = htm.bind(h);
const STATUS_WORDS = { differs: "Different", unknown: "Can't tell", match: "Same" };

/** How often an open Check my setup reads again, so opening or closing a ROM shows at once. */
export const SETUP_CHECK_POLL_MS = 1000;

/**
 * The check (`enabled` false reads nothing). `live` reads it again every
 * SETUP_CHECK_POLL_MS while mounted; a failed re-read keeps the last answer.
 */
export function useSetupCheck(enabled = true, { live = false } = {}) {
  const [check, setCheck] = useState(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!enabled) return undefined;
    let alive = true;
    let timer;
    setFailed(false);
    const read = async () => {
      try {
        const value = await getJSON("/api/setup/check");
        if (alive) { setCheck(value); setFailed(false); }
      } catch {
        if (alive) setFailed(true);
      }
      if (alive && live) timer = setTimeout(read, SETUP_CHECK_POLL_MS);
    };
    read();
    return () => { alive = false; clearTimeout(timer); };
  }, [enabled, live]);
  return { check, failed };
}

function SetupCheckItem({ item, highlighted }) {
  const noted = item.importance === "noted";
  const tone = item.status === "match" ? "is-same" : noted ? "is-noted" : `is-${item.status}`;
  return html`<li class=${`setup-check-item ${tone}${highlighted ? " is-highlighted" : ""}`}
      data-item=${item.id} data-status=${item.status}>
    <div class="setup-check-head">
      <strong>${item.label}</strong>
      <span class="setup-check-status">${noted && item.status !== "match" ? "Noted" : STATUS_WORDS[item.status]}</span>
    </div>
    <dl class="setup-check-values">
      <div><dt>Yours</dt><dd>${item.yours}</dd></div>
      ${item.status !== "match" && html`<div><dt>Tested</dt><dd>${item.tested}</dd></div>`}
    </dl>
    ${item.shared && html`<p class="setup-check-shared">Every Project64 on this Windows account uses this setting.</p>`}
    ${item.fix && html`<p class="setup-check-fix">${item.fix}</p>`}
    ${item.fix && !noted && html`<p class="setup-check-links">
      <a href=${item.link} target="_blank" rel="noopener noreferrer">How to fix ↗</a>
      ${item.download && html`<a href=${item.download} target="_blank" rel="noopener noreferrer">Download ↗</a>`}
    </p>`}
  </li>`;
}

/** Every item in the server's order; `only` limits it to those ids. */
export function SetupCheckList({ check, highlight = [], only = null }) {
  const items = (check?.items || []).filter(item => !only || only.includes(item.id));
  return html`<ul class="setup-check-list">${items.map(item => html`<${SetupCheckItem} key=${item.id}
    item=${item} highlighted=${highlight.includes(item.id)}/>`)}</ul>`;
}

const PLACES = { "Program Files": "in Program Files", VirtualStore: "in Program Files, settings in VirtualStore",
                 other: "outside Program Files" };

// Which Project64 copy this check read: its folder's own name, never its path.
function FolderLine({ folder }) {
  if (!folder || folder.location === "unknown") return null;
  return html`<p class="setup-check-folder">Project64 folder: <strong>${folder.name}</strong>
    ${" "}(${PLACES[folder.location] || folder.location}${folder.running ? "" : ", not running"})</p>`;
}

function summary(check) {
  const count = check.flagged.length;
  if (count) return `${count} ${count === 1 ? "setting is" : "settings are"} different from the tested setup.`;
  return check.unknown ? "We couldn't check everything. The rest matches the tested setup."
    : "Your setup matches the tested setup.";
}

export function SetupCheckModal({ onClose, highlight = [] }) {
  const { check, failed } = useSetupCheck(true, { live: true });
  return html`<${Modal} title="Check my setup" icon="settings" size="large" onClose=${onClose}>
    <div class="setup-check" data-testid="setup-check">
      ${failed && !check ? html`<p class="setup-error" role="alert">Couldn't check your setup. Try again.</p>`
        : !check ? html`<p role="status">Checking your setup…</p>`
        : html`<p class="setup-check-summary" role="status">${summary(check)}</p>
          <${FolderLine} folder=${check.folder}/>
          <${SetupCheckList} check=${check} highlight=${highlight}/>`}
    </div>
  </${Modal}>`;
}

/**
 * Under the Lineup, only while ghosts the player asked for have gone unseen
 * (`display_stalled`, ghost/display_stall.py) AND the setup check finds a
 * required or recommended difference. A matching setup shows nothing new.
 */
export function GhostSetupNotice({ session }) {
  const stalled = session?.display_stalled === true;
  const { check } = useSetupCheck(stalled);
  const [open, setOpen] = useState(false);
  const flagged = stalled ? check?.flagged || [] : [];
  return html`${flagged.length > 0 && html`<div class="ghost-setup-notice" role="status" data-testid="ghost-setup-notice">
      <p>Ghosts aren't showing. Your Project64 setup is different from the tested setup.</p>
      <button type="button" onclick=${() => setOpen(true)}>Check my setup</button>
    </div>`}
    ${open && html`<${SetupCheckModal} highlight=${flagged} onClose=${() => setOpen(false)}/>`}`;
}

/** The wizard's completion page: differences as notes, never a blocker. */
export function SetupCheckNotes({ check }) {
  const flagged = check?.flagged || [];
  if (!flagged.length) return null;
  return html`<div class="setup-check-notes" data-testid="setup-check-notes">
    <p>These settings are different from the tested setup. You can still practice.</p>
    <${SetupCheckList} check=${check} only=${flagged}/>
  </div>`;
}
