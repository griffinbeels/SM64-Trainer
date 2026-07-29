// src/sm64_events/ui/tunemarelo.js — the overall rank-up tuning inspector,
// served at /ui/tunemarelo.html.
//
// Mirrors ui/tune.js's shape (the climb inspector): every control is
// generated from ui/marelotuning.js's registry with no code of its own, SAVE
// POSTs to /api/tuning/marelo (rewrite_defaults, shared with the climb
// surface -- no Python of its own either), and the thing previewed is the
// REAL RouteRankCard + the REAL MareloCelebration overlay, driven exactly as
// app.js drives them -- not a lookalike that could drift from either.
//
// The header bar around the card is a REPLICA of header.js's context grid
// (the same reasoning tune.js's ObjectiveCard already documents: that section
// is welded into a live store, and extracting it is a follow-up) -- but the
// card ITSELF, and the overlay that flies it, are the shipped components.
// Mounting inside a replica of the real .app-shell/.context-bar matters
// because MareloCelebration measures ".marelo-slot"'s live geometry at
// runtime: a harness rendering into bare <body> would measure a layout that
// does not exist (`.claude/rules/ui-core.md`).
import { h, render } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { RANK_NAMES, DIVISION_NUMERALS, capName, divisionDigit, rankAt }
  from "/ui/components/caps.js";
import { RouteRankCard } from "/ui/components/marelo.js";
import { MareloCelebration } from "/ui/components/marelocelebrate.js";
import { MARELO_TUNABLES, MARELO_GROUPS, MARELO_DEFAULTS,
         withMareloDefaults, setMareloTuning } from "/ui/marelotuning.js";
import { ControlGroups, LevelPicker } from "/ui/tunecontrols.js";
import { celebrationsEnabled } from "/ui/celebrations.js";
import { prefersReducedMotion } from "/ui/useTween.js";

const html = htm.bind(h);
const STORE_KEY = "sm64.mareloTuneDraft";
const TOP_LEVEL = RANK_NAMES.length * DIVISION_NUMERALS.length - 1;

const rankLabel = (level) => {
  const { tier, division } = rankAt(level);
  return `${capName(tier)} ${divisionDigit(division)}`;
};

// Only what differs from the shipped defaults -- marelotuning.js exposes no
// settings-string/changedFromDefault helper of its own (it is a plainer
// registry than climbtuning.js by design, no CHOICES, no encode/decode), so
// this is computed locally rather than invented as a third export.
const changedFromMareloDefault = (values) =>
  Object.fromEntries(Object.entries(withMareloDefaults(values))
    .filter(([key, value]) => value !== MARELO_DEFAULTS[key]));

// A placeholder MARELO payload -- the numbers are for the preview card to
// have SOMETHING to show, never asserted by a test (the shipped-default rule
// this codebase already enforces for tuning pages: no test may assert a
// default's or a preview's CONTENTS).
function mareloFor(level, fill = 0.4) {
  const { tier, division } = rankAt(level);
  return {
    tier, division, division_progress: fill, marelo: 62.5, label: "Standard",
    mastery: 71.2, coverage: 0.63, n: 20, practiced: 13, scope_id: "overall",
  };
}

// A populated route list for the ROUTE SWAP demo (2026-07-28) -- "I think the
// marelo demo should just be updated to include the full functionality of the
// button, that is, the fact that we have to drop down and select something
// else. I'd like to test this feeling" (user). Deliberately spanning both a
// higher AND a lower rank than a typical mid-ladder start, so picking between
// them can show a swap that eases DOWN as well as one that goes up -- the
// case the feature exists to get right ("never a relaxation" of the no-false-
// celebration rule). Preview numbers only, same "never asserted by a test"
// contract as `mareloFor` above.
// Tier values are caps.js::CAP's internal KEYS, not the display names --
// "Gold" renders as "Waluigi", "Silver" as "Toadsworth", "Bronze" as "Toad"
// (capName()).
const DEMO_ROUTES = [
  { id: 1, name: "16 Star — LBLJ (Standard)", tier: "Gold", division: "II", fill: 0.62 },
  { id: 2, name: "70 Star (Standard)", tier: "Silver", division: "IV", fill: 0.18 },
  { id: 3, name: "Any% — BLJless", tier: "Bronze", division: "I", fill: 0.91 },
];

