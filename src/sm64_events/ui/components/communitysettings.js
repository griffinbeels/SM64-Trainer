import { h } from "preact";
import { useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { downloadAccountData } from "../community.js";
import { NAME_HINT, normalizedPlayerName, playerNameError } from "../communityname.js";
import { ScopeChips } from "./scopechips.js";
import { ProgressSettings } from "./progresssettings.js";

const html = htm.bind(h);

const PROVIDER_NAMES = { discord: "Discord", twitch: "Twitch", google: "Google", email: "email" };

// Account details belong to Settings, never a returning sign-in step (round
// 54). The chip row is the Rank tab's own, read from the
// same summary endpoint, so the rank here can never differ from the tab.
export function AccountSummary({ account }) {
  const provider = PROVIDER_NAMES[account.provider] || null;
  return html`<div class="community-summary">
    <p class="community-identity"><b>${account.name}</b>${provider && html`<span class="muted">${" · "}signed in with ${provider}</span>`}</p>
    <${ScopeChips} activeScopeId=${null} onPick=${() => {}} />
  </div>`;
}

export function CommunitySettings({ account, busy, run, refresh, onClose, onOpenPlayer }) {
  const [name, setName] = useState(account.name || "");
  const [sharing, setSharing] = useState(account.sharing === true);
  const [confirm, setConfirm] = useState(null);
  async function save() {
    if (normalizedPlayerName(name) !== account.name) await send("PUT", "/api/community/account/name", { name: normalizedPlayerName(name) });
    if (sharing !== account.sharing) await send("PUT", "/api/community/account/sharing", { enabled: sharing });
    await refresh(); onClose();
  }
  async function remove() {
    await send("POST", `/api/community/account/${confirm === "DELETE" ? "delete" : "withdraw"}`, { confirmation: confirm });
    await refresh(); setConfirm(null);
  }
  if (confirm) return html`<div class="community-form">
    <h3>${confirm === "DELETE" ? "Delete community account?" : "Remove your shared ghosts?"}</h3>
    <p>${confirm === "DELETE" ? "Your community profile and published ghosts will be removed."
      : "Your ghosts will stop appearing in searches and automatic rivals."}</p>
    <p>Local practice data stays on this computer. Previously downloaded copies may remain with other players.</p>
    <div class="community-actions"><button disabled=${busy} onclick=${() => setConfirm(null)}>Cancel</button>
      <button class="danger" disabled=${busy} onclick=${() => run(remove)}>
        ${confirm === "DELETE" ? "Delete account" : "Remove shared ghosts"}</button></div>
  </div>`;
  return html`<div class="community-form">
    <${AccountSummary} account=${account} />
    <label>Player name<input type="text" value=${name} disabled=${busy} maxlength="128"
      oninput=${event => setName(event.target.value)} autocomplete="nickname" /></label>
    <small class=${name && playerNameError(name) ? "community-error" : "muted"}>
      ${name && playerNameError(name) || NAME_HINT}</small>
    <label class="community-check"><input type="checkbox" checked=${sharing} disabled=${busy}
      onchange=${event => setSharing(event.target.checked)} /><span>Automatically share my best ghosts</span></label>
    ${!sharing && html`<p class="muted" role="status">Other players won't receive your new ghosts or be able to learn from those runs.
      Existing shared ghosts stay available until you remove them below.</p>`}
    <div class="community-actions"><button class="primary-button" disabled=${busy || !!playerNameError(name)}
      onclick=${() => run(save)}>Done</button></div>
    <${ProgressSettings} onOpenPlayer=${onOpenPlayer} />
    <details class="community-privacy"><summary>Privacy and account</summary>
      <div class="community-actions">
        <button disabled=${busy} onclick=${() => run(async () => downloadAccountData(await getJSON("/api/community/account/export")))}>Export account data</button>
        <button disabled=${busy} onclick=${() => setConfirm("WITHDRAW")}>Remove shared ghosts</button>
        <button class="danger" disabled=${busy} onclick=${() => setConfirm("DELETE")}>Delete account</button>
      </div>
      <p><a href=${account.terms_url} target="_blank" rel="noopener">Terms</a>${" · "}
        <a href=${account.privacy_url} target="_blank" rel="noopener">Privacy policy</a></p>
    </details>
    <div class="community-actions"><button disabled=${busy} onclick=${() => run(async () => {
      await send("POST", "/api/community/auth/sign-out"); await refresh(); onClose();
    })}>Sign out</button></div>
  </div>`;
}
