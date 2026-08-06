import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { send } from "../api.js";
import { RANK_MODE_OPTIONS } from "./ranks.js";
import { Icon } from "./icons.js";
import { ContextSelect } from "./contextselect.js";
import { RouteRankCard } from "./marelo.js";
import { Modal } from "./modal.js";
import { RUN_ACTIVE } from "../store.js";
import { ICON_STYLES } from "./rankicon.js";
import { useMareloTurn } from "../mareloturn.js";
import { celebrationsEnabled, setCelebrationsEnabled,
         CLIMB_SKIP_STYLES, climbSkipStyle, setClimbSkipStyle } from "./celebrate.js";

const html = htm.bind(h);

const CLOCK_OPTIONS = [["igt", "Usamune IGT"], ["rta", "Anchor → grab"]];

// The route IS the rank scope, so changing it mid-run would re-rate a run
// against a plan it is not following. `store.js::pickRoute` refuses and returns
// RUN_ACTIVE rather than asking anything itself; the question belongs here,
// where there is a shell to draw it in.
//
// His wording, and the button labels follow it exactly: *"they should be told
// they can't because they have an active run. You're allowed to change it, just
// that it will also stop their active run. The dialogue should warn them. you
// can press ok if you want to change it and cancel your run."* So this is a
// warning with a way through, never a refusal — and Cancel is the safe default,
// since the destructive half is the one that throws away a run in progress.
//
// The shared `Modal` rather than `window.confirm`: a native dialog blocks the
// event loop (the WS keeps queueing while the run it is asking about carries
// on), cannot be styled, and is invisible to the responsive rig.
function RunScopeWarning({ t, pending, onDone }) {
  if (pending === undefined) return null;
  const routeName = (t.routes || []).find((r) => r.id === pending);
  const going = pending == null ? "Overall" : (routeName ? routeName.name : "another route");
  return html`<${Modal} title="That will end your run"
      onClose=${() => onDone(false)}
      footer=${html`
        <button onclick=${() => onDone(false)}>Keep running</button>
        <button class="primary-button" onclick=${() => onDone(true)}>
          Switch to ${going} and end the run
        </button>`}>
    <p>You have a run in progress. The route you are rating against is the route
      you are running, so switching to <b>${going}</b> ends it.</p>
    <p class="meta">Nothing you have already recorded is lost — the run itself
      stops, and its splits stay in your history.</p>
  <//>`;
}

