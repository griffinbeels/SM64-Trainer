import { h } from "preact";
import { useEffect, useMemo, useState } from "preact/hooks";
import htm from "htm";
import { getJSON } from "../api.js";
import { SearchSelect } from "./searchselect.js";
import { normalizedPlayerName, playerNameError } from "../communityname.js";

const html = htm.bind(h);

function profileValid(name, rename, consent, policies) {
  return !playerNameError(name) && (rename || consent && policies);
}

export function CommunityProfile({ account, busy, onSave }) {
  const rename = account.status === "rename_required";
  const renewing = account.status === "consent_required";
  const [name, setName] = useState(account.name || "");
  const [runners, setRunners] = useState([]);
  const [runner, setRunner] = useState(renewing ? account.sheet_runner || "" : "");
  const [importTimes, setImportTimes] = useState(false);
  const sharing = renewing ? account.sharing === true : true;
  const [consent, setConsent] = useState(false);
  useEffect(() => {
    let alive = true;
    if (!rename) getJSON("/api/library/runners")
      .then(value => alive && setRunners(value.runners || [])).catch(() => {});
    return () => { alive = false; };
  }, [rename]);
  const groups = useMemo(() => [{ label: "", options: runners.map(value => ({ value, label: value })) }], [runners]);
  const policies = !account.policies_loading && account.terms_version_current && account.privacy_version_current;
  const valid = profileValid(name, rename, consent, policies);
  const save = event => {
    event.preventDefault();
    if (valid && !busy) onSave({ name: normalizedPlayerName(name), sharing,
      terms_version: account.terms_version_current, privacy_version: account.privacy_version_current,
      sheet_runner: runner || null }, importTimes);
  };
  return html`<form class="community-form" onsubmit=${save}>
    ${rename && html`<p>Your previous name was reassigned. Your account and ghosts are still yours.</p>`}
    <label>Player name<input autofocus type="text" value=${name} maxlength="128" required
      disabled=${busy} oninput=${event => setName(event.target.value)} autocomplete="nickname" /></label>
    <small class=${name && playerNameError(name) ? "community-error" : "muted"}>
      ${name && playerNameError(name) || "20 characters maximum."}</small>
    ${!rename && html`<div class="community-sheet">
      <span>Sheet name (optional)</span>
      <${SearchSelect} groups=${groups} value=${runner} valueLabel=${runner || "Find your name"}
        title="Runners on the Ultimate Sheet" onChange=${value => {
          setRunner(value); setName(value); setImportTimes(false);
        }} />
      ${runner && html`<label class="community-check"><input type="checkbox" checked=${importTimes}
        disabled=${busy} onchange=${event => setImportTimes(event.target.checked)} />
        <span>Import my Sheet best times<small>Updates local PBs and rank. It does not create ghosts or verify name ownership.</small></span></label>`}
    </div>`}
    ${!rename && html`<p class="muted">${sharing
      ? "Your best ghost in each subdivision and strategy is shared automatically so other players can learn from your runs. You can turn sharing off in Settings."
      : "Automatic sharing stays off. You can change it in Settings."}</p>
    <label class="community-check"><input type="checkbox" checked=${consent} disabled=${busy}
      onchange=${event => setConsent(event.target.checked)} />
      <span>I agree to the ${" "}<a href=${account.terms_url} target="_blank" rel="noopener">Terms</a>
      ${" "}and acknowledge the ${" "}<a href=${account.privacy_url} target="_blank" rel="noopener">Privacy policy</a>.</span></label>
    ${!policies && html`<p class="community-error">The beta's policies are not available yet.</p>`}`}
    <div class="community-actions"><button class="primary-button" type="submit" disabled=${!valid || busy}>
      ${busy ? "Saving…" : rename ? "Save new name" : "Start practicing"}</button></div>
  </form>`;
}
