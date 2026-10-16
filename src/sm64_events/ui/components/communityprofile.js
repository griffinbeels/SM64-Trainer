import { h } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON } from "../api.js";
import { SearchSelect } from "./searchselect.js";
import { NAME_HINT, normalizedPlayerName, playerNameError } from "../communityname.js";

const html = htm.bind(h);

export const NAME_CHECK_DEBOUNCE_MS = 400;

// The live name check (his ask, round 41): after a pause in typing, or when
// the field loses focus, the name is checked for validity here and for
// availability with the community service, and the field shows a check or a
// cross. Submit stays disabled until the name is valid AND known free. When
// the service has no availability check yet, the claim at submit decides and
// the field says so instead of pretending.
export function useNameCheck(value, { original = null } = {}) {
  const [state, setState] = useState({ name: null, status: "idle", text: "" });
  const timer = useRef(null), request = useRef(0), latest = useRef(state);
  const name = normalizedPlayerName(value || "");
  const error = playerNameError(value || "");
  const settle = next => { latest.current = next; setState(next); return next; };
  const ask = () => {
    const ticket = ++request.current;
    settle({ name, status: "checking", text: "Checking…" });
    return getJSON(`/api/community/names/check?name=${encodeURIComponent(name)}`).then(result => {
      if (ticket !== request.current) return latest.current;
      if (result.available === true) return settle({ name, status: "available", text: "Available." });
      if (result.available === false) return settle({ name, status: "taken", text: "This name is already taken." });
      return settle({ name, status: "unknown", text: "Checked when you continue." });
    }).catch(() => ticket === request.current
      ? settle({ name, status: "unknown", text: "Checked when you continue." }) : latest.current);
  };
  const check = (immediate = false) => {
    clearTimeout(timer.current);
    if (!value) return Promise.resolve(settle({ name: null, status: "idle", text: "" }));
    if (error) return Promise.resolve(settle({ name, status: "invalid", text: error }));
    if (original && name === original) return Promise.resolve(settle({ name, status: "available", text: "This is your current name." }));
    if (immediate) return ask();
    timer.current = setTimeout(ask, NAME_CHECK_DEBOUNCE_MS);
    return Promise.resolve(latest.current);
  };
  useEffect(() => { check(false); return () => clearTimeout(timer.current); }, [value]);
  const current = state.name === name ? state : { name, status: error ? "invalid" : "idle", text: error };
  const settled = s => ["available", "unknown"].includes(s.status);
  // Submit may come before the pause: `ensure` runs the check at once and
  // answers with the final state ("we can also check it when the user goes
  // to submit if that action comes first").
  const ensure = () => (current.name === name && settled(current) ? Promise.resolve(current) : check(true))
    .then(s => ({ ...s, ok: s.name === name && settled(s) }));
  return { ...current, blur: () => { check(true); }, ensure,
           ok: !error && !["taken", "invalid"].includes(current.status) };
}

export function NameState({ state }) {
  const mark = { available: "✓", taken: "✕", invalid: "✕", checking: "…", unknown: "", idle: "" }[state.status] || "";
  const tone = ["taken", "invalid"].includes(state.status) ? "community-error"
    : state.status === "available" ? "community-ok" : "muted";
  return html`<small class="community-name-state ${tone}" data-state=${state.status} role="status">
    ${mark && html`<span class="community-name-mark" aria-hidden="true">${mark}</span>`}${state.text || NAME_HINT}</small>`;
}

function useSheetRunners(enabled) {
  const [runners, setRunners] = useState([]);
  useEffect(() => {
    let alive = true;
    if (enabled) getJSON("/api/library/runners")
      .then(value => alive && setRunners(value.runners || [])).catch(() => {});
    return () => { alive = false; };
  }, [enabled]);
  return useMemo(() => [{ label: "", options: runners.map(value => ({ value, label: value })) }], [runners]);
}

