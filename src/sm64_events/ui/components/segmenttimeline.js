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
// 5. PATH MOMENTS KEEP JOURNAL ORDER. The earliest is the primary start,
//    latest is the finish, and middles are steps unless explicitly marked
//    as alternative starts. Extra starts can come from another attempt;
//    they never extend the representative path.
//
// The old modal's machinery survives unchanged where it was already right:
// ONE `definitionFor` builder so backtest, lint and save send the identical
// object; `StepPicker` for the walk the journal derives; the same
// arms/fires/unclosed diagnostic the hand-authored Builder shows.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { useSegmentAuthoring, useSegmentSave,
         shippedSegmentWarning, SegmentSaveControls } from "../segmentauthoring.js";
import { entityKeyForOption, optionForEntityKey } from "../entities.js";
import { visitCards } from "../visits.js";
import { useRecordedSelection, recordedPickSummary } from "../recordselection.js";
import { Icon } from "./icons.js";
import { Modal } from "./modal.js";
import { readRecorder, useUiLog } from "../uilog.js";

// Module-level, so the array identity is stable across renders.
const RECORDER_READERS = [readRecorder];
import { optionIconSrc } from "./entityicons.js";
import { StepPicker } from "./steptrack.js";
import { VisitCard } from "./recordrows.js";
import { RecordParents } from "./recordparents.js";

const html = htm.bind(h);

// How far back the live tail re-asks, in journal ids. A load's verdict is
// revised by rows that land after it — `eventlabel.level_entry_rows` settles
// its area edges once the spawn arrives, which the measured window puts up to
// 60 game frames later — so the tail has to be able to take a row BACK, and
// it can only do that for rows it re-fetches. Sized generously (the endpoint
// walks the whole journal either way, so a few extra rows in the response
// cost nothing) and bounded so the request never becomes the whole list.
const RETRACTION_WINDOW = 40;

// The same three-way diagnostic segments.js's Builder renders for a hand-
// authored definition's backtest (backtestSummary) -- recording reaches the
// IDENTICAL arms/fires/unclosed reasoning because it is the same
// BacktestReport shape, read through the same endpoint. Not a second copy:
// wording is scoped to "this recording" rather than "this definition",
// since nothing is saved yet when this renders.
function recordingSummary(report, replacing = false) {
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
  // "Never finished" is accurate for a CREATE because the backtest ran with
  // replaces: null. Re-recording sends the real id, and there
  // arms>0/fires=0/unclosed=[] can ALSO mean "it fired, and those attempts
  // were manually wiped" (backtest.py replays journaled clears) — so the
  // replacing wording hedges where the create wording may assert.
  if (replacing)
    return `It started ${report.arms} time${report.arms === 1 ? "" : "s"} `
      + "before and finished none we still hold — your END may be wrong, or "
      + "its earlier attempts were wiped.";
  return `It started ${report.arms} time${report.arms === 1 ? "" : "s"} `
    + "before and never finished — your END may be wrong, or unreachable "
    + "from there.";
}


