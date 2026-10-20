// Saved-PB sharing defaults on; explicit choices and publisher ownership persist.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { Modal } from "./modal.js";
import { readProgressPublication, useUiLog } from "../uilog.js";

const html = htm.bind(h);
const STATUS = "/api/community/progress";
const SOCIALS = [{kind:"twitch",label:"Twitch channel"},{kind:"youtube",label:"YouTube channel"},{kind:"website",label:"Website"}];
const stamp = value => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString() : null;
const samePublisher = (left, right) => Boolean(left && right
  && left.installation_id === right.installation_id && left.profile_id === right.profile_id);

function useProgressState() {
  const [data,setData] = useState(null), [error,setError] = useState(null);
  const [busy,setBusy] = useState(false), [loading,setLoading] = useState(true);
  const alive = useRef(true), generation = useRef(0), writing = useRef(false), request = useRef(null);
  async function refresh(clear = false) {
    const ticket = ++generation.current;
    request.current?.abort(); request.current = new AbortController();
    if (clear) setData(null);
    setLoading(true); setError(null);
    try {
      const next = await getJSON(STATUS,{signal:request.current.signal});
      if (alive.current && ticket === generation.current) setData(next);
    } catch (problem) {
      if (alive.current && ticket === generation.current && problem.name !== "AbortError") setError(problem.message);
    } finally { if (alive.current && ticket === generation.current) setLoading(false); }
  }
  useEffect(() => {
    refresh();
    const changed = () => refresh(true);
    window.addEventListener("community-account-changed",changed);
    return () => { alive.current = false; generation.current++; request.current?.abort();
      window.removeEventListener("community-account-changed",changed); };
  },[]);
  useEffect(() => {
    if (!data?.local?.default_pending || data.local.last_error || busy) return undefined;
    const timer = setTimeout(() => refresh(),2000);
    return () => clearTimeout(timer);
  },[data,busy]);
  function settled() { writing.current = false; if (alive.current) setBusy(false); }
  async function run(operation) {
    if (writing.current) return {ok:false};
    writing.current = true; setBusy(true); setError(null);
    const ticket = generation.current;
    try {
      const next = await operation();
      if (!alive.current || ticket !== generation.current) return {ok:false};
      setData(next); return {ok:true,data:next};
    } catch (problem) {
      if (alive.current && ticket === generation.current) {
        if (problem.status === 409) await refresh();
        setError(problem.message);
      }
      return {ok:false,status:problem.status};
    } finally { settled(); }
  }
  return {data,error,busy,loading,refresh,run};
}

function publicationMessage({ publication, local }, mine) {
  if (local.default_pending) return local.share_pbs
    ? "PB sharing is on. Waiting for the first sync." : "Saving sharing preference…";
  if (!publication.enabled) return "PB updates are paused. Previously shared PBs stay visible until you remove them.";
  return mine ? "This practice profile publishes saved PB changes automatically."
    : "Another practice profile publishes this account’s saved PBs.";
}

function PublicationSummary({ data }) {
  const publication = data.publication, local = data.local;
  const published = publication.visible && Boolean(publication.content_hash);
  const mine = samePublisher(publication.publisher,local.publisher);
  const updated = stamp(publication.synced_utc);
  return html`<div class="progress-publication-summary">
    <p>${published ? `Shared saved PBs${updated ? ` · Last synced ${updated}` : ""}` : "No saved PBs are currently public."}</p>
    <p class="meta">${publicationMessage(data,mine)}</p>
    ${publication.publisher ? html`<p class="meta">Publishing profile: ${publication.publisher.label || "Another Trainer profile"}</p>` : null}
    ${local.dirty && publication.enabled && mine ? html`<p class="meta" role="status">Your latest saved PB changes are waiting to sync.</p>` : null}
    ${local.last_error ? html`<p class="error" role="alert">${typeof local.last_error === "string" ? local.last_error
      : "The last PB sync did not finish. Try syncing again."}</p>` : null}
    ${local.conflict ? html`<p class="error" role="alert">The publishing profile changed. Review which profile should publish before continuing.</p>` : null}
  </div>`;
}

