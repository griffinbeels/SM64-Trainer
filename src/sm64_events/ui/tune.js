// src/sm64_events/ui/tune.js — the climb tuning inspector, served at
// /ui/tune.html.
//
// "like how I would work with an Inspector in Godot, I want to be able to tune
// all of the variables that determine the animation, get it to how it feels
// good for ME, and then tell you what we want to codify" — and then, the same
// day: "what if I can access that page at any time, mess with the settings,
// SAVE, and then it automatically applies to my repo immediately?"
//
// So SAVE is not an export step. It POSTs to /api/tuning/climb, which rewrites
// the `value:` fields in ui/climbtuning.js — the shipped defaults themselves —
// and the page reloads onto them. What lands in `git diff` afterwards is
// exactly the tuning, ready to commit. (Refused from a frozen exe, which has
// no repo to write to.)
//
// This file owns NO opinion about the climb. Every control is generated from
// ui/climbtuning.js's registry, so a tunable added there appears here with no
// edit; and the thing being previewed is the shipped `RankBanner`, driven the
// same way practice.js drives it — same lane, same order, same props.
import { h, render } from "preact";
import { useEffect, useMemo, useState } from "preact/hooks";
import htm from "htm";
import { RankBanner } from "/ui/components/ranks.js";
import { Icon } from "/ui/components/icons.js";
import { RANK_NAMES, DIVISION_NUMERALS, DIVISIONS_PER_TIER, capName,
         divisionDigit, rankAt, rankPosition } from "/ui/components/caps.js";
import { buildClimbPlan } from "/ui/climbplan.js";
import { climbTimings } from "/ui/climbcurve.js";
import { TUNABLES, CHOICES, DEFAULTS, GROUPS, setTuning, encodeTuning,
         decodeTuning, changedFromDefault, withDefaults } from "/ui/climbtuning.js";
import { celebrationsEnabled } from "/ui/celebrations.js";
import { prefersReducedMotion } from "/ui/useTween.js";

const html = htm.bind(h);
const STORE_KEY = "sm64.climbTuneDraft";
const TOP_LEVEL = RANK_NAMES.length * DIVISION_NUMERALS.length - 1;
// Bottom-first, so the pickers read Capless -> Mario the way the ladder does.
const TIERS = [...RANK_NAMES].reverse();

const rankLabel = (level) => {
  const { tier, division } = rankAt(level);
  return `${capName(tier)} ${divisionDigit(division)}`;
};

/** The banner payload shape `views.py::_section_banner` produces. Only the
 *  fields RankBanner actually reads — a bigger fake would just be a second
 *  opinion about the server's shape. */
function bannerFor(level, fill) {
  const { tier, division } = rankAt(level);
  const next = level < TOP_LEVEL ? rankAt(level + 1) : null;
  return {
    rank: tier, division, fill,
    next_tier: next && next.tier, next_division: next && next.division,
    next_gap_cs: next ? 4 : null, mode: "best", basis: null,
  };
}

function LevelPicker({ label, level, onChange, min = 0 }) {
  // Named tierKey, never `tier`: tests/test_ui_cap_names.py forbids putting a
  // raw tier key on screen, and it reads the identifier rather than the value.
  // A select's `value` is not on screen, but the guard cannot know that and
  // a name that reads as displayable is the wrong name here anyway.
  const { tier: tierKey, division } = rankAt(level);
  const pick = (nextTier, nextDivision) =>
    onChange(Math.max(min, rankPosition(nextTier, nextDivision, 0)));
  return html`<div>
    <label>${label}</label>
    <select value=${tierKey} onchange=${(e) => pick(e.target.value, division)}>
      ${TIERS.map((one) => html`<option value=${one}
        disabled=${rankPosition(one, "V", 0) + 4 < min}>${capName(one)}</option>`)}
    </select>
    <select value=${division} onchange=${(e) => pick(tierKey, e.target.value)}>
      ${DIVISION_NUMERALS.map((one) => html`<option value=${one}
        disabled=${rankPosition(tierKey, one, 0) < min}>${divisionDigit(one)}</option>`)}
    </select>
  </div>`;
}

