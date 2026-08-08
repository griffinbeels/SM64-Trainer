// src/sm64_events/ui/components/segments.js — definition list + builder.
// The form is 100% vocab-driven: GET /api/segments/vocab supplies trigger
// types, param schemas, sentence templates, and level/area/course/star
// enums; adding a trigger type in tracking/segments.py appears here with
// zero UI changes.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { fmtIgt } from "../format.js";
import { requestTarget } from "../target.js";
import { Icon } from "./icons.js";
import { IconPicker } from "./iconpicker.js";
import { PageState } from "./states.js";
import { buildTree } from "../group.js";
import { usePaneCap } from "../viewport.js";
import { GroupedList, useOpenGroups } from "./grouplist.js";
import { EntityPicker } from "./entitymodal.js";
import { courseOptions, levelOptions, parseStarId, segmentOptions, starId,
         starOptionsFromVocab } from "../entities.js";
import { entityIconSrc, optionIconSrc } from "./entityicons.js";
import { SegmentTimeline } from "./segmenttimeline.js";

const html = htm.bind(h);

// --- world-topology option filtering (vocab.connections + param `flow`) ----
// level_enter/level_exit params carry a `flow` annotation ({role, peer,
// peer_subarea} — tracking/segments.py): once the OTHER side of the move is
// picked, this side's dropdown only offers world-possible options
// (addresses.WORLD_EDGES_*). "dest" params filter by the source's
// SUCCESSORS, "source" params by the destination's PREDECESSORS. UI-only —
// a stored value the topology disagrees with stays selectable (ParamInput
// keeps the current value in the list) so legacy defs never blank out.

const nodeKey = ([level, area]) => (area == null ? String(level) : `${level}:${area}`);

function nodesFor(level, subarea, conn) {
  // A level with no subarea picked means "any of its subarea nodes" (only
  // Castle Inside has them — derived from the map itself, not hardcoded).
  if (subarea != null) return [`${level}:${subarea}`];
  const subKeys = Object.keys(conn).filter((k) => k.startsWith(`${level}:`));
  return subKeys.length ? subKeys : [String(level)];
}

export function allowedIds(schema, clause, conn) {
  // Set of permitted ids for a flow-annotated level/subarea param, or null
  // when unconstrained (no flow, no topology, peer side unpicked).
  const flow = schema.flow;
  if (!flow || !conn) return null;
  const peerLevel = clause[flow.peer];
  if (peerLevel == null) return null;
  const peerNodes = nodesFor(peerLevel, clause[flow.peer_subarea], conn);
  let pairs; // [level, area|null] nodes reachable on THIS param's side
  if (flow.role === "dest") {
    pairs = peerNodes.flatMap((k) => conn[k] || []);
  } else {
    const peerSet = new Set(peerNodes);
    pairs = Object.entries(conn)
      .filter(([, dests]) => dests.some((d) => peerSet.has(nodeKey(d))))
      .map(([k]) => (k.includes(":")
        ? k.split(":").map(Number) : [Number(k), null]));
  }
  if (schema.kind === "level")
    // a level_changed edge never stays inside one level, so the peer's own
    // level is excluded (no "Castle Inside -> Castle Inside")
    return new Set(pairs.map(([lvl]) => lvl).filter((lvl) => lvl !== peerLevel));
  // subarea param: its owning level is the only_when controller's value
  const ownLevel = clause[schema.only_when.param];
  return new Set(pairs.filter(([lvl, area]) => lvl === ownLevel && area != null)
    .map(([, area]) => area));
}

export function ParamInput({ schema, name, value, vocab, clause, onChange, t }) {
  // "" MUST become null, never Number("")===0 — 0 is a real area/level id,
  // so a bare Number() silently scoped cleared optional params to area 0.
  const numOrNull = (s) => (s === "" ? null : Number(s));
  const dropdown = (entries, anyLabel, pickLabel) => html`<select
      value=${value ?? ""} onchange=${(e) => onChange(numOrNull(e.target.value))}>
    <option value="">${schema.required ? pickLabel : anyLabel}</option>
    ${entries.map(([id, n]) => html`<option value=${id}>${n}</option>`)}
  </select>`;
  // world-topology filter (see allowedIds above); the CURRENT value always
  // stays listed so an out-of-topology stored def renders and saves intact.
  // This filter is THIS call site's rule — it is computed here and handed to
  // the picker as `allow`; EntityPicker never learns about world edges.
  const allowed = allowedIds(schema, clause, vocab.connections);
  const permitted = ([id]) => !allowed || allowed.has(Number(id))
    || Number(id) === value;
  const permittedId = (id) => permitted([id]);
  // Art comes from entityicons.js: the picker resolves no domain art of its
  // own, and a second context is how these cells came to disagree with the
  // banner (2026-07-26).
  if (schema.kind === "level") {
    // schema.enum restricts the choices (area_enter offers only the castle
    // hubs); absent enum = every level. Split into the castle regions the
    // library groups by (user request 2026-07-25 — a level picker should
    // read like the library reads, and stay categorised even when the
    // topology filter leaves two options).
    const groups = levelOptions(vocab).map((group) => ({
      ...group,
      options: group.options.filter((option) =>
        !schema.enum || schema.enum.includes(Number(option.id))),
    })).filter((group) => group.options.length > 0);
    return html`<${EntityPicker} groups=${groups} allow=${permittedId}
      value=${value == null ? null : String(value)}
      title="Choose a level"
      placeholder=${schema.required ? "— pick level —" : "(any level)"}
      iconFor=${(id) => optionIconSrc(t, "level", id)}
      onChange=${(id) => onChange(id == null ? null : Number(id))} />`;
  }
  if (schema.kind === "subarea")
    // Castle interior areas (lobby/upstairs/basement). Always optional — the
    // empty option is the explicit "Any" (matches any interior area). Shown
    // only when the companion level is Castle Inside (ClauseRow only_when).
    // A 3-item list has nothing to group, so this stays a plain dropdown.
    return dropdown(Object.entries(vocab.castle_areas).filter(permitted),
                    "Any", "— pick subarea —");
  if (schema.kind === "moment")
    // The mid-course vocabulary (doors, textboxes), served by the server's
    // own registry so a new moment reaches this dropdown with no JS edit.
    // Without this branch the param fell through to a bare text input and
    // rendered as an empty box labelled "kind" — the definition matched
    // perfectly server-side while the editor could not show what it was set
    // to (live report 2026-08-05, with a screenshot of exactly that).
    return dropdown((vocab.moments || []).map((m) => [m.key, m.label]),
                    "Any moment", "— pick a moment —");
  if (schema.kind === "landmark") {
    // A RECORDED clause pins THE specific thing (this door, this pole) by
    // its catalogue key -- the recorder writes it, and there is no
    // hand-authoring picker for one. The editor's whole offer is letting the
    // pin go; unset it renders nothing, because typing a key by hand is not
    // a thing (the fallback below is a NUMBER input, which would show a
    // string key as an empty box -- the exact trap the moment branch names).
    if (value == null) return null;
    return html`<button type="button" class="quiet-button"
        title=${`Pinned to one specific thing (${value}). Click to match any of its kind here.`}
        onclick=${() => onChange(null)}>✕ this specific one</button>`;
  }
  if (schema.kind === "course")
    // Grouped the same way, so a course picker and a level picker read alike.
    return html`<${EntityPicker} groups=${courseOptions(vocab)}
      value=${value == null ? null : String(value)}
      title="Choose a course"
      placeholder=${schema.required ? "— pick course —" : "(any course)"}
      iconFor=${(id) => optionIconSrc(t, "course", id)}
      onChange=${(id) => onChange(id == null ? null : Number(id))} />`;
  if (schema.kind === "star") {
    // Dependent on the sibling course param: with no course picked, any star
    // matches, so the control is disabled rather than lying about a choice.
    // The shared star groups carry composite ids, so this branch narrows them
    // to the picked course and unpacks the star index on the way out — this
    // control edits ONE param, so it must not set the course too.
    const groups = starOptionsFromVocab(vocab)
      .filter((group) => group.key === `course-${clause.course}`);
    return html`<${EntityPicker} groups=${groups}
      disabled=${clause.course == null}
      value=${value == null ? null : starId(clause.course, value)}
      title="Choose a star"
      placeholder=${schema.required ? "— pick star —" : "(any star)"}
      iconFor=${(id) => optionIconSrc(t, "star", id)}
      onChange=${(id) => onChange(id == null ? null : parseStarId(id).star)} />`;
  }
  if (schema.kind === "seconds") {
    // Stored as FRAMES (30 fps int — the project's primary clock); edited as
    // decimal seconds. "" stays null so a cleared input doesn't become 0
    // (0 is meaningful: "no minimum").
    // oninput, NOT onchange: the app re-renders on ~1s poll ticks, and a
    // controlled input's uncommitted keystrokes are wiped by every re-render
    // (onchange only commits on blur/Enter — live smoke test 2026-07-23 lost
    // typed values ~1s in). Any typed input in this UI must commit per
    // keystroke or hold local state; <select> commits instantly, so onchange
    // stays fine there.
    return html`<input type="number" min="0" step="0.1" style="width:5rem"
        value=${value == null ? "" : value / 30}
        placeholder="seconds"
        oninput=${(e) => onChange(e.target.value === ""
          ? null : Math.round(Number(e.target.value) * 30))} />`;
  }
  // oninput for the same reason as the seconds branch above (poll re-render
  // wipes uncommitted onchange values — this bit star_count guards too).
  return html`<input type="number" style="width:5rem" value=${value ?? ""}
      placeholder=${name}
      oninput=${(e) => onChange(numOrNull(e.target.value))} />`;
}

