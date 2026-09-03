// src/sm64_events/ui/components/scorecard.js — the Rank tab's scorecard as a
// GRID OF COURSE CARDS (round 9, 2026-08-28, replacing rounds 5-8's row
// list): "Each cell is a card representing a course / category of stars /
// segments... Each card contains the full name of each star prefixed by its
// star/segment icon, your rank icon + record time so far, and the goal rank
// + time, as well as the delta... compact enough so that if the window was
// landscape, I could take a nice picture." Each payload row IS a card
// (course / Bowser Fights / Secret — the server groups); each card draws
// its entries one LINE apiece and restates its Σ as a Stage Sum foot.
// Placement is DETERMINISTIC (round 10, replacing CSS multi-column, whose
// height-balancing staggered the card edges): three equal-height column
// stacks of course cards in order, a fourth column of Secret then Bowser
// Fights, stepping 4 → 2 → 1 columns as the pane narrows. Each card wears
// its course's own tint and art (`CARD_TINTS`, `.score-card-art`).
//
// The caps a line used to wear (server-graded `you_rank`/`goal_rank` behind
// a "Show rank caps" toggle, OFF by default) were DELETED in round 23 —
// "Not going to use it ever" — toggle, draw and server grading together,
// so no fetch grades 200 tiles for a cap nobody can switch on. Each legend
// pill carries an × (round 23) that writes the goal back without that
// pick, and the Copy button flashes "Copied ✓" for `COPIED_FLASH_MS`
// before returning to its label. A goal time is editable IN PLACE (`GoalCell`, the round
// 5-6 editor moved onto the line): edits live in `pendingOverrides` until
// the "N edited → save as a named goal" bar writes them through
// `PUT /api/scorecard/goal {kind:"custom", name, times}`.
//
// `divisionOptions()`/`fmtGapCs` stay exported import-free (no Preact) so
// tests/test_ui_scorecard.py drives them under node; the pure logic lives
// in ui/scorecardgoal.js (its header says why). The runner group of the
// goal picker fetches LAZILY on first open. The one Copy button COPIES to
// the clipboard rather than downloading — the desktop WebView2 shell's
// download behaviour is unverified, and a dead button is the shape this
// project treats as a bug. The CSV button was removed in round 19 (his
// call); `GET /api/scorecard/export.csv` stays reachable by URL.
import { h } from "preact";
import { useEffect, useMemo, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { useIdentityFetch } from "../refetch.js";
import { useMeasuredWidth } from "../viewport.js";
import { attainableCs, fmtSeconds } from "../format.js";
import { applyGoalOverrides, cardColumns, columnCountFor, divisionOptions,
         fmtGapCs, goalGroups, parseGapTime } from "../scorecardgoal.js";
import { capName, divisionDigit } from "./caps.js";
import { entityIconSrc } from "./entityicons.js";
import { Icon } from "./icons.js";
import { RegionSwitch } from "./versionswitch.js";
import { SearchSelect } from "./searchselect.js";
import { InlineState } from "./states.js";

const html = htm.bind(h);

// Re-exported at this path too -- ui/scorecardgoal.js's own header comment
// says why the pure logic lives in a separate, genuinely import-free file
// rather than here.
export { applyGoalOverrides, cardColumns, columnCountFor, divisionOptions,
         fmtGapCs, goalGroups, parseGapTime };

function goalToValue(goal) {
  if (!goal) return "";
  if (goal.kind === "division") return `division:${goal.tier}:${goal.division}`;
  if (goal.kind === "runner") return `runner:${goal.runner}`;
  if (goal.kind === "custom") return `custom:${goal.name}`;
  return "";
}

// The picker is a MULTI-select (round 14): its value is the list of picked
// sources, and a stored goal of any kind reads back as that list -- a
// single pick is simply a list of one, so there is no second shape for
// "one goal" and nothing to migrate.
function goalToValues(goal) {
  if (!goal) return [];
  if (goal.kind === "multi") {
    return (goal.sources || []).map(goalToValue).filter(Boolean);
  }
  const one = goalToValue(goal);
  return one ? [one] : [];
}

function goalToLabel(goal) {
  if (!goal) return "No goal";
  if (goal.kind === "division") return `${capName(goal.tier)} ${divisionDigit(goal.division)}`;
  if (goal.kind === "runner") return goal.runner;
  if (goal.kind === "custom") return goal.name;
  if (goal.kind === "multi") {
    const sources = goal.sources || [];
    // One name reads better than "1 picked"; past that the count IS the
    // useful summary, and the panel itself lists which ones are on.
    if (sources.length === 1) return goalToLabel(sources[0]);
    return `${sources.length} picked`;
  }
  return "No goal";
}

// The list of picks -> the goal to store. Nothing picked clears the goal;
// ONE pick stores that goal in its own shape (so a division stays a
// division everywhere it is read); several store a `multi`, whose per-tile
// answer is the FASTEST offer among them (round 16).
function valuesToGoal(values) {
  const goals = (values || []).map(valueToGoal).filter(Boolean);
  if (!goals.length) return null;
  if (goals.length === 1) return goals[0];
  return { kind: "multi", sources: goals };
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

// One colour per PICK of a multi goal (round 15). The server says which
// source set each tile (`tile.goal_source`, an index into `goal.sources`),
// and both surfaces that show attribution -- the legend pills under the
// picker and the dot after a star's name -- read this same table by that
// index, so the dot beside a time and the pill it points at cannot
// disagree. Ten hues, walked in order: distinct at a glance beside each
// other and against the card tints, and a pick past the tenth simply
// re-uses one (the pill's own name is still there to read).
const SOURCE_COLOURS = [
  "#5bb8ff", "#ffb347", "#7ee081", "#ff6f97", "#c191ff",
  "#ffd95b", "#4fd6c4", "#ff8f5b", "#9fb3ff", "#e08fd0",
];

export function sourceColour(index) {
  if (index == null || index < 0) return null;
  return SOURCE_COLOURS[index % SOURCE_COLOURS.length];
}

// The legend: every pick, in the order they were picked, each wearing the
// colour its dots use. His ask -- "it should show pills underneath the '4
// picked'... otherwise, it's very hard to understand which of the options
// you've selected." Round 23 gave each pill an × at its right: "I should
// be able to click this to remove that specific player / rank standard
// from my scorecard immediately." It shipped hover-revealed and he ruled
// it out on sight -- "Pills look weird if the X is hidden by default.
// Let's just show it at all times. Red X." -- so it is always drawn, red;
// clicking it hands the pick's index up, and the card writes the goal
// back without it through the same door the picker uses.
function GoalLegend({ goal, onRemove }) {
  if (!goal || goal.kind !== "multi") return "";
  const sources = goal.sources || [];
  if (!sources.length) return "";
  return html`<div class="goal-legend">
    ${sources.map((source, index) => html`<span class="goal-pill"
        key=${`${index}:${goalToValue(source)}`}
        style=${`--pill-colour:${sourceColour(index)}`}>
      <span class="goal-pill-dot" aria-hidden="true"></span>
      <span class="goal-pill-label">${goalToLabel(source)}</span>
      <button type="button" class="goal-pill-remove"
          title=${`Remove ${goalToLabel(source)} from the goal`}
          aria-label=${`Remove ${goalToLabel(source)} from the goal`}
          onclick=${() => onRemove && onRemove(index)}>×</button>
    </span>`)}
  </div>`;
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
//
// Icon and name together are a DOOR to the Library (round 11: "the star
// text hyperlinks to the library page for that star... it finds the
// closest example in the library to my goal time"): one button spanning
// both columns, carrying the tile's server-stamped active strategy and its
// goal time so the arrival can land on the right example. The hover
// affordance is his spec verbatim — the name turns blue and a hidden
// library glyph appears appended to it; the glyph reserves its space
// always (visibility, not display) so hovering never reflows a wrapped
// name.
function ScoreLine({ t, tile, onGoalOverride, onOpenLibrary,
                     sourceTitle = null }) {
  const gapCls = tile.delta_cs != null
    ? (tile.delta_cs <= 0 ? "good" : "bad") : "";
  const iconSrc = entityIconSrc(t, tile.key);
  const openLine = onOpenLibrary
    ? () => onOpenLibrary({ kind: "target", entity: tile.key,
                            strat: tile.strat || null,
                            goalCs: tile.goal_cs ?? null })
    : null;
  // The glyph rides GLUED to the last word (round 13). It reserves inline
  // space, so a name that fills its column pushed the glyph onto a line of
  // its own -- measured: six real names drew ONE line of text inside a
  // TWO-line box, which centred the icon below the text he could see
  // ("the text ends up not being center aligned, and it appears to have
  // incorrectly loaded above the course icon"). Bound to the last word in
  // a nowrap span, the glyph can only ever wrap TOGETHER with a visible
  // word, so no blank line exists to be centred against. A single-word
  // label keeps the plain form -- nowrap on the whole name could overflow
  // the column, which is a worse bug than the one being fixed.
  const lastSpace = tile.label.lastIndexOf(" ");
  const nameHead = lastSpace > 0 ? tile.label.slice(0, lastSpace + 1) : "";
  const nameTail = lastSpace > 0 ? tile.label.slice(lastSpace + 1) : tile.label;
  // The attribution dot (round 15) rides in the SAME nowrap tail as the
  // library glyph, for the same reason round 13 put the glyph there: a
  // mark that can wrap onto a line of its own leaves a blank line under
  // the text and drops the icon below it. Titled, so the colour is never
  // the only way to read it.
  const sourceColor = sourceColour(tile.goal_source);
  const dot = sourceColor
    ? html`<span class="score-line-source" title=${sourceTitle || ""}
        style=${`--source-colour:${sourceColor}`} aria-hidden="true"></span>`
    : "";
  const glyph = html`<span class="score-line-lib" aria-hidden="true">
    <${Icon} name="library" size=${11} /></span>`;
  return html`<div class="score-line">
    ${openLine ? html`<button type="button" class="score-line-link"
        title=${`Open ${tile.label} in the Library`} onclick=${openLine}>
      <img class="score-line-icon" alt="" src=${iconSrc} />
      <span class="score-line-name">${nameHead}<span
          class=${nameHead ? "score-line-tail" : ""}>${nameTail}${dot}${glyph}</span></span>
    </button>` : html`<img class="score-line-icon" alt="" src=${iconSrc} />
    <span class="score-line-name">${tile.label}${dot}</span>`}
    <span class="score-line-you">
      <span class="score-line-time">${tile.you_cs != null
        ? fmtSeconds(tile.you_cs / 100) : "—"}</span>
    </span>
    <span class="score-line-goal">
      <${GoalCell} tile=${tile} onGoalOverride=${onGoalOverride} />
    </span>
    <span class="score-gap ${gapCls}">${tile.delta_cs != null
      ? fmtGapCs(tile.delta_cs) : "—"}</span>
  </div>`;
}

// The card foot: the Σ restated as You · Goal · gap, "Stage Sum" on a
// course card and "Total" on Fights/Secret. SUM, not "Stage RTA" (round
// 14): Stage RTA names a real thing -- a route category in this app's own
// corpus, the run where you play a whole course start to finish -- and
// this number is not that, it is the addition of separately practised
// times. The CSV export keeps the sheet template's own wording, since it
// exists to be pasted back into that sheet. `counted === 0` still carries
// sums of 0 (Python `sum([])`), which is not a real 0'00"00 -- em-dashes,
// never two invented zeroes (`.claude/rules/acceptance.md`'s "cannot
// logically be compared" rule).
function CardFoot({ row }) {
  const sum = row.sum;
  const hasDelta = sum.delta_cs != null;
  const behind = hasDelta && sum.delta_cs > 0;
  return html`<div class="score-card-foot">
    <span class="score-card-foot-label">${row.course_id != null ? "Stage Sum" : "Total"}</span>
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
  // Two specials cards again since round 21: the Secret card wears the
  // castle gold its stars have always worn, the Bowser card its red. The
  // server says which is which (`kind`) so nothing here matches a label.
  return row.kind === "bowser" ? FIGHTS_TINT : SECRET_TINT;
}

// Round 20's placement (`cardColumns`/`columnCountFor`) lives in
// scorecardgoal.js so node can drive it -- that file's own header says why --
// and is re-exported at the top of this one.

function ScoreCard({ t, row, removing,
                     onRemove, onGoalOverride, onOpenLibrary,
                     sourceNames = [] }) {
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
    <div class="score-labels" aria-hidden="true">
      <span class="score-label-you">You</span>
      <span class="score-label-goal">Goal</span>
      <span class="score-label-gap">Δ</span>
    </div>
    <div class="score-lines">
      ${row.tiles.map((tile) => html`<${ScoreLine} key=${tile.key} t=${t}
          tile=${tile}
          sourceTitle=${sourceNames[tile.goal_source] || null}
          onGoalOverride=${onGoalOverride} onOpenLibrary=${onOpenLibrary} />`)}
    </div>
    <${CardFoot} row=${row} />
  </section>`;
}

// How long the button reads "Copied ✓" before returning to its label -- a
// transient success state, so the control reads as usable again (round 23:
// "should *briefly* be displayed as 'Copied' and then go back"). The
// constant lived here from the first export button, was lost in round 9's
// rewrite while its one use survived, and every click since threw
// `COPIED_FLASH_MS is not defined` into the error slot AFTER the copy had
// already succeeded -- the browser tests read the clipboard and never the
// label, and the lint gate's JavaScript half was silently skipping.
const COPIED_FLASH_MS = 1500;

function CopyButton({ className, label, onCopy, onError }) {
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);

  async function handleClick() {
    if (busy) return;
    setBusy(true);
    // Round 25: the LAST failure's message goes the moment he tries again --
    // "If there's an error, and I click 'copy sheet column' again, the error
    // should disappear. If there's a new error, the new error should show."
    // A message that outlives the gesture it explains reads as the retry
    // having failed the same way, which is the one thing it cannot tell him.
    onError(null);
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

// The column door's own 503 ("could not read the sheet: …") is shown
// INLINE beside the buttons, never a toast -- the same "put the reason
// where the click lands" rule `.claude/rules/acceptance.md` states for a
// disabled control applies to a button whose action just failed. The CSV
// column is the one door here now, and a live sheet fetch is exactly the
// kind of thing that fails, so its message has a home.
function ScorecardExports() {
  const [error, setError] = useState(null);

  // The sheet column deliberately takes NO scope: its whole contract is one
  // line per live worksheet row of the community sheet, whatever the card
  // above it is scoped to. The CSV button was removed in round 19 (his
  // call); `GET /api/scorecard/export.csv` stays reachable by URL for
  // anyone who wants the card as a file, it simply has no button now.
  return html`<div class="scorecard-exports">
    <${CopyButton} className="scorecard-copy-column" label="Copy sheet column"
        onCopy=${async () => (await getJSON("/api/scorecard/column")).lines.join("\n")}
        onError=${setError} />
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

// The regions a runner goal may offer a time from -- the SAME control the
// Library wears ("We should reuse the JP/US selector in the library"), with a
// different default: the Library shows both, this follows his DETECTED region
// until he picks otherwise. Its note says what that choice is doing, since
// the region a goal comes from is invisible in the times themselves.
function regionNote(regions, detected) {
  if (regions.length > 1) return "Both regions · faster time wins";
  const only = regions[0] || detected;
  return only === detected
    ? `${only.toUpperCase()} only · your detected region`
    : `${only.toUpperCase()} only · you are graded on ${detected.toUpperCase()}`;
}

function ScorecardHead({ goal, groups, onOpen, coverage, onGoalChange, scopeId,
                         regions, detectedRegion, onRegionsChange }) {
  return html`<div class="scorecard-head">
    <h3>Scorecard</h3>
    <${RegionSwitch} values=${regions} onChange=${onRegionsChange}
        label="Goal regions" note=${regionNote(regions, detectedRegion)} />
    <${SearchSelect} value=${goalToValues(goal)} valueLabel=${goalToLabel(goal)}
        title="Pick one or more goals" groups=${groups} onOpen=${onOpen}
        onChange=${onGoalChange} align="right" multi />
    ${goal && coverage.covered < coverage.tiles
      ? html`<p class="meta scorecard-note">goal covers ${coverage.covered}/${coverage.tiles}</p>`
      : ""}
  </div>`;
}

export function Scorecard({ t, scopeId = "overall", openLibrary = null }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  // null = not fetched yet (the Runners group is lazy, see the header
  // comment); [] once fetched even if the sheet somehow named nobody.
  const [runners, setRunners] = useState(null);
  const [setCardsElement, cardsWidth] = useMeasuredWidth(0);
  // The picks' own names, by the index the server attributes tiles with --
  // so a dot's tooltip says WHICH pick set that star without any surface
  // re-deriving the winner.
  const sourceNames = (data && data.goal && data.goal.kind === "multi"
    ? (data.goal.sources || []).map(goalToLabel) : []);
  // entity_key -> goal_cs, UNSAVED. Lives here (not per-row) because a save
  // can gather edits made across several rows before he ever presses Save.
  const [pendingOverrides, setPendingOverrides] = useState({});
  const [saveBusy, setSaveBusy] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [saveError, setSaveError] = useState(null);

  // Fetches on mount, on a scope switch, and on t.mareloRev -- the Rank
  // tab's own staleness key (an attempt or a PB save must not leave this
  // card showing a stale gap while open during play). Only the SCOPE switch
  // clears the card first: blanking it on a staleness bump is what he saw as
  // the page "randomly refreshing" (round 20; ui/refetch.js carries the
  // measurement).
  useIdentityFetch(scopeId, t.mareloRev, (cleared) => {
    let alive = true;
    setError(null);
    if (cleared) setData(null);
    getJSON(`/api/scorecard?scope=${encodeURIComponent(scopeId)}`)
      .then((response) => alive && setData(response))
      .catch((err) => alive && setError(err));
    return () => { alive = false; };
  });

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

  async function onGoalChange(values) {
    // Picking a different base goal makes any unsaved edit ambiguous (it
    // was relative to whatever was active a moment ago) -- discard rather
    // than silently carry it onto a goal it was never made against.
    setPendingOverrides({});
    setSaveError(null);
    try {
      await send("PUT", "/api/scorecard/goal", valuesToGoal(values));
      setData(await getJSON(`/api/scorecard?scope=${encodeURIComponent(scopeId)}`));
    } catch (err) { setError(err); }
  }

  // The region pick is the server's, not this component's: it decides which
  // of a runner's times a goal may offer, so it has to be the same choice the
  // desktop GUI reads (the KV `scorecard_regions`, beside `scorecard_goal`).
  // The whole card re-derives on the way back rather than being patched here,
  // for the same reason the goal write does: the goal tiles, the coverage
  // count and the per-tile attribution all move together or not at all.
  async function onRegionsChange(next) {
    setSaveError(null);
    try {
      await send("PUT", "/api/scorecard/regions", { regions: next });
      setData(await getJSON(`/api/scorecard?scope=${encodeURIComponent(scopeId)}`));
    } catch (err) {
      setSaveError(err.message || String(err));
    }
  }

  // A legend pill's ×: the same write the picker makes, minus that pick --
  // one pick left stores as that single goal, none left clears it, and the
  // refetch re-grades every tile ("Everything should update accordingly").
  function removeSource(index) {
    const values = goalToValues(data && data.goal);
    if (index < 0 || index >= values.length) return;
    values.splice(index, 1);
    onGoalChange(values);
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
              regions=${data.regions || ["us"]}
              detectedRegion=${data.detected_region || "us"}
              onRegionsChange=${onRegionsChange}
              coverage=${data.goal_coverage} onGoalChange=${onGoalChange} />
            ${pendingCount > 0
              ? html`<${ScorecardSaveBar} pendingCount=${pendingCount}
                    initialName=${data.goal && data.goal.kind === "custom" ? data.goal.name : ""}
                    busy=${saveBusy} error=${saveError}
                    onSave=${saveCustomGoal} onDiscard=${discardOverrides} />`
              : ""}
            <${GoalLegend} goal=${data.goal} onRemove=${removeSource} />
            <div class="score-cards" ref=${setCardsElement}
                data-cols=${String(columnCountFor(cardsWidth))}
                style=${`--score-rows:${Math.max(1, ...cardColumns(
                  displayData.rows, columnCountFor(cardsWidth))
                  .map((column) => column.rows.length))}`}>
              ${cardColumns(displayData.rows, columnCountFor(cardsWidth))
                .map((column, columnIndex) => html`<div
                  key=${columnIndex} class="score-col">
                ${column.rows.map((row) => html`<${ScoreCard} key=${row.label}
                    t=${t} row=${row} removing=${removing}
                    onRemove=${removeRow} onGoalOverride=${handleGoalOverride}
                    onOpenLibrary=${openLibrary} sourceNames=${sourceNames} />`)}
              </div>`)}
            </div>
            ${/* Round 25: the export sits UNDER the cards, not in the head --
                 "The button should go at the bottom, underneath all the
                 cards. I just think it would look better there." Its error
                 slot travels with it, so the reason a copy failed still
                 lands where the click did. */""}
            <${ScorecardExports} />`}
  </div>`;
}
