// src/sm64_events/ui/components/header.js
import { h } from "preact";
import { useState } from "preact/hooks";
import htm from "htm";
import { send } from "../api.js";
import { RecordingDot } from "./replay.js";

const html = htm.bind(h);

export function Header({ t }) {
  const v = t.view;
  const tgt = v && v.target;
  const [editing, setEditing] = useState(false);
  const [managing, setManaging] = useState(false);

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

  return html`<div class="bar">
    <span class="dot ${t.connected ? (t.paused ? "bad" : "ok") : "bad"}">
      ${t.connected
        ? (t.paused ? (t.pauseReason === "afk" ? "paused (afk)" : "paused")
                    : "live")
        : "offline"}</span>
    <button onclick=${t.togglePause}
            title=${t.pauseReason === "manual"
                     ? "resume event + replay processing"
                     : "manual pause: stops ALL processing; movement will NOT unpause"}>
      ${t.pauseReason === "manual" ? "▶ resume" : "⏸ pause"}</button>
    <${RecordingDot} />
    ${v && html`<select id="session-select" name="session"
                        value=${t.scope === "lifetime" ? "lifetime" : String(active)}
                        onchange=${pickSession}>
      <option value="lifetime">Lifetime</option>
      ${v.sessions.map((s) => html`<option value=${String(s.id)}>
        Session ${s.id}${s.id === active ? " ●" : ""} · ${s.attempts}</option>`)}
    </select>`}
    ${v && html`<button onclick=${() => setManaging(!managing)} title="manage sessions">…</button>`}
    <button onclick=${newSession} disabled=${!v}>New session</button>
    <span>Target:
      ${tgt && tgt.kind === "segment"
        ? html` <b>⏱ ${tgt.segment_name}</b>`
        : tgt && tgt.course_id !== null
          ? html` <b>${tgt.course_name} · ${tgt.star_name}</b>`
          : html` <span class="meta">none (grab a star or set one)</span>`}
      ${tgt && tgt.strat_tag ? html` <span class="meta">«${tgt.strat_tag}»</span>` : ""}
      <button onclick=${() => setEditing(!editing)} disabled=${!v}>▾</button>
    </span>
    <span style="margin-left:auto">Clock:
      <select id="clock-select" name="clock" value=${t.clock} onchange=${(e) => t.pickClock(e.target.value)}>
        <option value="igt">Usamune IGT</option>
        <option value="rta">anchor → grab</option>
      </select>
    </span>
    ${managing && v && html`<div class="popover">
      ${v.sessions.map((s) => html`<div style="display:flex;gap:.5rem;align-items:center">
        <span>Session ${s.id} · ${s.attempts} attempts · ${(s.started_utc || "").slice(0, 10)}</span>
        ${s.id !== active && html`<button onclick=${() => removeSession(s.id)}>×</button>`}
        ${s.id === active && html`<span class="meta">active</span>`}
      </div>`)}
      <div style="margin-top:.4rem"><button onclick=${() => setManaging(false)}>Close</button></div>
    </div>`}
    ${editing && v && html`<${TargetEditor} t=${t} close=${() => setEditing(false)} />`}
  </div>`;
}

function TargetEditor({ t, close }) {
  const v = t.view;
  const tgt = v.target;
  const [course, setCourse] = useState(tgt.course_id ?? 1);
  const [star, setStar] = useState(tgt.star_id ?? 0);
  const lastStratFor = (c, s) => v.last_strat_by_star[`${Number(c)}:${Number(s)}`] ?? "";
  const stratsFor = (c, s) => v.strategies[`${Number(c)}:${Number(s)}`] || [];
  const [strat, setStrat] = useState(lastStratFor(course, star));
  const [adding, setAdding] = useState(false);
  const [newStrat, setNewStrat] = useState("");

  function pickStar(c, s) {
    setCourse(c); setStar(s);
    setStrat(lastStratFor(c, s));   // load the star's own remembered strat
    setAdding(false);
  }

  async function apply() {
    const chosen = adding ? newStrat.trim() : strat;
    await send("POST", "/api/target", {
      course_id: Number(course), star_id: Number(star),
      strat_tag: chosen || null,
    });
    close(); t.refresh();
  }

  const courses = v.catalog.courses;
  const stars = (courses.find((c) => c.id === Number(course)) || { stars: [] }).stars;
  const options = stratsFor(course, star);

  return html`<div class="popover">
    <div>
      <select value=${course} onchange=${(e) => pickStar(e.target.value, 0)}>
        ${courses.map((c) => html`<option value=${c.id}>${c.name}</option>`)}
      </select>
      <select value=${star} onchange=${(e) => pickStar(course, e.target.value)}>
        ${stars.map((name, i) => html`<option value=${i}>${name}</option>`)}
      </select>
    </div>
    <div style="margin-top:.4rem">
      ${adding
        ? html`<input id="strat-name-input" name="strat_name" placeholder="new strategy name"
                      value=${newStrat} oninput=${(e) => setNewStrat(e.target.value)} />
               <button onclick=${() => setAdding(false)}>↩</button>`
        : html`<select value=${strat}
                       onchange=${(e) => e.target.value === "__new__"
                         ? setAdding(true) : setStrat(e.target.value)}>
                 <option value="">(no strategy)</option>
                 ${options.map((s) => html`<option value=${s}>${s}</option>`)}
                 <option value="__new__">+ new strategy…</option>
               </select>`}
      <button onclick=${apply}>Set target</button>
    </div>
  </div>`;
}