export function ClauseRow({ clause, types, vocab, tint, onChange, onRemove, t }) {
  const spec = types.find((t) => t.key === clause.type) || types[0];
  // A param with only_when shows only while its controlling param equals the
  // gate value (subarea selectors appear only for Castle Inside).
  const visible = (pname) => {
    const ow = spec.params[pname]?.only_when;
    return !ow || clause[ow.param] === ow.equals;
  };
  const setParam = (pname, v) => {
    const next = { ...clause, [pname]: v };
    // a star id is meaningless outside its course — clear it on course change
    if (pname === "course" && "star" in spec.params) next.star = null;
    // Consistency sweep, two passes (clearing a level param can invalidate
    // its subarea): (a) a hidden param holds nothing — a stale "Basement"
    // can't cling to "Castle Grounds"; (b) a sibling value the world
    // topology now rules out is cleared (picking "to LLL" drops a "from WF"
    // that can no longer reach it). The just-typed value itself is exempt
    // from (b) — the user's pick wins and the siblings adjust around it.
    for (let pass = 0; pass < 2; pass++)
      for (const [p, meta] of Object.entries(spec.params)) {
        if (meta.only_when
            && next[meta.only_when.param] !== meta.only_when.equals)
          next[p] = null;
        if (p !== pname && next[p] != null) {
          const allowed = allowedIds(meta, next, vocab.connections);
          if (allowed && !allowed.has(next[p])) next[p] = null;
        }
      }
    onChange(next);
  };
  const param = (pname) => html`<${ParamInput} schema=${spec.params[pname]}
      name=${pname} vocab=${vocab} clause=${clause} value=${clause[pname]}
      t=${t} onChange=${(v) => setParam(pname, v)} />`;
  // "{to} coming from {from}" → inputs interleaved with muted words.
  // Params a template forgets to mention render appended — the registry
  // test makes that unreachable; this keeps a bad vocab usable, not blank.
  // Hidden (only_when unmet) params render nothing but stay "mentioned" so
  // they don't reappear in the extras tail.
  const mentioned = new Set();
  const toks = (spec.template || "").split(/(\{\w+\})/);
  const rendered = toks.map((tok, i) => {
    const m = /^\{(\w+)\}$/.exec(tok);
    if (m && spec.params[m[1]]) {
      mentioned.add(m[1]);
      return visible(m[1]) ? param(m[1]) : null;
    }
    const word = tok.trim();
    if (!word) return null;
    // words introducing a hidden param hide with it — "coming from" must
    // not dangle when the castle-only 'from' selector is hidden
    const next = /^\{(\w+)\}$/.exec(toks[i + 1] || "");
    if (next && spec.params[next[1]] && !visible(next[1])) return null;
    return html`<span class="segword">${word}</span>`;
  });
  const extras = Object.keys(spec.params).filter(
    (p) => !mentioned.has(p) && visible(p));
  return html`<div class="segclause tint${tint ?? 0}">
    <select value=${clause.type}
        onchange=${(e) => onChange({ type: e.target.value })}>
      ${types.map((t) => html`<option value=${t.key}>${t.label}</option>`)}
    </select>
    ${rendered}
    ${extras.map(param)}
    ${onRemove ? html`<button class="icon-button clause-remove" title="Remove condition"
        aria-label="Remove condition" onclick=${onRemove}>
      <${Icon} name="close" size=${16} />
    </button>` : null}
  </div>`;
}

// The fields the Builder's save sends — must stay a SUBSET of the server's
// SegmentBody/SegmentPatch fields (cross-checked by tests/
// test_segments_editor_ui.py against the pydantic model). match_mode joined
// this list 2026-07-29 (final review of spec 2026-07-28-multi-step-segments,
// finding 4): the registry, the vocab payload and the write path all existed
// with no control reading any of it, so every Builder-created segment
// silently became "loose" (SegmentBody's own default) with no way to see or
// change it from the editor.
// `waypoints` joined this list on 2026-08-03, when the editor grew a Then
// section. Until then the field was deliberately absent and the omission was
// load-bearing: `SegmentPatch.waypoints` defaults to None = untouched, so a
// save from an editor that could not author them left a seeded movement's own
// steps intact. Now that they are editable that protection is gone by design,
// which is exactly why the editor must always send the FULL list — sending a
// partial one would clear the rest.
const SAVE_FIELDS = ["name", "enabled", "start_triggers", "end_triggers",
                     "guards", "match_mode", "clock_start", "waypoints"];

