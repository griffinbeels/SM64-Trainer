// src/sm64_events/ui/components/header.js
import { h } from "preact";
import { useState } from "preact/hooks";
import htm from "htm";
import { send } from "../api.js";

const html = htm.bind(h);

export function Header({ t }) {
  const v = t.view;
  const tgt = v && v.target;
  const [editing, setEditing] = useState(false);

  async function newSession() {
    await send("POST", "/api/session/new", {});
    t.refresh();
  }

  return html`<div class="bar">
    <span class="dot ${t.connected ? "ok" : "bad"}">${t.connected ? "live" : "offline"}</span>
    ${v && html`<span class="meta">session ${v.session.id}</span>`}
    <button onclick=${newSession} disabled=${!v}>New session</button>
    <span>Target:
      ${tgt && tgt.course_id !== null
        ? html` <b>${tgt.course_name} · ${tgt.star_name}</b>`
        : html` <span class="meta">none (grab a star or set one)</span>`}
      ${tgt && tgt.strat_tag ? html` <span class="meta">«${tgt.strat_tag}»</span>` : ""}
      <button onclick=${() => setEditing(!editing)} disabled=${!v}>▾</button>
    </span>
    <span style="margin-left:auto">Clock:
      <select value=${t.clock} onchange=${(e) => t.pickClock(e.target.value)}>
        <option value="igt">Usamune IGT</option>
        <option value="rta">anchor → grab</option>
      </select>
    </span>
    ${editing && v && html`<${TargetEditor} t=${t} close=${() => setEditing(false)} />`}
  </div>`;
}

function TargetEditor({ t, close }) {
  const v = t.view;
  const tgt = v.target;
  const [course, setCourse] = useState(tgt.course_id ?? 1);
  const [star, setStar] = useState(tgt.star_id ?? 0);
  const [strat, setStrat] = useState(tgt.strat_tag || "");
  const courses = v.catalog.courses;
  const stars = (courses.find((c) => c.id === Number(course)) || { stars: [] }).stars;

  async function apply() {
    await send("POST", "/api/target", {
      course_id: Number(course), star_id: Number(star),
      strat_tag: strat || null,
    });
    close(); t.refresh();
  }

  return html`<div class="popover">
    <div>
      <select value=${course} onchange=${(e) => { setCourse(e.target.value); setStar(0); }}>
        ${courses.map((c) => html`<option value=${c.id}>${c.name}</option>`)}
      </select>
      <select value=${star} onchange=${(e) => setStar(e.target.value)}>
        ${stars.map((name, i) => html`<option value=${i}>${name}</option>`)}
      </select>
    </div>
    <div style="margin-top:.4rem">
      <input id="strat-tag" name="strat_tag" placeholder="strat tag (optional)" value=${strat}
             oninput=${(e) => setStrat(e.target.value)} />
      <button onclick=${apply}>Set target</button>
    </div>
  </div>`;
}
