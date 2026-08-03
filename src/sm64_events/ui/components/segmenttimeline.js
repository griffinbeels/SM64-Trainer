// src/sm64_events/ui/components/segmenttimeline.js — "record what I just
// did" (spec 2026-07-28-multi-step-segments, Task 13): pick a start moment
// and an end moment off the recent journal (GET /api/segments/timeline),
// preview the synthesized clause pair + a backtest against your own history
// (POST /api/segments/backtest), then save (POST /api/segments) as a
// match_mode: "loose" definition. This is the tool the whole feature exists
// for — "I really love a 'record what I just did' tool. This also lets
// someone define super custom segments very very very easily" (the user,
// choosing this design 2026-07-28).
//
// Three states in ONE modal: "start" -> "end" -> "review". Consumes Task 8
// (backtest), Task 11 (timeline), Task 12 (tracking/synthesize.py). Neither
// synthesize() nor suggest_name() had a caller outside tracking/ before this
// component needed one, so GET /api/segments/synthesize (server/api.py) is
// this task's own addition — the wiring the plan's Task 12 report flagged as
// a concern to carry forward, not a contradiction of the brief's file list.
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { Icon } from "./icons.js";
import { Modal } from "./modal.js";
import { StepPicker } from "./steptrack.js";

const html = htm.bind(h);

// The same three-way diagnostic segments.js's Builder renders for a hand-
// authored definition's backtest (backtestSummary) -- recording reaches the
// IDENTICAL arms/fires/unclosed reasoning because it is the same
// BacktestReport shape, read through the same endpoint. Not a second copy:
// wording is scoped to "this recording" rather than "this definition",
// since nothing is saved yet when this renders.
function recordingSummary(report) {
  const n = report.attempts.length;
  if (report.fires > 0)
    return `${report.fires} fire${report.fires === 1 ? "" : "s"} out of `
      + `${n} attempt${n === 1 ? "" : "s"} in your history.`;
  if (report.arms === 0)
    return "Your START moment never happened before in your history — go "
      + "back and pick a different one.";
  if (report.unclosed.length > 0)
    return `It started ${report.arms} time${report.arms === 1 ? "" : "s"} `
      + "before, and is still running as of your most recent history.";
  // "Never finished" is accurate here ONLY because runBacktest() below
  // always sends replaces: null (recording only ever CREATES) -- with a
  // real `replaces`, arms>0/fires=0/unclosed=[] can also mean "it fired,
  // and those attempts were manually wiped" (backtest.py replays journaled
  // clears). If a later flow reuses this component to back an EDIT
  // (split/merge is the shape that would), this wording needs the same
  // hedge backtestSummary would need for that case.
  return `It started ${report.arms} time${report.arms === 1 ? "" : "s"} `
    + "before and never finished — your END may be wrong, or unreachable "
    + "from there.";
}

// One clickable row per timeline event -- the row IS the picker, no
// secondary confirm step (spec: "point at what you just did").
function TimelineRows({ rows, onPick, emptyText }) {
  if (!rows.length) return html`<p class="meta">${emptyText}</p>`;
  return html`<div class="record-rows">
    ${rows.map((row) => html`<button key=${row.id} type="button"
        class="record-row" onclick=${() => onPick(row)}>
      ${row.label}
    </button>`)}
  </div>`;
}