function mareloForRoute(route) {
  return {
    tier: route.tier, division: route.division, division_progress: route.fill,
    marelo: 62.5, label: route.name, mastery: 71.2, coverage: 0.63,
    n: 20, practiced: 13, scope_id: `route:${route.id}`,
  };
}

// The header's context grid, replicated around the REAL RouteRankCard -- see
// this file's header comment for why the chrome is a replica and the card is
// not. Three placeholder cards keep the 4-column grid template's proportions
// honest; MareloCelebration only ever reads the geometry of ".marelo-slot"
// itself, not its siblings.
//
// `routes`/`activeRouteId`/`onPickRoute` make the card's own dropdown a REAL
// picker (2026-07-28 -- "the fact that we have to drop down and select
// something else. I'd like to test this feeling") rather than the inert
// `routes=[]`/`interactive=false` it shipped with: driving it exactly the way
// the app does is what exercises the swap, not a scripted playback of it.
function HeaderBar({ marelo, identity, routes, activeRouteId, onPickRoute }) {
  return html`<header class="context-shell">
    <div class="context-bar" aria-label="Practice context (preview)">
      <div class="context-control"><span class="context-control-copy">
        <span class="context-label">Session</span>
        <span class="context-value">Preview</span>
      </span></div>
      <div class="marelo-slot">
        <${RouteRankCard} marelo=${marelo} routes=${routes} activeRouteId=${activeRouteId}
          onPickRoute=${onPickRoute} identity=${identity} interactive=${true} />
      </div>
      <div class="context-control"><span class="context-control-copy">
        <span class="context-label">Clock</span>
        <span class="context-value">Usamune IGT</span>
      </span></div>
      <div class="context-control"><span class="context-control-copy">
        <span class="context-label">Grading</span>
        <span class="context-value">Best PB</span>
      </span></div>
    </div>
  </header>`;
}

