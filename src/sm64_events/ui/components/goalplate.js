// ui/components/goalplate.js — PB over GOAL, one fixed-size plate per card.
//
// Task 0106 (Griffin, 2026-08-26): "We should update the card that appears
// for each star / segment to show a "GOAL" display. This should automatically
// stay in sync with the goal we have selected on the scorecard." The goal is
// the Scorecard's own per-entry answer (GET /api/scorecard/entry_goals), never
// a second derivation here, so the two surfaces cannot disagree.
//
// The plate keeps ONE size whatever it says, so a streamer can crop it out of
// the top card: "it needs to clearly display the PB/Goal on top of each other,
// and any goal celebrations should happen right on top of that display." The
// celebration therefore plays inside the plate's own box.
//
// Picking here PINS that one entry (his 2026-09-23 choice): the pin replaces
// the Scorecard's pick for this entry only. A goal is one time to beat; a
// round typed time reads "sub 1'45" (his "low 1:4x", without wildcards).
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { csOfFrame, fmtSeconds } from "../format.js";
import { capName, divisionDigit } from "./caps.js";
import { SearchMenu } from "./searchselect.js";
import { parseGapTime } from "../scorecardgoal.js";
import { celebrationsEnabled } from "../celebrations.js";
import { useIdentityFetch } from "../refetch.js";

const html = htm.bind(h);

// How long the plate holds its before/after once a goal falls. Long enough to
// read "GOAL!" on a stream, short enough to be over before the next attempt.
export const GOAL_CELEBRATION_MS = 2600;

export const fmtCs = (cs) => (cs == null ? "—" : fmtSeconds(cs / 100));

// The rank scope automatic goals follow on the Practice tab: the header's
// MARELO scope (the route you are practicing), else Overall.
export const goalScopeOf = (t) => (t.marelo && t.marelo.scope_id) || "overall";

/** Every card's goal in ONE request, refetched whenever anything that can move
 *  a goal lands (`mareloRev` bumps on every refresh event, goal_changed
 *  included). Returns {key: EntryGoal}. */
export function useEntryGoals(t, keys) {
  const [goals, setGoals] = useState({});
  const scope = goalScopeOf(t);
  const keyList = [...new Set(keys)].sort().join(",");
  // A card joining the list changes the identity too; keeping the goals
  // already on screen (rather than clearing) stops every plate blinking.
  useIdentityFetch(`${scope}|${keyList}`, t.mareloRev, () => {
    if (!keyList) { setGoals({}); return undefined; }
    let live = true;
    getJSON(`/api/scorecard/entry_goals?scope=${encodeURIComponent(scope)}`
        + `&keys=${encodeURIComponent(keyList)}`)
      .then((body) => { if (live) setGoals((body && body.goals) || {}); })
      .catch(() => {});
    return () => { live = false; };
  });
  return goals;
}

export function goalSourceLabel(goal) {
  const source = goal && goal.source;
  if (!source) return "";
  if (source.kind === "automatic" || source.kind === "division") {
    const rank = source.tier ? `${capName(source.tier)} ${divisionDigit(source.division)}`
      : source.label;
    return source.kind === "automatic" ? `Auto · ${rank}` : rank;
  }
  // A typed time's own words are already the goal line; the source is Custom.
  if (source.kind === "time") return "Custom";
  return source.label || "";
}

// The server words a typed time ("sub 1'45" for a whole second); every other
// goal prints as the plain time.
export function goalTimeLabel(goal) {
  if (!goal || goal.goal_cs == null) return "—";
  if (goal.source && goal.source.kind === "time" && goal.source.label) return goal.source.label;
  return fmtCs(goal.goal_cs);
}

// "10x streak" / "10x" / "" -- the repeat half of a goal, in the words
// streamers already use for it.
export function repeatLabel(goal) {
  if (!goal || !(goal.count > 1)) return "";
  return `${goal.count}x${goal.streak ? " streak" : ""}`;
}

/** The count row: one pip per required success (up to PIP_LIMIT; past it a
 *  plain N/M), the newest one popping as it fills. A streak that broke offers
 *  to forgive the break -- his call: a detected reset the player considers
 *  "fair game" should not cost the run. */
