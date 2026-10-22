import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { friendsModel, friendsRequest, memberState, readFriends, responseSnapshot } from "../friends.js";
import { useUiLog } from "../uilog.js";
import { Icon } from "./icons.js";
import { Modal } from "./modal.js";

const html = htm.bind(h);
const READERS = [readFriends];

export function useFriends() {
  const [snapshot, setSnapshot] = useState(null), [readError, setReadError] = useState(null);
  const [actionError, setActionError] = useState(null);
  const [busy, setBusy] = useState(null);
  const alive = useRef(true), generation = useRef(0), request = useRef(null), timer = useRef(null), writing = useRef(false);
  async function refresh() {
    if (writing.current || request.current) return;
    const ticket = generation.current;
    const controller = new AbortController();
    request.current = controller;
    try {
      const next = await friendsRequest("status", undefined, { signal: controller.signal });
      if (alive.current && ticket === generation.current) { setSnapshot(next); setReadError(null); }
    } catch (problem) {
      if (alive.current && ticket === generation.current && problem.name !== "AbortError") setReadError(problem.message);
    } finally {
      if (request.current === controller) request.current = null;
      if (alive.current) { clearTimeout(timer.current); timer.current = setTimeout(refresh, 2000); }
    }
  }
  useEffect(() => {
    refresh();
    return () => { alive.current = false; generation.current++; request.current?.abort(); clearTimeout(timer.current); };
  }, []);
  function settled() {
    writing.current = false;
    if (alive.current) { setBusy(null); clearTimeout(timer.current); timer.current = setTimeout(refresh, 0); }
  }
  async function act(action, body) {
    if (writing.current) return null;
    writing.current = true; generation.current++; request.current?.abort();
    setBusy(action); setActionError(null);
    try {
      const result = await friendsRequest(action, body);
      if (!alive.current) return null;
      const next = responseSnapshot(result);
      if (next) setSnapshot(next);
      return result;
    } catch (problem) { if (alive.current) setActionError(problem.message); return null; }
    finally { settled(); }
  }
  function retry() { setActionError(null); refresh(); }
  return { snapshot, error: actionError || readError, busy, act, refresh: retry };
}

function Invitations({ model, busy, act }) {
  if (!model.invitations.length) return null;
  return html`<section class="friends-invites" aria-label="Invites">
    <h3>Invites</h3>
    ${model.invitations.map(invite => html`<div class="friends-invitation" key=${invite.id}>
      <div class="friends-invitation-name"><strong>${invite.from_name}</strong><span class="meta">${invite.member_count}${" "}${invite.member_count === 1 ? "player" : "players"}</span></div>
      <div class="friends-row-actions">
        <button type="button" class="primary-button" disabled=${Boolean(busy) || model.room || invite.member_count >= 4}
          title=${model.room ? "Leave your current room to accept this invite." : invite.member_count >= 4 ? "Room is full." : "Accept invite"}
          onclick=${() => act("join", { invite_id: invite.id })}>Accept</button>
        <button type="button" disabled=${Boolean(busy)} onclick=${() => act("decline", { invite_id: invite.id })}>Decline</button>
      </div>
    </div>`)}</section>`;
}

function Room({ model, busy, act }) {
  return html`<section class="friends-room" aria-label="Practice room">
    <div class="friends-section-head"><h3>${model.owner ? "Your room" : "Practice room"}</h3><span class="meta">${model.count}/4 players</span></div>
    <div class="friends-slots" role="list">
      ${model.slots.map((member, index) => html`<div class=${`friends-seat${member ? " is-occupied" : " is-empty"}`}
          key=${index} role="listitem">
        <${Icon} name="ghost" size=${24} />
        <div class="friends-seat-info">
          <strong>${member ? member.name : "Open spot"}</strong>
          <span class="friends-member-state">${member ? memberState(member) : "Waiting for a friend"}</span>
          ${member?.self ? html`<span class="friends-self">${model.owner ? "You · Host" : "You"}</span>` : null}
        </div>
        ${member && !member.self ? html`<div class="friends-seat-actions">
          ${member.state === "disconnected" ? html`<button type="button" aria-label=${`Reconnect ${member.name}`}
            disabled=${Boolean(busy) || model.transitioning}
            onclick=${() => act("retry_peer", { member_id: member.id })}>Reconnect</button>` : null}
          ${model.owner ? html`<button type="button" class="friends-remove" aria-label=${`Remove ${member.name}`}
            disabled=${Boolean(busy) || model.transitioning}
            onclick=${() => act("kick", { member_id: member.id })}>Remove</button>` : null}
        </div>` : null}
      </div>`)}
    </div>
  </section>`;
}

function InviteForm({ model, busy, act }) {
  const [code, setCode] = useState(""), [sent, setSent] = useState(false);
  if (!model.canInvite && !model.full) return null;
  if (model.full) return html`<p class="friends-full" role="status">Room full</p>`;
  async function invite(event) {
    event.preventDefault();
    if (!code.trim() || busy) return;
    if (await act("invite", { code: code.trim() })) { setCode(""); setSent(true); }
  }
  return html`<form class="friends-invite-form" onsubmit=${invite}>
    <label class="friends-code-label">Friend’s player code
      <input class="friends-code" data-private-input="true" value=${code} autocomplete="off"
        autocapitalize="off" spellcheck="false" placeholder="Paste their ET1 code"
        oninput=${event => { setCode(event.currentTarget.value); setSent(false); }} />
    </label>
    <button type="submit" disabled=${Boolean(busy) || !code.trim()}>${busy === "invite" ? "Inviting…" : "Invite friend"}</button>
    <p class="meta friends-invite-note" role=${sent ? "status" : undefined}>${sent ? "Waiting for your friend…" : "Anyone here can invite friends. Ask them to copy their player code from Friends."}</p>
  </form>`;
}