function Inspector() {
  const [values, setValues] = useState(() => {
    try { return withMareloDefaults(JSON.parse(localStorage.getItem(STORE_KEY) || "null")); }
    catch { return withMareloDefaults(null); }
  });
  const [fromLevel, setFromLevel] = useState(0);          // Capless V
  const [toLevel, setToLevel] = useState(16);              // Waluigi IV
  const [celebration, setCelebration] = useState(null);
  const [status, setStatus] = useState(null);
  // Report 3 (2026-07-28): "after ranking up, it should stay at that new
  // rank ... I should rank up, and then see that new rank settle in the
  // header." `settled` is what the replica HeaderBar reads to decide which
  // rank it shows -- false is the from-rank (before Play, or after Restart),
  // true is the destination (once the celebration has landed and acked).
  // Nothing about the REAL app changes for this: MareloCelebration already
  // hides `.marelo-slot` for the duration via a live DOM query, which this
  // page's own `.marelo-slot` wrapper below is subject to as well.
  const [settled, setSettled] = useState(false);
  // Where the BAR sits at each end, as 0..1. Demo inputs, not marelotuning.js
  // rows: in the app this value comes from the server (division_progress),
  // so a tuning default would be a shipped lie. Here they exist so both ends
  // can be judged at any percentage (user, 2026-07-28: "We should probably
  // add a demo tuning option for this, to test what it looks like at
  // different start / end percentages").
  const [fromFill, setFromFill] = useState(0);
  const [toFill, setToFill] = useState(0.4);
  // The ROUTE SWAP demo's own state (2026-07-28) -- which DEMO_ROUTES entry
  // the header card's own picker has selected, `null` meaning "Overall".
  // Separate from fromLevel/toLevel/settled above on purpose: those drive the
  // Play/Restart CLIMB preview, which stays untouched and always previews
  // "Overall" regardless of which route is picked here (matching how Play
  // already behaved before this demo had a working picker at all).
  const [activeRouteId, setActiveRouteId] = useState(null);

  useEffect(() => { localStorage.setItem(STORE_KEY, JSON.stringify(values)); }, [values]);
  // The destination can never be at or below the start: a drop is not a
  // rank-up, and MareloCelebration would simply show no climb.
  useEffect(() => {
    if (toLevel <= fromLevel) setToLevel(Math.min(TOP_LEVEL, fromLevel + 1));
  }, [fromLevel, toLevel]);
  // Picking a different rank-up to preview makes the LAST one's destination
  // stale -- without this, changing "Start" after a completed Play would
  // keep showing the previous run's landed rank instead of the new from-rank.
  useEffect(() => { setSettled(false); }, [fromLevel, toLevel]);

  const changed = changedFromMareloDefault(values);
  const changedKeys = Object.keys(changed);
  const set = (name, value) => setValues((prior) => ({ ...prior, [name]: value }));

  // Restart: cancel any in-flight celebration and show the from-rank again --
  // the state a fresh Play begins from. Play performs the exact same restore
  // (a stale `settled` from a PRIOR run must not leak into a new one) before
  // it starts a new flight.
  const restart = () => {
    setCelebration(null);
    setSettled(false);
    setStatus(null);
  };

  // Committed to the module-level tuning slot BEFORE the celebration mounts
  // (marelocelebrate.js reads it once, mareloTuning()), the same ordering
  // tune.js's own play() uses for the climb registry.
  const play = () => {
    restart();
    setMareloTuning(values);
    const from = rankAt(fromLevel);
    const to = rankAt(toLevel);
    setCelebration({ from: { tier: from.tier, division: from.division },
                     to: { tier: to.tier, division: to.division },
                     key: Date.now() });
  };

  // Same commit-before-the-effect ordering as `play()` above, for the same
  // reason: RouteRankCard reads `mareloTuning()` when the swap it triggers
  // STARTS, not continuously, so a slider drag alone would never reach a
  // route pick made before the next Play. Committing here is what lets the
  // "Swap" group's controls actually affect the swap the picker fires.
  const pickRoute = (id) => {
    setMareloTuning(values);
    setActiveRouteId(id);
  };

  async function saveToRepo() {
    setStatus({ kind: "busy", text: "Saving..." });
    try {
      const response = await fetch("/api/tuning/marelo", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ values }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || response.statusText);
      localStorage.removeItem(STORE_KEY);
      setStatus({ kind: "ok",
        text: `Saved ${payload.written} value(s) into marelotuning.js. Reloading...` });
      setTimeout(() => location.reload(), 700);
    } catch (error) {
      setStatus({ kind: "bad", text: String(error.message || error) });
    }
  }

  const warnings = [];
  if (prefersReducedMotion()) {
    warnings.push("Your OS is set to reduce motion, so every celebration "
      + "SNAPS to the end state. Nothing here will animate until that is off.");
  }
  if (!celebrationsEnabled()) {
    warnings.push("Celebrations are switched off in the app settings, so the "
      + "climb inside the card snaps -- the flight itself still plays. Turn "
      + "them back on in the main app's settings drawer to see the whole thing.");
  }

  return html`<div class="tune-layout">
    <div class="tune-stage">
      <div class="app-shell">
        <div class="sidebar"></div>
        <div class="practice-page">
          <${HeaderBar}
            marelo=${activeRouteId != null
              ? mareloForRoute(DEMO_ROUTES.find((route) => route.id === activeRouteId))
              : mareloFor(settled ? toLevel : fromLevel, settled ? toFill : fromFill)}
            routes=${DEMO_ROUTES} activeRouteId=${activeRouteId} onPickRoute=${pickRoute}
            identity=${
              /* Report 3 (2026-07-28): "I would expect what I just saw to be
                 what's resting in the header ... The demo restarts the
                 animation once it lands." This card's `identity` never
                 changed across a Play, so once `settled` flips true its
                 `marelo` prop jumps straight from the from-rank to the
                 destination and useRankClimb reads that as the SAME
                 measurement legitimately improving -- a climb, not a snap --
                 and replays the whole multi-tier animation in the replica
                 header exactly where the user is looking right after the
                 overlay lands. `identity` is the mechanism this hook already
                 has for "this is a different measurement, nobody earned it"
                 (ui/rankclimb.js); flipping it in the SAME render that
                 flips `settled` is what makes landing SNAP straight to the
                 earned rank instead of climbing to it a second time.

                 A picked ROUTE (2026-07-28) gets its own identity, keyed on
                 the route id -- it must differ from BOTH "tunemarelo" and
                 "tunemarelo-settled" and from every other route, or
                 useRankClimb would read a route swap as the SAME measurement
                 improving and CLIMB it instead of snapping, which is exactly
                 the false celebration this whole feature exists to avoid.
                 The real app gets this for free (header.js's identity
                 already includes the scope label); the demo has to say it
                 explicitly because this replica's identity otherwise has
                 nothing to do with which route is selected. */
              activeRouteId != null ? `route:${activeRouteId}`
                : (settled ? "tunemarelo-settled" : "tunemarelo")} />
        </div>
      </div>
    </div>

    <div class="tune-panel">
      <h1>Overall rank-up tuning</h1>
      <p class="tune-note">The card above is the app's own RouteRankCard in
        the app's own stylesheet. Set a start and a destination, press Play,
        then tune. Save writes these values into${" "}
        <code>ui/marelotuning.js</code> as the new shipped defaults.</p>
      ${warnings.map((text) => html`<p class="tune-warn">${text}</p>`)}

      <h2>Rank-up</h2>
      <div class="tune-pickers">
        <${LevelPicker} label="Start" level=${fromLevel}
          onChange=${(level) => setFromLevel(Math.min(level, TOP_LEVEL - 1))} />
        <${LevelPicker} label="Destination" level=${toLevel} min=${fromLevel + 1}
          onChange=${setToLevel} />
      </div>
      <div class="tune-pickers" style="margin-top:8px">
        <div>
          <label>Showing</label>
          ${/* htm COLLAPSES the whitespace between running text and an
               interpolation, so "→" against ${rankLabel(toLevel)} renders as
               "→Waluigi 4". The space has to be written as an interpolation
               of its own -- there is no lint for this, only a render. */""}
          <div class="tune-showing">${rankLabel(fromLevel)}${" "}→${" "}${rankLabel(toLevel)}</div>
        </div>
        <div class="tune-row">
          <label for="fromfill" title="Where the bar sits on the rank you HAD, 0-100%.">Bar at start (%)</label>
          <input id="fromfill" type="number" min="0" max="100" step="1"
            value=${Math.round(fromFill * 100)}
            onchange=${(e) => setFromFill(Math.min(1, Math.max(0, Number(e.target.value) / 100)))} />
          <input type="range" min="0" max="100" step="1"
            value=${Math.round(fromFill * 100)}
            oninput=${(e) => setFromFill(Number(e.target.value) / 100)} />
        </div>
        <div class="tune-row">
          <label for="tofill" title="Where the bar lands on the rank you EARNED. This is division_progress in the app, and it is what the header shows once the card settles.">Bar at end (%)</label>
          <input id="tofill" type="number" min="0" max="100" step="1"
            value=${Math.round(toFill * 100)}
            onchange=${(e) => setToFill(Math.min(1, Math.max(0, Number(e.target.value) / 100)))} />
          <input type="range" min="0" max="100" step="1"
            value=${Math.round(toFill * 100)}
            oninput=${(e) => setToFill(Number(e.target.value) / 100)} />
        </div>
      </div>
      <div class="tune-actions" style="margin-top:10px">
        <button class="primary" onclick=${play}>▶ Play</button>
        <button onclick=${restart}>Restart</button>
      </div>

      <${ControlGroups} groups=${MARELO_GROUPS} rows=${MARELO_TUNABLES}
        values=${values} defaults=${MARELO_DEFAULTS} onChange=${set} />

      <h2>Changed from shipped (${changedKeys.length})</h2>
      <pre class=${`tune-diff${changedKeys.length ? "" : " empty"}`}>${
        changedKeys.length
          ? changedKeys.sort().map((key) =>
              `${key}: ${MARELO_DEFAULTS[key]} -> ${changed[key]}`).join("\n")
          : "nothing — this is exactly what ships"}</pre>
      <div class="tune-actions" style="margin-top:10px">
        <button class="save" disabled=${!changedKeys.length} onclick=${saveToRepo}>
          Save into the repo</button>
        <button onclick=${() => setValues(withMareloDefaults(null))}>Reset all to shipped</button>
      </div>
      ${status && html`<p class=${`tune-status ${status.kind}`}>${status.text}</p>`}
    </div>

    ${/* Keyed on the celebration's own key: pressing Play again while a
         previous flight is still in progress must restart it from "out"
         rather than resuming whatever phase the prior run had reached --
         a stale `phase` state surviving a prop change is exactly what a
         REMOUNT (Preact's own `key` reconciliation) exists to prevent. */""}
    ${celebration && html`<${MareloCelebration} key=${celebration.key}
      celebration=${celebration} scopeId="tune"
      marelo=${mareloFor(toLevel, toFill)}
      fromFill=${fromFill} toFill=${toFill}
      routes=${[]} activeRouteId=${null}
      onDone=${() => { setCelebration(null); setSettled(true); }} />`}
  </div>`;
}

render(html`<${Inspector} />`, document.getElementById("root"));