const PIP_LIMIT = 12;
function GoalProgress({ goal, goalKey, onChanged }) {
  const progress = goal && goal.progress;
  const lastDone = useRef(progress ? progress.done : 0);
  const [popAt, setPopAt] = useState(-1);
  const done = progress ? Math.min(progress.done, progress.needed) : 0;
  useEffect(() => {
    if (progress && progress.done > lastDone.current) setPopAt(done - 1);
    lastDone.current = progress ? progress.done : 0;
  }, [progress && progress.done]);
  if (!progress) return html`<div class="goal-plate-row goal-plate-count" aria-hidden="true"></div>`;
  const pips = progress.needed <= PIP_LIMIT;
  async function forgive() {
    try {
      await send("POST", "/api/scorecard/entry_goal/forgive",
        { key: goalKey, attempt_id: progress.broken_by });
      onChanged();
    } catch { /* the next refresh shows the unchanged count */ }
  }
  return html`<div class="goal-plate-row goal-plate-count"
      title=${goal.streak ? `Best streak this session: ${progress.best}` : ""}>
    ${pips
      ? html`<span class="goal-pips" aria-label=${`${done} of ${progress.needed}`}>
          ${Array.from({ length: progress.needed }, (_, index) => html`<i
            key=${index} class="goal-pip ${index < done ? "is-on" : ""}
            ${index === popAt ? "is-new" : ""}"></i>`)}
        </span>`
      : html`<span class="goal-count-text">${done}<small>/${progress.needed}</small></span>`}
    ${goal.streak && progress.broken_by != null && html`<button type="button"
        class="goal-plate-forgive" onclick=${forgive}
        title="That miss should not count: restore the streak">Undo break</button>`}
  </div>`;
}

// The attempt's time on the GOAL's clock, as displayed centiseconds -- the
// unit goals are stored in and ranks grade on.
function attemptCs(attempt, clock) {
  const frames = clock === "rta" ? attempt.rta_frames : attempt.igt_frames;
  return frames == null ? null : csOfFrame(frames);
}

/** Fires when a FRESH finished attempt meets the goal the plate was holding.
 *  Never on mount or on a page load catching up on history (the glossary's
 *  Celebration rule): the first render only records which attempts exist. */
export function useGoalCelebration(attempts, goal) {
  const seen = useRef(null);
  // The goal an attempt is judged against is the one on screen BEFORE it
  // landed. The goal fetch and the session fetch race; an automatic goal can
  // step up before the attempt row arrives, so the looser of the last two
  // goals shown is the one the player was chasing.
  const held = useRef({ current: null, previous: null });
  const [party, setParty] = useState(null);
  const goalCs = goal ? goal.goal_cs : null;
  if (goalCs !== held.current.current) {
    held.current = { current: goalCs, previous: held.current.current };
  }
  const wasAchieved = useRef(goal ? goal.achieved : false);
  useEffect(() => {
    const ids = new Set((attempts || []).map((a) => a.id));
    if (seen.current === null) { seen.current = ids; return; }
    const fresh = (attempts || []).filter((a) => !seen.current.has(a.id));
    seen.current = ids;
    // A repeat goal ("sub 1'55 10x") falls on the attempt that completes the
    // COUNT, not on any single qualifying time -- that is the server's
    // `achieved`, which the goals fetch may deliver a beat after the row.
    if (goal && goal.count > 1) return;
    const targets = [held.current.current, held.current.previous].filter((cs) => cs != null);
    if (!fresh.length || !targets.length || !goal) return;
    const chased = Math.max(...targets);
    const hit = fresh.find((a) => a.outcome === "success" && !a.cleared
      && attemptCs(a, goal.clock) != null && attemptCs(a, goal.clock) <= chased);
    if (!hit || !celebrationsEnabled()) return;
    setParty({ id: hit.id, beatenCs: chased, timeCs: attemptCs(hit, goal.clock) });
  }, [attempts]);
  const achievedNow = !!(goal && goal.count > 1 && goal.achieved);
  useEffect(() => {
    const was = wasAchieved.current;
    wasAchieved.current = achievedNow;
    if (!achievedNow || was || seen.current === null || !celebrationsEnabled()) return;
    const progress = goal.progress || {};
    setParty({ id: `count-${progress.window_start}-${progress.best}`,
               beatenCs: goal.goal_cs, timeCs: null,
               count: progress.needed, streak: goal.streak });
  }, [achievedNow]);
  useEffect(() => {
    if (!party) return undefined;
    const timer = setTimeout(() => setParty(null), GOAL_CELEBRATION_MS);
    return () => clearTimeout(timer);
  }, [party && party.id]);
  return party;
}

