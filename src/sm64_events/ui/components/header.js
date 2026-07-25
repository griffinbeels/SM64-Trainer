import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { send } from "../api.js";
import { RANK_MODE_OPTIONS } from "./ranks.js";
import { StratModal } from "./stratmodal.js";
import { Icon } from "./icons.js";
import { EntityPicker } from "./entitymodal.js";
import { optionIcon, parseStarId, starId, starOptionsFromCatalog } from "../entities.js";

const html = htm.bind(h);

export function Header({ t, settingsOpen, closeSettings }) {
  const v = t.view;
  const tgt = v && v.target;
  const [editing, setEditing] = useState(false);
  const [restarting, setRestarting] = useState(false);

  useEffect(() => {
    if (!settingsOpen && !editing) return;
    const onKey = (event) => {
      if (event.key !== "Escape") return;
      if (editing) setEditing(false);
      else closeSettings();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [settingsOpen, editing, closeSettings]);

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

  const targetName = tgt && tgt.kind === "segment"
    ? tgt.segment_name
    : tgt && tgt.course_id !== null
      ? `${tgt.course_name} · ${tgt.star_name}`
      : "Choose a practice target";
  const running = [...t.armedOrder].reverse()
    .map((id) => t.armedNames[id] || `segment ${id}`).join(" · ");

  return html`<header class="context-shell">
    <div class="context-bar" aria-label="Practice context">
      <label class="context-control">
        <${Icon} name="sessions" size=${19} />
        <span class="context-control-copy">
          <span class="context-label">Session</span>
          ${v ? html`<select id="session-select" name="session"
              value=${t.scope === "lifetime" ? "lifetime" : String(active)}
              onchange=${pickSession}>
            <option value="lifetime">Lifetime</option>
            ${v.sessions.map((s) => html`<option value=${String(s.id)}>
              Session ${s.id}${s.id === active ? " ●" : ""} · ${s.attempts}</option>`)}
          </select>` : html`<span>Loading…</span>`}
        </span>
      </label>

      <button type="button" class="context-control target-context"
          disabled=${!v} onclick=${() => setEditing(!editing)}
          title="Choose a star, segment, or strategy">
        <${Icon} name="target" size=${19} />
        <span class="context-control-copy">
          <span class="context-label">${running ? "Running" : "Practice target"}</span>
          <span class="context-value">${running || targetName}</span>
        </span>
        <${Icon} name="chevron" size=${16} />
      </button>

      <label class="context-control">
        <${Icon} name="clock" size=${19} />
        <span class="context-control-copy">
          <span class="context-label">Clock</span>
          <select id="clock-select" name="clock" value=${t.clock}
              onchange=${(e) => t.pickClock(e.target.value)}>
            <option value="igt">Usamune IGT</option>
            <option value="rta">Anchor → grab</option>
          </select>
        </span>
      </label>

      <label class="context-control">
        <${Icon} name="rank" size=${19} />
        <span class="context-control-copy">
          <span class="context-label">Rank</span>
          ${v ? html`<select id="rankmode-select" name="rank_mode"
              value=${v.rank_mode}
              title="Grade medals by saved PB or by a recent/best average"
              onchange=${(e) => send("PUT", "/api/ranks/mode",
                { mode: e.target.value }).then(() => t.refresh())}>
            ${RANK_MODE_OPTIONS.map(([k, label]) => html`<option value=${k}>${label}</option>`)}
          </select>` : html`<span>—</span>`}
        </span>
      </label>
    </div>

    ${editing && v && html`<div class="context-editor">
      <${TargetEditor} t=${t} close=${() => setEditing(false)} />
    </div>`}

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

function TargetEditor({ t, close }) {
  const v = t.view;
  const tgt = v.target;
  const [course, setCourse] = useState(tgt.course_id ?? 1);
  const [star, setStar] = useState(tgt.star_id ?? 0);
  const lastStratFor = (c, s) => v.last_strat_by_star[`${Number(c)}:${Number(s)}`] ?? "";
  const stratsFor = (c, s) => v.strategies[`${Number(c)}:${Number(s)}`] || [];
  const [strat, setStrat] = useState(lastStratFor(course, star));
  const [showStratModal, setShowStratModal] = useState(false);
  const [stratNonce, setStratNonce] = useState(0);

  function pickStar(c, s) {
    setCourse(c); setStar(s);
    setStrat(lastStratFor(c, s));
    setShowStratModal(false);
  }

  async function apply() {
    await send("POST", "/api/target", {
      course_id: Number(course), star_id: Number(star),
      strat_tag: strat || null,
    });
    close(); t.refresh();
  }

  const options = stratsFor(course, star);

  const iconContext = {
    courseIcons: t.courseIcons || {},
    starIconsMode: t.starIcons || "course",
  };
  // The group IS a course, so its heading carries the portrait — once per
  // course rather than repeated on all seven star rows (spec decision 3).
  const starGroups = starOptionsFromCatalog(v.catalog).map((group) => ({
    ...group,
    icon: optionIcon("course", String(parseStarId(group.options[0].id).course),
                     iconContext),
  }));

  return html`<div class="target-editor-card" role="dialog" aria-modal="true"
      aria-label="Choose a practice target">
    <div class="target-editor-head">
      <div><span class="eyebrow">Practice target</span><b>Choose a star</b></div>
      <button type="button" class="icon-button" aria-label="Close target editor"
          onclick=${close}><${Icon} name="close" /></button>
    </div>
    <div class="target-editor-fields">
      <label>Star<${EntityPicker} groups=${starGroups}
        value=${starId(Number(course), Number(star))}
        title="Choose a star"
        iconFor=${(id) => optionIcon("star", id, iconContext)}
        onChange=${(id) => {
          // One control, still two fields on the wire: unpack and reuse the
          // existing pickStar so the strategy list re-resolves for the new star.
          const picked = parseStarId(id);
          pickStar(picked.course, picked.star);
        }} /></label>
      <label>Strategy<select key=${`hstrat-${stratNonce}`} value=${strat}
          onchange=${(changeEvent) => changeEvent.target.value === "__new__"
            ? setShowStratModal(true) : setStrat(changeEvent.target.value)}>
        <option value="">No strategy</option>
        ${options.map((s) => html`<option value=${s}>${s}</option>`)}
        ${strat && !options.includes(strat)
          ? html`<option value=${strat}>${strat}</option>` : null}
        <option value="__new__">+ New strategy…</option>
      </select></label>
    </div>
    <div class="target-editor-actions">
      <button type="button" onclick=${close}>Cancel</button>
      <button type="button" class="primary-button" onclick=${apply}>Set target</button>
    </div>
    ${showStratModal ? html`<${StratModal}
        entity=${`star:${Number(course)}:${Number(star)}`} existing=${options}
        onSaved=${(stratName) => { setShowStratModal(false); setStrat(stratName); }}
        onClose=${() => { setShowStratModal(false); setStratNonce((n) => n + 1); }} />` : null}
  </div>`;
}