// `replaces` (round 16): the /api/segments row being RE-RECORDED, or null for
// an ordinary create. Replace beats delete-and-recreate because the segment's
// IDENTITY survives — same id, so routes that reference it, its PBs, attempts,
// strategies and ladders all stay attached; only the recording itself moves.
// The new recording's own defaults win over the row's stored modes
// (clock "move" + Strict) — a re-record IS a new recording, stated in copy.
//
// `prefill` (task 0096): `{name, parents}` from a caller that already knows
// the answers this surface would otherwise ask — the Library's record door
// opens the recorder ON the row being recorded, so the row's own name and its
// target's entity arrive filled in. The name lands pre-marked as his (same
// rule as a re-record: the auto-name may never overwrite a name that arrived
// with the intent); `parents` are ENTITY keys, translated once here.
export function SegmentTimeline({ t, onSaved, onCancel, replaces = null,
                                  prefill = null }) {
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
  const { pickedIds, extraStartIds, addingStart, pickNotice,
    toggleRow, resetPicks, toggleStartMode } = useRecordedSelection(derive);
  const deriveEpoch = useRef(0);
  const checkEpoch = useRef(0);
  // NAMING, and it applies BACKWARDS: nothing stores the name a row was drawn
  // with, so bumping `catalogue` refetches and every row that landmark ever
  // appeared in re-labels at once. His pick over a separate Entities screen
  // (2026-08-05) -- he names the thing while standing in front of it.
  const [renamingId, setRenamingId] = useState(null);
  const [catalogue, setCatalogue] = useState(0);
  const startRename = (row) => setRenamingId(row.id);
  const cancelRename = () => setRenamingId(null);
  const commitRename = async (row, name) => {
    setRenamingId(null);
    if ((name || "").trim() === (row.landmark_name || "")) return;
    try {
      await send("POST", "/api/landmark", { key: row.landmark, name });
      setCatalogue((n) => n + 1);
    } catch (err) { setRowsErr(String(err)); }
  };
  // The COLLAPSED cards, keyed by their oldest row's id. Collapsed rather than
  // open, because he asked for open by default and an empty set has to mean
  // "everything open" — the library's own `useOpenGroups` stores the opposite
  // (an OPEN set in localStorage, where nothing stored means all collapsed),
  // so reusing it here would mean fighting its default on every first render.
  // Not persisted: which trips you folded a minute ago is not a preference,
  // and a remembered fold would hide the newest place on the next open.
  const [folded, setFolded] = useState(new Set());
  const [synth, setSynth] = useState(null);
  const [synthErr, setSynthErr] = useState(null);
  const [lintPending, setLintPending] = useState(false);
  // A re-record opens carrying the row's own name, PRE-MARKED as his: the
  // name field keeps HIS name (it already exists), so the auto-name may
  // never overwrite it. Emptying the field still hands it back.
  const [name, setName] = useState(
    replaces ? replaces.name : (prefill && prefill.name) || "");
  // THE AUTO-NAME MAY ONLY FILL A FIELD HE HAS NOT EDITED (round 12 item 4:
  // "once I set the name for the segment name it shouldn't change" — every
  // pick toggle re-derived and overwrote what he typed). A ref, not state:
  // derive()'s .then would otherwise read the value frozen at call time.
  // Emptying the field hands it back to the auto-name; Clear resets both.
  // A prefilled name counts as his for the same reason a re-record's does.
  const nameEditedRef = useRef(!!replaces || !!(prefill && prefill.name));
  // Which entities this is a piece of (`SegmentDef.parents`) -- the ONLY way
  // a subsection can be created, and it is asked HERE because this is the
  // moment you know the answer: "nothing asks 'you just recorded this, what
  // is it a piece of?' when you would want to answer" (spec). Distinct from
  // `origin`, which is where the LIBRARY files it; a subsection of a JRB star
  // is parented to that star and filed under JRB.
  //
  // PLURAL since round 20 -- "sometimes the same subsection might be
  // practicable in multiple stars... We need to just add a + button to the
  // right and allow multiple selections." A LIST of picks; the + appends,
  // re-picking a pill changes that slot, and picking the "Nothing"
  // placeholder on a pill removes it.
  //
  // Held as the PICKER's own option ids ("8:1" / "segment:12"), never as
  // entity keys, so `value=` can highlight the chosen cell -- a star option's
  // id is the bare composite and the key is "star:8:1", and storing the key
  // here would leave every star looking unpicked while segments highlighted
  // fine. `entityKeyForOption` is the one translation, applied where the
  // definition is built.
  const [parentOptions, setParentOptions] = useState(
    replaces ? (replaces.parents || []).map(optionForEntityKey)
    : prefill ? (prefill.parents || []).map(optionForEntityKey) : []);
  const parents = parentOptions.map(entityKeyForOption);
  const [btReport, setBtReport] = useState(null);
  const [btErr, setBtErr] = useState(null);
  // Author-time lint (Task 16, spec 2026-07-28-multi-step-segments) --
  // the recorder is where lint pays best: it's what explains a backtest that
  // came back arms=0/fires=0 rather than leaving the user at a dead end.
  // `segment_id` is the replaced row's own id on a re-record, null on a
  // create (runLint below).
  const [lintFindings, setLintFindings] = useState([]);
  const [lintErr, setLintErr] = useState(null);
  // Node keys of the walked stops this definition REQUIRES. A Set, not a
  // list of clauses: the walk is ground truth and the only open question is
  // which of its stops count, so an invalid step is unreachable rather than
  // validated away. Only consulted when exactly TWO moments are picked --
  // picking a third says the walk is not the answer.
  const [required, setRequired] = useState(new Set());
  const authoring = useSegmentAuthoring();
  // WHAT THIS SURFACE ACTUALLY SHOWED, and when. Its own call rather than the
  // practice page's, because that page is unmounted while this tab is open.
  // Every latency report about the recorder so far has been unanswerable for
  // want of this: the journal says what the GAME did, and nothing said what
  // the LIST held (2026-08-06, *"I've clearly grabbed this star, but I don't
  // see the event in the recorder"* -- measured innocent on the server side,
  // 18 frames from the touch to the x-cam and published on the same frame).
  const recorderRef = useRef(null);
  useUiLog(recorderRef, RECORDER_READERS);
  // THE TAIL MACHINE'S STATE, all in refs, on purpose (round 8 item 3, his
  // 4.7 s star grab). The tail used to live inside the feed-keyed effect
  // below, and an effect RE-RUN cleans up the previous instance — but a star
  // is never one broadcast, it is a BURST (star_collected, attempt_completed,
  // rank traffic), so every fetch the burst triggered had its response
  // discarded as stale, and the serialiser's retry ran inside the dead
  // closure so ITS response was discarded too. The row only appeared when a
  // LONE message let one instance survive its own round trip — usually his
  // reset, which is the report verbatim. Refs give the machine nothing to die
  // in: `deadRef` flips on UNMOUNT only, and `epochRef` counts base-list
  // replacements so a tail racing a view switch cannot append old-view rows
  // (its response is simply discarded; the fresh base load repaints anyway).
  //
  // `tailing`/`tailAgain` serialise the fetches. Not a debounce: latency IS
  // the deliverable here, so a burst must not be made to wait -- what this
  // prevents is two fetches in flight racing each other into the row list
  // out of order.
  const tailing = useRef(false);
  const tailAgain = useRef(false);
  const rowsRef = useRef(null);
  const viewRef = useRef(view);
  viewRef.current = view;
  const epochRef = useRef(0);
  const deadRef = useRef(false);
  useEffect(() => () => { deadRef.current = true; }, []);

  // ONE writer for the held rows: the ref is what the tail machine reads for
  // its after-id (state would hand it a stale closure), the state is what
  // renders. Splitting the writes is how they would drift.
  const holdRows = (next) => { rowsRef.current = next; setRows(next); };

  async function tail() {
    if (tailing.current) { tailAgain.current = true; return; }
    const held = rowsRef.current;
    if (held == null) return;   // base list in flight; its landing re-kicks
    tailing.current = true;
    const epoch = epochRef.current;
    try {
      // RE-ASK FOR THE LAST FEW ROWS, and REPLACE them with what comes back.
      // A pure append cannot be right, because the server's answer about a
      // row CHANGES after the fact: `eventlabel.level_entry_rows` settles a
      // load's own area edges RETROACTIVELY, once the spawn that speaks for
      // the load arrives. Poll during the load and the endpoint honestly says
      // "Moved to another part of Whomp's Fortress"; poll again after the
      // spawn and that row is gone and "Started Whomp's Fortress" is there
      // instead. An append-only tail keeps the first answer forever — which
      // is his report, twice: *"I'm still getting the 'moved to another part'
      // event before the 'Started' event"* (2026-08-07), with the stray row
      // carrying the load's own time.
      //
      // So the window is re-fetched rather than skipped, and rows inside it
      // are taken from the SERVER rather than from what we hold. A row the
      // server no longer returns is dropped, which is the whole point. It
      // also picks up the repeat counter and any rename that moved a label
      // inside the window, for free.
      const newest = held.length ? held[held.length - 1].id : 0;
      const after = Math.max(0, newest - RETRACTION_WINDOW);
      const body = await getJSON(
        `/api/segments/timeline?limit=200&view=${viewRef.current}`
        + `&after_id=${after}`);
      if (!deadRef.current && epoch === epochRef.current) {
        const kept = rowsRef.current.filter((row) => row.id <= after);
        const next = [...kept, ...body.rows];
        // Only repaint when something actually differs — the recorder polls
        // per game event, and a list rebuilt identically would remount every
        // row and lose the caret in an open rename input.
        const held_now = rowsRef.current;
        const same = next.length === held_now.length
          && next.every((row, index) => row.id === held_now[index].id
                        && row.label === held_now[index].label);
        if (!same) holdRows(next);
      }
    } catch { /* a dropped tail is covered by the next event */ }
    finally {
      tailing.current = false;
      if (!deadRef.current && tailAgain.current) {
        tailAgain.current = false; tail();
      }
    }
  }

  useEffect(() => {
    epochRef.current += 1;
    const epoch = epochRef.current;
    holdRows(null); setRowsErr(null);
    getJSON(`/api/segments/timeline?limit=200&view=${view}`)
      .then((body) => {
        if (deadRef.current || epoch !== epochRef.current) return;
        holdRows(body.rows);
        tail();   // anything broadcast while the base load was in flight
      })
      .catch((err) => {
        if (!deadRef.current && epoch === epochRef.current)
          setRowsErr(String(err));
      });
    // `catalogue` is in here on purpose: a rename changes no event, only what
    // the labels READ, so the refetch is the whole of "names apply backwards".
  }, [view, catalogue]);

  // THE LIVE HALF, and the whole of it. `t.feed` grows on every websocket
  // message (store.js), so this effect fires the moment the game does
  // anything -- and merely KICKS the machine above, which asks only for what
  // is newer than the newest row held. The old modal fetched inside a
  // useEffect keyed on [view] and subscribed to nothing, so it was a snapshot
  // of the moment it opened, forever.
  const newestSeq = t && t.feed && t.feed.length ? t.feed[0].seq : null;
  useEffect(() => { tail(); }, [newestSeq]);

  function resetDownstream() {
    checkEpoch.current += 1;
    setSynth(null); setSynthErr(null);
    setBtReport(null); setBtErr(null);
    setLintFindings([]); setLintErr(null); clearSaveFeedback();
    setRequired(new Set());
  }

  // ONE builder for the definition every one of backtest / lint / save sends,
  // so the thing tested, the thing linted and the thing written are the same
  // object. They were three literals and stayed in step by hand; the moment
  // waypoints entered the picture that stopped being tenable -- a backtest run
  // against a DIFFERENT match_mode than the save uses reports on a matcher
  // branch the user will never get.
  //
  // TWO sources of waypoints and they are exclusive. `synthBody.picked` is
  // what the PERSON pointed at; `synthBody.steps` filtered by `requiredNodes`
  // is the walk we DERIVED for them, and it is offered only when exactly two
  // path moments are picked. `picked_explicit` remembers explicit middles
  // even when every middle was marked as another start, so an empty explicit
  // path cannot accidentally restore automatically derived steps.
  function definitionFor(synthBody, requiredNodes) {
    const waypoints = (synthBody.picked_explicit ?? !!(synthBody.picked || []).length)
      ? synthBody.picked.map((step) => [step.clause])
      : (synthBody.steps || [])
          .filter((step) => requiredNodes.has(step.node))
          .map((step) => [step.clause]);
    // STRICT unconditionally since round 15 — his ruling: "They should also
    // be default Strict." A recorded piece voids when you deviate, stops or
    // no stops; the old stops-only rule kept a stop-less recording
    // byte-compatible with the pre-waypoint tool, and that guarantee is
    // retired on his word. The BUILDER's own blank default is untouched.
    // clock_start "move" (round 15 item 3): the trigger detects, the
    // section entry it causes starts the clock -- "the timer doesn't
    // actually START until mario is able to finally move". His default for
    // everything this tool records.
    return { name: (name.trim() || synthBody.name), enabled: true,
      start_triggers: synthBody.start_clauses || [synthBody.start_clause],
      end_triggers: [synthBody.end_clause], guards: [], waypoints,
      parents, match_mode: "strict", clock_start: "move" };
  }

  async function runBacktest(definition, epoch) {
    setBtReport(null); setBtErr(null);
    try {
      // A re-record backtests AGAINST the row it replaces, so the report's
      // gained/lost compares the new recording with the old one's history.
      const report = await send("POST", "/api/segments/backtest",
        { definition, replaces: replaces ? replaces.id : null });
      if (!deadRef.current && epoch === checkEpoch.current) setBtReport(report);
    } catch (err) {
      if (!deadRef.current && epoch === checkEpoch.current) setBtErr(String(err));
    }
  }

  async function runLint(definition, epoch) {
    setLintFindings([]); setLintErr(null);
    try {
      // `segment_id` excludes the row being replaced from the duplicate
      // rule, or an unchanged walk reports itself as its own duplicate.
      const result = await send("POST", "/api/segments/lint",
        { definition, segment_id: replaces ? replaces.id : null });
      if (!deadRef.current && epoch === checkEpoch.current)
        setLintFindings(result.warnings);
    } catch (err) {
      if (!deadRef.current && epoch === checkEpoch.current) setLintErr(String(err));
    } finally {
      if (!deadRef.current && epoch === checkEpoch.current) setLintPending(false);
    }
  }

  function recheck(synthBody, requiredNodes) {
    const epoch = ++checkEpoch.current;
    setLintPending(true);
    const definition = definitionFor(synthBody, requiredNodes);
    runBacktest(definition, epoch);
    runLint(definition, epoch);
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
  async function derive(ids, starts = extraStartIds) {
    const epoch = ++deriveEpoch.current;
    resetDownstream();
    if (ids.length < 2) return;
    try {
      const body = await getJSON(
        `/api/segments/synthesize?ids=${ids.join(",")}`
        + (starts.length ? `&start_ids=${starts.join(",")}` : ""));
      if (deadRef.current || epoch !== deriveEpoch.current) return;
      // Everywhere you went starts REQUIRED. You walked the route you meant to
      // practise, so the common case is that all of it counts; unticking a room
      // you were only crossing is one click, and the alternative default (none
      // required) makes the tool silently produce the loose definition it
      // produced before, which is the outcome this whole pass exists to change.
      const walked = new Set((body.steps || []).map((step) => step.node));
      setSynth(body);
      if (!nameEditedRef.current) setName(body.name);
      setRequired(walked);
      recheck(body, walked);
    } catch (err) {
      if (!deadRef.current && epoch === deriveEpoch.current) setSynthErr(String(err));
    }
  }


  function clearPicks() {
    deriveEpoch.current += 1;
    resetPicks(); resetDownstream();
    // Clear un-picks the MOMENTS; on a re-record the name is the row's own
    // and stays his, exactly as it arrived.
    setName(replaces ? replaces.name : "");
    nameEditedRef.current = !!replaces;
  }

  function toggleFold(key) {
    setFolded((held) => {
      const next = new Set(held);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }

  // The id a CREATE already minted, if a Save got that far. `onSaved` is
  // awaited (a caller may still have work to finish — the Library's record
  // door LINKS the new segment, and its refusal belongs beside Save, where
  // the click landed), so a Save can fail AFTER the segment exists. A second
  // click must then re-use the id (a PUT, same as a re-record) rather than
  // mint a duplicate — the definition still lands if he re-picked meanwhile.
  const lintHasError = lintFindings.some((finding) => finding.severity === "error");
  const saveDisabled = !btReport || lintPending || !!lintErr || lintHasError;
  const { save, saving, error: saveErr, savedScope, message: saveMessage,
    clearFeedback: clearSaveFeedback } = useSegmentSave({
    authoring, initialId: replaces?.id, t, onSaved,
    disabled: saveDisabled,
    definition: () => definitionFor(synth, required),
  });

  // NEWEST FIRST is a display choice, made once, here. Every id handed to the
  // server keeps the journal's own order.
  const shown = rows ? [...rows].reverse() : [];
  const cards = visitCards(shown);
  // Same name/shape as segments.js's Builder -- an "error" severity finding
  // disables Save there too; a "warning" one does not.

  return html`<${Modal} title=${replaces ? `Re-record ${replaces.name}`
                                         : "Record a segment"}
      icon="segments" size="large"
      onClose=${onCancel}
      description=${replaces
        ? "Play it again, then point at what you just did. Save replaces this "
          + "movement's recording — its routes, PBs, attempts and strategies "
          + "stay attached."
        : "Play, then point at what you just did. Pick the moment it started, "
          + "the moment it finished, and any stops in between."}>
    ${/* A plain block wrapper, and the only thing it does is give the UI log a
         root to read this surface from. `.modal-body` is block flow, so it
         changes no layout -- the alternative was a `rootRef` prop on the
         SHARED modal, which is a change to a primitive nine other screens use
         for the sake of one. */""}
    <div ref=${recorderRef} class="record-observed">
    <label class="record-view-toggle">
      <input type="checkbox" checked=${view === "all"}
          onchange=${(e) => setView(e.target.checked ? "all" : "steps")} />
      Show every kind of moment (resets, spawns…)
    </label>

    ${rowsErr && html`<p class="badx">${rowsErr}</p>`}
    ${!rows && !rowsErr && html`<p class="meta">Loading your recent history…</p>`}

    ${rows && html`<div class="record-picks">
      <span class="meta">${recordedPickSummary(pickedIds, extraStartIds)}</span>
      ${pickedIds.length > 0 && html`<button type="button" class="quiet-button"
          aria-pressed=${addingStart}
          onclick=${toggleStartMode}>
        ${addingStart ? "Done adding starts" : "Add another start"}
      </button>`}
      ${pickedIds.length > 0 && html`<button class="quiet-button"
          onclick=${clearPicks}>
        <${Icon} name="close" size=${14} /> Clear
      </button>`}
    </div>`}
    ${addingStart && html`<p class="meta record-start-instruction" role="status">
      Pick any other recorded moment that can start this segment. Each start
      follows the same steps and finish. Pick it again to remove it.
    </p>`}
    ${pickNotice && html`<p class="meta" role="status">${pickNotice}</p>`}

    ${/* ONE CARD PER PLACE YOU WERE IN, his ask 2026-08-05: "That way, when we
         observe the live viewer, we can easily and quickly identify which
         areas contained which events." The place is server-derived (a running
         position over the whole journal — most rows do not say where they
         are), and the symbol is the SAME chain every other surface resolves
         art through, addressed as `level:<id>`. */""}
    ${rows && !shown.length && html`<p class="meta">
      Nothing recorded yet — play a bit and it will appear here.</p>`}
    ${rows && shown.length > 0 && html`<div class="record-cards">
      ${cards.map((card) => html`<${VisitCard} key=${card.key} card=${card}
        collapsed=${folded.has(card.key)}
        onToggleFold=${() => toggleFold(card.key)}
        icon=${card.level == null ? null
                                  : optionIconSrc(t, "level", card.level)}
        order=${pickedIds} starts=${extraStartIds} onToggleRow=${toggleRow}
        renamingId=${renamingId} onRenameStart=${startRename}
        onRenameCommit=${commitRename} onRenameCancel=${cancelRename} />`)}
    </div>`}

    ${synthErr && html`<p class="badx">${synthErr}</p>`}
    ${pickedIds.length >= 2 && !synth && !synthErr
      && html`<p class="meta">Working it out…</p>`}
    ${synth && html`<div class="record-review">
      <label class="builder-name">
        <span class="field-label">Segment name</span>
        <input value=${name} oninput=${(e) => {
          setName(e.target.value);
          nameEditedRef.current = e.target.value.trim() !== "";
        }} />
      </label>
      <div class="record-start-review">
        <p class="meta">Starts when any one happens:</p>
        ${(synth.start_sentences || [synth.start_sentence]).map((sentence, index) =>
          html`<p key=${index} class="meta">${index > 0 ? "OR " : ""}<b>${sentence}</b></p>`)}
      </div>
      ${(synth.picked || []).map((step, i) => html`<p key=${i} class="meta">
        Then: <b>${step.sentence}</b></p>`)}
      <p class="meta">Ends when: <b>${synth.end_sentence}</b></p>
      ${!(synth.picked_explicit ?? !!(synth.picked || []).length)
        && html`<${StepPicker} steps=${synth.steps} required=${required}
          onToggle=${toggleStep} />`}

      ${/* The ONLY door into a subsection. `parents` is absent from
           segments.js's SAVE_FIELDS and no other control writes one, which
           is why he asked "what star has subsections? I don't see a way to
           define that?" One pill per chosen parent, and the + to the right
           appends another (round 20: the same piece can belong to several
           stars). */""}
      <${RecordParents} t=${t} options=${parentOptions} onChange=${setParentOptions} />

      ${/* What a re-record COSTS, said where the save lands, before it does:
           the new recording's own defaults (clock "move" + Strict) replace
           the row's stored modes. A shipped movement's update behavior follows
           the selected save scope, as in the editor's seeded panel. */""}
      ${replaces && html`<p class="meta record-replace-note">
        Saving replaces <b>${replaces.name}</b> — new recording, same segment.
        It times from the move and voids when you deviate (Strict).${" "}
        ${replaces.seed_key
          ? shippedSegmentWarning(replaces, authoring, savedScope)
          : ""}
      </p>`}
      ${btErr && html`<p class="badx">${btErr}</p>`}
      ${!btReport && !btErr
        && html`<p class="meta">Testing against your history…</p>`}
      ${btReport && html`<p class="meta">${recordingSummary(btReport, !!replaces)}</p>`}
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
    ${saveMessage && html`<p class="meta" role="status">${saveMessage}</p>`}
    <div class="builder-actions">
      <button onclick=${onCancel}>Cancel</button>
      ${/* Save is simply ABSENT below two picks rather than present and
           refused: a start with no end can never complete, and a disabled
           control whose reason lives in its own hover is the shape he
           reported as a dead button (2026-08-02). The line above the list
           says what to do instead. */""}
      ${/* While the backtest is in flight the button is disabled — so the
           REASON lives on the button itself (his rule: put the reason where
           the click lands, or do not disable it). A click during that window
           does nothing silently, which is exactly how a re-record can be
           believed saved when it never was (round 17 item 1). */""}
      ${pickedIds.length >= 2 && html`<${SegmentSaveControls}
        authoring=${authoring} onSave=${save}
        disabled=${saveDisabled} busy=${saving}
        disabledReason=${lintHasError ? "Fix the error above before saving"
          : lintErr ? "Conditions could not be checked — pick again to retry" : ""}
        waitingLabel=${!btReport && !btErr ? "Testing your history…"
          : lintPending ? "Checking conditions…" : null}
        personalLabel=${replaces ? "Replace segment" : "Save segment"} />`}
    </div>
    </div>
  <//>`;
}
