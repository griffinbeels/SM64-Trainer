// src/sm64_events/ui/components/ghoststorage.js -- Settings -> Ghost files: how much
// space this computer's ghost files use, and two buttons that delete them to free it.
//
// His words (2026-11-08): "Maybe we need an extra button in the settings menu for purging
// all non-PB ghost files (and another for purging all local ghost files)? This wouldn't
// clear the server, but would free up space for the user."
//
// GET /api/ghost/storage says what each button would delete right now (`except_pbs` keeps
// PB, saved and imported ghosts; `all` keeps nothing), with ghosts in use already left
// out; POST /api/ghost/storage/purge {keep_pbs} deletes and answers the usage after it.
// The server then broadcasts `ghost_files_changed`: this section asks again on it, and so
// do the Practice log's ghost buttons (attemptlog.js -> attemptscene.js), so a deleted
// scene loses its button. Each button asks first, in one short dialog. A server without
// the endpoint, or one answering available:false, leaves both buttons disabled under one
// sentence -- never an error on the page.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { Icon } from "./icons.js";
import { Modal } from "./modal.js";

const html = htm.bind(h);
export const GHOST_FILES_CHANGED = "ghost_files_changed";
const MEGABYTE = 1024 * 1024;          // the size Windows and the rest of this app call a MB
export const UNAVAILABLE = "Ghost file sizes are not available right now.";
const PURGE_FAILED = "The ghosts could not be deleted. Try again.";

// The newest ghost_files_changed event in the store's raw feed (newest first), or null.
// The same object stays in the feed until a newer one arrives, so it is a revision a
// hook can depend on without asking again on every render.
export function newestGhostFilesChange(feed) {
  for (const event of feed || []) {
    if (event && event.type === GHOST_FILES_CHANGED) return event;
  }
  return null;
}

// "241 MB" at 10 MB and above, "4.5 MB" below, "less than 1 MB" for a tiny amount.
export function ghostMegabytes(bytes) {
  const megabytes = Math.max(0, Number(bytes) || 0) / MEGABYTE;
  if (megabytes < 1) return "less than 1 MB";
  const tenths = Math.round(megabytes * 10) / 10;
  return tenths < 10 ? `${tenths.toFixed(1)} MB` : `${Math.round(megabytes).toLocaleString("en-US")} MB`;
}

const ghosts = (count) => `${count} ${count === 1 ? "ghost" : "ghosts"}`;
const inUse = (count) => (count === 1
  ? "1 ghost is in use right now, so it stays."
  : `${count} ghosts are in use right now, so they stay.`);

// The two buttons, each with what it deletes (the usage key) and what its dialog asks.
export const PURGES = [
  { keepPbs: true, scope: "except_pbs", label: "Delete ghosts, keep PBs",
    ask: ({ ghosts: n, bytes }) => `Delete ${ghosts(n)} and free ${ghostMegabytes(bytes)}?`,
    detail: "Your PB, saved and imported ghosts stay." },
  { keepPbs: false, scope: "all", label: "Delete all ghosts",
    ask: ({ ghosts: n, bytes }) => `Delete ${n === 1 ? "1 ghost" : `all ${n} ghosts`} and free ${ghostMegabytes(bytes)}?`,
    detail: "This deletes your PB ghosts too." },
];

export function usageSentence(usage) {
  if (!usage) return "Checking ghost files…";
  if (!usage.available) return usage.reason || UNAVAILABLE;
  const total = usage.total || {};
  if (!total.ghosts) return "There are no ghost files on this computer.";
  return `Ghost files use ${ghostMegabytes(total.bytes)} on this computer.`;
}

// Why a button cannot run, or null when it can.
export function blockedReason(usage, purge) {
  if (!usage) return "Checking ghost files…";
  if (!usage.available) return usage.reason || UNAVAILABLE;
  if (usage[purge.scope]?.ghosts > 0) return null;
  if (!usage.total?.ghosts) return "There are no ghost files to delete.";
  const kept = purge.keepPbs && usage.all?.ghosts > 0 ? " Only your PB, saved and imported ghosts are here." : "";
  const busy = usage.in_use?.ghosts > 0 ? ` ${inUse(usage.in_use.ghosts)}` : "";
  return `There is nothing to delete.${kept}${busy}`;
}

