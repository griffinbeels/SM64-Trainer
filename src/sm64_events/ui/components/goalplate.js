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
  useEffect(() => {
    if (!keyList) { setGoals({}); return; }
    let live = true;
    getJSON(`/api/scorecard/entry_goals?scope=${encodeURIComponent(scope)}`
        + `&keys=${encodeURIComponent(keyList)}`)
      .then((body) => { if (live) setGoals((body && body.goals) || {}); })
      .catch(() => {});
    return () => { live = false; };
  }, [keyList, scope, t.mareloRev]);
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
  return source.label || "";
}

// "sub 1'45" for a whole-second threshold; the exact time otherwise.
export function goalTimeLabel(goal) {
  if (!goal || goal.goal_cs == null) return "—";
  if (goal.source && goal.source.kind === "time" && goal.goal_cs % 100 === 0)
    return `sub ${fmtSeconds(goal.goal_cs / 100).replace(/"00$/, "")}`;
  return fmtCs(goal.goal_cs);
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
  useEffect(() => {
    const ids = new Set((attempts || []).map((a) => a.id));
    if (seen.current === null) { seen.current = ids; return; }
    const fresh = (attempts || []).filter((a) => !seen.current.has(a.id));
    seen.current = ids;
    const targets = [held.current.current, held.current.previous].filter((cs) => cs != null);
    if (!fresh.length || !targets.length || !goal) return;
    const chased = Math.max(...targets);
    const hit = fresh.find((a) => a.outcome === "success" && !a.cleared
      && attemptCs(a, goal.clock) != null && attemptCs(a, goal.clock) <= chased);
    if (!hit || !celebrationsEnabled()) return;
    setParty({ id: hit.id, beatenCs: chased, timeCs: attemptCs(hit, goal.clock) });
  }, [attempts]);
  useEffect(() => {
    if (!party) return undefined;
    const timer = setTimeout(() => setParty(null), GOAL_CELEBRATION_MS);
    return () => clearTimeout(timer);
  }, [party && party.id]);
  return party;
}

function GoalPicker({ goalKey, t, goal, onClose, onSaved }) {
  const [options, setOptions] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [typed, setTyped] = useState("");
  const scope = goalScopeOf(t);
  useEffect(() => {
    getJSON(`/api/scorecard/entry_goal_options?key=${encodeURIComponent(goalKey)}`
        + `&scope=${encodeURIComponent(scope)}`)
      .then(setOptions).catch(() => setOptions({ divisions: [], runners: [] }));
  }, [goalKey, scope]);
  async function save(value) {
    setBusy(true); setError(null);
    try {
      await send("PUT", "/api/scorecard/entry_goal", { key: goalKey, scope, goal: value });
      onSaved();
      onClose();
    } catch (err) {
      setError((err && err.message) || "Could not save that goal.");
    } finally { setBusy(false); }
  }
  const groups = [{ label: null, options: [
    { value: "auto", label: goal && goal.pinned
        ? "Automatic (follow the Scorecard)" : "Automatic ✓" }] }];
  if (options) {
    groups.push({ label: "Rank", options: (options.divisions || []).map((d) => ({
      value: `division:${d.tier}:${d.division}`,
      label: `${capName(d.tier)} ${divisionDigit(d.division)} · ${fmtCs(d.cs)}` })) });
    groups.push({ label: "Players", options: (options.runners || []).map((r) => ({
      value: `runner:${r.runner}`, label: `${r.runner} · ${fmtCs(r.cs)}` })) });
  }
  function pick(value) {
    if (value === "auto") return save(null);
    const [kind, ...rest] = value.split(":");
    if (kind === "division") return save({ kind, tier: rest[0], division: rest[1] });
    return save({ kind: "runner", runner: rest.join(":") });
  }
  const typedCs = parseGapTime(typed) ?? parseLooseTime(typed);
  const footer = html`<form class="goal-picker-time"
      onsubmit=${(e) => { e.preventDefault(); if (typedCs) save({ kind: "time", cs: typedCs }); }}>
    <input type="text" inputmode="decimal" placeholder="Your own time, e.g. 1'45"
      value=${typed} oninput=${(e) => setTyped(e.target.value)} aria-label="Goal time" />
    <button type="submit" class="quiet-button" disabled=${!typedCs || busy}>Set</button>
  </form>`;
  return html`<${SearchMenu} title="Goal for this star" groups=${groups}
    onPick=${pick} onClose=${onClose} busy=${busy} error=${error}
    align="right" footer=${footer}
    emptyNote=${options ? "No ranks or players for this one — type a time below." : "Loading…"} />`;
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
    <div class="goal-plate-row goal-plate-pb">${pbTag}</div>
    <div class="goal-plate-row goal-plate-goal">
      <button type="button" class="goal-plate-trigger" aria-expanded=${open}
          title="Pick the goal for this star (pins it on the Scorecard too)"
          onclick=${() => setOpen(!open)}>
        <span class="goal-plate-word">GOAL</span>
        <span class="goal-plate-time">${goalTimeLabel(goal)}</span>
        ${achieved ? html`<span class="goal-plate-done" aria-label="achieved">✓</span>` : null}
        <span class="goal-plate-source">${goalSourceLabel(goal)}</span>
      </button>
      ${open ? html`<${GoalPicker} goalKey=${goalKey} t=${t} goal=${goal}
          onClose=${() => setOpen(false)} onSaved=${onChanged} />` : null}
    </div>
    ${party ? html`<div class="goal-plate-party" aria-live="polite">
        <span class="goal-plate-party-word">GOAL!</span>
        <span class="goal-plate-party-time">${fmtCs(party.timeCs)}
          <s>${fmtCs(party.beatenCs)}</s></span>
      </div>` : null}
  </div>`;
}