function SheetSection({ groups, runner, importTimes, busy, onPick, onImport }) {
  return html`<div class="community-sheet">
    <span>Sheet name (optional)</span>
    <${SearchSelect} groups=${groups} value=${runner} valueLabel=${runner || "Find your name"}
      title="Runners on the Ultimate Sheet" onChange=${onPick} />
    ${runner && html`<label class="community-check"><input type="checkbox" checked=${importTimes}
      disabled=${busy} onchange=${event => onImport(event.target.checked)} />
      <span>Import my Sheet best times<small>Updates local PBs and rank. It does not create ghosts or verify name ownership.</small></span></label>`}
  </div>`;
}

function ConsentSection({ account, sharing, consent, busy, policies, onConsent }) {
  return html`<p class="muted">${sharing
      ? "Your best ghost in each subdivision and strategy is shared automatically so other players can learn from your runs. You can turn sharing off in Settings."
      : "Automatic sharing stays off. You can change it in Settings."}</p>
    <label class="community-check"><input type="checkbox" checked=${consent} disabled=${busy}
      onchange=${event => onConsent(event.target.checked)} />
      <span>I agree to the ${" "}<a href=${account.terms_url} target="_blank" rel="noopener">Terms</a>
      ${" "}and acknowledge the ${" "}<a href=${account.privacy_url} target="_blank" rel="noopener">Privacy policy</a>.</span></label>
    ${!policies && html`<p class="community-error">The beta's policies are not available yet.</p>`}`;
}

// The form's state and its one submit rule: a name that is valid and free,
// plus consent (or a forced rename, which carries the earlier consent).
function useProfileForm(account, busy, onSave) {
  const rename = account.status === "rename_required";
  const renewing = account.status === "consent_required";
  const [name, setName] = useState(account.name || "");
  const [runner, setRunner] = useState(renewing ? account.sheet_runner || "" : "");
  const [importTimes, setImportTimes] = useState(false);
  const [consent, setConsent] = useState(false);
  const sharing = renewing ? account.sharing === true : true;
  const nameState = useNameCheck(name, { original: renewing ? account.name : null });
  const policies = !account.policies_loading && account.terms_version_current && account.privacy_version_current;
  const valid = nameState.ok && (rename || consent && policies);
  const save = event => {
    event.preventDefault();
    if (!valid || busy) return;
    nameState.ensure().then(checked => {
      if (checked.ok) onSave({ name: normalizedPlayerName(name), sharing,
        terms_version: account.terms_version_current, privacy_version: account.privacy_version_current,
        sheet_runner: runner || null }, importTimes);
    });
  };
  const pickRunner = value => { setRunner(value); setName(value); setImportTimes(false); };
  return { rename, name, setName, runner, pickRunner, importTimes, setImportTimes, consent, setConsent,
           sharing, nameState, policies, valid, save };
}

export function CommunityProfile({ account, busy, onSave }) {
  const form = useProfileForm(account, busy, onSave);
  const groups = useSheetRunners(!form.rename);
  const label = form.rename ? "Save new name" : "Start practicing";
  return html`<form class="community-form" onsubmit=${form.save}>
    ${form.rename && html`<p>Your previous name was reassigned. Your account and ghosts are still yours.</p>`}
    <label>Player name<input autofocus type="text" value=${form.name} maxlength="128" required
      disabled=${busy} oninput=${event => form.setName(event.target.value)} onblur=${form.nameState.blur}
      autocomplete="nickname" aria-invalid=${["taken", "invalid"].includes(form.nameState.status)} /></label>
    <${NameState} state=${form.nameState} />
    ${!form.rename && html`<${SheetSection} groups=${groups} runner=${form.runner} importTimes=${form.importTimes}
      busy=${busy} onPick=${form.pickRunner} onImport=${form.setImportTimes} />
    <${ConsentSection} account=${account} sharing=${form.sharing} consent=${form.consent} busy=${busy}
      policies=${form.policies} onConsent=${form.setConsent} />`}
    <div class="community-actions"><button class="primary-button" type="submit" disabled=${!form.valid || busy}>
      ${busy ? "Saving…" : label}</button></div>
  </form>`;
}
