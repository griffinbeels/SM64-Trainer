// src/sm64_events/ui/goaloverlaypage.js — the stream overlay page itself
// (ui/goal-overlay.html). Rules and settings live in goaloverlay.js; this
// module fetches, listens and draws.
//
// What it shows is the Scorecard's own answer for the current target (GET
// /api/scorecard/entry_goals, the same call the practice log's goal plate
// makes), formatted by the plate's own helpers, so the stream, the card and
// the Scorecard cannot disagree about a PB or a goal.
//
// `?demo=goal|streak|count|achieved|idle` runs on sample data with no server,
// which is what the designer's preview and the screenshots use. The designer
// drives a demo through postMessage ({type: "goal-overlay-demo", action}).
import { h, render } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON } from "./api.js";
import { coalesce } from "./coalesce.js";
import { REFRESH_ON } from "./store.js";
import { displayName, entityKey, standardsIdentity } from "./entitysection.js";
import { entityIconSrc } from "./components/entityicons.js";
import { GOAL_CELEBRATION_MS, fmtCs, goalScopeOf, goalSourceLabel,
         goalTimeLabel, useGoalCelebration } from "./components/goalplate.js";
import { THEME_BOX, parseOverlayConfig, progressMoment, progressView,
         targetSection } from "./goaloverlay.js";

const html = htm.bind(h);

// How long a tick or a break stays marked. Shorter than an attempt, so the
// next one never lands on a still-running animation.
const MOMENT_MS = 1600;
const RECONNECT_MS = 2000;
// Events that change the art or the segment list rather than the view.
const STATIC_REFRESH_ON = new Set(["icons_changed", "segments_changed", "origins_changed"]);

// ---- live data ---------------------------------------------------------------

async function loadTarget() {
  const [view, marelo] = await Promise.all([
    getJSON("/api/session?clock=igt&scope=session"),
    getJSON("/api/marelo").catch(() => null),
  ]);
  const sec = targetSection(view);
  if (!sec) return { view, sec: null, key: null, goal: null };
  const key = standardsIdentity(sec).entity;
  const scope = goalScopeOf({ marelo });
  // A goal that cannot load still leaves the target on screen, with dashes.
  const body = await getJSON(`/api/scorecard/entry_goals?scope=${encodeURIComponent(scope)}`
    + `&keys=${encodeURIComponent(key)}`).catch(() => null);
  return { view, sec, key, goal: ((body && body.goals) || {})[key] || null };
}

async function loadArt() {
  const [courseIcons, segments, vocab] = await Promise.all([
    getJSON("/api/icons/courses").catch(() => ({})),
    getJSON("/api/segments").catch(() => []),
    getJSON("/api/segments/vocab").catch(() => ({})),
  ]);
  return { courseIcons, segments, vocab };
}

/** The current target and its goal, kept live over /ws/events. Every fetch
 *  lands as ONE state update, so the overlay never draws a new star with the
 *  old star's goal. A reconnect refetches: events missed while the socket was
 *  down are not replayed, and the view is the authoritative state. */
function useLiveTarget() {
  const [state, setState] = useState(null);
  const [art, setArt] = useState({});
  useEffect(() => {
    let socket = null;
    let closed = false;
    const refresh = coalesce(async () => {
      try { setState(await loadTarget()); } catch (error) { console.error(error); }
    });
    const refreshArt = () => loadArt().then(setArt).catch(() => {});
    function connect() {
      socket = new WebSocket(`ws://${location.host}/ws/events`);
      socket.onopen = () => { refresh(); refreshArt(); };
      socket.onclose = () => { if (!closed) setTimeout(connect, RECONNECT_MS); };
      socket.onmessage = (message) => {
        const event = JSON.parse(message.data);
        if (REFRESH_ON.has(event.type)) refresh();
        if (STATIC_REFRESH_ON.has(event.type)) refreshArt();
      };
    }
    connect();
    return () => { closed = true; if (socket) socket.close(); };
  }, []);
  if (!state || !state.sec) return { model: { status: "idle" }, attempts: null };
  const { view, sec, goal } = state;
  const names = displayName(sec, ((view.catalog || {}).courses) || []);
  const icon = entityIconSrc({ view, starIcons: "course", ...art }, entityKey(sec));
  return {
    model: { status: "ready", entity: state.key, name: names.name,
             context: names.context, icon, goal },
    attempts: sec.attempts || [],
  };
}

// ---- moments: ticks, breaks and celebrations -----------------------------------