export function resultSentence(result) {
  const done = result.deleted > 0
    ? `Deleted ${ghosts(result.deleted)} and freed ${ghostMegabytes(result.bytes)}.`
    : "No ghosts were deleted.";
  return result.skipped > 0 ? `${done} ${inUse(result.skipped)}` : done;
}

// The usage answer, asked when the section mounts (the drawer opens) and again whenever a
// newer ghost_files_changed event arrives. An older answer never overwrites a newer one.
function useGhostStorage(change) {
  const [usage, setUsage] = useState(null);
  const asked = useRef(0);
  useEffect(() => {
    const mine = ++asked.current;
    getJSON("/api/ghost/storage")
      .then((body) => { if (asked.current === mine) setUsage(body && typeof body === "object" ? body : { available: false }); })
      .catch(() => { if (asked.current === mine) setUsage({ available: false, reason: UNAVAILABLE }); });
  }, [change]);
  useEffect(() => () => { asked.current += 1; }, []);
  const settle = (body) => { asked.current += 1; setUsage(body); };
  return [usage, settle];
}

function PurgeDialog({ purge, usage, busy, error, onCancel, onDelete }) {
  const scope = usage[purge.scope];
  const held = usage.in_use?.ghosts > 0 ? ` ${inUse(usage.in_use.ghosts)}` : "";
  return html`<${Modal} title=${purge.label} icon="trash" onClose=${busy ? undefined : onCancel}
      footer=${html`<button type="button" autofocus disabled=${busy} onclick=${onCancel}>Cancel</button>
        <button type="button" class="danger-button ghost-storage-delete" disabled=${busy}
          onclick=${onDelete}>${busy ? "Deleting…" : "Delete"}</button>`}>
    <div class="ghost-storage-ask">
      <p class="ghost-storage-question">${purge.ask(scope)}</p>
      <p class="ghost-storage-detail">${purge.detail}${held}</p>
    </div>
    ${error ? html`<p class="modal-error" role="alert">${error}</p>` : null}
  </${Modal}>`;
}

export function GhostStorageSection({ feed }) {
  const [usage, settle] = useGhostStorage(newestGhostFilesChange(feed));
  const [asking, setAsking] = useState(null);       // the PURGES row whose dialog is open
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const open = (purge) => { setError(""); setAsking(purge); };
  const cancel = () => { if (!busy) { setAsking(null); setError(""); } };
  async function remove() {
    if (busy || !asking) return;
    setBusy(true);
    setError("");
    try {
      const answer = await send("POST", "/api/ghost/storage/purge", { keep_pbs: asking.keepPbs });
      setResult(answer || { deleted: 0, bytes: 0, skipped: 0 });
      if (answer?.usage) settle(answer.usage);
      setAsking(null);
    } catch (failure) {
      setError((failure?.status === 409 || failure?.status === 503) && failure.message ? failure.message : PURGE_FAILED);
    } finally {
      setBusy(false);
    }
  }
  return html`<section class="settings-section ghost-storage" aria-label="Ghost files">
    <h3>Ghost files</h3>
    <p class="settings-note ghost-storage-usage">${usageSentence(usage)}</p>
    <div class="settings-actions">
      ${PURGES.map((purge) => {
        const blocked = busy ? "Deleting ghosts…" : blockedReason(usage, purge);
        const scope = usage?.[purge.scope];
        return html`<button key=${purge.scope} type="button" class="danger-button"
            data-purge=${purge.scope} disabled=${Boolean(blocked)}
            title=${blocked || `Frees ${ghostMegabytes(scope.bytes)} on this computer.`}
            onclick=${() => open(purge)}>
          <${Icon} name="trash" />${purge.label}
        </button>`;
      })}
    </div>
    <p class="settings-note">This frees space on this computer. Ghosts you shared stay online.</p>
    ${result ? html`<p class="settings-note is-ok ghost-storage-result" role="status">${resultSentence(result)}</p>` : null}
  </section>
  ${/* Beside the section, never inside it: the section's paragraph rule would restyle the dialog's words. */""}
  ${asking && usage?.available ? html`<${PurgeDialog} purge=${asking} usage=${usage} busy=${busy}
    error=${error} onCancel=${cancel} onDelete=${remove} />` : null}`;
}
