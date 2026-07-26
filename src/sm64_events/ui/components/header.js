import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { RANK_MODE_OPTIONS } from "./ranks.js";
import { Icon } from "./icons.js";
import { MareloBar } from "./marelo.js";
import { celebrationsEnabled, setCelebrationsEnabled } from "./celebrate.js";
import { PickerDialog } from "./entitymodal.js";
import { StrategyStep } from "./strategystep.js";
import { courseUnionGroups, optionIcon, parseSegmentId,
         segmentLevelsOf, starId } from "../entities.js";

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
  const tgt = v && v.target;
  const [editing, setEditing] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [celebrateOn, setCelebrateOn] = useState(celebrationsEnabled());
  // The target picker's rank badges (courseUnionGroups' optional 4th arg).
  // Keyed on `editing` alone, never fetched on the header's other renders --
  // it re-renders on every WebSocket event while this dialog is normally
  // closed.
  const [targetRanks, setTargetRanks] = useState({});
  useEffect(() => {
    if (!editing) return;
    let alive = true;
    getJSON("/api/target/ranks")
      .then((ranks) => { if (alive) setTargetRanks(ranks); })
      .catch((fetchError) => console.error(fetchError));
    return () => { alive = false; };
  }, [editing]);

  // marelo is store-owned (store.js) -- app.js reads the same object to
  // decide whether the rank-up overlay is showing, so the header and the
  // overlay can never disagree about a pending celebration.
  const openMarelo = () => setTab("Rank");

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

  const sessionOptions = v ? [["lifetime", "Lifetime"], ...v.sessions.map(
    (s) => [String(s.id),
            `Session ${s.id}${s.id === active ? " ●" : ""} · ${s.attempts}`],
  )] : [];

  // Re-picking the SAME star that's already the target, with a new (or
  // first) strategy, leaves the projector's target tuple unchanged -- no
  // WebSocket event in store.js's REFRESH_ON fires for that specific case
  // (set_target's truthy-strat_tag branch never publishes strat_set, unlike
  // set_target_segment's; verified empirically against TrackerService).
  // Every other /api/target call site (stagebanner.js, practice.js, this
  // card's own predecessor) refreshes explicitly right after for exactly
  // this reason -- this picker must too, on every dismissal (a plain
  // Esc/backdrop close just refetches data that didn't change, which is
  // harmless, and keeps one path rather than two).
  function closeTargetPicker() { setEditing(false); t.refresh(); }

  // Only ever CALLED while the dialog is open (the `editing && v` guard at
  // the call site short-circuits before this runs) -- courseUnionGroups
  // walks every course and segment, and the header re-renders on every
  // WebSocket event, so this must not run on the other renders.
  function renderTargetPicker() {
    // segmentLevels + iconOverrides are NOT optional here: without them
    // every segment cell falls through optionIcon's chain to a plain gold
    // star, while the banner and the route editor show its real art — and a
    // user's explicit per-segment icon override is ignored (whole-branch
    // review I1, 2026-07-25).
    const iconContext = {
      courseIcons: t.courseIcons || {},
      starIconsMode: t.starIcons || "course",
      iconOverrides: v.icon_overrides || {},
      segmentLevels: segmentLevelsOf(t.segments),
    };
    // Layer 1 is a grid of COURSES carrying their portraits; layer 2 is that
    // course's stars AND the segments that begin in it, because both are
    // things you practice and /api/target already takes either (user,
    // 2026-07-25).
    const courseGroups = courseUnionGroups(
      v.catalog, t.segments || [], (t.vocab || {}).course_by_level || {},
      targetRanks,
    ).map((group) => ({
      ...group,
      icon: optionIcon("course", group.key.replace("course-", ""), iconContext),
    }));
    // The currently-set target, so the picker highlights it. `tgt` always
    // exists once `v` does (views.py always populates it with defaults).
    const targetValue = tgt.kind === "segment"
      ? `segment:${tgt.segment_id}`
      : tgt.course_id != null
        ? starId(Number(tgt.course_id), Number(tgt.star_id))
        : null;
    // placeholder=null renders no clear cell at either layer. The native
    // <select> this replaced had no "no target" option either -- /api/target
    // always requires an identity, so a clear cell here posted
    // {course_id: null, star_id: null}, the server 409d, and the button
    // silently did nothing (dead by construction; whole-branch review, task
    // 7 brief, 2026-07-25). This removes a live bug, not just a refactor.
    return html`<${PickerDialog} groups=${courseGroups} value=${targetValue}
      title="Choose a course" depth=${2} placeholder=${null}
      iconFor=${(id) => optionIcon(
        parseSegmentId(id) == null ? "star" : "segment",
        parseSegmentId(id) == null ? id : parseSegmentId(id), iconContext)}
      nextStep=${StrategyStep}
      onPick=${closeTargetPicker} onClose=${closeTargetPicker} />`;
  }

  return html`<header class="context-shell">
    <div class="context-bar" aria-label="Practice context">
      <${ContextSelect} icon="sessions" label="Session" id="session-select"
        name="session" options=${sessionOptions} onChange=${pickSession}
        value=${t.scope === "lifetime" ? "lifetime" : String(active)}
        empty="Loading…" />

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

      <${ContextSelect} icon="clock" label="Clock" id="clock-select"
        name="clock" options=${CLOCK_OPTIONS} value=${t.clock}
        onChange=${(e) => t.pickClock(e.target.value)} empty="—" />

      <${ContextSelect} icon="rank" label="Rank" id="rankmode-select"
        name="rank_mode" options=${v ? RANK_MODE_OPTIONS : []}
        value=${v ? v.rank_mode : null}
        title="Grade medals by saved PB or by a recent/best average"
        onChange=${(e) => send("PUT", "/api/ranks/mode",
          { mode: e.target.value }).then(() => t.refresh())} empty="—" />
    </div>

    <div class="marelo-row">
      <${MareloBar} marelo=${t.marelo} onOpen=${openMarelo} />
    </div>

    ${editing && v && renderTargetPicker()}

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