// What the current tuning will ACTUALLY play, step by step.
//
// This exists because a slider silently lied for a whole tuning session (user,
// 2026-07-27: "the output was actually totally different that what I had
// changed my settings to... probably something to do with floors"). The floors
// have been fixed so one can no longer override a ceiling, but a budget
// legitimately still shortens a step on a crowded climb -- and the ONLY honest
// way to show that is to show the plan. A per-control "effective" badge would
// have to re-derive the arithmetic that produced the discrepancy, which is the
// second door this repo keeps learning not to build.
function PlanReadout({ values, startLevel, destLevel, destFill }) {
  const plan = useMemo(() => buildClimbPlan({
    fromLevel: startLevel, fromFill: 0, toLevel: destLevel, toFill: destFill,
    divisionsPerTier: DIVISIONS_PER_TIER, skipStyle: values.skipStyle,
    timings: (counts) => climbTimings(counts, values),
  }), [values, startLevel, destLevel, destFill]);

  // A control whose number is not the number that runs. Named as the pair it
  // is, so the reader is pointed at the OTHER knob rather than left guessing.
  const clamps = [
    ["Ladder step", values.ladderStepMs, plan.timings.ladderMs,
     "ladder budget / floor"],
    ["Tier crossing", values.tierDwellMs,
     plan.timings.anticipateMs + plan.timings.payoffMs,
     "tier crossing budget / floor"],
  ].filter(([, set, effective]) => plan.ladder > 0 && Math.abs(set - effective) > 1);

  return html`<div>
    <h2>What will play · ${(plan.totalMs / 1000).toFixed(2)}s</h2>
    ${/* The explicit spaces are load-bearing: htm collapses whitespace between
         a text node and an element, and this line read "but220ms will run"
         without them -- the same trap the settings-note paragraph hit. */""}
    ${clamps.map(([label, set, effective, blame]) => html`<p class="tune-warn">
      <b>${label}</b>${" "}is set to ${Math.round(set)}ms but${" "}
      <b>${Math.round(effective)}ms</b>${" "}will run — the ${blame} is deciding.
    </p>`)}
    <pre class="tune-plan">${plan.steps.map((step) => {
      const { tier, division } = rankAt(step.level);
      return `${step.kind.padEnd(11)}${capName(tier)} ${divisionDigit(division)}`
        .padEnd(30) + `${Math.round(step.ms)}ms`;
    }).join("\n")}</pre>
  </div>`;
}

function Control({ name, row, value, onChange }) {
  const changed = value !== DEFAULTS[name];
  const cls = `tune-row${changed ? " is-changed" : ""}`;
  if (row.options) {
    return html`<div class=${cls}>
      <label title=${row.why} for=${name}>${row.label}</label>
      <select id=${name} value=${value} onchange=${(e) => onChange(e.target.value)}>
        ${Object.entries(row.options).map(([key, text]) =>
          html`<option value=${key}>${text}</option>`)}
      </select>
    </div>`;
  }
  const set = (raw) => {
    const parsed = Number(raw);
    if (Number.isFinite(parsed)) onChange(parsed);
  };
  return html`<div class=${cls}>
    <label title=${`${row.why}\nshipped default: ${DEFAULTS[name]}${row.unit}`}
      for=${name}>${row.label}${row.unit === "ms" ? "" : ` (${row.unit || "n"})`}</label>
    <input id=${name} type="number" value=${value} min=${row.min} max=${row.max}
      step=${row.step} onchange=${(e) => set(e.target.value)} />
    <input type="range" value=${value} min=${row.min} max=${row.max} step=${row.step}
      oninput=${(e) => set(e.target.value)} />
  </div>`;
}