function FriendsFooter({ state, model, copyIdentity }) {
  return html`<button type="button" disabled=${Boolean(state.busy)} onclick=${copyIdentity}>
    <${Icon} name="copy" size=${16} />${state.busy === "identity" ? "Getting code…" : "Copy my player code"}</button>
    ${model.room ? html`<button type="button" class="friends-leave" disabled=${Boolean(state.busy) || model.phase === "leaving"}
      onclick=${() => state.act("leave", {})}>Leave room</button>` : null}`;
}

function FriendsStart({ model, state }) {
  return html`<div class="friends-start"><h3>Practice with friends</h3><p class="meta">See each other’s ghosts while you play.</p>
    <button type="button" class="primary-button" disabled=${Boolean(state.busy) || model.transitioning}
      onclick=${() => state.act("host", {})}><${Icon} name="plus" size=${16} />${model.transitioning ? model.title : "Start a room"}</button>
    <p class="meta friends-join-help">Joining a friend? Copy your player code and send it to them.</p></div>`;
}

function FriendsReady({ model, state, copy, privateCode }) {
  const reason = state.snapshot.source?.reason;
  return html`<${Invitations} model=${model} busy=${state.busy} act=${state.act} />
    ${model.room ? html`<${Room} model=${model} busy=${state.busy} act=${state.act} />
        <${InviteForm} model=${model} busy=${state.busy} act=${state.act} />`
      : html`<${FriendsStart} model=${model} state=${state} />`}
    ${["reconnecting", "leaving", "joining", "starting"].includes(model.phase) && model.room ? html`<p class="friends-phase" role="status">${model.title}</p>` : null}
    ${model.room && reason ? html`<p class="friends-source meta">${reason}</p>` : null}
    ${model.room && state.snapshot.display?.reason ? html`<p class="friends-source meta" role="status">${state.snapshot.display.reason}</p>` : null}
    ${copy ? html`<p class="friends-copy-state" role="status">${copy}</p>` : null}
    ${privateCode ? html`<label class="friends-code-label">Your player code<input readonly data-private-input="true"
      value=${privateCode} onfocus=${event => event.currentTarget.select()} /></label>` : null}`;
}

function FriendsProblem({ state }) {
  const issue = state.error || state.snapshot?.error;
  if (!issue) return null;
  const retry = Boolean(state.snapshot?.error);
  return html`<div class="friends-error" role="alert"><span>${issue}</span>
    ${state.snapshot?.display?.retryable !== false ? html`<button type="button" disabled=${Boolean(state.busy)}
      onclick=${retry ? () => state.act("retry", {}) : state.refresh}>${retry ? "Retry" : "Refresh"}</button>` : null}
  </div>`;
}

function FriendsPanel({ state, onClose }) {
  const model = friendsModel(state.snapshot);
  const [copy, setCopy] = useState(null), [privateCode, setPrivateCode] = useState(null);
  const root = useRef(null);
  useUiLog(root, READERS);
  async function copyIdentity() {
    setCopy(null); setPrivateCode(null);
    const result = await state.act("identity", {});
    if (typeof result?.code !== "string" || !result.code) return;
    try { await navigator.clipboard.writeText(result.code); setCopy("Player code copied."); }
    catch { setPrivateCode(result.code); setCopy("Select your player code and copy it."); }
  }
  return html`<${Modal} title="Friends" icon="friends" size="large" onClose=${onClose}
      footer=${state.snapshot?.available ? html`<${FriendsFooter} state=${state} model=${model} copyIdentity=${copyIdentity} />` : null}>
    <div ref=${root} class="friends-surface" data-phase=${state.snapshot ? model.phase : "loading"}>
      ${!state.snapshot ? html`<p class="friends-loading" role="status">${state.error ? "Friends couldn’t load." : "Loading Friends…"}</p>`
        : !model.available ? html`<p class="friends-unavailable">${state.snapshot.unavailable_reason || "Friends is unavailable in this build."}</p>`
        : html`<${FriendsReady} model=${model} state=${state} copy=${copy} privateCode=${privateCode} />`}
      <${FriendsProblem} state=${state} />
    </div>
  </${Modal}>`;
}

export function FriendsControl({ className = "" }) {
  const state = useFriends();
  return html`<${FriendsEntry} state=${state} className=${className} />`;
}

export function FriendsEntry({ state, className = "" }) {
  const model = friendsModel(state.snapshot);
  const [open, setOpen] = useState(false);
  const label = model.badge ? `Friends, ${model.badgeLabel}` : "Friends";
  return html`<button type="button" class=${`friends-control ${className}`} aria-haspopup="dialog"
      aria-expanded=${open} aria-label=${label} title=${label}
      onclick=${() => setOpen(true)}>
    <${Icon} name="friends" /><span class="friends-control-label">Friends</span>
    ${model.badge ? html`<span class="friends-badge" aria-hidden="true">${model.badge}</span>` : null}
  </button><span class="sr-only" role="status" aria-live="polite">${model.badge ? label : ""}</span>
  ${open ? html`<${FriendsPanel} state=${state} onClose=${() => setOpen(false)} />` : null}`;
}