// The full definition shape POST /api/segments/backtest validates against
// (server/api.py's SegmentBody) -- a SUPERSET of SAVE_FIELDS, deliberately:
// an existing segment's waypoints already sit in `d` (spread from the GET row
// this editor opened with) even though this editor has no control for
// authoring them yet, and leaving them out of the preview would silently
// backtest against the wrong matcher branch for any seeded movement that
// carries one -- not a hypothetical, the 56 castle movements are exactly
// that shape. A brand-new segment has neither key on `d` yet; `JSON.
// stringify` drops an undefined-valued key on its own, so the server's own
// defaults (no waypoints, no category) apply instead of a wrong client-side
// guess. match_mode no longer needs the same treatment -- it is a SAVE_FIELDS
// member now (the editor control below), so it is always present on `d`
// under its own value, never a guess.
const BACKTEST_FIELDS = [...SAVE_FIELDS, "category"];

// One-line verdict for the backtest panel: distinguishes "never even armed"
// from "armed and never closed" (the diagnostic this feature exists for --
// a definition that looks right and never fires) from "fired, here's how
// often" -- three different remedies, so a single fires-count would blur
// the two zero-fire cases together.
//
// The arm count is report.arms, NEVER attempts.length -- attempts are only
// written when an arm CLOSES (tracking/backtest.py, BacktestReport.arms'
// docstring), so a def that arms and is silently disarmed every time writes
// no attempt row at all. attempts.length then reads 0 even after dozens of
// arms, which used to fall all the way through to the false "Never armed
// anywhere in your history" below for a def that plainly did arm.
function backtestSummary(report) {
  const n = report.attempts.length;
  if (report.fires > 0)
    return `${report.fires} fire${report.fires === 1 ? "" : "s"} out of `
      + `${n} attempt${n === 1 ? "" : "s"} in your history.`;
  if (report.unclosed.length > 0)
    return "Never fired — but it DID arm, and never closed. See below.";
  if (report.arms > 0)
    // "no completion is recorded", not "never completed successfully": like
    // segmenttimeline.js's recordingSummary in its re-record intent, this
    // Builder backtests a real `replaces` when editing an existing segment
    // -- so arms>0/fires=0 here
    // can ALSO mean "it fired, and those attempts were wiped"
    // (tracking/backtest.py replays journaled data_wiped clears against
    // `current`), not only "it never completed". Both readings make this
    // sentence true; only one of them makes "never completed successfully" a
    // lie.
    return `Armed ${report.arms} time${report.arms === 1 ? "" : "s"} in your `
      + "history, but no completion is recorded.";
  return "Never armed anywhere in your history.";
}

