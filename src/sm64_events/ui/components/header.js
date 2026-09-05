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
import { SetupModal } from "./setupmodal.js";

const html = htm.bind(h);

// Sticks for the browser SESSION only (his ask: "does not reopen until the
// next app session") -- sessionStorage, never localStorage.
const SETUP_NOT_NOW_KEY = "sm64.setupNotNow";

function seenSetupPrompt() {
  try { return sessionStorage.getItem(SETUP_NOT_NOW_KEY) === "1"; }
  catch { return false; }
}

function rememberSetupPrompt() {
  try { sessionStorage.setItem(SETUP_NOT_NOW_KEY, "1"); } catch { /* private mode */ }
}

// Auto-open criteria: practicing on the emulator (or nothing chosen yet --
// core/modes.py defaults there), the capture layer has never been touched,
// and Project64 is at least findable (a folder we know, or it is running
// right now) -- otherwise the modal would open onto a checklist with nothing
// for him to act on yet. Never while the layer is already active.
// His rule (2026-09-05, the first launch after the layer shipped): "If the
// user hasn't installed / set up, it should trigger the screen. This should
// trigger for existing users." So the screen opens whenever the layer is
// not consented (or was and regressed) -- whether or not Project64 has been
// seen yet: the Project64 row is itself the door for that. Only a build with
// no layer to install, or the N64 platform, stays quiet.
function shouldOfferSetup(setup) {
  if (!setup || setup.platform === "n64") return false;
  const emu = setup.emu;
  if (!emu) return false;
  // ...and for a layer this build has outgrown: the steps to update it are
  // the onboarding, so the screen carries them the moment the page opens.
  const stale = !!emu.consented_at && emu.wrapper_present && emu.wrapper_current === false;
  return emu.state === "not_installed" || emu.state === "regressed" || stale;
}

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

  // The setup screen (setupmodal.js): a manual "Setup" entry, PLUS a once-
  // per-session auto-open the first time this page loads onto a fresh
  // capture layer -- see shouldOfferSetup's own comment for the criteria.
  // Closing it for ANY reason ("Not now", Esc, the backdrop) remembers that
  // for the rest of this browser session, same as a manual open never
  // re-triggers it: only the criteria above decide whether it opens itself.
  const [setupOpen, setSetupOpen] = useState(false);
  useEffect(() => {
    if (seenSetupPrompt()) return;
    send("GET", "/api/setup").then((setup) => {
      if (shouldOfferSetup(setup)) setSetupOpen(true);
    }).catch(() => {});
  }, []);
  const closeSetup = () => {
    setSetupOpen(false);
    rememberSetupPrompt();
  };

  useEffect(() => {
    if (!settingsOpen) return;
    const onKey = (event) => { if (event.key === "Escape") closeSettings(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [settingsOpen, closeSettings]);

  // The Game version setting (GET|PUT /api/mode, docs/api.md "Game version").
  // Loads the same way everything else in this drawer does: fetch on open,
  // never on every render. Applies LIVE -- `PUT /api/mode` flips the
  // standards store's grading version and broadcasts `game_version_changed`
  // itself, so no restart copy belongs here (his ruling 2026-08-15: freely
  // swap between ROMs).
  const [gameMode, setGameMode] = useState(null);
  const loadGameMode = () => send("GET", "/api/mode").then(setGameMode).catch(() => setGameMode(null));
  useEffect(() => { if (settingsOpen) loadGameMode(); }, [settingsOpen]);
  async function putGameVersion(version) {
    setGameMode(await send("PUT", "/api/mode", { version }));
  }

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

  const [reportBusy, setReportBusy] = useState(false);
  const [reportMsg, setReportMsg] = useState("");

  async function debugReport() {
    if (reportBusy) return;
    setReportBusy(true);
    try {
      const r = await send("POST", "/api/diagnostics");
      try { await send("POST", "/api/replay/reveal", { path: r.path }); }
      catch { /* best effort - dev servers have no reveal route */ }
      setReportMsg(`Report saved: ${r.path}`);
    } catch (e) {
      console.error(e);
      setReportMsg("Could not generate the report.");
    }
    setReportBusy(false);
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

  // Built as plain JS, not inline htm interpolation: a text run meeting an
  // interpolation across a line break fuses words together (ui-core.md), and
  // this sentence has to wrap in the drawer's narrow column.
  const gameVersionNote = gameMode
    ? `Graded on ${gameMode.effective.toUpperCase()} standards.`
      + (gameMode.version === "auto" ? " Auto-detect is US on the emulator." : "")
    : null;

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
            identity=${`${mareloTurn.marelo ? mareloTurn.marelo.label : ""}|${v ? v.rank_mode : ""}|${v && v.game_version ? v.game_version.effective : ""}`} />
      </div>

      ${/* The Clock card left the bar 2026-08-08 (user): the default is
            always Usamune IGT, so a control nobody changes was spending a
            column the rank card can use. Its select lives in the settings
            drawer's Display section now. */
        null}
      ${/* Labelled "Grading", not "Rank": it sets HOW a rank is graded, and
            it sits directly beside the route rank card, which shows what your
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
            <button type="button" onclick=${debugReport} disabled=${reportBusy}
                title="Write a debug report file and show it in Explorer - attach it when reporting a bug">
              <${Icon} name="shield" />
              ${reportBusy ? "Generating…" : "Debug report"}
            </button>
            <button type="button" onclick=${() => setSetupOpen(true)}>
              <${Icon} name="settings" />Setup
            </button>
          </div>
          ${t.updateMsg && html`<p class="settings-note">${t.updateMsg}</p>`}
          ${reportMsg && html`<p class="settings-note">${reportMsg}</p>`}
        </section>

        ${/* This is the same record feature/console-support edits from its
             own "Console" section (Tracking mode + N64 setup) -- the option
             text is "JP", never "Japan" (his correction). When that branch
             merges main it drops its own Game version dropdown here and
             keeps only Tracking mode + N64 setup, so the field lives in
             exactly one place. Dated 2026-08-15. */""}
        <section class="settings-section">
          <h3>Game</h3>
          <label class="settings-field">
            <span>Game version</span>
            <select value=${gameMode ? gameMode.version : "auto"}
                disabled=${!gameMode}
                onchange=${(e) => putGameVersion(e.target.value)}>
              <option value="auto">Auto-detect</option>
              <option value="jp">JP</option>
              <option value="us">US</option>
            </select>
          </label>
          ${gameVersionNote && html`<p class="settings-note">${gameVersionNote}</p>`}
          ${gameMode && gameMode.unsupported && html`<p class="settings-note">Emulator tracking has no verified JP addresses yet — detection stays US while grading uses JP standards.</p>`}
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
            <br />
            <a href="/ui/sync.html" target="_blank" rel="noopener">Open the
              version sync dashboard</a> — every US/JP address, behaviour and
            calibration gate, and how much of each is verified.
          </p>
          <label class="settings-field">
            <span>Dust-trick counts</span>
            <input type="checkbox" checked=${t.showDust}
                onchange=${(e) => t.pickShowDust(e.target.checked)} />
          </label>
          <p class="settings-note">Show dustless rollout/jump counts on
            attempt rows and in the stats menu. Off by default while the
            detection is being tuned.</p>
          ${/* Was a card in the top bar until 2026-08-08. The default is
               always Usamune IGT (x-cam timing), so the control earns a
               hidden shelf here, not a column up top. */""}
          <label class="settings-field">
            <span>Timing clock</span>
            <select value=${t.clock}
                onchange=${(e) => t.pickClock(e.target.value)}>
              ${CLOCK_OPTIONS.map(([key, label]) =>
                html`<option value=${key}>${label}</option>`)}
            </select>
          </label>
          <p class="settings-note">Which clock grades star times. Usamune IGT
            (x-cam timing) is the community standard and the default; Anchor
            to grab times from your anchor instead, for star-grab practice.</p>
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
    ${setupOpen && html`<${SetupModal} onClose=${closeSetup} initialPane="emu" />`}
  </header>`;
}
