// src/sm64_events/ui/components/librarynav.js — the Library tab's own
// course -> target navigation.
//
// task-3-caveats.md (2026-08-07) corrected the plan here: `.picker-grid` /
// `.picker-cell` do not exist anywhere in this repo. The real picker markup
// is entitymodal.js's `PickerDialog`, and it is a DIALOG (an overlay with its
// own close button) -- the Library's navigation is the PAGE, so this module
// renders its OWN inline markup instead of mounting a dialog with nowhere to
// close to. It reuses the picker's ingredients everywhere it can, so the grid
// looks identical without pretending to be a modal:
//   - `PracticeCell` (components/practicecell.js) is the exact cell the
//     practice banner and the picker both render, `rankBadge=true` so an
//     unranked cell costs no in-flow row (same choice the picker makes for
//     every cell it has no rank data for yet);
//   - the `.entity-grid`/`.entity-section-head`/`.entity-back` CSS classes
//     (index.html) are the picker's own grid geometry and drill-in chrome.
//
// Two layers, like the picker's own depth=2 (entitymodal.js): a grid of
// GROUPS (the 15 numbered courses, Castle Secret Stars, Bowser Courses, the
// three Castle Movements groups -- 20 today), then that group's TARGETS.
// Unlike the picker this is inline page state, not dialog state, so "drilled
// into a group" is this component's own `openGroupKey`, never a second
// PickerDialog mounted on top of the tab.
import { h } from "preact";
import { useState } from "preact/hooks";
import htm from "htm";
import { PracticeCell } from "./practicecell.js";
import { Icon } from "./icons.js";
import { searchTargets, resultSub } from "../librarysearch.js";

const html = htm.bind(h);

// A sheet target that maps to no entity still needs SOMETHING in the sub-line
// -- caveat 5: Castle Movements (`miss_reason: "castle_movement"`) and stage
// RTAs (`miss_reason: "route"`) are shown, marked, never claiming to be a
// practiceable entity. Anything else with an entity_key gets its coverage
// count instead, which is the number a runner actually cares about here.
function targetSub(target) {
  if (target.miss_reason === "castle_movement")
    return html`<span class="chip">Browse only</span>`;
  if (target.miss_reason === "route")
    return html`<span class="chip">Stage route</span>`;
  const word = target.entries === 1 ? "entry" : "entries";
  return `${target.entries} ${word}`;
}

/**
 * index    GET /api/library body -- {sheet_revision, groups:[{group,
 *          targets:[{index, section, label, entity_key, miss_reason,
 *          approaches, subsections, entries}]}]}
 * onPick   (entityKeyOrTargetIndex) => void -- a target WITH an entity_key
 *          hands back that string (so the caller can jump straight to
 *          `/api/library/entity/{key}`, the same door auto-open uses); a
 *          Castle Movement or a stage RTA has no entity, so it hands back
 *          its numeric `index` into `index.groups[].targets[]` instead --
 *          the caller already holds that same list and can look the row up
 *          without ever calling `/api/library/target/{index}` (Task 4's own
 *          door, not this task's to open).
 * iconFor  (entityKeyOrNull) => URL -- built by the caller (library.js),
 *          which is the only place holding the tracker store `t` the real
 *          art chain needs (entityicons.js::entityIconSrc). Threading `t`
 *          itself down here would make this module know about the store for
 *          no reason beyond art, so it takes the resolved answer instead --
 *          the same split PickerDialog draws between its own grid and its
 *          caller's `iconFor`.
 */
export function LibraryNav({ index, onPick, iconFor }) {
  const [openGroupKey, setOpenGroupKey] = useState(null);
  const [query, setQuery] = useState("");

  if (!index)
    return html`<div class="library-courses"><p class="meta">Loading the library…</p></div>`;

  const openGroup = index.groups.find((group) => group.group === openGroupKey) || null;

  // THE SEARCH BOX (round 12). Rendered above whichever layer is showing, and
  // typing REPLACES that layer with the results -- his call over a panel
  // floating on a dimmed grid, so the page never holds two scrolling regions
  // at once (the same rule that shaped the entity picker: "the user clicks on
  // the course -> clicks on the star / segment. No scrolling"). Clearing the
  // box puts back exactly what was there, including a drilled-into group,
  // because `openGroupKey` is never touched by searching -- a search that
  // silently dropped you back to the top would punish a mistyped letter.
  const hits = searchTargets(index, query);
  const searching = query.trim().length > 0;
  const searchBox = html`<div class="library-find">
    <input type="search" class="library-find-input" value=${query}
      placeholder="type to search for a strat…"
      aria-label="Search the library"
      oninput=${(event) => setQuery(event.target.value)} />
    ${searching && html`<button type="button" class="library-find-clear"
        title="Clear the search" aria-label="Clear the search"
        onclick=${() => setQuery("")}>✕</button>`}
  </div>`;

  if (searching)
    return html`<div class="library-courses library-searching">
      ${searchBox}
      ${hits.length === 0
        ? html`<p class="meta library-find-empty">Nothing in the library matches
            ${" "}“${query.trim()}”.</p>`
        : html`<div class="library-results">
            ${hits.map((hit) => html`<button type="button" class="library-result"
              key=${hit.target.index}
              title=${`${hit.group} — ${hit.target.label}`}
              onclick=${() => onPick(hit.target.entity_key || hit.target.index)}>
              <img class="library-result-icon" src=${iconFor(hit.target.entity_key)}
                alt="" />
              <span class="library-result-text">
                <span class="library-result-name">${hit.group}${" — "}${hit.target.label}</span>
                <span class="library-result-sub">${resultSub(hit)}</span>
              </span>
            </button>`)}
          </div>`}
    </div>`;

  if (openGroup)
    return html`<div class="library-group">
      ${searchBox}
      <button type="button" class="entity-back" onclick=${() => setOpenGroupKey(null)}>
        <${Icon} name="chevron" size=${15} /> Back
      </button>
      <div class="entity-section-head"><b>${openGroup.group}</b></div>
      <div class="entity-grid">
        ${openGroup.targets.map((target) => html`<${PracticeCell} key=${target.index}
          iconSrc=${iconFor(target.entity_key)}
          rankBadge=${true}
          name=${target.label}
          sub=${targetSub(target)}
          title=${target.label}
          onPick=${() => onPick(target.entity_key || target.index)} />`)}
      </div>
    </div>`;

  return html`<div class="library-courses">
    ${searchBox}
    <div class="entity-grid">
      ${index.groups.map((group) => html`<${PracticeCell} key=${group.group}
        iconSrc=${iconFor(groupEntityKey(group))}
        rankBadge=${true}
        name=${group.group}
        sub=${`${group.targets.length} to browse`}
        title=${group.group}
        onPick=${() => setOpenGroupKey(group.group)} />`)}
    </div>
  </div>`;
}

// A group's own art borrows its first entity-mapped target's key -- the same
// "one representative cell's art stands for the group" choice
// targetpicker.js's course cells already make. A Castle Movements group maps
// to no entity anywhere inside it, so `iconFor(null)` is asked instead, which
// resolves to the generic star (entities.js's own documented fallback) rather
// than a second lookup table living here.
function groupEntityKey(group) {
  const withEntity = group.targets.find((target) => target.entity_key);
  return withEntity ? withEntity.entity_key : null;
}
