import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { send } from "../api.js";
import { RANK_MODE_OPTIONS } from "./ranks.js";
import { Icon } from "./icons.js";
import { MareloBar } from "./marelo.js";
import { ICON_STYLES } from "./rankicon.js";
import { celebrationsEnabled, setCelebrationsEnabled,
         CLIMB_SKIP_STYLES, climbSkipStyle, setClimbSkipStyle } from "./celebrate.js";

const html = htm.bind(h);

const CLOCK_OPTIONS = [["igt", "Usamune IGT"], ["rta", "Anchor → grab"]];

// One context card = one hit target. The practice-target card already
// highlighted and opened as a whole because it IS a <button>; the three
// <select> cards only reacted on the select itself, which read as an
// inconsistency (user, 2026-07-25). Here the native <select> is stretched
// over the entire card and painted transparent (see .context-select in
// index.html), so a click anywhere opens the real dropdown — which means the
// closed-state value and the chevron are drawn by us. Both the value and the
// <option>s come from the SAME `options` list, so they cannot disagree.
function ContextSelect({ icon, label, options, value, onChange, id, name,
                        title, empty }) {
  const picked = options.find(([optionValue]) => optionValue === value);
  return html`<div
      class=${`context-control${options.length ? " context-select" : ""}`}>
    <${Icon} name=${icon} size=${19} />
    <span class="context-control-copy">
      <span class="context-label">${label}</span>
      <span class="context-value">${picked ? picked[1] : empty}</span>
    </span>
    ${options.length ? html`<${Icon} name="chevron" size=${16} />` : null}
    ${/* title rides the SELECT, not the card: it covers the card anyway, so
          the tooltip still answers a hover anywhere — and this way it also
          reaches a screen reader as the combobox's description. */
      options.length ? html`<select id=${id} name=${name} aria-label=${label}
        title=${title} value=${value} onchange=${onChange}>
      ${options.map(([optionValue, optionLabel]) =>
        html`<option value=${optionValue}>${optionLabel}</option>`)}
    </select>` : null}
  </div>`;
}

export function Header({ t, settingsOpen, closeSettings, setTab }) {
  const v = t.view;
  const [restarting, setRestarting] = useState(false);
  const [celebrateOn, setCelebrateOn] = useState(celebrationsEnabled());
  const [skipStyle, setSkipStyle] = useState(climbSkipStyle());

  // marelo is store-owned (store.js) -- app.js reads the same object to
  // decide whether the rank-up overlay is showing, so the header and the
  // overlay can never disagree about a pending celebration.
  const openMarelo = () => setTab("Rank");

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

      ${/* Slot 2 used to be a PRACTICE TARGET card naming the current target
            and opening the picker. Removed 2026-07-26 (user): the
            Active-target card and the quick-select row both name the target
            already, and its own pick was mostly a dead end -- you cannot
            practice Shifting Sand Land while loaded into Lethal Lava Land.
            The picker moved to the Active-target card (targetpicker.js) and
            the MARELO bar took the space, which is how it comes to sit in the
            middle of the bar instead of on a second row of its own.
            The wrapper around it is NOT decoration: MareloBar renders null
            until the first /api/marelo lands, and a null grid child is no
            child at all -- the clock card would slide into this column and
            the whole bar would shift left for a beat. An always-present cell
            holds the place, wearing its neighbours' panel while empty. */
        null}
      <div class="marelo-slot">
        <!-- The identity prop is what tells a genuine rank RISE apart from
             the same bar being handed a different measurement: switching the
             active scope re-rates against a different set of entities, and
             changing the grading mode re-grades every one of them. Both can
             legitimately produce a higher rank nobody earned, and neither may
             fire a level-up climb (ui/rankclimb.js). -->
        <${MareloBar} marelo=${t.marelo} onOpen=${openMarelo}
            identity=${`${t.marelo ? t.marelo.label : ""}|${v ? v.rank_mode : ""}`} />
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
  </header>`;
}
