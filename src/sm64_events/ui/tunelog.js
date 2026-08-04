// src/sm64_events/ui/tunelog.js — the practice-log card tuning inspector,
// served at /ui/tunelog.html.
//
// Mirrors tune.js/tunemarelo.js's shape: every control is generated from
// ui/logtuning.js's registry with no code of its own, SAVE POSTs to
// /api/tuning/log (rewrite_defaults, shared with the other two surfaces --
// no Python of its own either), and the thing previewed is the REAL
// `PracticeLog` (and therefore the REAL `LogCard`, `UnassignedLogCard`,
// `RankBanner`, `AttemptTable`) driven with fixture data -- not a lookalike.
//
// ONE thing this page does that tune.js/tunemarelo.js do not: it shows the
// surface at TWO widths side by side, because the four bugs this registry
// exists to fix were reported as a WIDE/NARROW pair ("collapsed cards at
// both widths", "narrow is vertically massive", "wide should look the
// same, just bigger") -- judging them one width at a time is how they end
// up inconsistent with each other. Each pane is a genuine `.app-shell` >
// `.practice-page` at a fixed pixel width (854px narrow, just above the
// app's own 850px supported floor; 1494px wide), so `@container` rules
// measure a REAL container width rather than something a harness invented.
//
// `PracticeLog` reads the tuning registry's active slot ONCE per mount
// (practicelog.js's own `useMemo(..., [])`) -- exactly like `rankclimb.js`
// reading `tuning()` once per climb, and for the identical reason (nothing
// here should retime/reflow mid-interaction from a slider three renders
// ago). So a slider drag commits the new values to the slot
// (`setLogTuning`) and then REMOUNTS both `PracticeLog` instances via a
// changed `key`, the same commit-then-remount ordering tune.js's own
// `play()` and tunemarelo.js's own `pickRoute()` already use.
//
// KNOWN GAP, written down rather than hidden: `LogCard` has no prop for its
// initial open/closed state (that is local `useState(true)`), and this page
// wants BOTH a real open card (to judge item 4, the indent/tint) and several
// real closed ones side by side (to judge items 1-3, alignment). Rather than
// adding a prop to `LogCard` for a demo-only need, this page drives the
// REAL fold button after mount -- a genuine click on the real control, the
// same gesture a player's mouse would make -- to close every card but the
// first. That is why closing this page's cards on load is a script and not
// a prop.
import { h, render } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import htm from "htm";
import { PracticeLog } from "/ui/components/practicelog.js";
import { TUNABLES, CHOICES, DEFAULTS, GROUPS, setLogTuning, encodeTuning,
         decodeTuning, changedFromDefault, withDefaults } from "/ui/logtuning.js";
import { ControlGroups } from "/ui/tunecontrols.js";

const html = htm.bind(h);
const STORE_KEY = "sm64.logTuneDraft";

// ---- Fixture data -----------------------------------------------------------
//
// Never asserted by a test (the shipped-default rule this codebase already
// enforces for tuning pages: no test may assert a preview's CONTENTS) --
// these numbers exist purely to give the real components something to draw.

let nextAttemptId = 1;
function mkAttempt(overrides = {}) {
  const id = nextAttemptId++;
  return {
    id, journal_id: id, outcome: "success",
    igt: "0'24\"11", igt_frames: 733, rta: "0'25\"02", rta_frames: 750,
    pb_delta_frames: null, cleared: false, cleared_reason: null,
    strat_tag: "Standard", rank: null, caveat: null, pb_blocked_by: null,
    segment_id: null, is_current_pb: false, outcome_detail: null,
    rollouts_total: 0, jumps_total: 0, rollouts_dustless: 0, jumps_dustless: 0,
    ...overrides,
  };
}

const OPEN_ATTEMPTS = [
  mkAttempt({ outcome: "success", igt: "0'22\"41", igt_frames: 673,
    is_current_pb: true, pb_delta_frames: -34 }),
  mkAttempt({ outcome: "reset", igt: "0'06\"10" }),
  mkAttempt({ outcome: "death", outcome_detail: "fell", igt: "0'14\"02" }),
  mkAttempt({ outcome: "success", igt: "0'25\"77", igt_frames: 773, pb_delta_frames: 100 }),
  mkAttempt({ outcome: "success", igt: "0'23\"90", igt_frames: 717, pb_delta_frames: 44 }),
];

// A graded strategy rank, shared shape between the star and segment secs.
const GRADED = (tier, division, fillPct) => ({
  rank: tier, division, fill: fillPct / 100,
  next_tier: tier, next_division: division === "I" ? tier : "I", // close enough for a preview
  next_gap_cs: 240, mode: "pb", basis: { count: 12, window: null, display: "0'24\"11" },
  reason: null,
});

