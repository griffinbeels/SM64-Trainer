// src/sm64_events/ui/components/segmenttimeline.js — the RECORDER: point at
// what you just did and it becomes a definition.
//
// Rewritten 2026-08-05 from the three-state "Record a segment" modal that
// shipped 2026-07-28 (spec 2026-07-28-multi-step-segments, Task 13). His
// complaint about that one framed the whole rewrite:
//
//   "the user should not have to waste time on figuring out the vocabulary
//    for what they want to do. They should just be able to DO THE THING THEY
//    WANT TO DO IN GAME. Once they do it, they should be able to select from
//    the history of what they just did, and be able to extract the important
//    bit."
//
// and then, settling the shape:
//
//   "It should be one surface. If I come to that screen after playing, it
//    should show stuff that I just did; if I open that screen and KEEP
//    playing, it should show what I'm now doing. I should be able to select
//    any number of the events, in chronological order, to define the segment
//    that I want to capture."
//
// Five properties, each of which the old modal got wrong in its own way:
//
// 1. NO LIVE MODE. Arriving after playing and staying while playing are the
//    SAME state. There is no toggle, no "go live", no paused/streaming
//    distinction, because there is nothing for a person to choose between.
// 2. IT OPENS ONTO HISTORY, never an empty screen waiting for input.
// 3. NEWEST FIRST. `GET /api/segments/timeline` answers oldest-first (its
//    `id` sort is the only chronological key the journal has — `frame` runs
//    backward across every reset), so the reversal is a DISPLAY choice made
//    here and the ids the endpoint is asked about are untouched by it.
// 4. ROWS LAND AS THEY HAPPEN. Every websocket event triggers a tail fetch
//    (`after_id=<newest held>`), which is why `label_event` stays server-side
//    and there is no second labeller in the browser. A broadcast carries
//    `seq`, never the journal `id` a row is picked by, so the client has to
//    come back for the id regardless.
// 5. N MOMENTS ARE THE DEFINITION. The earliest picked is the start, the
//    latest is the end, everything between is a waypoint. The ORDER is the
//    journal's, not the click order — see `pickedIds` below.
//
// The old modal's machinery survives unchanged where it was already right:
// ONE `definitionFor` builder so backtest, lint and save send the identical
// object; `StepPicker` for the walk the journal derives; the same
// arms/fires/unclosed diagnostic the hand-authored Builder shows.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { courseUnionGroups, entityKeyForOption, parseSegmentId }
  from "../entities.js";
import { fmtIgtShort } from "../format.js";
import { Icon } from "./icons.js";
import { Modal } from "./modal.js";
import { PickerDialog } from "./entitymodal.js";
import { optionIconSrc } from "./entityicons.js";
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

// What a picked row is FOR, given how many are picked and where this one
// sits. Checked by RENDER rather than by a node test of its own -- the class
// it produces is the only thing telling a reader which end of a newest-first
// list is the start, so `tests/test_fixture_reaches_the_real_page.py` counts
// `.record-mark.role-start`/`.role-finish`/`.role-stop` at two picks and at
// three, which covers every branch including the boundary (at two, index 1 is
// the finish and never a stop).
function roleOf(index, count) {
  if (index === 0) return "start";
  if (index === count - 1) return "finish";
  return "stop";
}

const ROLE_WORDS = { start: "Start", stop: "Stop", finish: "Finish" };

