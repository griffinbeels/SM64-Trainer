// src/sm64_events/ui/tunecontrols.js — the control an inspector renders per
// registry row. Extracted from tune.js on 2026-07-28 when a second surface
// (the overall rank-up) got its own inspector: one renderer, so the two
// pages cannot disagree about what a tunable looks like or when it counts as
// changed. `defaults` is a prop rather than an import for exactly that
// reason -- the two inspectors read different registries (climbtuning.js /
// marelotuning.js), so a module-level import would only ever be right for one
// of them.
import { h } from "preact";
import htm from "htm";
import { RANK_NAMES, DIVISION_NUMERALS, capName, divisionDigit, rankAt,
         rankPosition } from "./components/caps.js";

const html = htm.bind(h);

// Bottom-first, so the pickers read Capless -> Mario the way the ladder does.
const TIERS = [...RANK_NAMES].reverse();

// The from-rank/to-rank picker both inspectors need. Moved here rather than
// staying local to tune.js (2026-07-28, when the overall rank-up got its own
// inspector page): `tune.js` is a served PAGE with a module-level `render()`
// call of its own, so importing anything from it -- even a pure component --
// would also execute that render as an import side effect and mount the
// climb inspector into whatever page did the importing. tunecontrols.js has
// no such side effect, which is the whole reason it exists.
export function LevelPicker({ label, level, onChange, min = 0 }) {
  // Named tierKey, never `tier`: tests/test_ui_cap_names.py forbids putting a
  // raw tier key on screen, and it reads the identifier rather than the value.
  // A select's `value` is not on screen, but the guard cannot know that and
  // a name that reads as displayable is the wrong name here anyway.
  const { tier: tierKey, division } = rankAt(level);
  const pick = (nextTier, nextDivision) =>
    onChange(Math.max(min, rankPosition(nextTier, nextDivision, 0)));
  return html`<div>
    <label>${label}</label>
    <select value=${tierKey} onchange=${(e) => pick(e.target.value, division)}>
      ${TIERS.map((one) => html`<option value=${one}
        disabled=${rankPosition(one, "V", 0) + 4 < min}>${capName(one)}</option>`)}
    </select>
    <select value=${division} onchange=${(e) => pick(tierKey, e.target.value)}>
      ${DIVISION_NUMERALS.map((one) => html`<option value=${one}
        disabled=${rankPosition(tierKey, one, 0) < min}>${divisionDigit(one)}</option>`)}
    </select>
  </div>`;
}

export function Control({ name, row, value, defaults, onChange }) {
  const changed = value !== defaults[name];
  const cls = `tune-row${changed ? " is-changed" : ""}`;
  if (row.options) {
    return html`<div class=${cls}>
      <label title=${row.why} for=${name}>${row.label}</label>
      <select id=${name} value=${value} onchange=${(e) => onChange(e.target.value)}>
        ${Object.entries(row.options).map(([key, text]) =>
          html`<option value=${key}>${text}</option>`)}
      </select>
    </div>`;
  }
  const set = (raw) => {
    const parsed = Number(raw);
    if (Number.isFinite(parsed)) onChange(parsed);
  };
  return html`<div class=${cls}>
    <label title=${`${row.why}\nshipped default: ${defaults[name]}${row.unit}`}
      for=${name}>${row.label}${row.unit === "ms" ? "" : ` (${row.unit || "n"})`}</label>
    <input id=${name} type="number" value=${value} min=${row.min} max=${row.max}
      step=${row.step} onchange=${(e) => set(e.target.value)} />
    <input type="range" value=${value} min=${row.min} max=${row.max} step=${row.step}
      oninput=${(e) => set(e.target.value)} />
  </div>`;
}

// The group loop, also lifted from tune.js's Inspector so both pages render
// their sections identically. `rows` is every row the caller wants rendered
// -- for the climb inspector that is CHOICES and TUNABLES already merged
// (`{ ...CHOICES, ...TUNABLES }`, so a choice still renders before the
// tunables in a group it shares with any, matching the original render
// order); the overall rank-up's registry has no CHOICES at all, so it passes
// MARELO_TUNABLES alone.
//
// Returns an ARRAY of group `<div>`s rather than a single wrapped element --
// exactly what a caller interpolating `${...}` into a surrounding template
// already expects (the same shape header.js's own `NAV_GROUPS.map` returns).
export function ControlGroups({ groups, rows, values, defaults, onChange }) {
  return groups.map((group) => html`<div>
    <h2>${group}</h2>
    ${Object.entries(rows).filter(([, row]) => row.group === group)
      .map(([name, row]) => html`<${Control} name=${name} row=${row}
        value=${values[name]} defaults=${defaults}
        onChange=${(value) => onChange(name, value)} />`)}
  </div>`);
}