export function Header({ t, settingsOpen, closeSettings }) {
  // `undefined` = nothing pending. `null` is a real pick (Overall), which is
  // why this is not a boolean — the same distinction store.js draws for a
  // route intent.
  const [pendingScope, setPendingScope] = useState(undefined);
  const pickRouteOrWarn = (id) => {
    if (t.pickRoute(id) === RUN_ACTIVE) setPendingScope(id);
  };
  const resolveScope = async (proceed) => {
    const id = pendingScope;
    setPendingScope(undefined);
    if (!proceed) return;
    await t.endRun();
    t.pickRoute(id, { confirmed: true });
  };
  const v = t.view;
  const [restarting, setRestarting] = useState(false);
  const [celebrateOn, setCelebrateOn] = useState(celebrationsEnabled());
  const [skipStyle, setSkipStyle] = useState(climbSkipStyle());
  const mareloTurn = useMareloTurn(t.marelo);

  useEffect(() => {
    if (!settingsOpen) return;
    const onKey = (event) => { if (event.key === "Escape") closeSettings(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [settingsOpen, closeSettings]);

  async function restartServer() {
    if (restarting) return;
    setRestarting(true);
    try {
      await send("POST", "/api/admin/restart");
    } catch (e) {
      console.error(e); // the request may drop as the server restarts
    }
    setTimeout(() => setRestarting(false), 8000);
  }

  const active = v && v.session.id;

  async function newSession() {
    await send("POST", "/api/session/new", {});
    t.refresh();
  }

  async function pickSession(e) {
    const val = e.target.value;
    if (val === "lifetime") { t.pickScope("lifetime"); return; }
    const sid = Number(val);
    if (sid !== active) {
      await send("POST", "/api/session/continue", { session_id: sid });
    }
    t.pickScope("session");
    t.refresh();
  }

  async function removeSession(sid) {
    if (!window.confirm(`Delete session ${sid} and all its data? This cannot be undone.`)) return;
    await send("DELETE", `/api/session/${sid}`);
    t.refresh();
  }

  async function wipeAll() {
    const msg = t.scope === "lifetime"
      ? "Wipe ALL practice data — every session, every star and segment?\n"
        + "All attempts, sessions and PBs are permanently removed. Segment "
        + "definitions, markers and settings are kept.\nThis cannot be undone."
      : `Wipe all data in session ${active}?\n`
        + "Its attempts and any PBs saved from them are permanently removed "
        + "(the session stays open).\nThis cannot be undone.";
    if (!window.confirm(msg)) return;
    await send("POST", "/api/wipe", { kind: "all", scope: t.scope });
    closeSettings();
    t.refresh();
  }

  const sessionOptions = v ? [["lifetime", "Lifetime"], ...v.sessions.map(
    (s) => [String(s.id),
            `Session ${s.id}${s.id === active ? " ●" : ""} · ${s.attempts}`],
  )] : [];

  return html`<header class="context-shell">
    <div class="context-bar" aria-label="Practice context">
      <${ContextSelect} icon="sessions" label="Session" id="session-select"
        name="session" options=${sessionOptions} onChange=${pickSession}
        value=${t.scope === "lifetime" ? "lifetime" : String(active)}
        empty="Loading…" />

      ${/* The wrapper carries `container-type: inline-size` so the card's own
            @container rules measure THIS COLUMN rather than the viewport --
            the sidebar's 1180px step means the column's width is not
            monotonic in window width. It used to also hold the grid cell open
            while MareloBar rendered null; the card no longer does that,
            because it now hosts the route picker and the control has to exist
            before the rating does. */
        null}
      <div class="marelo-slot">
        <!-- The identity prop is what tells a genuine rank RISE apart from
             the same card being handed a different measurement: switching the
             active scope re-rates against a different set of entities, and
             changing the grading mode re-grades every one of them. Both can
             legitimately produce a higher rank nobody earned, and neither may
             fire a level-up climb (ui/rankclimb.js). -->
        ${/* The card shows the payload ui/mareloturn.js says it may show --
             the OLD one while a scope rank-up is still waiting behind the
             entity banners, so it does not quietly climb to the new rank
             while they are animating ("it should be as if nothing has
             changed before then"). The celebration overlay reads the same
             hook, so the two can never disagree about whose turn it is. */""}
        <${RouteRankCard} marelo=${mareloTurn.marelo} routes=${t.routes}
            activeRouteId=${t.activeRouteId} onPickRoute=${pickRouteOrWarn}
            identity=${`${mareloTurn.marelo ? mareloTurn.marelo.label : ""}|${v ? v.rank_mode : ""}`} />
      </div>

      <${ContextSelect} icon="clock" label="Clock" id="clock-select"
        name="clock" options=${CLOCK_OPTIONS} value=${t.clock}
        onChange=${(e) => t.pickClock(e.target.value)} empty="—" />

      ${/* Labelled "Grading", not "Rank": it sets HOW a rank is graded, and
            it now sits two cards from the MARELO bar, which shows what your
            rank IS. Two cards reading RANK side by side, one of them a mode,
            is the kind of correct-but-unexplained pairing that reads as a
            rendering fault. The id/name stay rank_mode -- the wire contract
            is unchanged. */
        null}
      <${ContextSelect} icon="rank" label="Grading" id="rankmode-select"
        name="rank_mode" options=${v ? RANK_MODE_OPTIONS : []}
        value=${v ? v.rank_mode : null}
        title="Grade medals by saved PB or by a recent/best average"
        onChange=${(e) => send("PUT", "/api/ranks/mode",
          { mode: e.target.value }).then(() => t.refresh())} empty="—" />
    </div>

    ${settingsOpen && html`<div class="settings-backdrop" onclick=${closeSettings}>
      <aside class="settings-drawer" role="dialog" aria-modal="true"
          aria-label="Settings"
          onclick=${(e) => e.stopPropagation()}>
        <div class="settings-head">
          <div><span class="eyebrow">System</span><h2>Settings</h2></div>
          <button type="button" class="icon-button" aria-label="Close settings"
              onclick=${closeSettings}><${Icon} name="close" /></button>
        </div>

        <section class="settings-section">
          <h3>Trainer</h3>
          <div class="settings-actions">
            <button type="button" onclick=${t.togglePause}
                title=${t.pauseReason === "manual"
                  ? "Resume event and replay processing"
                  : "Pause all event and replay processing"}>
              <${Icon} name=${t.pauseReason === "manual" ? "play" : "pause"} />
              ${t.pauseReason === "manual" ? "Resume trainer" : "Pause trainer"}
            </button>
            <button type="button" onclick=${restartServer} disabled=${restarting}>
              <${Icon} name="restart" />
              ${restarting ? "Restarting…" : "Restart server"}
            </button>
            <button type="button" onclick=${t.checkUpdates}>
              <${Icon} name="updates" />Check for updates
            </button>
          </div>
          ${t.updateMsg && html`<p class="settings-note">${t.updateMsg}</p>`}
        </section>

        <section class="settings-section">
          <h3>Display</h3>
          <label class="settings-field">
            <span>Star icons</span>
            <select value=${t.starIcons}
                onchange=${(e) => t.pickStarIcons(e.target.value)}>
              <option value="classic">Classic gold star</option>
              <option value="course">Per-star course icons</option>
            </select>
          </label>
          <p class="settings-note">Per-star icons show each star's
            split-icon artwork in the course selector row.</p>
          <label class="settings-field">
            <span>Rank icons</span>
            <select value=${t.rankIcons}
                onchange=${(e) => t.pickRankIcons(e.target.value)}>
              ${Object.entries(ICON_STYLES).map(([key, style]) =>
                html`<option value=${key}>${style.label}</option>`)}
            </select>
          </label>
          <p class="settings-note">Choose how a rank is drawn everywhere in
            the app -- Mario caps or medals.</p>
          <label class="settings-field">
            <span>Celebrate rank-ups</span>
            <input type="checkbox" checked=${celebrateOn}
                onchange=${(e) => {
                  setCelebrationsEnabled(e.target.checked);
                  setCelebrateOn(e.target.checked);
                }} />
          </label>
          <p class="settings-note">Show a full-screen cap climb when your
            MARELO rank rises. The rank-up is acknowledged either way, so
            turning this off never leaves one waiting to fire later.</p>
          <label class="settings-field">
            <span>Skipped ranks</span>
            <select value=${skipStyle}
                onchange=${(e) => {
                  setClimbSkipStyle(e.target.value);
                  setSkipStyle(e.target.value);
                }}>
              ${Object.entries(CLIMB_SKIP_STYLES).map(([key, style]) =>
                html`<option value=${key}>${style.label}</option>`)}
            </select>
          </label>
          <p class="settings-note">When one PB climbs through a whole rank you
            never stop in, either pop its wings out on the way past or keep the
            wings on and chain the caps together.</p>
          ${/* An ORIGIN-RELATIVE href, which is the whole point of putting the
               link here: the server's port moves (8064 frozen, 8065 from
               source, whatever run-test-server.bat was given), and a written-
               down URL is wrong the moment it does -- which is exactly how
               this page went missing on its first day (user, 2026-07-27: "I
               clicked run-test-server.bat and restarted our server on 8066,
               but I don't see the demo page loading"). A relative link cannot
               name the wrong port. */""}
          <p class="settings-note">
            <a href="/ui/tune.html" target="_blank" rel="noopener">Open the
              rank-up tuning page</a> — play any climb, tune every timing,
            and save the result straight back into the code.
          </p>
          <p class="settings-note">
            <a href="/ui/tunemarelo.html" target="_blank" rel="noopener">Open the
              overall rank-up tuning page</a> — fly the card out, tune the
            flight and the hold, and save the result straight back into the code.
          </p>
          <p class="settings-note">
            <a href="/ui/tunelog.html" target="_blank" rel="noopener">Open the
              practice log tuning page</a> — dial the card's layout at two
            widths side by side, and save the result straight back into the code.
            <br />
            <a href="/ui/tuneselector.html" target="_blank" rel="noopener">Open the
              selector exchange tuning page</a> — swap the row's cards, tune how
            the old set leaves and the new set arrives, and save it into the code.
            <br />
            <a href="/ui/tunefeed.html" target="_blank" rel="noopener">Open the
              feed and disclosure tuning page</a> — push a card into the log to
            tune the arrival and the shove it gives the cards below, open one to
            tune every dropdown in the app, and save it into the code.
          </p>
          <label class="settings-field">
            <span>Dust-trick counts</span>
            <input type="checkbox" checked=${t.showDust}
                onchange=${(e) => t.pickShowDust(e.target.checked)} />
          </label>
          <p class="settings-note">Show dustless rollout/jump counts on
            attempt rows and in the stats menu. Off by default while the
            detection is being tuned.</p>
        </section>

        <section class="settings-section">
          <div class="settings-section-head">
            <div><h3>Sessions</h3><p>Switch, start, or remove practice sessions.</p></div>
            <button type="button" onclick=${newSession} disabled=${!v}>
              <${Icon} name="plus" />New session
            </button>
          </div>
          ${v ? html`<div class="session-list">
            ${v.sessions.map((s) => html`<div class="session-row">
              <button type="button" class="session-pick"
                  onclick=${() => pickSession({ target: { value: String(s.id) } })}>
                <span>Session ${s.id}${s.id === active ? " · Active" : ""}</span>
                <span>${s.attempts} attempts · ${(s.started_utc || "").slice(0, 10)}</span>
              </button>
              ${s.id !== active && html`<button type="button" class="danger-icon"
                  aria-label=${`Delete session ${s.id}`} onclick=${() => removeSession(s.id)}>×</button>`}
            </div>`)}
          </div>` : html`<p class="settings-note">Session data is loading.</p>`}
        </section>

        <section class="settings-section danger-zone">
          <h3>Data</h3>
          <p>Replay storage limits are available by selecting the REC status.</p>
          <button type="button" class="danger-button" onclick=${wipeAll} disabled=${!v}>
            Clear ${t.scope === "lifetime" ? "all practice data" : `session ${active} data`}
          </button>
        </section>
      </aside>
    </div>`}
    ${/* Mounted inside the header because that is where the control lives, and
         the Modal shell portals itself out of the flow -- keeping it beside
         the card it is about is what stops a second surface growing its own
         copy of this question. */""}
    <${RunScopeWarning} t=${t} pending=${pendingScope} onDone=${resolveScope} />
  </header>`;
}