/** Compares each goal with the one before it and reports what the player
 *  just did to a counted goal. Keyed per entity by the caller, so switching
 *  star is a fresh start, never a "break". */
function useProgressMoment(goal) {
  const before = useRef(goal);
  const [moment, setMoment] = useState(null);
  useEffect(() => {
    const kind = progressMoment(before.current, goal);
    const was = progressView(before.current);
    before.current = goal;
    if (!kind) return undefined;
    setMoment({ kind, from: was ? was.done : 0, id: Date.now() });
    const timer = setTimeout(() => setMoment(null), MOMENT_MS);
    return () => clearTimeout(timer);
  }, [goal]);
  return moment;
}

function useTimedParty() {
  const [party, setParty] = useState(null);
  useEffect(() => {
    if (!party) return undefined;
    const timer = setTimeout(() => setParty(null), GOAL_CELEBRATION_MS);
    return () => clearTimeout(timer);
  }, [party]);
  return [party, setParty];
}

// ---- drawing -------------------------------------------------------------------

const SPARKS = [[-86, -34], [-40, -52], [8, -46], [58, -30], [-70, 30], [-12, 40], [44, 34]];

function Party({ party, goal }) {
  const detail = party.kind === "count"
    ? html`<span class="ov-party-time">${party.needed}${party.streak ? " in a row" : " done"}</span>
        <span class="ov-party-beaten">${goalTimeLabel(goal)}</span>`
    : html`<span class="ov-party-time">${fmtCs(party.timeCs)}</span>
        <span class="ov-party-beaten"><s>${goalTimeLabel({ ...goal, goal_cs: party.beatenCs })}</s></span>`;
  return html`<div class="ov-party" aria-live="polite" key=${party.id}>
    <span class="ov-party-sweep" aria-hidden="true"></span>
    <span class="ov-party-burst" aria-hidden="true">
      ${SPARKS.map(([dx, dy], index) => html`<i style=${`--dx:${dx}px;--dy:${dy}px;--i:${index}`}>★</i>`)}
    </span>
    <span class="ov-party-word">GOAL!</span>
    <span class="ov-party-detail">${detail}</span>
  </div>`;
}

function Pips({ view, moment }) {
  const breaking = moment && moment.kind === "break";
  const fresh = moment && moment.kind !== "break";
  const pips = [];
  for (let index = 0; index < view.needed; index += 1) {
    const drained = breaking && index < moment.from;
    const cls = ["ov-pip", index < view.done ? "is-on" : "",
      fresh && index === view.done - 1 ? "is-new" : "", drained ? "is-drain" : ""].join(" ");
    pips.push(html`<i class=${cls}
      style=${drained ? `--d:${(moment.from - 1 - index) * 45}ms` : null}></i>`);
  }
  return html`<span class="ov-pips" role="img"
    aria-label=${`${view.done} of ${view.needed} ${view.unit}`}>${pips}</span>`;
}

function Bar({ view }) {
  return html`<span class="ov-bar"><span
    style=${`width:${(view.done / view.needed) * 100}%`}></span></span>`;
}

// `marks` is "dots", "bar" (too many successes for dots) or "none" (the
// streamer turned the dots off and keeps the N / M count alone).
function Progress({ view, moment, marks }) {
  const breaking = !!moment && moment.kind === "break";
  const fresh = !!moment && !breaking;
  const Marks = { dots: Pips, bar: Bar }[marks];
  const classes = ["ov-progress", view.streak ? "is-streak" : "is-count",
    view.complete ? "is-complete" : "", breaking ? "is-breaking" : ""].join(" ");
  return html`<div class=${classes}>
    ${Marks ? html`<${Marks} view=${view} moment=${moment} />` : null}
    <span class="ov-count">
      ${view.complete ? html`<span class="ov-check" aria-hidden="true">✓</span>` : null}
      <b class=${fresh ? "is-new" : ""} key=${view.done}>${view.done}</b>
      <span class="ov-of">/ ${view.needed}</span>
      <span class="ov-unit">${breaking ? "streak broken" : view.unit}</span>
    </span>
    ${view.streak && view.best > view.done && !breaking
      ? html`<span class="ov-best">best ${view.best}</span>` : null}
  </div>`;
}

function Head({ config, model, idle }) {
  if (!config.icon && !config.name) return null;
  const icon = idle || !model.icon
    ? html`<span class="ov-icon ov-icon-empty" aria-hidden="true">★</span>`
    : html`<img class="ov-icon" src=${model.icon} alt="" />`;
  const title = idle ? html`<span class="ov-name">Waiting for a star</span>`
    : html`${model.context ? html`<span class="ov-course">${model.context}</span>` : null}
      <span class="ov-name">${model.name}</span>`;
  return html`<div class="ov-head">
    ${config.icon ? icon : null}
    ${config.name ? html`<span class="ov-title">${title}</span>` : null}
  </div>`;
}