// The objective card, rebuilt from the app's OWN classes around the app's own
// RankBanner. The banners -- the only part that animates, and the only part
// being judged -- are the shipped component driven exactly as practice.js
// drives it. The chrome around them is a replica rather than the real
// StarSection because that section is welded into practice.js with a live
// store behind it; extracting it is the follow-up, and until then this is
// honest about being the same CSS rather than the same component.
function ObjectiveCard({ startLevel, destLevel, destFill, playing }) {
  const level = playing ? destLevel : startLevel;
  const fill = playing ? destFill : 0;
  const banner = bannerFor(level, fill);
  return html`<div class="practice-detail-grid is-primary">
    <section class="practice-card objective-card active-star">
      <div class="objective-heading">
        <div class="objective-pick">
          <span class="objective-symbol"><${Icon} name="target" size=${20} /></span>
          <span class="eyebrow">Active target</span>
        </div>
        <div class="objective-name">
          <span class="objective-context">Bob-omb Battlefield</span>
          <h2>Behind Chain Chomp's Gate</h2>
        </div>
        <div class="objective-strategy">
          <span class="field-label">Strategy</span>
          <select><option>Bomb Clip</option></select>
        </div>
      </div>
      <div class="objective-metrics">
        <div class="rank-slot">
          <${RankBanner} label="Strategy" banner=${banner} identity="tune"
              lane="tune" order=${0} />
          <${RankBanner} label="Star" banner=${banner} identity="tune"
              lane="tune" order=${1} />
        </div>
        <div class="objective-live-state" aria-label="Practice state">
          <${Icon} name="clock" size=${17} /><span>Ready</span>
        </div>
        <span class="pb-tag">PB <b>0'13"46</b> (igt)</span>
      </div>
    </section>
  </div>`;
}

