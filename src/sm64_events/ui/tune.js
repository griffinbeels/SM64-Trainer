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
         divisionDigit, rankAt } from "/ui/components/caps.js";
import { buildClimbPlan } from "/ui/climbplan.js";
import { climbTimings } from "/ui/climbcurve.js";
import { TUNABLES, CHOICES, DEFAULTS, GROUPS, setTuning, encodeTuning,
         decodeTuning, changedFromDefault, withDefaults } from "/ui/climbtuning.js";
import { celebrationsEnabled } from "/ui/celebrations.js";
import { prefersReducedMotion } from "/ui/useTween.js";
import { ControlGroups, LevelPicker } from "/ui/tunecontrols.js";

const html = htm.bind(h);
const STORE_KEY = "sm64.climbTuneDraft";
const TOP_LEVEL = RANK_NAMES.length * DIVISION_NUMERALS.length - 1;

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

  // A control whose number is not the number that runs, and which the reader
  // did NOT ask for. Only the ladder qualifies: its budget still squeezes a
  // step on a crowded climb, which is a surprise.
  //
  // The tier crossing deliberately does NOT appear here. It interpolates from
  // its one-tier length down to its many-tier one by design, so warning about
  // it would fire on every multi-tier climb and mean nothing — a warning that
  // is always on is a warning nobody reads. It gets a neutral line instead,
  // stating the fall-off as the arithmetic it is.
  const clamps = [
    ["Ladder step", values.ladderStepMs, plan.timings.ladderMs,
     "ladder budget / floor"],
  ].filter(([, set, effective]) => plan.ladder > 0 && Math.abs(set - effective) > 1);
  const crossingMs = plan.timings.anticipateMs + plan.timings.payoffMs;

  return html`<div>
    <h2>What will play · ${(plan.totalMs / 1000).toFixed(2)}s</h2>
    ${plan.crossings > 0 && html`<p class="tune-note">
      ${plan.crossings} tier crossing${plan.crossings === 1 ? "" : "s"}${" "}
      → <b>${Math.round(crossingMs)}ms</b> each${" "}
      ${plan.crossings > 1 ? html`(${values.tierDwellMs}ms at one,${" "}
        ${values.tierDwellMinMs}ms at ${values.tierDwellMinAt})` : null}
    </p>`}
    ${/* The explicit spaces are load-bearing: htm collapses whitespace between
         a text node and an element, and this line read "but220ms will run"
         without them -- the same trap the settings-note paragraph hit. */""}
    ${clamps.map(([label, set, effective, blame]) => html`<p class="tune-warn">
      <b>${label}</b>${" "}is set to ${Math.round(set)}ms but${" "}
      <b>${Math.round(effective)}ms</b>${" "}will run — the ${blame} is deciding.
    </p>`)}
    ${/* The START offset is a column, not a derived running total, because a
         step may OVERLAP the one before it (the destination tier's ladder
         plays over its own crossing). Printing only durations would make two
         concurrent steps look sequential — hiding the exact thing the overlap
         control exists to move. "over" marks a step that begins before its
         predecessor has finished animating. */""}
    <pre class="tune-plan">${plan.steps.map((step, index) => {
      const { tier, division } = rankAt(step.level);
      const previous = plan.steps[index - 1];
      const over = previous && step.at < previous.at + previous.ms - 0.5;
      // A step that does not own the level is described by the BAR it moves,
      // never by a rank: the closing sweep runs while earlier ranks are still
      // on screen, so printing its destination rank beside a cap that reads
      // something else would be the readout telling its own small lie.
      const what = step.ownsLevel === false
        ? `bar ${Math.round(step.barFrom * 100)}% → ${Math.round(step.barTo * 100)}%`
        : `${capName(tier)} ${divisionDigit(division)}`;
      return `${String(Math.round(step.at)).padStart(5)}ms  `
        + `${step.kind.padEnd(11)}${what}`.padEnd(28)
        + `${String(Math.round(step.ms)).padStart(4)}ms`
        + (over ? "  over" : "");
    }).join("\n")}</pre>
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

      <${ControlGroups} groups=${GROUPS} rows=${{ ...CHOICES, ...TUNABLES }}
        values=${values} defaults=${DEFAULTS} onChange=${set} />

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
