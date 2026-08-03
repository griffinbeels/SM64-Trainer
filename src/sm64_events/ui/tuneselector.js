// src/sm64_events/ui/tuneselector.js — the selector-exchange inspector, served
// at /ui/tuneselector.html.
//
// Live report 2026-08-02: "when we invalidate / add / remove cards from the menu
// here, it feels more like a bug / error than intentional… we need a smoother
// animation for removing / adding cards." How long each half of that swap should
// take is a feel judgement, and feel judgements are his: "like how I would work
// with an Inspector in Godot… get it to how it feels good for ME, and then tell
// you what we want to codify" (2026-07-27).
//
// The row above the controls is the REAL `CellRow` holding REAL `PracticeCell`s
// in the app's own stylesheet, mounted inside a replica of `.app-shell` /
// `.practice-page` because a harness rendering into bare <body> measures a
// layout that does not exist (`.claude/rules/ui-core.md`). Only the CARD CHROME
// around the row is a replica — the exchange itself, the cells, and every number
// driving them are the shipped code.
//
// Every control is generated from ui/selectortuning.js's registry, so a new
// tunable appears here with no edit to this file. SAVE POSTs to
// /api/tuning/selector, which rewrites that registry's `value:` fields — what
// lands in `git diff` afterwards IS the tuning, ready to commit.
import { h, render } from "preact";
import { useEffect, useMemo, useState } from "preact/hooks";
import htm from "htm";

import { CellRow } from "/ui/components/cellrow.js";
import { PracticeCell } from "/ui/components/practicecell.js";
import { genericStarSrc } from "/ui/entities.js";
import { SELECTOR_TUNABLES, SELECTOR_GROUPS, SELECTOR_DEFAULTS,
         withSelectorDefaults, setSelectorTuning } from "/ui/selectortuning.js";
import { ControlGroups } from "/ui/tunecontrols.js";
import { prefersReducedMotion } from "/ui/useTween.js";

const html = htm.bind(h);
const STORE_KEY = "sm64.selectorTuneDraft";

// Two sets shaped like the real thing: a course's stars, and what the same row
// holds after a warp — fewer stars plus a movement chip. Names and sub-lines are
// placeholders and no test may assert them (the shipped-default rule this
// codebase enforces for every tuning page).
const STAR_SET = [
  { key: "2:0", name: "Chip off Whomp's Block", sub: "Standard" },
  { key: "2:1", name: "To the Top of the Fortress", sub: "—" },
  { key: "2:2", name: "Shoot into the Wild Blue", sub: "Single Jump" },
  { key: "2:3", name: "Red Coins on the Floating Isle", sub: "—" },
  { key: "2:4", name: "Fall onto the Caged Island", sub: "TJ Owlless" },
  { key: "2:5", name: "Blast Away the Wall", sub: "Sockfolder" },
  { key: "2:6", name: "100 Coins", sub: "—" },
];

const AFTER_WARP_SET = [
  { key: "21:0", name: "Reds", sub: "8 Red Coins (Star)" },
  { key: "21:1", name: "No Reds", sub: "Left TJWK" },
  { key: "seg:53", name: "Bowser 2 → WDW", sub: "Standard" },
];

const SETS = { course: STAR_SET, warp: AFTER_WARP_SET };

const cellsFor = (list, activeKey) => list.map((cell, at) => html`
  <${PracticeCell} key=${cell.key} dimIdle=${true}
    active=${cell.key === activeKey}
    iconSrc=${genericStarSrc(at)} fallbackSlot=${at}
    name=${cell.name}
    sub=${html`<span class="strat ${cell.sub === "—" ? "none" : ""}">${cell.sub}</span>`}
    onPick=${() => {}} />`);

const changedFromShipped = (values) =>
  Object.fromEntries(Object.entries(withSelectorDefaults(values))
    .filter(([key, value]) => value !== SELECTOR_DEFAULTS[key]));