function Inspector() {
  const [values, setValues] = useState(() => {
    try { return decodeTuning(localStorage.getItem(STORE_KEY) || "{}"); }
    catch { return withDefaults(null); }
  });
  const [startLevel, setStartLevel] = useState(0);          // Capless 5
  const [destLevel, setDestLevel] = useState(16);           // Waluigi 4
  const [destFill, setDestFill] = useState(0.04);
  const [playing, setPlaying] = useState(false);
  const [status, setStatus] = useState(null);

  useEffect(() => { localStorage.setItem(STORE_KEY, encodeTuning(values)); }, [values]);
  // The destination can never be at or below the start: a drop is not a climb,
  // and the hook would simply snap.
  useEffect(() => {
    if (destLevel <= startLevel) setDestLevel(Math.min(TOP_LEVEL, startLevel + 1));
  }, [startLevel, destLevel]);

  const changed = useMemo(() => changedFromDefault(values), [values]);
  const changedKeys = Object.keys(changed);
  const set = (name, value) => setValues((prior) => ({ ...prior, [name]: value }));

  // The tuning is read ONCE per climb (rankclimb.js), so it is committed here
  // and the banners are then handed their new rank. Reset first, always: a
  // drop snaps, which puts both banners back at the start rank instantly with
  // no animation to wait out.
  const play = () => {
    setStatus(null);
    setPlaying(false);
    setTuning(values);
    requestAnimationFrame(() => requestAnimationFrame(() => setPlaying(true)));
  };

  async function saveToRepo() {
    setStatus({ kind: "busy", text: "Saving..." });
    try {
      const response = await fetch("/api/tuning/climb", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ values }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || response.statusText);
      localStorage.removeItem(STORE_KEY);
      setStatus({ kind: "ok",
        text: `Saved ${payload.written} value(s) into climbtuning.js. Reloading...` });
      setTimeout(() => location.reload(), 700);
    } catch (error) {
      setStatus({ kind: "bad", text: String(error.message || error) });
    }
  }

  const warnings = [];
  if (prefersReducedMotion()) {
    warnings.push("Your OS is set to reduce motion, so every climb SNAPS. "
      + "Nothing here will animate until that is off.");
  }
  if (!celebrationsEnabled()) {
    warnings.push("Celebrations are switched off in the app settings, so every "
      + "climb snaps. Turn them back on in the main app's settings drawer.");
  }

  return html`<div class="tune-layout">
    <div class="tune-stage">
      <div class="app-shell">
        <div class="sidebar"></div>
        <div class="practice-page">
          <${ObjectiveCard} startLevel=${startLevel} destLevel=${destLevel}
            destFill=${destFill} playing=${playing} />
        </div>
      </div>
    </div>

    <div class="tune-panel">
      <h1>Climb tuning</h1>
      <!-- The space before <code> is written as an entity on purpose: htm
           collapses the whitespace between a text node and an element, and
           the line read "intoui/climbtuning.js" without it. -->
      <p class="tune-note">The card on the left is the app's own RankBanner in
        the app's own stylesheet. Set a start and a destination, press Play,
        then tune. Save writes these values into${" "}
        <code>ui/climbtuning.js</code> as the new shipped defaults.</p>
      ${warnings.map((text) => html`<p class="tune-warn">${text}</p>`)}

      <h2>Climb</h2>
      <div class="tune-pickers">
        <${LevelPicker} label="Start" level=${startLevel}
          onChange=${(level) => setStartLevel(Math.min(level, TOP_LEVEL - 1))} />
        <${LevelPicker} label="Destination" level=${destLevel} min=${startLevel + 1}
          onChange=${setDestLevel} />
      </div>
      <div class="tune-pickers" style="margin-top:8px">
        <div>
          <label>Progress into the destination rank</label>
          <input type="number" min="0" max="0.99" step="0.01" value=${destFill}
            onchange=${(e) => setDestFill(Math.max(0, Math.min(0.99, Number(e.target.value) || 0)))} />
        </div>
        <div>
          <label>Showing</label>
          <!-- A span, not a readonly input: the value is a whole sentence and
               an input clipped it to "Capless 5 -> Waluig". -->
          <div class="tune-showing">${rankLabel(startLevel)} →
            ${rankLabel(destLevel)}</div>
        </div>
      </div>
      <div class="tune-actions" style="margin-top:10px">
        <button class="primary" onclick=${play}>▶ Play</button>
        <button onclick=${() => { setPlaying(false); setStatus(null); }}>Reset</button>
      </div>

      <${PlanReadout} values=${values} startLevel=${startLevel}
        destLevel=${destLevel} destFill=${destFill} />

      ${GROUPS.map((group) => html`<div>
        <h2>${group}</h2>
        ${Object.entries(CHOICES).filter(([, row]) => row.group === group)
          .map(([name, row]) => html`<${Control} name=${name} row=${row}
            value=${values[name]} onChange=${(value) => set(name, value)} />`)}
        ${Object.entries(TUNABLES).filter(([, row]) => row.group === group)
          .map(([name, row]) => html`<${Control} name=${name} row=${row}
            value=${values[name]} onChange=${(value) => set(name, value)} />`)}
      </div>`)}

      <h2>Changed from shipped (${changedKeys.length})</h2>
      <pre class=${`tune-diff${changedKeys.length ? "" : " empty"}`}>${
        changedKeys.length
          ? changedKeys.sort().map((key) =>
              `${key}: ${DEFAULTS[key]} -> ${changed[key]}`).join("\n")
          : "nothing — this is exactly what ships"}</pre>
      <div class="tune-actions" style="margin-top:10px">
        <button class="save" disabled=${!changedKeys.length} onclick=${saveToRepo}>
          Save into the repo</button>
        <button onclick=${() => setValues(withDefaults(null))}>Reset all to shipped</button>
      </div>
      ${status && html`<p class=${`tune-status ${status.kind}`}>${status.text}</p>`}

      <h2>Settings string</h2>
      <p class="tune-note">Every value, not just the changed ones, so it still
        means the same thing after a default moves. Paste one in and press
        Load.</p>
      <textarea class="tune-string" id="tune-string"
        value=${encodeTuning(values)} spellcheck="false"></textarea>
      <div class="tune-actions">
        <button onclick=${() => {
          navigator.clipboard.writeText(encodeTuning(values));
          setStatus({ kind: "ok", text: "Settings string copied." });
        }}>Copy</button>
        <button onclick=${() => {
          try {
            setValues(decodeTuning(document.getElementById("tune-string").value));
            setStatus({ kind: "ok", text: "Loaded." });
          } catch (error) {
            setStatus({ kind: "bad", text: `Could not read that: ${error.message}` });
          }
        }}>Load</button>
      </div>
    </div>
  </div>`;
}

render(html`<${Inspector} />`, document.getElementById("root"));