function Times({ config, goal, view, achieved }) {
  const source = config.source ? goalSourceLabel(goal) : "";
  return html`<div class="ov-times">
    <div class="ov-row ov-pb">
      <span class="ov-label">PB</span>
      <span class="ov-time">${fmtCs(goal ? goal.you_cs : null)}</span>
    </div>
    <div class="ov-row ov-goal">
      <span class="ov-label">GOAL</span>
      <span class="ov-time">${goalTimeLabel(goal)}${view
        ? html`<small class="ov-times-n">×${view.needed}</small>` : null}</span>
      ${achieved && !view ? html`<span class="ov-check" aria-label="achieved">✓</span>` : null}
      ${source ? html`<span class="ov-source">${source}</span>` : null}
    </div>
  </div>`;
}

const marksFor = (config, view) => (!config.pips ? "none" : view.dots ? "dots" : "bar");

/** The overlay: header, PB over GOAL, a counted goal's progress, and the
 *  celebration drawn inside the same box. */
export function GoalOverlay({ config, model, party, moment }) {
  const idle = model.status !== "ready";
  const goal = idle ? null : model.goal;
  const view = progressView(goal);
  const achieved = !party && !!goal && (view ? view.complete : goal.achieved);
  const box = THEME_BOX[config.theme];
  const rootClass = ["ov", `theme-${config.theme}`, `align-${config.align}`,
    idle ? "is-idle" : "is-ready", idle && config.idle === "hide" ? "is-hidden" : "",
    party ? "is-celebrating" : "", achieved ? "is-achieved" : "",
    view ? "has-progress" : ""].join(" ");
  return html`<div class=${rootClass}
      style=${`--accent:#${config.accent};zoom:${config.scale}`}>
    <div class="ov-box" style=${`width:${box.width}px;height:${box.height}px`}>
      <${Head} config=${config} model=${model} idle=${idle} />
      <${Times} config=${config} goal=${goal} view=${view} achieved=${achieved} />
      ${view ? html`<${Progress} view=${view} moment=${moment} marks=${marksFor(config, view)} />` : null}
      ${party ? html`<${Party} party=${party} goal=${goal} />` : null}
    </div>
  </div>`;
}

// One target's overlay. Keyed on the entity by its parent, so the celebration
// hook's "attempts already seen" and the progress comparison both start over
// when the target changes, instead of reading the new star's history as news.
function TargetOverlay({ config, model, attempts }) {
  const goal = model.goal;
  const single = useGoalCelebration(attempts, goal && !(goal.count > 1) ? goal : null);
  const moment = useProgressMoment(goal);
  const [counted, setCounted] = useTimedParty();
  useEffect(() => {
    if (moment && moment.kind === "complete") {
      const view = progressView(goal);
      setCounted({ id: moment.id, kind: "count", needed: view.needed, streak: view.streak });
    }
  }, [moment && moment.id]);
  const party = config.celebrate ? (counted || (single && { ...single, kind: "single" })) : null;
  return html`<${GoalOverlay} config=${config} model=${model} party=${party} moment=${moment} />`;
}

function LiveOverlay({ config }) {
  const { model, attempts } = useLiveTarget();
  return html`<${TargetOverlay} key=${model.entity || "idle"} config=${config}
    model=${model} attempts=${attempts} />`;
}

// ---- demo ----------------------------------------------------------------------

// Sample data in the EntryGoal shape. The PB and goal are Griffin's own
// example ("pb: 1:48.56 / goal: low 1:4x").
const DEMO_BASE = {
  entity: "star:2:6", context: "Whomp's Fortress", name: "100 Coins",
  goal: { key: "star:2:6", clock: "igt", you_cs: 10856, goal_cs: 10500,
          source: { kind: "time", label: "Custom" }, pinned: true,
          achieved: false, count: 1, streak: false, progress: null },
};
const counted = (streak, done) => ({
  ...DEMO_BASE, entity: "star:1:0", context: "Bob-omb Battlefield",
  name: "Big Bob-omb on the Summit",
  goal: { key: "star:1:0", clock: "igt", you_cs: 1456, goal_cs: 1500,
          source: { kind: "time", label: "Custom" }, pinned: true,
          achieved: false, count: 10, streak,
          progress: { done, needed: 10, best: streak ? 4 : done, broken_by: null,
                      window_start: "2026-09-23T18:00:00Z" } },
});
export const DEMO_SCENES = {
  goal: () => DEMO_BASE,
  achieved: () => ({ ...DEMO_BASE,
    goal: { ...DEMO_BASE.goal, you_cs: 10433, achieved: true } }),
  auto: () => ({ ...DEMO_BASE,
    goal: { ...DEMO_BASE.goal, goal_cs: 10387, pinned: false,
            source: { kind: "automatic", label: "Gold 2", tier: "Gold", division: "II" } } }),
  streak: () => counted(true, 3),
  count: () => counted(false, 6),
  idle: () => null,
};

function withDone(model, done) {
  const goal = model.goal;
  const needed = goal.progress.needed;
  const next = Math.max(0, Math.min(done, needed));
  return { ...model, goal: { ...goal, achieved: next >= needed,
    progress: { ...goal.progress, done: next, best: Math.max(goal.progress.best, next) } } };
}

// A single-time goal's celebration in the demo. The before is the plate as
// it stands, then the celebration over it; the after is the goal settling as
// beaten, just before the celebration hands the box back.
function celebrateOnce(shown, celebrate, setParty, setModel, settle) {
  const timeCs = shown.goal.goal_cs - 13;
  if (celebrate) setParty({ id: Date.now(), kind: "single", timeCs, beatenCs: shown.goal.goal_cs });
  clearTimeout(settle.current);
  settle.current = setTimeout(() => setModel((m) => m && ({ ...m,
    goal: { ...m.goal, you_cs: Math.min(m.goal.you_cs, timeCs), achieved: true } })),
  celebrate ? GOAL_CELEBRATION_MS - 500 : 0);
}

/** The overlay on sample data. The designer changes its settings and example
 *  through postMessage rather than by reloading the frame, so the preview
 *  follows a slider without flashing. */
function DemoOverlay({ initialConfig, scene }) {
  const [config, setConfig] = useState(initialConfig);
  const [model, setModel] = useState(() => DEMO_SCENES[scene]());
  const [party, setParty] = useTimedParty();
  const current = useRef({ model, config });
  current.current = { model, config };
  const settle = useRef(null);
  useEffect(() => {
    function act(action, value) {
      const shown = current.current.model;
      if (action === "config") {
        const next = parseOverlayConfig(value);
        document.documentElement.dataset.align = next.align;
        setConfig(next);
      } else if (action === "scene" && DEMO_SCENES[value]) {
        clearTimeout(settle.current); setParty(null); setModel(DEMO_SCENES[value]());
      } else if (shown && progressView(shown.goal)) {
        const view = progressView(shown.goal);
        const done = { celebrate: view.needed, tick: view.done + 1, break: 0 }[action];
        if (done !== undefined) setModel(withDone(shown, done));
      } else if (shown && action === "celebrate") {
        celebrateOnce(shown, current.current.config.celebrate, setParty, setModel, settle);
      }
    }
    const onMessage = (event) => {
      const data = event.data || {};
      if (event.origin === location.origin && data.type === "goal-overlay-demo") {
        act(data.action, data.value);
      }
    };
    window.addEventListener("message", onMessage);
    window.goalOverlayDemo = act;
    return () => window.removeEventListener("message", onMessage);
  }, []);
  const shown = model ? { status: "ready", icon: entityIconSrc({}, model.entity), ...model }
    : { status: "idle" };
  return html`<${DemoTarget} key=${shown.entity || "idle"} config=${config}
    model=${shown} single=${config.celebrate ? party : null} />`;
}

function DemoTarget({ config, model, single }) {
  const moment = useProgressMoment(model.goal);
  const [counted, setCounted] = useTimedParty();
  useEffect(() => {
    if (moment && moment.kind === "complete" && config.celebrate) {
      const view = progressView(model.goal);
      setCounted({ id: moment.id, kind: "count", needed: view.needed, streak: view.streak });
    }
  }, [moment && moment.id]);
  return html`<${GoalOverlay} config=${config} model=${model}
    party=${counted || single} moment=${moment} />`;
}

// ---- mount ---------------------------------------------------------------------

const params = new URLSearchParams(location.search);
const config = parseOverlayConfig(params);
const demo = params.get("demo");
const scene = demo && (DEMO_SCENES[demo] ? demo : "goal");
document.documentElement.dataset.align = config.align;
render(scene ? html`<${DemoOverlay} initialConfig=${config} scene=${scene} />`
  : html`<${LiveOverlay} config=${config} />`, document.getElementById("overlay"));
