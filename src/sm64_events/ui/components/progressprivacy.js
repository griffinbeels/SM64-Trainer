// Authenticated owners retain privacy controls when new sharing is unavailable.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { downloadAccountData } from "../community.js";
import { Modal } from "./modal.js";

const html = htm.bind(h);
const PRIVACY = "/api/community/progress/privacy";
const READ_ERROR = "Your stored PB privacy settings could not be read. Try again.";

function checkedPrivacy(value, owner) {
  if (!value || typeof value.available !== "boolean") throw new Error(READ_ERROR);
  if (!value.available) return {available:false,reason:typeof value.reason === "string" ? value.reason : READ_ERROR};
  const publication = value.publication;
  if (!publication || publication.v !== 2 || publication.uid !== owner
      || !Number.isSafeInteger(publication.revision) || publication.revision < 0
      || typeof publication.enabled !== "boolean" || typeof publication.visible !== "boolean"
      || !(publication.content_hash === null || typeof publication.content_hash === "string"
        && /^[0-9a-f]{64}$/.test(publication.content_hash))) {
    throw new Error(READ_ERROR);
  }
  return value;
}

function usePrivacy(account) {
  const [data,setData] = useState(null), [error,setError] = useState(null);
  const [busy,setBusy] = useState(false), [loading,setLoading] = useState(true);
  const [choice,setChoice] = useState(null), [notice,setNotice] = useState(null);
  const alive = useRef(true), request = useRef(null), writing = useRef(false);
  async function refresh() {
    request.current?.abort(); request.current = new AbortController();
    const current = request.current;
    setData(null); setChoice(null); setError(null); setNotice(null); setLoading(true);
    try {
      const next = checkedPrivacy(await getJSON(PRIVACY,{signal:current.signal}),account.uid);
      if (alive.current && request.current === current) setData(next);
    } catch (problem) {
      if (alive.current && request.current === current && problem.name !== "AbortError") setError(problem.message || READ_ERROR);
    } finally { if (alive.current && request.current === current) setLoading(false); }
  }
  useEffect(() => { refresh(); return () => { alive.current = false; request.current?.abort(); }; },[]);
  function settled() { writing.current = false; if (alive.current) setBusy(false); }
  async function act(operation, message) {
    if (writing.current) return;
    writing.current = true; setBusy(true); setError(null); setNotice(null);
    try {
      const result = await operation();
      if (!alive.current) return;
      if (result) setData(checkedPrivacy(result,account.uid));
      setChoice(null); setNotice(message);
    } catch (problem) { if (alive.current) setError(problem.message || "This privacy action did not finish. Try again."); }
    finally { settled(); }
  }
  const control = (action,revision) => act(() => send("POST",`${PRIVACY}/control`,{
    action,expected_revision:revision,
  }),action === "withdraw" ? "Stored PBs removed." : "PB updates paused.");
  const exportData = () => act(async () => {
    const value = await getJSON("/api/community/account/export");
    if (alive.current) downloadAccountData(value);
    return null;
  },"Account export prepared.");
  return {data,error,busy,loading,choice,notice,refresh,control,exportData,
    remove:() => { setError(null); setChoice({revision:data.publication.revision}); },
    cancel:() => setChoice(null)};
}

function PrivacyBody({ data, busy, control, remove }) {
  const publication = data.publication;
  const retained = publication.content_hash !== null;
  return html`<div class="progress-privacy-state">
    <p>${retained ? "Your last saved-PB snapshot is still stored by the community service."
      : "No saved-PB snapshot is stored for this account."}</p>
    ${retained && !publication.visible ? html`<p class="meta">That stored snapshot is not visible on your player page.</p>` : null}
    ${publication.enabled ? html`<p class="meta">PB updates remain enabled in your saved settings. You can pause them here.</p>` : null}
    <div class="community-actions">
      ${publication.enabled ? html`<button type="button" disabled=${busy}
        onclick=${() => control("disable",publication.revision)}>Pause PB updates</button>` : null}
      ${retained ? html`<button type="button" class="danger" disabled=${busy} onclick=${remove}>Remove stored PBs</button>` : null}
    </div>
  </div>`;
}

function ProgressPrivacyView({ account }) {
  const state = usePrivacy(account);
  return html`<section class="progress-privacy">
    <div class="player-section-head"><h3>Saved PB privacy</h3>
      <button type="button" disabled=${state.busy || state.loading} onclick=${state.refresh}>Refresh</button></div>
    <p class="meta">You can review, pause and remove stored PB data for this account.</p>
    ${state.loading ? html`<p class="meta" role="status">Loading PB privacy settings…</p>` : null}
    ${state.error ? html`<p class="error" role="alert">${state.error}</p>` : null}
    ${state.data?.available ? html`<${PrivacyBody} data=${state.data} busy=${state.busy}
      control=${state.control} remove=${state.remove} />`
      : state.data ? html`<p class="meta">${state.data.reason}</p>` : null}
    ${state.error || state.data?.available === false ? html`<button type="button" disabled=${state.busy || state.loading}
      onclick=${state.refresh}>Retry</button>` : null}
    <div class="community-actions"><button type="button" disabled=${state.busy}
      onclick=${state.exportData}>Export account data</button></div>
    ${state.notice ? html`<p class="meta" role="status">${state.notice}</p>` : null}
    ${state.choice ? html`<${Modal} title="Remove stored PBs?" icon="trash"
        onClose=${state.busy ? undefined : state.cancel}
        footer=${html`<button type="button" disabled=${state.busy} onclick=${state.cancel}>Cancel</button>
          <button type="button" class="danger" disabled=${state.busy}
            onclick=${() => state.control("withdraw",state.choice.revision)}>Remove stored PBs</button>`}>
      <p>This removes your stored saved-PB snapshot from the community service.</p>
      <p class="meta">Your local practice data stays on this computer. Ghost sharing has its own privacy controls.</p>
      ${state.error ? html`<p class="error" role="alert">${state.error}</p>` : null}
    </${Modal}>` : null}
  </section>`;
}

export function ProgressPrivacy({ account }) {
  return html`<${ProgressPrivacyView} key=${JSON.stringify([account.uid,account.status])} account=${account} />`;
}
