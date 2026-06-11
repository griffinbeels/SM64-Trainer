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