function Builder({ vocab, initial, onSaved, onCancel, apiRef, t, load, allDefs,
                   onRerecord }) {
  // match_mode's own default mirrors the server's (SegmentBody.match_mode =
  // "loose") rather than naming "loose" a second time -- vocab.match_modes
  // is ordered loose-first specifically so a caller that wants "the default"
  // can read it positionally (tracking/segments.py::vocab's own comment).
  const blank = { name: "", enabled: true,
    start_triggers: [{ type: "level_enter" }],
    end_triggers: [{ type: "level_enter" }], guards: [],
    match_mode: (vocab.match_modes && vocab.match_modes[0]
                 && vocab.match_modes[0].key) || "loose",
    // "move" leads vocab.clock_starts because he ruled it the default for
    // NEW definitions (round 15 item 3) -- read positionally like
    // match_modes above, never a JS literal a registry change would strand.
    clock_start: (vocab.clock_starts && vocab.clock_starts[0]
                  && vocab.clock_starts[0].key) || "trigger" };
  const [d, setD] = useState(initial || blank);
  const [resetting, setResetting] = useState(false);
  const [resetErr, setResetErr] = useState(null);

  // Editing a SHIPPED movement opts it out of every future corpus refresh --
  // `seed_dirty` blocks reconcile's update branch unconditionally, which is
  // how six rows sat frozen against their own seed until migration v17 went
  // looking for them. The cost was invisible from in here, and the undo was
  // WORSE than invisible: `POST /api/segments/{id}/reset` has existed the
  // whole time and no UI ever called it, so "put it back" was a capability
  // that, by his own rule, did not exist. Both live at the point of the edit
  // now, not in a library menu nobody opens before typing.
  async function resetToDefault() {
    setResetting(true); setResetErr(null);
    try {
      await send("POST", `/api/segments/${initial.id}/reset`, {});
      const rows = await load();
      const fresh = rows.find((row) => row.id === initial.id);
      if (fresh) { setD(fresh); onSaved(initial.id); }
    } catch (err) { setResetErr(String(err)); }
    finally { setResetting(false); }
  }
  // Icon override (existing segments only — the override is keyed by id, so
  // a not-yet-saved segment has nowhere to hang one). Read live from the
  // session view so a pick from the banner's ✎ shows here too.
  const [pickingIcon, setPickingIcon] = useState(false);
  const iconOverride = initial && initial.id != null
    ? ((((t || {}).view || {}).icon_overrides || {})[`segment:${initial.id}`]
       || null)
    : null;
  // Origin override (existing segments only — keyed by id, like the icon).
  // The library files a segment by where its rules say it starts; when that
  // reads wrong, this pins it. "Auto" always NAMES the detected place, so a
  // misclassification is visible to the person who has to fix it.
  const detected = (initial && initial.origin) || null;
  const [origin, setOrigin] = useState(
    detected && detected.source === "override" ? detected.key : "");
  const [err, setErr] = useState(null);
  // Backtest preview state -- reset for free on every open/switch because
  // Builder remounts (Segments keys it by editing.id/"new"), so a stale
  // report from a different segment can never bleed through.
  const [btBusy, setBtBusy] = useState(false);
  const [btReport, setBtReport] = useState(null);
  const [btErr, setBtErr] = useState(null);
  // Author-time lint (Task 16, spec 2026-07-28-multi-step-segments) --
  // re-checked automatically on every edit (debounced, unlike the backtest
  // panel above which is button-triggered) so findings sit beside Save
  // without a click. `segment_id` is `initial`'s own id, if any, so the
  // duplicate rule excludes THIS row from its own comparison (server/api.py's
  // LintBody docstring) -- without it, opening an existing segment and
  // changing nothing would report it as a duplicate of itself.
  const [lintFindings, setLintFindings] = useState([]);
  const [lintErr, setLintErr] = useState(null);
  useEffect(() => {
    let cancelled = false;
    const timer = setTimeout(() => {
      const definition = Object.fromEntries(
        BACKTEST_FIELDS.map((field) => [field, d[field]]));
      send("POST", "/api/segments/lint", {
        definition, segment_id: initial && initial.id != null ? initial.id : null,
      }).then((result) => {
        if (!cancelled) { setLintFindings(result.warnings); setLintErr(null); }
      }).catch((e) => { if (!cancelled) setLintErr(String(e)); });
    }, 400);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [d]);
  const lintHasError = lintFindings.some((f) => f.severity === "error");
  const edit = (k, i, clause) => setD({ ...d,
    [k]: d[k].map((c, j) => (j === i ? clause : c)) });
  const add = (k, types) => setD({ ...d, [k]: [...d[k], { type: types[0].key }] });
  const drop = (k, i) => setD({ ...d, [k]: d[k].filter((_, j) => j !== i) });

  // `waypoints` is an ORDERED list of any-of clause SETS, so it needs its own
  // three mutators rather than the flat ones above -- the outer index is the
  // STEP (order is the whole meaning under the strict path matcher) and the
  // inner one is an alternative within that step. Every alternative is
  // preserved on every edit: the shipped corpus holds exactly one clause per
  // step today, and an editor that quietly kept only the first would delete
  // information the moment one does not.
  const steps = () => d.waypoints || [];
  const setSteps = (next) => setD({ ...d, waypoints: next });
  const editStep = (stepIndex, clauseIndex, clause) => setSteps(
    steps().map((set, i) => (i !== stepIndex ? set
      : set.map((c, j) => (j === clauseIndex ? clause : c)))));
  const addStepClause = (stepIndex, types) => setSteps(
    steps().map((set, i) => (i === stepIndex
      ? [...set, { type: types[0].key }] : set)));
  const dropStepClause = (stepIndex, clauseIndex) => setSteps(
    steps().map((set, i) => (i !== stepIndex ? set
      : set.filter((_, j) => j !== clauseIndex)))
      .filter((set) => set.length > 0));
  const addStep = (types) => setSteps([...steps(), [{ type: types[0].key }]]);
  const dropStep = (stepIndex) => setSteps(
    steps().filter((_, i) => i !== stepIndex));
  const moveStep = (stepIndex, delta) => {
    const next = [...steps()];
    const target = stepIndex + delta;
    if (target < 0 || target >= next.length) return;
    [next[stepIndex], next[target]] = [next[target], next[stepIndex]];
    setSteps(next);
  };

  async function save() {
    try {
      setErr(null);
      // ALLOWLIST of what this editor edits — never spread the GET row back:
      // rows are raw db rows and grow server-only columns over time
      // (id, created_utc, then seed_key/seed_dirty in migration v11), which
      // the strict SegmentPatch (extra="forbid") rejects — a denylist here
      // 422'd every save of a seeded segment (regression 2026-07-24, pinned
      // by tests/test_segments_editor_ui.py). waypoints/category are
      // deliberately absent: this editor doesn't author them, and an
      // omitted PATCH field stays untouched server-side.
      const body = Object.fromEntries(
        SAVE_FIELDS.map((field) => [field, d[field]]));
      // Report WHICH segment was saved, so the parent can keep it open rather
      // than dropping the user back to "Choose a segment to edit" (live audit
      // 2026-07-25). A create only learns its id from the response.
      let savedId;
      if (initial && initial.id != null) {
        await send("PUT", `/api/segments/${initial.id}`, body);
        savedId = initial.id;
      } else {
        savedId = (await send("POST", "/api/segments", body)).id;
      }
      onSaved(savedId);
      return true;
    } catch (e) { setErr(String(e)); return false; }
  }

  // "Try it against my history" -- the whole point is finding out BEFORE
  // Save, so this sends whatever is CURRENTLY in the form, unsaved
  // (tracking/backtest.py). `replaces` is the segment being edited, if any,
  // so the response can diff the candidate against its own real history.
  async function runBacktest() {
    setBtBusy(true); setBtErr(null);
    try {
      const definition = Object.fromEntries(
        BACKTEST_FIELDS.map((field) => [field, d[field]]));
      const report = await send("POST", "/api/segments/backtest", {
        definition, replaces: initial && initial.id != null ? initial.id : null,
      });
      setBtReport(report);
    } catch (e) { setBtErr(String(e)); setBtReport(null); }
    finally { setBtBusy(false); }
  }

  async function saveOrigin(nextKey) {
    setOrigin(nextKey);
    try {
      await send("POST", `/api/segments/${initial.id}/origin`,
                 { origin: nextKey || null });
      // Like toggle/remove below: the library's grouping reads `defs`, which
      // only `load()` refreshes — `t.refresh()` alone updates the session
      // view (no `origin` field), so the row silently stayed in its old
      // group until the tab was re-entered (review I2).
      load(); t.refresh();
    } catch (e) { setErr(String(e)); }
  }

  // --- Split into two segments (Task 18, spec 2026-07-28-multi-step-
  // segments) -- offered only for a SAVED segment carrying exactly one
  // waypoint. That is the one shape split_definition can act on without
  // asking the author to invent a boundary from nothing: 0 waypoints has no
  // natural split point, and 2+ is refused server-side anyway (folding
  // several into one shared `mid` would silently drop the rest --
  // tracking/segments.py's own docstring). `mid` is the segment's own
  // waypoint, verbatim -- there is nothing else for the user to author here.
  const [splitNames, setSplitNames] = useState(["", ""]);
  const [splitBusy, setSplitBusy] = useState(false);
  const [splitErr, setSplitErr] = useState(null);

  async function doSplit() {
    setSplitBusy(true); setSplitErr(null);
    try {
      const result = await send(
        "POST", `/api/segments/${initial.id}/split`, {
          mid: initial.waypoints[0],
          first_name: splitNames[0].trim() || `${initial.name} (1 of 2)`,
          second_name: splitNames[1].trim() || `${initial.name} (2 of 2)`,
        });
      // Land on the first new half, the SAME "stay on what you just did"
      // rule save()'s own onSaved(savedId) follows (live audit 2026-07-25).
      onSaved(result.first_id);
    } catch (e) { setSplitErr(String(e)); }
    finally { setSplitBusy(false); }
  }

  // --- Merge with another segment (Task 18) -- the inverse gesture: chain
  // this segment with any OTHER saved one, in either order, into one new
  // definition. `mergeGroups` reuses the exact same segment picker the
  // Routes tab's step editor already offers (entities.js::segmentOptions +
  // EntityPicker) rather than a second hand-rolled select -- same grouping,
  // same art, one less pattern in the codebase. Domain refusals (the pair
  // doesn't meet) surface as the server's own 409 message; this control does
  // not try to predict that client-side.
  const [mergeWithId, setMergeWithId] = useState(null);
  const [mergeOrder, setMergeOrder] = useState("after");   // this seg first, or second
  const [mergeName, setMergeName] = useState("");
  const [mergeBusy, setMergeBusy] = useState(false);
  const [mergeErr, setMergeErr] = useState(null);
  const mergeCandidates = initial && initial.id != null
    ? (allDefs || []).filter((def) => def.id !== initial.id) : [];
  const mergeGroups = segmentOptions(mergeCandidates, vocab.origins || []);

  async function doMerge() {
    if (mergeWithId == null) return;
    setMergeBusy(true); setMergeErr(null);
    try {
      const otherId = Number(mergeWithId);
      const other = mergeCandidates.find((def) => def.id === otherId);
      const otherName = other ? other.name : "?";
      const [firstId, secondId, defaultName] = mergeOrder === "after"
        ? [initial.id, otherId, `${initial.name} + ${otherName}`]
        : [otherId, initial.id, `${otherName} + ${initial.name}`];
      const result = await send("POST", "/api/segments/merge", {
        first_id: firstId, second_id: secondId,
        name: mergeName.trim() || defaultName,
      });
      onSaved(result.id);
    } catch (e) { setMergeErr(String(e)); }
    finally { setMergeBusy(false); }
  }

  // Expose a save handle + live dirty flag so the parent can offer "save your
  // changes?" when the user clicks edit on a DIFFERENT segment (Segments
  // tryEdit). dirty = the form differs from what we opened with (reverting an
  // edit back clears it). Reassigned each render so the parent reads current
  // state at click time.
  if (apiRef) apiRef.current = {
    save, dirty: JSON.stringify(d) !== JSON.stringify(initial || blank),
  };

  // Match-mode control (Task 27, spec 2026-07-28-multi-step-segments,
  // final-review finding 4): vocab.match_modes is the ONLY source for the
  // label/description text, never a hardcoded copy here (this repo fails
  // builds over a second door -- tests/test_single_source.py). `blank`
  // above already seeds a real value, so `d.match_mode` is always set.
  // `matchModeInfo` may come back undefined for a value vocab no longer
  // ships -- the CURRENT value stays selectable via the appended option
  // below rather than rendering blank (a filtered <select> silently losing
  // its stored value has bitten this app before).
  const matchModes = vocab.match_modes || [];
  const matchModeInfo = matchModes.find((mode) => mode.key === d.match_mode);
  const clockStarts = vocab.clock_starts || [];
  const clockStartInfo = clockStarts.find(
    (mode) => mode.key === d.clock_start);

  // One bordered group per side; each alternative clause inside gets its
  // own tinted card (cycling) so "new color = new alternative" reads at a
  // glance even when a wrapped row spans two lines.
  const section = (label, hint, icon, k, types, cls) => html`<section class="segsection ${cls}">
    <div class="seghead">
      <span class="seghead-icon"><${Icon} name=${icon} size=${17} /></span>
      <span><b>${label}</b><small>${hint}</small></span>
    </div>
    ${d[k].map((c, i) => html`<${ClauseRow} clause=${c} types=${types}
        tint=${i % 4} vocab=${vocab} t=${t} onChange=${(cl) => edit(k, i, cl)}
        onRemove=${() => drop(k, i)} />`)}
    <button class="quiet-button segment-add-condition" onclick=${() => add(k, types)}>
      <${Icon} name="plus" size=${15} /> Add another condition
    </button>
  </section>`;

  // THEN — the ordered stops between Start and Finish. The builder had no way
  // to author one until 2026-08-03, which is why `WF → SSL` could only exist
  // in the shipped corpus and not be made by a user (his own bar, from the
  // spec: "It needs to be easy for me to have, theoretically, made the WF→SSL
  // segment on my own as a user"). Its shape is deliberately NOT `section`'s:
  // Start and Finish are each ONE any-of set where order means nothing, and
  // here order is the entire content — under the strict path matcher a step
  // out of sequence voids the run — so every step wears its own number and its
  // own move controls, and the "any of these" nesting is one level in.
  const thenSection = () => html`<section class="segsection seg-then">
    <div class="seghead">
      <span class="seghead-icon"><${Icon} name="split" size=${17} /></span>
      <span><b>Then</b><small>Stops the run must pass, in this order. Going
        anywhere else voids it.</small></span>
    </div>
    ${steps().length === 0 && html`<p class="meta">No stops — any route from
      Start to Finish counts. Easier still: close this and use
      <b>Record what I just did</b>, which reads the stops off the run you
      already played.</p>`}
    ${steps().map((set, stepIndex) => html`<div class="then-step" key=${stepIndex}>
      <div class="then-step-head">
        <span class="then-step-n">${stepIndex + 1}</span>
        <div class="then-step-moves">
          <button class="icon-button" title="Move this stop earlier"
              aria-label="Move this stop earlier" disabled=${stepIndex === 0}
              onclick=${() => moveStep(stepIndex, -1)}>
            <${Icon} name="stepBack" size=${14} /></button>
          <button class="icon-button" title="Move this stop later"
              aria-label="Move this stop later"
              disabled=${stepIndex === steps().length - 1}
              onclick=${() => moveStep(stepIndex, 1)}>
            <${Icon} name="stepForward" size=${14} /></button>
          <button class="icon-button" title="Remove this stop"
              aria-label="Remove this stop" onclick=${() => dropStep(stepIndex)}>
            <${Icon} name="close" size=${14} /></button>
        </div>
      </div>
      ${set.map((clause, clauseIndex) => html`<${ClauseRow} key=${clauseIndex}
          clause=${clause} types=${vocab.triggers} tint=${clauseIndex % 4}
          vocab=${vocab} t=${t}
          onChange=${(cl) => editStep(stepIndex, clauseIndex, cl)}
          onRemove=${() => dropStepClause(stepIndex, clauseIndex)} />`)}
      <button class="quiet-button segment-add-condition"
          onclick=${() => addStepClause(stepIndex, vocab.triggers)}>
        <${Icon} name="plus" size=${15} /> Add another way to reach this stop
      </button>
    </div>`)}
    <button class="quiet-button segment-add-condition"
        onclick=${() => addStep(vocab.triggers)}>
      <${Icon} name="plus" size=${15} /> Add a stop
    </button>
  </section>`;

  return html`<div class="segbuilder">
    <div class="builder-heading">
      <div>
        <span class="eyebrow">${initial ? "Edit segment" : "New segment"}</span>
        <h2>${initial ? initial.name : "Create a practice segment"}</h2>
      </div>
      <button class="icon-button" title="Close editor" aria-label="Close editor"
          onclick=${onCancel}><${Icon} name="close" /></button>
    </div>
    <label class="builder-name">
      <span class="field-label">Segment name</span>
      <input placeholder="e.g. Lobby to BitDW" value=${d.name}
          oninput=${(e) => setD({ ...d, name: e.target.value })} />
    </label>
    ${/* The re-record door (round 16): "OH BOY this segment was actually
         recorded wrong" is noticed FROM the segment, so the editor is where
         the door lives. It opens the recorder with this row as its
         `replaces` intent -- picks empty, name his, save replaces in place.
         Existing rows only: a not-yet-saved definition has nothing to
         replace. */""}
    ${initial && initial.id != null && onRerecord && html`<div
        class="builder-rerecord">
      <span class="field-label">Recorded wrong?</span>
      <button type="button" onclick=${() => onRerecord(initial)}>
        <${Icon} name="bookmark" size=${15} />${" "}Re-record this movement
      </button>
      <span class="meta">play it again and point at what you did — routes,
        PBs and history stay attached</span>
    </div>`}
    ${initial && initial.seed_key && html`<div
        class="builder-seeded ${initial.seed_dirty ? "is-dirty" : ""}">
      <span class="field-label"><${Icon} name="shield" size=${15} />${" "}
        ${initial.seed_dirty ? "Edited copy of a shipped movement"
          : "Shipped with the app"}</span>
      <p class="meta">${initial.seed_dirty
        ? "This one is yours now — it stops updating when a new version of the app ships a better version of this movement."
        : "Saving an edit here stops this movement updating when a new version of the app ships a better version of it. Reset puts it back."}</p>
      ${resetErr && html`<div class="badx">${resetErr}</div>`}
      <button onclick=${resetToDefault} disabled=${resetting}>
        <${Icon} name="restart" size=${15} />${" "}${resetting
          ? "Resetting…" : "Reset to the shipped version"}
      </button>
    </div>`}
    ${initial && initial.id != null && html`<div class="builder-icon">
      <span class="field-label">Icon</span>
      <img class="builder-icon-preview" alt="" draggable="false"
           src=${entityIconSrc(t || {}, `segment:${initial.id}`)} />
      <button type="button" onclick=${() => setPickingIcon(true)}>
        Choose icon…</button>
      <span class="meta">${iconOverride || "default"} · shown on the course
        quick-select</span>
    </div>`}
    ${initial && initial.id != null && html`<label class="builder-origin">
      <span class="field-label">Library category</span>
      <select value=${origin} onchange=${(e) => saveOrigin(e.target.value)}>
        <option value="">Auto (${detected ? detected.label : "Anywhere"})</option>
        ${(vocab.origins || []).filter((region) => region.key !== null)
          .map((region) => html`<optgroup key=${region.key} label=${region.label}>
            ${region.children.map((place) => html`<option key=${place.key}
              value=${place.key}>${place.label}</option>`)}
          </optgroup>`)}
      </select>
      <span class="meta">where the library files this segment</span>
    </label>`}
    ${pickingIcon && html`<${IconPicker}
        identity=${{ kind: "segment", segment_id: initial.id }}
        current=${iconOverride}
        onDone=${() => { setPickingIcon(false); if (t) t.refresh(); }} />`}
    <label class="builder-enabled">
      <input type="checkbox" checked=${d.enabled}
          onchange=${(e) => setD({ ...d, enabled: e.target.checked })} />
      <span><b>Available for practice</b><small>Show this segment in target pickers.</small></span>
    </label>
    <label class="builder-matchmode">
      <span class="field-label">Matching</span>
      <select value=${d.match_mode}
          onchange=${(e) => setD({ ...d, match_mode: e.target.value })}>
        ${matchModes.map((mode) => html`<option value=${mode.key}>${mode.label}</option>`)}
        ${!matchModeInfo && d.match_mode
          ? html`<option value=${d.match_mode}>${d.match_mode}</option>` : null}
      </select>
      <span class="meta">${matchModeInfo ? matchModeInfo.description : ""}</span>
    </label>
    <label class="builder-clockstart">
      <span class="field-label">Clock starts</span>
      <select value=${d.clock_start || "trigger"}
          onchange=${(e) => setD({ ...d, clock_start: e.target.value })}>
        ${clockStarts.map((mode) => html`<option value=${mode.key}>${mode.label}</option>`)}
        ${!clockStartInfo && d.clock_start
          ? html`<option value=${d.clock_start}>${d.clock_start}</option>` : null}
      </select>
      <span class="meta">${clockStartInfo ? clockStartInfo.description : ""}</span>
    </label>
    <div class="segment-definition-grid">
      ${section("Start", "Arm when any one of these happens.", "play",
        "start_triggers", vocab.triggers, "seg-start")}
      ${thenSection()}
      ${section("Finish", "Complete when any one of these happens.", "target",
        "end_triggers", vocab.triggers, "seg-end")}
      ${section("Rules", "Optional checks that keep attempts valid.", "shield",
        "guards", vocab.guards, "seg-guard")}
    </div>
    ${initial && initial.id != null && (initial.waypoints || []).length === 1 && html`
      <div class="builder-split">
        <span class="field-label"><${Icon} name="split" size=${15} /> Split into two segments</span>
        <p class="meta">This segment already tracks the stop where it splits.
          Splitting saves two new, independent segments — this one stays
          exactly as it is.</p>
        <div class="split-names">
          <input placeholder="First half name" value=${splitNames[0]}
              oninput=${(e) => setSplitNames([e.target.value, splitNames[1]])} />
          <input placeholder="Second half name" value=${splitNames[1]}
              oninput=${(e) => setSplitNames([splitNames[0], e.target.value])} />
        </div>
        ${splitErr && html`<div class="badx">${splitErr}</div>`}
        <button onclick=${doSplit} disabled=${splitBusy}>
          <${Icon} name="split" size=${15} />${" "}${splitBusy
            ? "Splitting…" : "Split into two segments"}
        </button>
      </div>`}
    ${initial && initial.id != null && html`
      <div class="builder-merge">
        <span class="field-label"><${Icon} name="merge" size=${15} /> Merge with another segment</span>
        <p class="meta">Chain this segment with another one that starts where
          it ends (or ends where it starts) — saved as one new segment; both
          originals are kept, untouched.</p>
        ${mergeCandidates.length === 0
          ? html`<p class="meta">No other segments to merge with yet.</p>`
          : html`<div class="merge-body">
              <div class="merge-controls">
                <select value=${mergeOrder}
                    onchange=${(e) => setMergeOrder(e.target.value)}>
                  <option value="after">This segment, then…</option>
                  <option value="before">…then this segment</option>
                </select>
                <${EntityPicker} groups=${mergeGroups} value=${mergeWithId}
                    depth=${2} title="Choose a segment"
                    placeholder="— pick a segment —"
                    iconFor=${(id) => optionIconSrc(t, "segment", id)}
                    onChange=${(id) => setMergeWithId(id)} />
              </div>
              <input placeholder="Merged segment name (optional)"
                  value=${mergeName} oninput=${(e) => setMergeName(e.target.value)} />
              ${mergeErr && html`<div class="badx">${mergeErr}</div>`}
              <button onclick=${doMerge} disabled=${mergeBusy || mergeWithId == null}>
                <${Icon} name="merge" size=${15} />${" "}${mergeBusy
                  ? "Merging…" : "Merge into one segment"}
              </button>
            </div>`}
      </div>`}
    ${lintErr && html`<div class="badx">${lintErr}</div>`}
    ${lintFindings.length > 0 && html`<div class="lint-panel">
      ${lintFindings.map((finding, i) => html`<div key=${i}
          class="lint-finding lint-${finding.severity}">
        <${Icon} name=${finding.severity === "error" ? "close" : "shield"} size=${14} />
        ${" "}${finding.message}
      </div>`)}
    </div>`}
    ${err && html`<div class="badx">${err}</div>`}
    <div class="builder-actions">
      <span class="meta">Saving automatically recalculates this segment's history.</span>
      <button onclick=${runBacktest} disabled=${btBusy}>
        <${Icon} name="play" size=${16} />${" "}${btBusy
          ? "Testing…" : "Try it against my history"}
      </button>
      <button onclick=${onCancel}>Cancel</button>
      <button class="primary-button" onclick=${save} disabled=${lintHasError}
          title=${lintHasError ? "Fix the error above before saving" : ""}>
        <${Icon} name="save" size=${16} /> Save segment
      </button>
    </div>
    ${btErr && html`<div class="badx backtest-panel">${btErr}</div>`}
    ${btReport && html`<div class="backtest-panel">
      <div class="backtest-summary">
        ${backtestSummary(btReport)}
        ${btReport.fires > 0 && btReport.pb_after != null
          ? html` <span class="meta">Fastest: ${fmtIgt(btReport.pb_after)}</span>` : ""}
        ${initial && initial.id != null ? html` <span class="meta">
          vs. current definition: +${btReport.gained} / -${btReport.lost} attempts</span>` : ""}
      </div>
      ${btReport.unclosed.length > 0 && html`<div class="backtest-unclosed badx">
        ${btReport.unclosed.map((u, i) => html`<div key=${i}>
          ⚠ Armed at frame ${u.frame} (step ${u.progress}/${u.total}), ${u.reason}.
        </div>`)}
      </div>`}
      ${btReport.attempts.length > 0 && html`<div class="backtest-attempts">
        ${btReport.attempts.map((a, i) => html`<div key=${i}
            class="meta ${a.cleared ? "cleared" : ""}">
          ${a.outcome === "success" ? "✔" : "✘"}${" "}${a.outcome.replace(/_/g, " ")}
          ${a.outcome === "success" && a.rta_frames != null
            ? html` <b>${fmtIgt(a.rta_frames)}</b>` : ""}
          ${a.cleared && a.cleared_reason ? html` (${a.cleared_reason})` : ""}
        </div>`)}
      </div>`}
    </div>`}
  </div>`;
}

// Grouping POLICY for the segment library: castle region -> place, both read
// off the server's `origin` stamp (GET /api/segments). The JS never derives
// region membership — the taxonomy has ONE home, tracking/segments.py, and
// the editor's override picker reads the same list from vocab.
//
// Order is carried by the server too: vocab().origins is already in gameflow
// order with each region's Bowser and secret stages pinned above its main
// courses, so the level `order` functions just look up that position.
const originOf = (segment) => (segment.origin || {});

function originLevels(taxonomy) {
  const regionOrder = new Map();
  const placeOrder = new Map();
  taxonomy.forEach((region, regionIndex) => {
    regionOrder.set(String(region.key), regionIndex);
    region.children.forEach((place, placeIndex) =>
      placeOrder.set(place.key, placeIndex));
  });
  const regionLabels = new Map(taxonomy.map((r) => [String(r.key), r.label]));
  const placeLabels = new Map(taxonomy.flatMap((r) =>
    r.children.map((place) => [place.key, place.label])));
  return [
    // ?? null, not || {} alone: a row with no `origin` stamp at all makes
    // originOf return {}, and String(undefined) is the literal string
    // "undefined" — which matches no taxonomy entry and renders its own
    // group header (review M4). String(null) correctly routes into
    // "Anywhere" (the {key: null} taxonomy entry).
    { of: (segment) => String(originOf(segment).region ?? null),
      label: (key) => regionLabels.get(key) || key,
      // an unknown region (a stored override we no longer offer) sorts last
      // rather than vanishing
      order: (key) => regionOrder.has(key) ? regionOrder.get(key) : 999 },
    { of: (segment) => originOf(segment).key || null,
      label: (key) => placeLabels.get(key) || key,
      order: (key) => placeOrder.has(key) ? placeOrder.get(key) : 999 },
  ];
}

export function Segments({ t, intent, clearIntent }) {
  const [defs, setDefs] = useState(null);
  const [query, setQuery] = useState("");
  const [vocabData, setVocabData] = useState(null);
  const [editing, setEditing] = useState(null);   // null | "new" | def object
  // The timeline picker: false, or {replaces: def|null} — the hero button
  // opens it to CREATE ({replaces: null}), the editor's re-record door opens
  // it to REPLACE that row (round 16).
  const [recording, setRecording] = useState(false);
  const editorRef = useRef(null);   // the open Builder's {save, dirty} handle
  const [openGroups, toggleGroup] = useOpenGroups("sm64.segOriginsOpen");
  // Panes cap themselves to the space actually left below them (ui/viewport.js)
  // so the PAGE never scrolls; --pane-cap inherits to both panes from here.
  const workshopRef = usePaneCap();
  // Returns the rows as well as storing them — saving re-selects the row it
  // just wrote, which needs the FRESH copy (its origin stamp and seed_dirty
  // may both have changed server-side).
  const load = async () => {
    const rows = await getJSON("/api/segments");
    setDefs(rows);
    return rows;
  };
  useEffect(() => { load();
    getJSON("/api/segments/vocab").then(setVocabData); }, []);
  // An id handed over by the practice card's step track (app.js::openSegment,
  // the same intent-plus-tab shape openCompare uses). Waits for `defs`, since
  // the intent arrives on the same render the tab switches and the library may
  // not have loaded yet; clears itself so re-opening the tab later does not
  // silently reopen an editor the user closed.
  useEffect(() => {
    if (intent == null || !defs) return;
    const wanted = defs.find((row) => row.id === intent);
    if (wanted) setEditing(wanted);
    clearIntent();
  }, [intent, defs]);
  if (!defs || !vocabData) return html`<${PageState}
      kind=${t.connected ? "loading" : "offline"}
      title="Preparing the segment workshop" />`;

  const tgt = (t.view && t.view.target) || {};
  // Case-insensitive substring match on the name, applied per keystroke. The
  // seeded corpus pushes this library past 60 entries, where scrolling for
  // "BitFS Pipe Entry" is slower than typing "pipe" (user request
  // 2026-07-24). Matching the CATEGORY too means "castle" finds every
  // movement without anyone having to name them consistently.
  const needle = query.trim().toLowerCase();
  const shown = needle
    ? defs.filter((d) => `${d.name} ${d.category || ""}`
                           .toLowerCase().includes(needle))
    : defs;
  // armedSegs is the single live source: WS notices keep it instant,
  // every view fetch reconciles it so it never stays stale (store.js).
  const isArmed = (id) => t.armedSegs.has(id);
  async function setTarget(d) {
    await requestTarget(t, { kind: "segment", segment_id: d.id });
  }
  async function toggle(d) {
    await send("PUT", `/api/segments/${d.id}`, { enabled: !d.enabled });
    load(); t.refresh();
  }
  async function remove(d) {
    if (!window.confirm(`Delete "${d.name}" and its history/PBs?`)) return;
    await send("DELETE", `/api/segments/${d.id}`);
    load(); t.refresh();
  }
  // Clicking "edit" while another segment's editor is open: offer to save any
  // unsaved changes, then swap immediately. Previously the click was a no-op
  // until the open editor was closed (a wasted click) — the Builder ignored
  // the new `initial` (useState reads it once) and had no key to remount.
  async function tryEdit(d) {
    if (editing) {
      const isNew = editing === "new";
      if (!isNew && editing.id === d.id) return;   // already editing this one
      const api = editorRef.current;
      if (api && api.dirty) {
        const keep = window.confirm(
          `Save your changes to "${isNew ? "the new segment" : editing.name}" `
          + `before editing "${d.name}"?\n\nOK = save · Cancel = discard`);
        if (keep && !(await api.save())) return;   // save failed -> stay put
      }
    }
    setEditing(d);
  }

  return html`<div class="workshop-page segments-page">
    <header class="practice-card workshop-hero">
      <div class="workshop-title">
        <span class="workshop-title-icon"><${Icon} name="segments" size=${22} /></span>
        <div>
          <span class="eyebrow">Build</span>
          <h2>Segments</h2>
          <p>Define repeatable sections once, then practice and rank them like stars.</p>
        </div>
      </div>
      <button class="quiet-button" onclick=${() => setRecording({ replaces: null })}>
        <${Icon} name="bookmark" size=${16} /> Record a segment
      </button>
      <button class="primary-button" onclick=${() => setEditing("new")}>
        <${Icon} name="plus" size=${17} /> New segment
      </button>
    </header>
    ${/* Keyed by intent: the recorder seeds name/parent/picks from `replaces`
         in useState initializers, so swapping intents without a remount would
         carry one recording's state into the other's. */""}
    ${recording && html`<${SegmentTimeline} t=${t}
        key=${recording.replaces ? `re-${recording.replaces.id}` : "new"}
        replaces=${recording.replaces}
        onCancel=${() => setRecording(false)}
        onSaved=${async (savedId) => {
          // Same "stay on what you just saved" rule the Builder's own
          // onSaved follows (live audit 2026-07-25): land on the new (or
          // re-recorded) segment's own editor rather than the empty state.
          setRecording(false);
          const rowsList = await load();
          setEditing(rowsList.find((row) => row.id === savedId) || null);
          t.refresh();
        }} />`}

    <div class="segments-workshop" ref=${workshopRef}>
      <aside class="practice-card workshop-card segment-library">
        <div class="workshop-card-heading">
          <div>
            <span class="eyebrow">Library</span>
            <h3>Your segments</h3>
          </div>
          <span class="count-badge">${shown.length}${
            shown.length === defs.length ? "" : ` / ${defs.length}`}</span>
        </div>
        <input class="library-search" type="search" value=${query}
          placeholder="Search segments…" aria-label="Search segments"
          oninput=${(e) => setQuery(e.target.value)} />
        <div class="segment-list">
          ${defs.length === 0 ? html`<div class="workshop-empty compact">
            No segments yet. Create one to time a repeatable section of the game.
          </div>` : shown.length === 0 ? html`<div class="workshop-empty compact">
            No segment matches “${query}”.
          </div>` : html`<${GroupedList}
            tree=${buildTree(shown, originLevels(vocabData.origins || []))}
            open=${openGroups} toggle=${toggleGroup}
            forceOpen=${() => needle.length > 0}
            renderRow=${(d) => {
              const targeted = tgt.kind === "segment" && tgt.segment_id === d.id;
              return html`<article key=${d.id}
                  class=${`segrow ${editing !== "new" && editing?.id === d.id ? "on" : ""}`}>
                <button class="segment-row-main" onclick=${() => tryEdit(d)}>
                  <span class="segment-row-name">${d.name}</span>
                  <span class="segment-row-state">
                    ${isArmed(d.id) && html`<span class="chip good">● Running</span>`}
                    ${targeted && html`<span class="chip target-chip">◎ Target</span>`}
                    ${!d.enabled && html`<span class="chip muted-chip">Hidden</span>`}
                  </span>
                </button>
                <div class="segment-row-actions">
                  <button class=${targeted ? "is-selected" : ""} onclick=${() => setTarget(d)}
                      title="Set as practice target">
                    <${Icon} name="target" size=${15} /> Target
                  </button>
                  <button onclick=${() => toggle(d)} title=${d.enabled ? "Hide from practice" : "Show in practice"}>
                    <${Icon} name=${d.enabled ? "eyeOff" : "check"} size=${15} />
                    ${d.enabled ? "Hide" : "Show"}
                  </button>
                  <button onclick=${() => tryEdit(d)} title="Edit segment">
                    <${Icon} name="edit" size=${15} /> Edit
                  </button>
                  <button class="icon-button danger-icon" onclick=${() => remove(d)}
                      title="Delete segment" aria-label=${`Delete ${d.name}`}>
                    <${Icon} name="trash" size=${15} />
                  </button>
                </div>
              </article>`;
            }} />`}
        </div>
      </aside>

      <main class="practice-card workshop-card segment-editor">
        ${editing
          ? html`<${Builder} key=${editing === "new" ? "new" : editing.id}
              vocab=${vocabData} apiRef=${editorRef} t=${t} load=${load}
              allDefs=${defs} initial=${editing === "new" ? null : editing}
              onRerecord=${(row) => setRecording({ replaces: row })}
              onSaved=${async (savedId) => {
                // Stay on what you just saved (live audit 2026-07-25): closing
                // the editor threw the user back to the empty state, and after
                // creating a segment there was no way back to it but hunting
                // the library. Re-select from the RELOADED rows so the editor
                // shows the server's version, not the form's.
                const rows = await load();
                setEditing(rows.find((row) => row.id === savedId) || null);
                t.refresh();
              }}
              onCancel=${() => setEditing(null)} />`
          : html`<div class="workshop-empty">
              <span class="workshop-empty-icon"><${Icon} name="segments" size=${34} /></span>
              <h3>Choose a segment to edit</h3>
              <p>Its start, finish, and rules will stay organized in separate cards.</p>
              <button class="primary-button" onclick=${() => setEditing("new")}>
                <${Icon} name="plus" size=${16} /> Create a segment
              </button>
            </div>`}
      </main>
    </div>
  </div>`;
}
