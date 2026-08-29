// src/sm64_events/ui/components/scorecard.js — the Rank tab's scorecard as a
// GRID OF COURSE CARDS (round 9, 2026-08-28, replacing rounds 5-8's row
// list): "Each cell is a card representing a course / category of stars /
// segments... Each card contains the full name of each star prefixed by its
// star/segment icon, your rank icon + record time so far, and the goal rank
// + time, as well as the delta... compact enough so that if the window was
// landscape, I could take a nice picture." Each payload row IS a card
// (course / Bowser Fights / Secret — the server groups); each card draws
// its entries one LINE apiece and restates its Σ as a Stage RTA foot.
// Placement is DETERMINISTIC (round 10, replacing CSS multi-column, whose
// height-balancing staggered the card edges): three equal-height column
// stacks of course cards in order, a fourth column of Secret then Bowser
// Fights, stepping 4 → 2 → 1 columns as the pane narrows. Each card wears
// its course's own tint and art (`CARD_TINTS`, `.score-card-art`).
//
// A line's caps (`you_rank`/`goal_rank`) are SERVER-graded — the server
// picks, the client draws — and the "Show rank caps" toggle under the grid
// (localStorage `sm64.scorecardCaps`, OFF by default since round 10) adds
// both on top of the lean sheet look. A goal time is editable IN PLACE (`GoalCell`, the round
// 5-6 editor moved onto the line): edits live in `pendingOverrides` until
// the "N edited → save as a named goal" bar writes them through
// `PUT /api/scorecard/goal {kind:"custom", name, times}`.
//
// `divisionOptions()`/`fmtGapCs` stay exported import-free (no Preact) so
// tests/test_ui_scorecard.py drives them under node; the pure logic lives
// in ui/scorecardgoal.js (its header says why). The runner group of the
// goal picker fetches LAZILY on first open. Both Copy buttons COPY to the
// clipboard — the desktop WebView2 shell's download behaviour is
// unverified, and a dead button is the shape this project treats as a bug;
// `GET /api/scorecard/export.csv` stays reachable by URL regardless.
import { h } from "preact";
import { useEffect, useMemo, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { attainableCs, fmtSeconds } from "../format.js";
import { applyGoalOverrides, divisionOptions, fmtGapCs, goalGroups,
         parseGapTime } from "../scorecardgoal.js";
import { capName, divisionDigit } from "./caps.js";
import { entityIconSrc } from "./entityicons.js";
import { RankIcon } from "./rankicon.js";
import { SearchSelect } from "./searchselect.js";
import { InlineState } from "./states.js";

const html = htm.bind(h);

// Re-exported at this path too -- ui/scorecardgoal.js's own header comment
// says why the pure logic lives in a separate, genuinely import-free file
// rather than here.
export { applyGoalOverrides, divisionOptions, fmtGapCs, goalGroups, parseGapTime };

function goalToValue(goal) {
  if (!goal) return "";
  if (goal.kind === "division") return `division:${goal.tier}:${goal.division}`;
  if (goal.kind === "runner") return `runner:${goal.runner}`;
  if (goal.kind === "custom") return `custom:${goal.name}`;
  return "";
}

function goalToLabel(goal) {
  if (!goal) return "No goal";
  if (goal.kind === "division") return `${capName(goal.tier)} ${divisionDigit(goal.division)}`;
  if (goal.kind === "runner") return goal.runner;
  if (goal.kind === "custom") return goal.name;
  return "No goal";
}

// Re-selecting an EXISTING custom goal (picked from the dropdown) carries no
// `times` -- the server already has them (`_CUSTOM_KEY`); `times` is added
// separately, only at SAVE time (see `saveCustomGoal` below), which is the
// one call site allowed to turn a picker value into a body carrying data.
function valueToGoal(value) {
  if (!value) return null;
  const [kind, ...rest] = value.split(":");
  if (kind === "division") {
    const [tier, division] = rest;
    return { kind: "division", tier, division };
  }
  if (kind === "runner") return { kind: "runner", runner: rest.join(":") };
  if (kind === "custom") return { kind: "custom", name: rest.join(":") };
  return null;
}

// The picker's group list -- `goalGroups`, imported above -- lives in
// scorecardgoal.js so it stays node-testable the same way divisionOptions()
// already is; this file just calls it with whatever runners it has.

// One cap, drawn only when the server graded that side and the toggle is
// on. 14px — big enough that the tier colour and division numeral read,
// small enough that 120 lines of them stay a texture rather than a wall.
function LineCap({ rank, show }) {
  if (!show || !rank) return "";
  return html`<span class="score-line-cap">
    <${RankIcon} tier=${rank.tier} division=${rank.division} size=${14} />
  </span>`;
}

// The in-place goal editor — the round 5-6 editor moved onto the LINE
// (round 9 deleted the expandable detail table; the card is the detail).
// Class names are kept from the table era (`.score-detail-*`) because they
// name the EDITOR, not the table, and the blur/snap/hint render tests key
// on them.
function GoalCell({ tile, onGoalOverride }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [invalid, setInvalid] = useState(false);

  function startEdit() {
    setDraft(tile.goal_cs != null ? fmtSeconds(tile.goal_cs / 100) : "");
    setInvalid(false);
    setEditing(true);
  }
  function cancel() { setEditing(false); setInvalid(false); }
  function commit() {
    // An EMPTY blur/Enter is "I changed my mind", not "I typed garbage" --
    // cancelling (rather than flagging it invalid) is the only way out of
    // edit mode a blur on an empty box ever had, and leaving it red and
    // open is the un-dismissable-control shape `.claude/rules/acceptance.md`
    // rules against.
    if (!draft.trim()) { cancel(); return; }
    const parsed = parseGapTime(draft);
    if (parsed === null) { setInvalid(true); return; }
    // The one snap the app already has (format.js::attainableCs, the
    // import field's own door): only 30 of every 100 centisecond values
    // can appear on the timer, so a typed goal rounds onto the displayable
    // set and the cell shows the snapped value the moment the edit lands --
    // never a number nobody typed (round 5).
    onGoalOverride(tile.key, attainableCs(parsed));
    setEditing(false);
  }

  if (editing) {
    return html`<span class="score-detail-editing">
      <input class="score-detail-input ${invalid ? "is-invalid" : ""}"
          value=${draft} placeholder="0'00&quot;00" autoFocus
          oninput=${(inputEvent) => { setDraft(inputEvent.target.value); setInvalid(false); }}
          onkeydown=${(keyEvent) => {
            if (keyEvent.key === "Enter") commit();
            if (keyEvent.key === "Escape") cancel();
          }}
          onblur=${commit} />
      ${invalid
        ? html`<span class="score-detail-hint">type it like 51"83</span>` : ""}
    </span>`;
  }
  return html`<button type="button" class="score-detail-goal-btn" onclick=${startEdit}>
    ${tile.goal_cs != null ? fmtSeconds(tile.goal_cs / 100) : "set a time…"}
  </button>`;
}

// One star (or segment) of a card: icon · full wrapping name · your cap +
// time · goal cap + editable goal time · signed gap. The name WRAPS rather
// than truncating — the card owns its width, so round 9's "no word is cut
// off" holds by construction rather than by a tuned column.
function ScoreLine({ t, tile, showCaps, onGoalOverride }) {
  const gapCls = tile.delta_cs != null
    ? (tile.delta_cs <= 0 ? "good" : "bad") : "";
  return html`<div class="score-line">
    <img class="score-line-icon" alt="" src=${entityIconSrc(t, tile.key)} />
    <span class="score-line-name">${tile.label}</span>
    <span class="score-line-you">
      <${LineCap} rank=${tile.you_rank} show=${showCaps} />
      <span class="score-line-time">${tile.you_cs != null
        ? fmtSeconds(tile.you_cs / 100) : "—"}</span>
    </span>
    <span class="score-line-goal">
      <${LineCap} rank=${tile.goal_rank} show=${showCaps} />
      <${GoalCell} tile=${tile} onGoalOverride=${onGoalOverride} />
    </span>
    <span class="score-gap ${gapCls}">${tile.delta_cs != null
      ? fmtGapCs(tile.delta_cs) : "—"}</span>
  </div>`;
}

// The card foot: the Σ restated as You · Goal · gap, "Stage RTA" on a
// course card and "Total" on Fights/Secret (the reference sheet's own
// wording — only a stage has a Stage RTA). `counted === 0` still carries
// sums of 0 (Python `sum([])`), which is not a real 0'00"00 -- em-dashes,
// never two invented zeroes (`.claude/rules/acceptance.md`'s "cannot
// logically be compared" rule).
function CardFoot({ row }) {
  const sum = row.sum;
  const hasDelta = sum.delta_cs != null;
  const behind = hasDelta && sum.delta_cs > 0;
  return html`<div class="score-card-foot">
    <span class="score-card-foot-label">${row.course_id != null ? "Stage RTA" : "Total"}</span>
    <span class="score-line-you"><span class="score-line-time">${hasDelta
      ? fmtSeconds(sum.you_cs / 100) : "—"}</span></span>
    <span class="score-line-goal">${hasDelta
      ? fmtSeconds(sum.goal_cs / 100) : "—"}</span>
    <span class="score-gap ${hasDelta ? (behind ? "bad" : "good") : ""}">
      ${hasDelta ? fmtGapCs(sum.delta_cs) : "—"}</span>
    ${sum.counted < sum.total
      ? html`<span class="score-sum-coverage">${sum.counted}/${sum.total}</span>` : ""}
  </div>`;
}

// Course-THEMED card tints (round 10: "Each card should get a unique
// background color"): the hue is what the course IS — grass, lava, sand —
// never a hash, because the point is legibility for a human skimming his
// progress. The CSS mixes these down to a dark wash off one `--card-tint`
// custom property, so the stylesheet never names a course.
const CARD_TINTS = {
  1: "#6fae4e",   // BOB — grass
  2: "#9aa5b1",   // WF — stone
  3: "#3f7fbf",   // JRB — ocean
  4: "#6fc7e0",   // CCM — ice slide
  5: "#8f6bc7",   // BBH — haunt purple
  6: "#5f8f80",   // HMC — cave teal
  7: "#d95f3b",   // LLL — lava
  8: "#d8b25a",   // SSL — sand
  9: "#3b5fc9",   // DDD — deep sea
  10: "#a9d7e8",  // SL — pale ice
  11: "#47b3a5",  // WDW — aqua town
  12: "#a3814f",  // TTM — mountain earth
  13: "#62b657",  // THI — leafy island
  14: "#c9a23b",  // TTC — clock brass
  15: "#7f6fd9",  // RR — sky violet
};
const SECRET_TINT = "#d7b64f";  // castle-star gold
const FIGHTS_TINT = "#b03a3a";  // Bowser red

function cardTint(row) {
  if (row.course_id != null) return CARD_TINTS[row.course_id] || SECRET_TINT;
  return row.label === "Secret" ? SECRET_TINT : FIGHTS_TINT;
}

// Round 10's placement, his words as geometry: "3 columns of 5 cards (for
// each of the courses, in order), and then a 4th column on the far right
// for the remainder (secret stars card, followed by bowser fights card)".
// Course cards chunk into three stacks in payload order; the specials
// column reorders Secret ahead of Fights for DISPLAY only — the payload
// and the CSV keep the builder's order. A route scope with fewer course
// cards chunks the same way; empty columns simply don't render.
function cardColumns(rows) {
  const courses = rows.filter((row) => row.course_id != null);
  const specials = rows.filter((row) => row.course_id == null)
    .sort((a, b) => (a.label === "Secret" ? 0 : 1)
                  - (b.label === "Secret" ? 0 : 1));
  const perColumn = Math.ceil(courses.length / 3) || 1;
  const columns = [];
  for (let start = 0; start < courses.length; start += perColumn)
    columns.push({ kind: "courses", rows: courses.slice(start, start + perColumn) });
  if (specials.length) columns.push({ kind: "specials", rows: specials });
  return columns;
}

function ScoreCard({ t, row, showCaps, removing,
                     onRemove, onGoalOverride }) {
  // The remove control writes the SAME exclusion the Rank tab's own
  // breakdown writes (`POST /api/marelo/exclude`), never a second
  // scorecard-only ignore list -- round 7 settled that this card and the
  // scope's rating must agree. The title says what the click actually does
  // ("put the reason where the click lands").
  const removeTitle = `Ignore ${row.label} — drops it from this card and`
    + " from this scope's ranking (undo from the Rank tab's breakdown)";
  const headGap = row.sum.delta_cs;
  const isCourse = row.course_id != null && row.course_id >= 1
    && row.course_id <= 15;
  return html`<section class="score-card"
      style=${`--card-tint:${cardTint(row)}`}>
    <div class="score-card-head">
      ${isCourse ? html`<img class="score-card-art" alt=""
          src=${entityIconSrc(t, `course:${row.course_id}`)} />` : ""}
      <span class="score-card-name">${row.label}</span>
      <span class="score-gap ${headGap != null
        ? (headGap > 0 ? "bad" : "good") : ""}">${headGap != null
        ? fmtGapCs(headGap) : ""}</span>
      <button type="button" class="score-row-remove" title=${removeTitle}
          aria-label=${removeTitle} disabled=${removing}
          onclick=${() => onRemove(row)}>×</button>
    </div>
    ${row.tiles.map((tile) => html`<${ScoreLine} key=${tile.key} t=${t}
        tile=${tile} showCaps=${showCaps}
        onGoalOverride=${onGoalOverride} />`)}
    <${CardFoot} row=${row} />
  </section>`;
}

function CopyButton({ className, label, onCopy, onError }) {
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);

  async function handleClick() {
    if (busy) return;
    setBusy(true);
    try {
      // `routes.js:269`'s own `navigator.clipboard &&` guard, but this
      // button owns an inline error slot (routes.js's plain Copy JSON does
      // not), so a missing clipboard gets a plain sentence there instead of
      // a silent no-op.
      if (!navigator.clipboard) {
        throw new Error("clipboard access is not available here");
      }
      const text = await onCopy();
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), COPIED_FLASH_MS);
    } catch (err) {
      onError(err.message || String(err));
    } finally {
      setBusy(false);
    }
  }

  return html`<button class=${`scorecard-copy-btn ${className}`}
      onclick=${handleClick} disabled=${busy}>
    ${copied ? "Copied ✓" : label}
  </button>`;
}

async function fetchCsvText(scopeId) {
  const response = await fetch(
    `/api/scorecard/export.csv?scope=${encodeURIComponent(scopeId)}`);
  if (response.ok) return response.text();
  let detail = null;
  try { detail = (await response.json()).detail; } catch { /* non-JSON body */ }
  throw new Error(detail || `export.csv: ${response.status}`);
}

// The column door's own 503 ("could not read the sheet: …") is shown
// INLINE beside the buttons, never a toast -- the same "put the reason
// where the click lands" rule `.claude/rules/acceptance.md` states for a
// disabled control applies to a button whose action just failed. The CSV
// door reads the cached snapshot rather than fetching live, so it is far
// less likely to fail, but a network hiccup on the fetch itself still
// lands in the same slot rather than going nowhere.
function ScorecardExports({ scopeId }) {
  const [error, setError] = useState(null);

  // The sheet column deliberately takes NO scope: its whole contract is one
  // line per live worksheet row of the community sheet, whatever the card
  // above it is scoped to. The CSV is the CARD, so it follows the scope.
  return html`<div class="scorecard-exports">
    <${CopyButton} className="scorecard-copy-column" label="Copy sheet column"
        onCopy=${async () => (await getJSON("/api/scorecard/column")).lines.join("\n")}
        onError=${setError} />
    <${CopyButton} className="scorecard-copy-csv" label="Copy scorecard CSV"
        onCopy=${() => fetchCsvText(scopeId)} onError=${setError} />
    ${error ? html`<${InlineState} kind="error">${error}<//>` : ""}
  </div>`;
}

// Appears only while there are unsaved goal edits -- his flow, verbatim:
// "This should automatically adjust my goal time comparison... I should
// then be able to SAVE my custom comparison with a given name." The name
// field defaults to the ACTIVE goal's own name when it is already a custom
// one, so re-editing and re-saving under the SAME name is the natural path
// -- "If I modify a saved comparison, it should just overwrite it" needs no
// separate control, since the store is a plain dict keyed by name and
// saving under an unchanged name IS the overwrite. Discard is a pure local
// reset (nothing has been written to the server yet), matching the
// "abandonable with no side effects" rule every other multi-step control in
// this app follows.
function ScorecardSaveBar({ pendingCount, initialName, busy, error, onSave, onDiscard }) {
  const [name, setName] = useState(initialName);
  useEffect(() => setName(initialName), [initialName]);
  return html`<div class="scorecard-savebar">
    <span class="meta">${pendingCount} custom ${pendingCount === 1 ? "time" : "times"} edited</span>
    <input class="scorecard-savebar-input" value=${name}
        placeholder="Name this goal…"
        oninput=${(inputEvent) => setName(inputEvent.target.value)} />
    <button type="button" class="quiet-button" disabled=${busy || !name.trim()}
        onclick=${() => onSave(name.trim())}>Save goal</button>
    <button type="button" class="quiet-button" disabled=${busy}
        onclick=${onDiscard}>Discard</button>
    ${error ? html`<${InlineState} kind="error">${error}<//>` : ""}
  </div>`;
}

function ScorecardHead({ goal, groups, onOpen, coverage, onGoalChange, scopeId }) {
  return html`<div class="scorecard-head">
    <h3>Scorecard</h3>
    <${SearchSelect} value=${goalToValue(goal)} valueLabel=${goalToLabel(goal)}
        title="Pick a goal" groups=${groups} onOpen=${onOpen} onChange=${onGoalChange}
        align="right" />
    ${goal && coverage.covered < coverage.tiles
      ? html`<p class="meta scorecard-note">goal covers ${coverage.covered}/${coverage.tiles}</p>`
      : ""}
    <${ScorecardExports} scopeId=${scopeId} />
  </div>`;
}

// The caps preference — per BROWSER, like `sm64.rankIcons`: what a card
// looks like is a display choice, not practice data. try/catch because a
// blocked-storage context (thumbnail capture, hardened browser) must render
// the default, never crash the card.
const CAPS_KEY = "sm64.scorecardCaps";

// OFF by default since round 10 ("No rank caps by default"); enabling
// persists under the same key, so the lean sheet is what a fresh browser
// sees and the caps are one remembered click away.
function readCapsPreference() {
  try { return localStorage.getItem(CAPS_KEY) === "on"; }
  catch { return false; }
}

function writeCapsPreference(on) {
  try { localStorage.setItem(CAPS_KEY, on ? "on" : "off"); }
  catch { /* display preference only -- losing it costs a click */ }
}

export function Scorecard({ t, scopeId = "overall" }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  // null = not fetched yet (the Runners group is lazy, see the header
  // comment); [] once fetched even if the sheet somehow named nobody.
  const [runners, setRunners] = useState(null);
  const [showCaps, setShowCaps] = useState(readCapsPreference);
  // entity_key -> goal_cs, UNSAVED. Lives here (not per-row) because a save
  // can gather edits made across several rows before he ever presses Save.
  const [pendingOverrides, setPendingOverrides] = useState({});
  const [saveBusy, setSaveBusy] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [saveError, setSaveError] = useState(null);

  // Fetches on mount and on t.mareloRev, the Rank tab's own staleness key
  // (RankPage's own useEffect does the same -- an attempt or a PB save must
  // not leave this card showing a stale gap while open during play).
  useEffect(() => {
    let alive = true;
    // A scope switch clears the OLD scope's card up front, the same rule
    // RankPage's own fetch follows: a 404 on a stale route id must never
    // leave the previous scope's rows under the new scope's label.
    setError(null);
    setData(null);
    getJSON(`/api/scorecard?scope=${encodeURIComponent(scopeId)}`)
      .then((response) => alive && setData(response))
      .catch((err) => alive && setError(err));
    return () => { alive = false; };
  }, [t.mareloRev, scopeId]);

  function loadRunnersOnce() {
    if (runners != null) return;
    getJSON("/api/library/runners")
      .then((body) => setRunners(body.runners || []))
      .catch(() => setRunners([]));
  }

  const groups = useMemo(() => goalGroups(runners, data && data.custom_goals),
    [runners, data && data.custom_goals]);

  // The payload actually RENDERED -- the server's own resolved goal with any
  // unsaved edits laid on top, recomputed live (scorecardgoal.js's own
  // header comment on `applyGoalOverrides` has the "no server round trip
  // per keystroke" reasoning). A no-op object identity when there is
  // nothing pending, so this costs nothing on every other render.
  const displayData = useMemo(
    () => (data ? applyGoalOverrides(data, pendingOverrides) : null),
    [data, pendingOverrides]);
  // Editing even ONE star without a base goal set still means "I am
  // comparing against something now" -- coloring should not wait for a
  // saved goal to exist.
  const hasGoal = !!(data && data.goal) || Object.keys(pendingOverrides).length > 0;

  function handleGoalOverride(entityKey, goalCs) {
    setPendingOverrides((current) => ({ ...current, [entityKey]: goalCs }));
  }

  async function removeRow(row) {
    // SEQUENTIAL, never Promise.all: `set_rank_excluded` is a
    // read-modify-write over one ui_state KV, so parallel writes race and
    // all but the last are silently lost -- a row of ten cells would drop
    // nine of its exclusions and look like it half-worked. A row is at most
    // ten keys, and this is a once-in-a-while gesture.
    setRemoving(true);
    setError(null);
    try {
      for (const tile of row.tiles) {
        await send("POST", "/api/marelo/exclude",
                   { entity: tile.key, excluded: true });
      }
      setData(await getJSON(
        `/api/scorecard?scope=${encodeURIComponent(scopeId)}`));
    } catch (err) {
      setError(err);
    } finally {
      setRemoving(false);
    }
  }

  function discardOverrides() {
    setPendingOverrides({});
    setSaveError(null);
  }

  async function onGoalChange(value) {
    // Picking a different base goal makes any unsaved edit ambiguous (it
    // was relative to whatever was active a moment ago) -- discard rather
    // than silently carry it onto a goal it was never made against.
    setPendingOverrides({});
    setSaveError(null);
    try {
      await send("PUT", "/api/scorecard/goal", valueToGoal(value));
      setData(await getJSON("/api/scorecard"));
    } catch (err) { setError(err); }
  }

  async function saveCustomGoal(name) {
    if (!name || !displayData) return;
    setSaveBusy(true);
    setSaveError(null);
    try {
      // The FULL currently-displayed times, not just the touched keys -- a
      // save carries every star's existing target forward, so the ones he
      // never opened keep whatever the base goal already gave them.
      const times = {};
      for (const row of displayData.rows) {
        for (const tile of row.tiles) {
          if (tile.goal_cs != null) times[tile.key] = tile.goal_cs;
        }
      }
      await send("PUT", "/api/scorecard/goal", { kind: "custom", name, times });
      setPendingOverrides({});
      setData(await getJSON("/api/scorecard"));
    } catch (err) {
      setSaveError(err.message || String(err));
    } finally {
      setSaveBusy(false);
    }
  }

  const pendingCount = Object.keys(pendingOverrides).length;

  return html`<div class="practice-card scorecard-card">
    ${error
      ? html`<${InlineState} kind="error">${error.message}<//>`
      : !data
        ? html`<${InlineState}>Loading your scorecard…<//>`
        : html`<${ScorecardHead} goal=${data.goal} groups=${groups}
              onOpen=${loadRunnersOnce} scopeId=${scopeId}
              coverage=${data.goal_coverage} onGoalChange=${onGoalChange} />
            ${pendingCount > 0
              ? html`<${ScorecardSaveBar} pendingCount=${pendingCount}
                    initialName=${data.goal && data.goal.kind === "custom" ? data.goal.name : ""}
                    busy=${saveBusy} error=${saveError}
                    onSave=${saveCustomGoal} onDiscard=${discardOverrides} />`
              : ""}
            <div class="score-cards">
              ${cardColumns(displayData.rows).map((column, columnIndex) => html`<div
                  key=${columnIndex}
                  class="score-col ${column.kind === "courses"
                    ? "score-col-courses" : "score-col-specials"}">
                ${column.rows.map((row) => html`<${ScoreCard} key=${row.label}
                    t=${t} row=${row} showCaps=${showCaps} removing=${removing}
                    onRemove=${removeRow} onGoalOverride=${handleGoalOverride} />`)}
              </div>`)}
            </div>
            <label class="scorecard-caps-toggle">
              <input type="checkbox" checked=${showCaps}
                  onchange=${(changeEvent) => {
                    const on = changeEvent.target.checked;
                    setShowCaps(on);
                    writeCapsPreference(on);
                  }} />
              ${" "}Show rank caps
            </label>`}
  </div>`;
}