const SECTIONS = [
  // 1. Two DIFFERENT rank ladders -- both banners render. Long name +
  //    attempts, so it also carries the OPEN-card / item-4 demonstration.
  {
    course_id: 1, star_id: 6,
    course_name: "Whomp's Fortress",
    star_name: "Blast Away the Wall in Front",
    pipe_segment_id: null, attempts: OPEN_ATTEMPTS,
    strategies: ["Standard", "Cannonless"], last_strat: "Standard", default_strat: null,
    rank: GRADED("Gold", "II", 40),
    entity_rank: GRADED("Silver", "IV", 70),
    one_ladder: false, armed_detail: null,
    pb: { igt: { display: "0'22\"41", attempt_id: 1, caveat: null },
          rta: { display: "0'23\"90", attempt_id: 1, caveat: null } },
    last_activity: 5,
  },
  // 2. ONE combined banner (`one_ladder: true`) -- the strategy ladder IS
  //    the star's own best-possible ladder.
  {
    course_id: 3, star_id: 1,
    course_name: "Jolly Roger Bay", star_name: "Plunder in the Sunken Ship",
    pipe_segment_id: null, attempts: [],
    strategies: ["Standard"], last_strat: "Standard", default_strat: null,
    rank: GRADED("Mario", "V", 12), entity_rank: null,
    one_ladder: true, armed_detail: null,
    pb: { igt: { display: "1'02\"33", attempt_id: null, caveat: null },
          rta: { display: "1'04\"90", attempt_id: null, caveat: null } },
    last_activity: 4,
  },
  // 3. NO rank at all -- the void item 4's brief calls out ("— pick a strat
  //    to see your rank"). No strategy picked yet, so `sec.rank.reason` is
  //    "no_strat" rather than a graded value.
  {
    course_id: 9, star_id: 3,
    course_name: "Lethal Lava Land",
    star_name: "Bully the Bullies",
    pipe_segment_id: null, attempts: [],
    strategies: [], last_strat: null, default_strat: null,
    rank: { rank: null, division: null, fill: 0, next_tier: null,
            next_division: null, next_gap_cs: null, mode: null, basis: null,
            reason: "no_strat" },
    entity_rank: null, one_ladder: false, armed_detail: null,
    pb: { igt: { display: null, attempt_id: null, caveat: null },
          rta: { display: null, attempt_id: null, caveat: null } },
    last_activity: 3,
  },
];

const SEGMENT_SECTIONS = [
  // 4. A segment card -- rule 11 parity: the same LogCard, the same
  //    RankBanner, a different `kind`.
  {
    kind: "segment", segment_id: 12, name: "DDD → BITFS",
    broken: false, pipe_star_entity: null, is_no_reds_pipe: false, course_id: null,
    attempts: [], strategies: ["Standard"], last_strat: "Standard", default_strat: "Standard",
    rank: GRADED("Bronze", "III", 55), entity_rank: null, one_ladder: true,
    armed_detail: null,
    pb: { rta: { display: "0'41\"09", attempt_id: null, caveat: null },
          igt: { display: null, attempt_id: null, caveat: null } },
    last_activity: 2,
  },
];

const MORE_SECTIONS = [
  // 5. A short name, contrasting item 3's longest one above -- if identity
  //    alignment is working, this card's icon sits at the SAME x as every
  //    other card's despite the wildly different rank/name widths.
  {
    course_id: 15, star_id: 2,
    course_name: "Tiny-Huge Island", star_name: "Pole-Jumping for Red Coins",
    pipe_segment_id: null, attempts: [],
    strategies: ["Standard"], last_strat: "Standard", default_strat: null,
    rank: GRADED("Grandmaster", "I", 95), entity_rank: GRADED("Diamond", "II", 30),
    one_ladder: false, armed_detail: null,
    pb: { igt: { display: "0'18\"04", attempt_id: null, caveat: null },
          rta: { display: "0'18\"90", attempt_id: null, caveat: null } },
    last_activity: 1,
  },
];

const UNASSIGNED_ATTEMPTS = [
  mkAttempt({ outcome: "reset", igt: "0'03\"20" }),
  mkAttempt({ outcome: "hard_reset", igt: "0'00\"40" }),
];

function fixtureView() {
  return {
    stars: [...SECTIONS, ...MORE_SECTIONS],
    segments: SEGMENT_SECTIONS,
    unassigned: UNASSIGNED_ATTEMPTS,
  };
}