// One clickable row per timeline moment. The row IS the picker -- no
// secondary confirm step (spec: "point at what you just did") -- and it is a
// TOGGLE, because with N selections there is no step to advance to that could
// stand in for un-picking one.
function TimelineRows({ rows, order, onToggle, emptyText }) {
  if (!rows.length) return html`<p class="meta">${emptyText}</p>`;
  return html`<div class="record-rows">
    ${rows.map((row) => {
      const index = order.indexOf(row.id);
      const picked = index >= 0;
      const role = picked ? roleOf(index, order.length) : null;
      return html`<button key=${row.id} type="button"
          class=${`record-row${picked ? " picked" : ""}`}
          aria-pressed=${picked ? "true" : "false"}
          onclick=${() => onToggle(row)}>
        <span class=${`record-mark${picked ? ` role-${role}` : ""}`}>
          ${picked ? index + 1 : ""}
        </span>
        <span class="record-label">${row.label}</span>
        ${picked && order.length > 1
          && html`<span class="record-role">${ROLE_WORDS[role]}</span>`}
        ${/* "what was the timer in game", his own words -- and it is the
             thing you choose BY, so it belongs on the row rather than in the
             review. Through fmtIgtShort, the display form every other time on
             screen goes through; a type with no Usamune number of its own (a
             level change) shows nothing rather than a computed stand-in. */""}
        ${row.igt_frames != null
          && html`<span class="record-igt">${fmtIgtShort(row.igt_frames)}</span>`}
      </button>`;
    })}
  </div>`;
}