function SharingPreview({ preview }) {
  const coverage = preview.coverage;
  const count = Number.isInteger(preview.rows) ? `${preview.rows} current saved PB rows` : "PB row count unavailable";
  const size = Number.isInteger(preview.bytes) ? `${Math.ceil(preview.bytes/1024)} KiB` : "Size unavailable";
  return html`<div class="progress-sharing-preview">
    <h4>What this profile will share</h4>
    <p>${count}${" · "}${size}</p>
    <p class="meta">Saved PBs include imported and manually saved times. Your full practice history is not included.</p>
    ${coverage ? html`<p class="meta">${coverage.shared_rows} shared${" · "}${coverage.withheld_rows} withheld${" · "}${coverage.unsupported_rows} unsupported</p>` : null}
    ${coverage?.complete === false ? html`<p class="meta">This publication is partial. Withheld or unsupported entries remain unavailable on the shared page.</p>` : null}
    ${preview.oversize ? html`<p class="error" role="alert">${preview.reason || "This profile exceeds the publication limits. Nothing will be silently truncated."}</p>` : null}
  </div>`;
}

function PublishingConsent({ data, switching, busy, changed, accepted, setAccepted }) {
  return html`${switching ? html`<p>This replaces ${data.publication.publisher?.label || "the current publishing profile"} with ${data.local.publisher.label}.</p>
    <p class="meta">Existing published PBs will be replaced by this profile’s current saved PBs. They are not merged.</p>` : null}
    <${SharingPreview} preview=${data.preview} />
    <label class="community-check"><input type="checkbox" checked=${accepted} disabled=${busy || changed}
      onchange=${event => setAccepted(event.currentTarget.checked)} />
      <span>${data.consent.text}</span></label>`;
}

function PublicationConfirm({ choice, data, busy, error, onClose, onCommit }) {
  const [accepted,setAccepted] = useState(false);
  const withdrawing = choice.action === "withdraw";
  const switching = choice.action === "switch";
  const changed = choice.revision !== data.publication.revision;
  const valid = !changed && (withdrawing || accepted && !data.preview.oversize);
  return html`<${Modal} title=${withdrawing ? "Remove shared PBs?" : switching ? "Switch publishing profile?" : "Share saved PBs?"}
      icon="upload" onClose=${busy ? undefined : onClose}
      footer=${html`<button type="button" disabled=${busy} onclick=${onClose}>Cancel</button>
        <button type="button" class=${withdrawing ? "danger" : "primary-button"} disabled=${busy || !valid}
          onclick=${() => onCommit(choice,accepted)}>${withdrawing ? "Remove shared PBs" : switching ? "Use this profile" : "Share saved PBs"}</button>`}>
    ${withdrawing ? html`<p>This removes the published PB snapshot from your player page. Local practice data stays on this computer.</p>
      <p class="meta">Ghost sharing has its own setting.</p>`
      : html`<${PublishingConsent} data=${data} switching=${switching} busy=${busy}
        changed=${changed} accepted=${accepted} setAccepted=${setAccepted} />`}
    ${changed ? html`<p class="error" role="alert">Publication changed while this preview was open. Cancel and review the current profile.</p>` : null}
    ${error ? html`<p class="error" role="alert">${error}</p>` : null}
  </${Modal}>`;
}

function profileDraft(profile) {
  return {revision:profile?.revision ?? 0,bio:profile?.bio || "",
    links:Object.fromEntries(SOCIALS.map(({kind}) => [kind,(profile?.socials || []).find(social => social.kind === kind)?.url || ""]))};
}

function ProfileDetails({ profile, busy, run }) {
  const [draft,setDraft] = useState(() => profileDraft(profile));
  const [saved,setSaved] = useState(false);
  const edit = change => { setDraft(previous => ({...previous,...change})); setSaved(false); };
  async function save(event) {
    event.preventDefault();
    const result = await run(() => send("PUT","/api/community/player-profile",{
      expected_revision:draft.revision,bio:draft.bio,
      socials:SOCIALS.filter(({kind}) => draft.links[kind].trim()).map(({kind}) => ({kind,url:draft.links[kind].trim()})),
    }));
    if (result.ok) { setDraft(profileDraft(result.data.player_profile)); setSaved(true); }
  }
  return html`<form class="community-form progress-profile-details" onsubmit=${save}>
    <h4>Public page details</h4><p class="meta">These optional fields appear on your claimed player page.</p>
    <label>Bio<textarea value=${draft.bio} disabled=${busy} rows="3"
      oninput=${event => edit({bio:event.currentTarget.value})}></textarea></label>
    ${SOCIALS.map(({kind,label}) => html`<label key=${kind}>${label} URL
      <input type="url" value=${draft.links[kind]} disabled=${busy} placeholder="https://…"
        oninput=${event => edit({links:{...draft.links,[kind]:event.currentTarget.value}})} /></label>`)}
    <div class="community-actions"><button type="submit" disabled=${busy}>Save public details</button>
      ${profile?.revision !== draft.revision ? html`<button type="button" disabled=${busy}
        onclick=${() => { setDraft(profileDraft(profile)); setSaved(false); }}>Reload shared details</button>` : null}</div>
    ${saved ? html`<p class="meta" role="status">Public page details saved.</p>` : null}
  </form>`;
}

