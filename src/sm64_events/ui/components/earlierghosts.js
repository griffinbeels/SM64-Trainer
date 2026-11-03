// Ghosts this install recorded that may never have been shared. The published
// terms promise that signup shares future ghosts, so these wait for a yes
// (community/earlier_ghosts.py). The question appears once; Settings keeps the button.
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { Modal } from "./modal.js";

const html = htm.bind(h);
const ROUTE = "/api/community/earlier-ghosts";
const NOTHING = { ask: false, offer: false };

export function EarlierGhostsQuestion({ account }) {
  const [state, setState] = useState(NOTHING);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    if (account?.status === "active" && account.sharing === true) {
      getJSON(ROUTE).then(value => { if (live) setState(value); }).catch(() => {});
    } else {
      setState(NOTHING);
    }
    return () => { live = false; };
  }, [account?.uid, account?.status, account?.sharing]);
  if (!state.ask) return null;
  const choose = share => {
    setBusy(true);
    send("PUT", ROUTE, { share }).then(setState).catch(() => setFailed(true)).finally(() => setBusy(false));
  };
  return html`<${Modal} title="Share your older ghosts?" size="small" onClose=${() => choose(false)}
    footer=${html`<button disabled=${busy} onclick=${() => choose(false)}>Not now</button>
      <button class="primary-button" disabled=${busy} onclick=${() => choose(true)}>Share them</button>`}>
    <p>Some older ghosts on this computer may not be shared yet. If you share them,
      they will be on your account, and other players can race them.</p>
    ${failed && html`<p class="community-error" role="alert">Your answer could not be saved. Please try again.</p>`}
  <//>`;
}

export function ShareEarlierGhosts({ busy, run }) {
  const [state, setState] = useState(NOTHING);
  useEffect(() => { getJSON(ROUTE).then(setState).catch(() => {}); }, []);
  if (!state.offer) return null;
  return html`<div class="community-actions"><button disabled=${busy}
    onclick=${() => run(async () => setState(await send("PUT", ROUTE, { share: true })))}>Share my older ghosts</button></div>`;
}