export function SegmentTimeline({ onSaved, onCancel }) {
  const [step, setStep] = useState("start");      // "start" | "end" | "review"
  // Task 11's own carried concern: the default view (view=steps) is only
  // ~10% of the journal by design, and the rarer reset/spawn-triggered
  // starts are reachable ONLY through view=all -- without this control here
  // they are unreachable through this tool at all.
  const [view, setView] = useState("steps");
  const [rows, setRows] = useState(null);
  const [rowsErr, setRowsErr] = useState(null);
  const [startRow, setStartRow] = useState(null);
  const [endRow, setEndRow] = useState(null);
  const [synth, setSynth] = useState(null);       // {start_clause, end_clause, start_sentence, end_sentence, name}
  const [synthErr, setSynthErr] = useState(null);
  const [name, setName] = useState("");
  const [btReport, setBtReport] = useState(null);
  const [btErr, setBtErr] = useState(null);
  // Author-time lint (Task 16, spec 2026-07-28-multi-step-segments) --
  // the recorder is where lint pays best: it's what explains a backtest that
  // came back arms=0/fires=0 rather than leaving the user at a dead end.
  // `segment_id: null` always -- this flow only ever CREATES.
  const [lintFindings, setLintFindings] = useState([]);
  const [lintErr, setLintErr] = useState(null);
  const [saveErr, setSaveErr] = useState(null);
  // Node keys of the walked stops this definition REQUIRES. A Set, not a
  // list of clauses: the walk is ground truth and the only open question is
  // which of its stops count, so an invalid step is unreachable rather than
  // validated away.
  const [required, setRequired] = useState(new Set());
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setRows(null); setRowsErr(null);
    getJSON(`/api/segments/timeline?limit=200&view=${view}`)
      .then((body) => setRows(body.rows))
      .catch((err) => setRowsErr(String(err)));
  }, [view]);

  function resetDownstream() {
    setSynth(null); setSynthErr(null);
    setBtReport(null); setBtErr(null);
    setLintFindings([]); setLintErr(null); setSaveErr(null);
    setRequired(new Set());
  }

  function pickStart(row) {
    setStartRow(row); setEndRow(null);
    resetDownstream();
    setStep("end");
  }

  // ONE builder for the definition every one of backtest / lint / save sends,
  // so the thing tested, the thing linted and the thing written are the same
  // object. They were three literals and stayed in step by hand; the moment
  // waypoints entered the picture that stopped being tenable -- a backtest run
  // against a DIFFERENT match_mode than the save uses reports on a matcher
  // branch the user will never get (the exact trap the old match_mode comment
  // here was already working around).
  //
  // STRICT the moment a stop is required, LOOSE when none is. That is not a
  // preference: a declared path only means anything under the strict matcher's
  // path cursor, and a recording with no declared stops is byte-for-byte the
  // loose definition this tool has always produced.
  function definitionFor(synthBody, requiredNodes) {
    const waypoints = (synthBody.steps || [])
      .filter((step) => requiredNodes.has(step.node))
      .map((step) => [step.clause]);
    return { name: (name.trim() || synthBody.name), enabled: true,
      start_triggers: [synthBody.start_clause],
      end_triggers: [synthBody.end_clause], guards: [], waypoints,
      match_mode: waypoints.length ? "strict" : "loose" };
  }

  async function runBacktest(definition) {
    setBtReport(null); setBtErr(null);
    try {
      const report = await send("POST", "/api/segments/backtest",
        { definition, replaces: null });
      setBtReport(report);
    } catch (err) { setBtErr(String(err)); }
  }

  async function runLint(definition) {
    setLintFindings([]); setLintErr(null);
    try {
      const result = await send("POST", "/api/segments/lint",
        { definition, segment_id: null });
      setLintFindings(result.warnings);
    } catch (err) { setLintErr(String(err)); }
  }

  function recheck(synthBody, requiredNodes) {
    const definition = definitionFor(synthBody, requiredNodes);
    runBacktest(definition);
    runLint(definition);
  }

  // Toggling a stop re-tests against his real history immediately. That is the
  // whole safety net for a strict definition: under the path rule a wrong step
  // does not record a sloppy time, it SILENTLY VOIDS the run -- so "would this
  // have fired on the runs I have already done" has to be answerable before
  // Save, not discovered a week later by a movement that never records.
  function toggleStep(node) {
    const next = new Set(required);
    if (next.has(node)) next.delete(node); else next.add(node);
    setRequired(next);
    if (synth) recheck(synth, next);
  }

  async function pickEnd(row) {
    setEndRow(row);
    resetDownstream();
    setStep("review");
    try {
      const body = await getJSON(
        `/api/segments/synthesize?start_id=${startRow.id}&end_id=${row.id}`);
      // Everywhere you went starts REQUIRED. You walked the route you meant to
      // practise, so the common case is that all of it counts; unticking a room
      // you were only crossing is one click, and the alternative default (none
      // required) makes the tool silently produce the loose definition it
      // produced before, which is the outcome this whole pass exists to change.
      const walked = new Set((body.steps || []).map((step) => step.node));
      setSynth(body);
      setName(body.name);
      setRequired(walked);
      recheck(body, walked);
    } catch (err) { setSynthErr(String(err)); }
  }

  // Abandonable with no side effects at every step (user rule 2026-07-26,
  // the target-picker flow): nothing is written until Save, so going back
  // just re-derives the review from scratch -- there is no partial state to
  // unwind.
  function backTo(target) {
    if (target === "start") setEndRow(null);
    resetDownstream();
    setStep(target);
  }

  async function save() {
    if (!synth || !btReport) return;   // see Save's disabled= below
    setSaving(true); setSaveErr(null);
    try {
      const body = await send("POST", "/api/segments",
                              definitionFor(synth, required));
      onSaved(body.id);
    } catch (err) { setSaveErr(String(err)); }
    finally { setSaving(false); }
  }

  const later = startRow
    ? (rows || []).filter((row) => row.id > startRow.id) : [];
  // Same name/shape as segments.js's Builder -- an "error" severity finding
  // disables Save there too; a "warning" one does not.
  const lintHasError = lintFindings.some((finding) => finding.severity === "error");

  return html`<${Modal} title="Record a segment" icon="segments" size="large"
      onClose=${onCancel}
      description="Point at what you just did: pick when it started, then when it finished.">
    <div class="record-steps">
      <span class="record-step ${step === "start" ? "on" : ""}">1. Start</span>
      <span class="record-step ${step === "end" ? "on" : ""}">2. End</span>
      <span class="record-step ${step === "review" ? "on" : ""}">3. Review</span>
    </div>

    <label class="record-view-toggle">
      <input type="checkbox" checked=${view === "all"}
          onchange=${(e) => setView(e.target.checked ? "all" : "steps")} />
      Show every kind of moment (resets, spawns…)
    </label>

    ${rowsErr && html`<p class="badx">${rowsErr}</p>`}
    ${!rows && !rowsErr && html`<p class="meta">Loading your recent history…</p>`}

    ${rows && step === "start" && html`<${TimelineRows} rows=${rows}
        onPick=${pickStart}
        emptyText="Nothing recorded yet — play a bit, then come back." />`}

    ${rows && step === "end" && html`<div>
      <p class="meta record-picked">Start: <b>${startRow.label}</b>
        <button class="quiet-button" onclick=${() => backTo("start")}>
          <${Icon} name="stepBack" size=${14} /> Change
        </button>
      </p>
      <${TimelineRows} rows=${later} onPick=${pickEnd}
          emptyText="Nothing later than that in your history yet — play a bit more, then come back." />
    </div>`}

    ${step === "review" && html`<div class="record-review">
      <p class="meta record-picked">Start: <b>${startRow.label}</b>
        <button class="quiet-button" onclick=${() => backTo("start")}>
          <${Icon} name="stepBack" size=${14} /> Change
        </button>
      </p>
      <p class="meta record-picked">End: <b>${endRow.label}</b>
        <button class="quiet-button" onclick=${() => backTo("end")}>
          <${Icon} name="stepBack" size=${14} /> Change
        </button>
      </p>
      ${synthErr && html`<p class="badx">${synthErr}</p>`}
      ${!synth && !synthErr && html`<p class="meta">Working it out…</p>`}
      ${synth && html`<div>
        <label class="builder-name">
          <span class="field-label">Segment name</span>
          <input value=${name} oninput=${(e) => setName(e.target.value)} />
        </label>
        <p class="meta">Starts when: <b>${synth.start_sentence}</b></p>
        <p class="meta">Ends when: <b>${synth.end_sentence}</b></p>
        <${StepPicker} steps=${synth.steps} required=${required}
          onToggle=${toggleStep} />
        ${btErr && html`<p class="badx">${btErr}</p>`}
        ${!btReport && !btErr
          && html`<p class="meta">Testing against your history…</p>`}
        ${btReport && html`<p class="meta">${recordingSummary(btReport)}</p>`}
        ${lintErr && html`<p class="badx">${lintErr}</p>`}
        ${lintFindings.length > 0 && html`<div class="lint-panel">
          ${lintFindings.map((finding, i) => html`<div key=${i}
              class="lint-finding lint-${finding.severity}">
            <${Icon} name=${finding.severity === "error" ? "close" : "shield"} size=${14} />
            ${" "}${finding.message}
          </div>`)}
        </div>`}
      </div>`}
    </div>`}

    ${saveErr && html`<p class="badx">${saveErr}</p>`}
    <div class="builder-actions">
      <button onclick=${onCancel}>Cancel</button>
      ${step === "review" && html`<button class="primary-button"
          disabled=${!btReport || saving || lintHasError} onclick=${save}>
        <${Icon} name="save" size=${16} />${" "}${saving ? "Saving…" : "Save segment"}
      </button>`}
    </div>
  <//>`;
}