function PbSharingToggle({ data, busy, onChange }) {
  const {publication,local,preview} = data;
  return html`<label class="community-check"><input type="checkbox"
    checked=${local.default_pending ? local.share_pbs : publication.enabled}
    disabled=${busy || !publication.enabled && preview.oversize}
    onchange=${event => onChange(event.currentTarget.checked)} />
    <span>Automatically share my PBs<small>Saved best times appear on your player page.</small></span></label>`;
}

function ProgressControls({ data, busy, error, run, onOpenPlayer }) {
  const [choice,setChoice] = useState(null), [notice,setNotice] = useState(null);
  const publication = data.publication;
  const mine = samePublisher(publication.publisher,data.local.publisher);
  const choose = action => { setNotice(null); setChoice({action,revision:publication.revision}); };
  async function control(action, revision, accepted = false) {
    const result = await run(() => send("POST",`${STATUS}/control`,{action,expected_revision:revision,
      ...(accepted ? {notice_version:data.consent.version} : {})}));
    if (result.ok) { setChoice(null); setNotice(action === "withdraw" ? "Shared PBs removed." : "Publication settings saved."); }
    else if (result.status === 409) setChoice(null);
  }
  function setSharing(enabled) {
    if (!enabled) return control("disable",publication.revision);
    if (publication.publisher && !mine) return choose("switch");
    return control("enable",publication.revision,true);
  }
  return html`<div class="progress-sharing-controls">
    <${PbSharingToggle} data=${data} busy=${busy} onChange=${setSharing} />
    <${PublicationSummary} data=${data} />
    ${data.preview.oversize ? html`<p class="error" role="alert">${data.preview.reason || "This profile exceeds the saved-PB publication limits."}</p>` : null}
    <div class="community-actions">
      <button type="button" disabled=${busy} onclick=${() => onOpenPlayer?.(data.uid)}>Preview shared page</button>
      ${publication.publisher && !mine ? html`<button type="button" disabled=${busy || data.preview.oversize}
        onclick=${() => choose("switch")}>Publish from this profile</button>` : null}
      ${publication.enabled && mine ? html`<button type="button" disabled=${busy || data.preview.oversize}
        onclick=${() => run(() => send("POST",`${STATUS}/sync`))}>Sync now</button>` : null}
      ${publication.visible && publication.content_hash ? html`<button type="button" class="danger" disabled=${busy}
        onclick=${() => choose("withdraw")}>Remove shared PBs</button>` : null}
    </div>
    ${notice ? html`<p class="meta" role="status">${notice}</p>` : null}
    <${ProfileDetails} profile=${data.player_profile} busy=${busy} run=${run} />
    ${choice ? html`<${PublicationConfirm} choice=${choice} data=${data} busy=${busy} error=${error}
      onClose=${() => setChoice(null)} onCommit=${(selected,accepted) => control(selected.action,selected.revision,accepted)} />` : null}
  </div>`;
}

export function ProgressSettings({ onOpenPlayer }) {
  const root = useRef(null);
  useUiLog(root, [readProgressPublication]);
  const {data,error,busy,loading,refresh,run} = useProgressState();
  return html`<section ref=${root} class="progress-settings">
    <div class="player-section-head"><h3>Your public player page</h3>
      <button type="button" disabled=${busy || loading} onclick=${() => refresh()}>Refresh</button></div>
    ${loading && !data ? html`<p class="meta" role="status">Loading saved-PB publication…</p>` : null}
    ${error ? html`<p class="error" role="alert">${error}</p><button type="button" disabled=${busy || loading} onclick=${() => refresh()}>Retry</button>` : null}
    ${data?.available === false ? html`<p class="meta">${data.reason || "Saved-PB publication is unavailable in this Trainer window."}</p>` : null}
    ${data?.available ? html`<${ProgressControls} key=${data.uid} data=${data} busy=${busy} error=${error} run=${run} onOpenPlayer=${onOpenPlayer} />` : null}
  </section>`;
}