// Minimal store slice `entityIconSrc`/`AttemptTable`/`RankBanner` need. Every
// field falls back gracefully to "no art override, generic star" when
// missing -- entityicons.js's own contract (`t || {}` at every call site) --
// so this can stay small rather than mirroring the whole real store.
function fixtureT() {
  return {
    clock: "igt", showDust: false,
    view: { catalog: { courses: [] }, icon_overrides: {}, segment_targets: [],
            rank_mode: "pb" },
    segments: [], courseIcons: {}, starIcons: "course", vocab: {},
    refresh() {},
  };
}

// ---- Close every card but the first, for real ------------------------------
//
// A genuine click on the real `.log-card-fold` button -- see the header
// comment for why this is a script rather than a prop on LogCard.
function collapseAllButTheFirst(container) {
  const folds = container.querySelectorAll(
    ".log-card:not(.is-unassigned) .log-card-fold");
  folds.forEach((button, index) => { if (index > 0) button.click(); });
}

function Pane({ label, width, view, t, ui, tuningKey }) {
  const ref = useRef(null);
  useEffect(() => {
    if (!ref.current) return;
    // One frame so LogCard's own mount-time `useState(true)` has already
    // committed before this queries and clicks it.
    requestAnimationFrame(() => { if (ref.current) collapseAllButTheFirst(ref.current); });
  }, [tuningKey]);
  return html`<div class="tunelog-pane" style=${`width:${width}px`}>
    <p class="tunelog-pane-label">${label} · ${width}px pane</p>
    <div class="app-shell">
      <div class="sidebar"></div>
      <div class="practice-page" ref=${ref}>
        <${PracticeLog} key=${tuningKey} v=${view} t=${t} ui=${ui}
          freshIds=${new Set()} openCompare=${null}
          focus=${null} clearFocus=${() => {}}
          focusKey=${null} onSelect=${() => {}} />
      </div>
    </div>
  </div>`;
}

function Inspector() {
  const [values, setValues] = useState(() => {
    try { return decodeTuning(localStorage.getItem(STORE_KEY) || "{}"); }
    catch { return withDefaults(null); }
  });
  const [status, setStatus] = useState(null);
  const [sort, setSort] = useState("newest");
  const [hideResets, setHideResets] = useState(false);

  useEffect(() => { localStorage.setItem(STORE_KEY, encodeTuning(values)); }, [values]);
  // Commit to the active slot SYNCHRONOUSLY, in the render body -- not in a
  // `useEffect`. PracticeLog reads the slot ONCE, in its own mount-time
  // `useMemo`, and remounts here whenever `tuningKey` (below) changes; a
  // `useEffect` runs AFTER children have already mounted and read the OLD
  // slot value, which is a real race this page shipped with once (measured:
  // the diff panel showed a slider's new value while every rendered pixel
  // stayed at the old one). A function component's body runs top to bottom
  // before its children are created, so a plain statement here is what
  // actually lands before the remount rather than after it.
  setLogTuning(values);

  const changed = useMemo(() => changedFromDefault(values), [values]);
  const changedKeys = Object.keys(changed);
  const set = (name, value) => setValues((prior) => ({ ...prior, [name]: value }));
  const tuningKey = encodeTuning(values);

  const ui = useMemo(() => ({
    sort, hideResets, setSort, setHideResets,
  }), [sort, hideResets]);
  const view = useMemo(() => fixtureView(), []);
  const t = useMemo(() => fixtureT(), []);

  async function saveToRepo() {
    setStatus({ kind: "busy", text: "Saving..." });
    try {
      const response = await fetch("/api/tuning/log", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ values }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || response.statusText);
      localStorage.removeItem(STORE_KEY);
      setStatus({ kind: "ok",
        text: `Saved ${payload.written} value(s) into logtuning.js. Reloading...` });
      setTimeout(() => location.reload(), 700);
    } catch (error) {
      setStatus({ kind: "bad", text: String(error.message || error) });
    }
  }

  return html`<div class="tune-layout">
    <div class="tune-stage">
      <${Pane} label="Narrow" width=${854} view=${view} t=${t} ui=${ui} tuningKey=${tuningKey} />
      <${Pane} label="Wide" width=${1494} view=${view} t=${t} ui=${ui} tuningKey=${tuningKey} />
    </div>

    <div class="tune-panel">
      <h1>Practice-log card tuning</h1>
      <p class="tune-note">Both panes are the app's own <code>PracticeLog</code>
        in the app's own stylesheet, one at 854px (just above the app's
        850px supported floor) and one at 1494px. Tune here; Save writes
        these values into <code>ui/logtuning.js</code> as the new shipped
        defaults.</p>
      <p class="tune-note">Every card but the first in each pane is closed
        with a real click on its own fold button after it mounts, so the
        alignment items (1-3) can be judged across several closed cards at
        once; the first card stays open, with attempts, for item 4.</p>

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
    </div>
  </div>`;
}

render(html`<${Inspector} />`, document.getElementById("root"));