// The picker's list: Automatic, then every rank and every player with a time
// on this entry, each wearing the time it would set.
function pickerGroups(options, goal) {
  const groups = [{ label: null, options: [
    { value: "auto", label: goal && goal.pinned
        ? "Automatic (follow the Scorecard)" : "Automatic ✓" }] }];
  if (!options) return groups;
  groups.push({ label: "Rank", options: (options.divisions || []).map((d) => ({
    value: `division:${d.tier}:${d.division}`,
    label: `${capName(d.tier)} ${divisionDigit(d.division)} · ${fmtCs(d.cs)}` })) });
  groups.push({ label: "Players", options: (options.runners || []).map((r) => ({
    value: `runner:${r.runner}`, label: `${r.runner} · ${fmtCs(r.cs)}` })) });
  return groups;
}

// A picked option -> the PUT body's goal (null = back to Automatic).
function goalOfOption(value) {
  if (value === "auto") return null;
  const [kind, ...rest] = value.split(":");
  if (kind === "division") return { kind, tier: rest[0], division: rest[1] };
  return { kind: "runner", runner: rest.join(":") };
}

// "Repeat N× in total / in a row". Held locally until a pick lands, so
// closing the picker changes nothing. `onApply` re-saves the pinned
// threshold with new repeat settings without finding it in the list again.
function RepeatControls({ count, setCount, streak, setStreak, onApply, busy }) {
  const mode = (on, label, value) => html`<button type="button"
      class="quiet-button ${on ? "is-selected" : ""}" disabled=${count < 2}
      aria-pressed=${on ? "true" : "false"} onclick=${() => setStreak(value)}>${label}</button>`;
  return html`<div class="goal-picker-repeat">
    <label>Repeat <input type="number" min="1" max="999" value=${count}
      oninput=${(e) => setCount(Math.max(1, Math.min(999, Number(e.target.value) || 1)))}
      aria-label="How many times" />×</label>
    <span class="goal-picker-mode" role="group" aria-label="Count how">
      ${mode(!streak, "in total", false)}${mode(streak, "in a row", true)}
    </span>
    ${onApply && html`<button type="button" class="quiet-button"
      disabled=${busy} onclick=${onApply}>Apply</button>`}
  </div>`;
}

function TypedTime({ busy, onSet }) {
  const [typed, setTyped] = useState("");
  const typedCs = parseGapTime(typed) ?? parseLooseTime(typed);
  return html`<form class="goal-picker-time"
      onsubmit=${(e) => { e.preventDefault(); if (typedCs) onSet({ kind: "time", cs: typedCs }); }}>
    <input type="text" inputmode="decimal" placeholder="Your own time, e.g. 1'45"
      value=${typed} oninput=${(e) => setTyped(e.target.value)} aria-label="Goal time" />
    <button type="submit" class="quiet-button" disabled=${!typedCs || busy}>Set</button>
  </form>`;
}

function useGoalOptions(goalKey, scope) {
  const [options, setOptions] = useState(null);
  useEffect(() => {
    getJSON(`/api/scorecard/entry_goal_options?key=${encodeURIComponent(goalKey)}`
        + `&scope=${encodeURIComponent(scope)}`)
      .then(setOptions).catch(() => setOptions({ divisions: [], runners: [] }));
  }, [goalKey, scope]);
  return options;
}