function Inspector() {
  const [values, setValues] = useState(() => {
    try { return withSelectorDefaults(JSON.parse(localStorage.getItem(STORE_KEY))); }
    catch (error) { return withSelectorDefaults(null); }
  });
  const [which, setWhich] = useState("course");
  const [status, setStatus] = useState(null);

  // The ACTIVE slot is what CellRow reads, so it is set here — the wiring
  // layer's job, never a consumer's (a consumer that reads the slot itself
  // cannot be driven by this page at all).
  useEffect(() => {
    setSelectorTuning(values);
    localStorage.setItem(STORE_KEY, JSON.stringify(values));
  }, [values]);

  const set = (key, value) => setValues((prev) => ({ ...prev, [key]: value }));
  const changed = useMemo(() => changedFromShipped(values), [values]);
  const changedKeys = Object.keys(changed);

  // The case the whole mechanism exists for: several validations inside one
  // fade window. If any intermediate set is visible here, it is visible in the
  // app — this is the same component reading the same numbers.
  function burst() {
    setWhich("warp");
    setTimeout(() => setWhich("course"), 40);
    setTimeout(() => setWhich("warp"), 90);
  }

  async function saveToRepo() {
    setStatus({ kind: "busy", text: "Saving..." });
    try {
      const response = await fetch("/api/tuning/selector", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ values }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || response.statusText);
      localStorage.removeItem(STORE_KEY);
      setStatus({ kind: "ok",
        text: `Saved ${payload.written} value(s) into selectortuning.js. Reloading...` });
      setTimeout(() => location.reload(), 700);
    } catch (error) {
      setStatus({ kind: "bad", text: String(error.message || error) });
    }
  }

  const list = SETS[which];
  return html`<div class="tune-layout">
    <div class="tune-stage">
      <div class="app-shell">
        <div class="sidebar"></div>
        <div class="practice-page">
          <section class="practice-card selector-card stagebanner">
            <div class="shead"><b>${which === "course" ? "Whomp's Fortress"
                                                      : "Bowser in the Sky"}</b>
              <span class="meta">${which === "course"
                ? "tap a star to practice"
                : "tap Star or Pipe to pin the reds run"}</span></div>
            <${CellRow} class="starrow">
              ${cellsFor(list, list[0].key)}
            <//>
          </section>
        </div>
      </div>
    </div>

    <div class="tune-panel">
      <h1>Selector exchange tuning</h1>
      <p class="tune-note">The row above is the app's own cells in the app's own
        stylesheet, drawn through the same exchange the selector uses. Swap the
        set, or press Burst to change it three times inside one fade — the point
        of the mechanism is that a burst still reads as ONE swap. Save writes
        these values into${" "}<code>ui/selectortuning.js</code> as the new
        shipped defaults.</p>
      ${prefersReducedMotion() ? html`<p class="tune-warn">Your OS is set to
        reduce motion, so the row SNAPS between sets and nothing here will
        animate until that is off.</p>` : null}

      <div class="tune-showing">Showing:${" "}${which === "course"
        ? "a course's stars" : "after a warp — two stars and a movement"}</div>
      <div class="tune-actions">
        <button class="primary"
          onclick=${() => setWhich(which === "course" ? "warp" : "course")}>
          ⇄ Swap the set</button>
        <button onclick=${burst}>Burst ×3</button>
      </div>

      <${ControlGroups} groups=${SELECTOR_GROUPS} rows=${SELECTOR_TUNABLES}
        values=${values} defaults=${SELECTOR_DEFAULTS} onChange=${set} />

      <h2>Changed from shipped (${changedKeys.length})</h2>
      <pre class=${`tune-diff${changedKeys.length ? "" : " empty"}`}>${
        changedKeys.length
          ? changedKeys.sort().map((key) =>
              `${key}: ${SELECTOR_DEFAULTS[key]} -> ${changed[key]}`).join("\n")
          : "nothing — this is exactly what ships"}</pre>
      <div class="tune-actions" style="margin-top:10px">
        <button class="save" disabled=${!changedKeys.length} onclick=${saveToRepo}>
          Save into the repo</button>
        <button onclick=${() => setValues(withSelectorDefaults(null))}>
          Reset all to shipped</button>
      </div>
      ${status && html`<p class=${`tune-status ${status.kind}`}>${status.text}</p>`}
    </div>
  </div>`;
}

render(html`<${Inspector} />`, document.getElementById("root"));