export function SegmentTimeline({ t, onSaved, onCancel }) {
  // Task 11's own carried concern: the default view (view=steps) is only
  // ~10% of the journal by design, and the rarer reset/spawn-triggered
  // starts are reachable ONLY through view=all -- without this control here
  // they are unreachable through this tool at all. It is NOT a live-mode
  // toggle: both views are equally live.
  const [view, setView] = useState("steps");
  const [rows, setRows] = useState(null);      // oldest first, as served
  const [rowsErr, setRowsErr] = useState(null);
  // The ids the player pointed at, ALWAYS in journal order. Not click order:
  // the list is drawn newest-first, so clicking down it hands them over
  // backwards, and "in chronological order" is a property the events already
  // have. The server sorts too (GET /api/segments/synthesize), so the two
  // cannot disagree about which end is the start.
  const [pickedIds, setPickedIds] = useState([]);
  const [synth, setSynth] = useState(null);
  const [synthErr, setSynthErr] = useState(null);
  const [name, setName] = useState("");
  // Which entity this is a piece of (`SegmentDef.parent`) -- the ONLY way a
  // subsection can be created, and it is asked HERE because this is the
  // moment you know the answer: "nothing asks 'you just recorded this, what
  // is it a piece of?' when you would want to answer" (spec). Distinct from
  // `origin`, which is where the LIBRARY files it; a subsection of a JRB star
  // is parented to that star and filed under JRB.
  //
  // Held as the PICKER's own option id ("8:1" / "segment:12"), never as the
  // entity key, so `value=` can highlight the chosen cell -- a star option's
  // id is the bare composite and the key is "star:8:1", and storing the key
  // here would leave every star looking unpicked while segments highlighted
  // fine. `entityKeyForOption` is the one translation, applied where the
  // definition is built.
  const [parentOption, setParentOption] = useState(null);
  const [pickingParent, setPickingParent] = useState(false);
  const parent = parentOption == null ? null
                                      : entityKeyForOption(parentOption);
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
  // validated away. Only consulted when exactly TWO moments are picked --
  // picking a third says the walk is not the answer.
  const [required, setRequired] = useState(new Set());
  const [saving, setSaving] = useState(false);
  // Serialises the tail fetches. Not a debounce: latency IS the deliverable
  // here, so a burst must not be made to wait -- what this prevents is two
  // fetches in flight racing each other into the row list out of order.
  const tailing = useRef(false);
  const tailAgain = useRef(false);

  useEffect(() => {
    let alive = true;
    setRows(null); setRowsErr(null);
    getJSON(`/api/segments/timeline?limit=200&view=${view}`)
      .then((body) => { if (alive) setRows(body.rows); })
      .catch((err) => { if (alive) setRowsErr(String(err)); });
    return () => { alive = false; };
  }, [view]);

  // THE LIVE HALF, and the whole of it. `t.feed` grows on every websocket
  // message (store.js), so this effect fires the moment the game does
  // anything -- and asks only for what is newer than the newest row held.
  // The old modal fetched inside a useEffect keyed on [view] and subscribed
  // to nothing, so it was a snapshot of the moment it opened, forever.
  const newestSeq = t && t.feed && t.feed.length ? t.feed[0].seq : null;
  useEffect(() => {
    if (!rows) return;                       // the first load is still in flight
    let alive = true;
    async function tail() {
      if (tailing.current) { tailAgain.current = true; return; }
      tailing.current = true;
      try {
        const after = rows.length ? rows[rows.length - 1].id : 0;
        const body = await getJSON(
          `/api/segments/timeline?limit=200&view=${view}&after_id=${after}`);
        if (alive && body.rows.length)
          setRows((held) => [...held, ...body.rows]);
      } catch { /* a dropped tail is covered by the next event */ }
      finally {
        tailing.current = false;
        if (tailAgain.current) { tailAgain.current = false; tail(); }
      }
    }
    tail();
    return () => { alive = false; };
  }, [newestSeq]);

  function resetDownstream() {
    setSynth(null); setSynthErr(null);
    setBtReport(null); setBtErr(null);
    setLintFindings([]); setLintErr(null); setSaveErr(null);
    setRequired(new Set());
  }

  // ONE builder for the definition every one of backtest / lint / save sends,
  // so the thing tested, the thing linted and the thing written are the same
  // object. They were three literals and stayed in step by hand; the moment
  // waypoints entered the picture that stopped being tenable -- a backtest run
  // against a DIFFERENT match_mode than the save uses reports on a matcher
  // branch the user will never get.
  //
  // STRICT the moment a stop is required, LOOSE when none is. That is not a
  // preference: a declared path only means anything under the strict matcher's
  // path cursor, and a recording with no declared stops is byte-for-byte the
  // loose definition this tool has always produced.
  //
  // TWO sources of waypoints and they are exclusive. `synthBody.picked` is
  // what the PERSON pointed at; `synthBody.steps` filtered by `requiredNodes`
  // is the walk we DERIVED for them, and it is offered only when exactly two
  // moments are picked. Picking a third is how you say the derivation is not
  // the answer -- which is what keeps the two-click case working unchanged.
  function definitionFor(synthBody, requiredNodes) {
    const waypoints = (synthBody.picked || []).length
      ? synthBody.picked.map((step) => [step.clause])
      : (synthBody.steps || [])
          .filter((step) => requiredNodes.has(step.node))
          .map((step) => [step.clause]);
    return { name: (name.trim() || synthBody.name), enabled: true,
      start_triggers: [synthBody.start_clause],
      end_triggers: [synthBody.end_clause], guards: [], waypoints,
      parent, match_mode: waypoints.length ? "strict" : "loose" };
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

  // Every change to the selection re-derives the whole definition. Nothing is
  // written until Save, so there is no partial state to unwind and un-picking
  // a moment costs exactly what picking one does (his standing rule that a
  // multi-step flow is abandonable with NO side effects).
  async function derive(ids) {
    resetDownstream();
    if (ids.length < 2) return;
    try {
      const body = await getJSON(
        `/api/segments/synthesize?ids=${ids.join(",")}`);
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

  function toggleRow(row) {
    const next = pickedIds.includes(row.id)
      ? pickedIds.filter((id) => id !== row.id)
      : [...pickedIds, row.id].sort((left, right) => left - right);
    setPickedIds(next);
    derive(next);
  }

  function clearPicks() { setPickedIds([]); resetDownstream(); }

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

  // NEWEST FIRST is a display choice, made once, here. Every id handed to the
  // server keeps the journal's own order.
  const shown = rows ? [...rows].reverse() : [];
  const parentGroups = t && t.view ? courseUnionGroups(
    t.view.catalog, (t.segments || []).filter((s) => !s.is_hundred_coin_engine),
    (t.vocab || {}).course_by_level || {}) : [];
  const parentName = (() => {
    if (parentOption == null) return null;
    for (const group of parentGroups)
      for (const option of group.options)
        if (option.id === parentOption) return option.name;
    return parent;
  })();
  // Same name/shape as segments.js's Builder -- an "error" severity finding
  // disables Save there too; a "warning" one does not.
  const lintHasError = lintFindings.some((finding) => finding.severity === "error");

  return html`<${Modal} title="Record a segment" icon="segments" size="large"
      onClose=${onCancel}
      description="Play, then point at what you just did. Pick the moment it started, the moment it finished, and any stops in between.">
    <label class="record-view-toggle">
      <input type="checkbox" checked=${view === "all"}
          onchange=${(e) => setView(e.target.checked ? "all" : "steps")} />
      Show every kind of moment (resets, spawns…)
    </label>

    ${rowsErr && html`<p class="badx">${rowsErr}</p>`}
    ${!rows && !rowsErr && html`<p class="meta">Loading your recent history…</p>`}

    ${rows && html`<div class="record-picks">
      <span class="meta">${pickedIds.length === 0
        ? "Nothing picked yet — newest is at the top."
        : pickedIds.length === 1
          ? "1 moment picked. Pick the one it finished on."
          : `${pickedIds.length} moments picked.`}</span>
      ${pickedIds.length > 0 && html`<button class="quiet-button"
          onclick=${clearPicks}>
        <${Icon} name="close" size=${14} /> Clear
      </button>`}
    </div>`}

    ${rows && html`<${TimelineRows} rows=${shown} order=${pickedIds}
        onToggle=${toggleRow}
        emptyText="Nothing recorded yet — play a bit and it will appear here." />`}

    ${synthErr && html`<p class="badx">${synthErr}</p>`}
    ${pickedIds.length >= 2 && !synth && !synthErr
      && html`<p class="meta">Working it out…</p>`}
    ${synth && html`<div class="record-review">
      <label class="builder-name">
        <span class="field-label">Segment name</span>
        <input value=${name} oninput=${(e) => setName(e.target.value)} />
      </label>
      <p class="meta">Starts when: <b>${synth.start_sentence}</b></p>
      ${(synth.picked || []).map((step, i) => html`<p key=${i} class="meta">
        Then: <b>${step.sentence}</b></p>`)}
      <p class="meta">Ends when: <b>${synth.end_sentence}</b></p>
      ${(synth.picked || []).length === 0
        && html`<${StepPicker} steps=${synth.steps} required=${required}
          onToggle=${toggleStep} />`}

      ${/* The ONLY door into a subsection. `parent` is absent from
           segments.js's SAVE_FIELDS and no other control writes one, which
           is why he asked "what star has subsections? I don't see a way to
           define that?" */""}
      <div class="record-parent">
        <span class="field-label">What is this a piece of?</span>
        <button type="button" class="entity-trigger"
            onclick=${() => setPickingParent(true)}>
          ${parentName || "Nothing — it stands on its own"}
        </button>
      </div>
      ${pickingParent && html`<${PickerDialog} groups=${parentGroups}
        value=${parentOption} title="What is this a piece of?" depth=${2}
        placeholder="Nothing — it stands on its own"
        iconFor=${(id) => optionIconSrc(t,
          parseSegmentId(id) == null ? "star" : "segment",
          parseSegmentId(id) == null ? id : parseSegmentId(id))}
        onPick=${(id) => { setParentOption(id); setPickingParent(false); }}
        onClose=${() => setPickingParent(false)} />`}

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

    ${saveErr && html`<p class="badx">${saveErr}</p>`}
    <div class="builder-actions">
      <button onclick=${onCancel}>Cancel</button>
      ${/* Save is simply ABSENT below two picks rather than present and
           refused: a start with no end can never complete, and a disabled
           control whose reason lives in its own hover is the shape he
           reported as a dead button (2026-08-02). The line above the list
           says what to do instead. */""}
      ${pickedIds.length >= 2 && html`<button class="primary-button"
          disabled=${!btReport || saving || lintHasError} onclick=${save}>
        <${Icon} name="save" size=${16} />${" "}${saving ? "Saving…" : "Save segment"}
      </button>`}
    </div>
  <//>`;
}