function GoalPicker({ goalKey, t, goal, onClose, onSaved }) {
  const scope = goalScopeOf(t);
  const options = useGoalOptions(goalKey, scope);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const held = goal || {};
  const [count, setCount] = useState(held.count > 1 ? held.count : 1);
  const [streak, setStreak] = useState(!!held.streak);
  async function save(value) {
    setBusy(true); setError(null);
    try {
      const repeat = value && count > 1 ? { count, streak } : {};
      await send("PUT", "/api/scorecard/entry_goal",
        { key: goalKey, scope, goal: value && { ...value, ...repeat } });
      onSaved();
      onClose();
    } catch (err) {
      setError((err && err.message) || "Could not save that goal.");
    } finally { setBusy(false); }
  }
  const current = held.pinned && held.source ? pinValueOf(held) : null;
  const repeatChanged = current && (count !== (held.count || 1)
    || (count > 1 && streak !== !!held.streak));
  const footer = html`<${RepeatControls} count=${count} setCount=${setCount}
      streak=${streak} setStreak=${setStreak} busy=${busy}
      onApply=${repeatChanged ? () => save(current) : null} />
    <${TypedTime} busy=${busy} onSet=${save} />`;
  return html`<${SearchMenu} title="Pick a goal" groups=${pickerGroups(options, goal)}
    onPick=${(value) => save(goalOfOption(value))} onClose=${onClose}
    busy=${busy} error=${error} align="right" footer=${footer}
    emptyNote=${options ? "No ranks or players for this one — type a time below." : "Loading…"} />`;
}

// The PUT body that re-creates a pinned goal's threshold.
function pinValueOf(goal) {
  const source = goal.source;
  if (source.kind === "division") return { kind: "division", tier: source.tier, division: source.division };
  if (source.kind === "runner") return { kind: "runner", runner: source.runner || source.label };
  return { kind: "time", cs: goal.goal_cs };
}

// "1'45", "1:45", "1:45.2", "105", "45.5" -> centiseconds. The picker accepts
// how people SAY a goal, not only the display format parseGapTime reads.
export function parseLooseTime(text) {
  const match = String(text ?? "").trim().match(/^(?:(\d+)['’:])?(\d{1,2})(?:["”.](\d{1,2}))?$/);
  if (!match) return null;
  const minutes = match[1] ? Number(match[1]) : 0;
  const seconds = Number(match[2]);
  if (match[1] && seconds > 59) return null;
  const centis = match[3] ? (match[3].length === 1 ? Number(match[3]) * 10 : Number(match[3])) : 0;
  const cs = (minutes * 60 + seconds) * 100 + centis;
  return cs > 0 ? cs : null;
}

/** The plate: the card's PB line (the existing PbTag, still a link) over the
 *  GOAL line, which opens the picker. */
export function GoalPlate({ goalKey, goal, pbTag, attempts, t, onChanged }) {
  const [open, setOpen] = useState(false);
  const party = useGoalCelebration(attempts, goal);
  const achieved = goal && goal.achieved && !party;
  return html`<div class="goal-plate ${party ? "is-celebrating" : ""}
      ${achieved ? "is-achieved" : ""}" data-goal-key=${goalKey}>
    <div class="goal-plate-row goal-plate-pb">
      <span class="goal-plate-word is-pb">PB</span>${pbTag}
      <span class="goal-plate-clock">${goal ? goal.clock.toUpperCase() : ""}</span>
    </div>
    <div class="goal-plate-row goal-plate-goal">
      <button type="button" class="goal-plate-trigger" aria-expanded=${open}
          title="Pick the goal for this star (pins it on the Scorecard too)"
          onclick=${() => setOpen(!open)}>
        <span class="goal-plate-word">GOAL</span>
        <span class="goal-plate-time">${goalTimeLabel(goal)}</span>
        ${achieved ? html`<span class="goal-plate-done" aria-label="achieved">✓</span>` : null}
        <span class="goal-plate-source">${[repeatLabel(goal), goalSourceLabel(goal)]
          .filter(Boolean).join(" · ")}</span>
      </button>
      ${open ? html`<${GoalPicker} goalKey=${goalKey} t=${t} goal=${goal}
          onClose=${() => setOpen(false)} onSaved=${onChanged} />` : null}
    </div>
    <${GoalProgress} goal=${goal} goalKey=${goalKey} onChanged=${onChanged} />
    ${party ? html`<div class="goal-plate-party" aria-live="polite">
        <span class="goal-plate-party-word">GOAL!</span>
        <span class="goal-plate-party-time">${party.timeCs != null
          ? html`${fmtCs(party.timeCs)} <s>${fmtCs(party.beatenCs)}</s>`
          : `${party.count}x${party.streak ? " in a row" : ""} under ${fmtCs(party.beatenCs)}`}</span>
      </div>` : null}
  </div>`;
}
