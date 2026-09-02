// src/sm64_events/ui/components/versionswitch.js — the shared JP/US region
// controls (spec 2026-08-15-game-version-design, "Shared primitive"), in the
// two cardinalities the app actually has. Both draw their segments as the
// flag `regionflag.js` owns (round 24 item 3) with the word kept in
// `aria-label`/`title`, so the picture is never the only carrier.
//
// `VersionSwitch` — pick ONE. A ladder TABLE can only be one region's, so
// the standards panel explores the other version by swapping which ladder is
// drawn. His 2026-08-15 ruling retired the Library's old per-section
// `library-jp-toggle` chip in favour of exactly this: one page-level switch
// every section reads, "for fun exploration of the differences."
//
// `RegionSwitch` — pick ONE OR BOTH, round 24 items 2 and 4, his mechanism
// verbatim: "the way the selector works is that it's a toggle for both
// buttons... There must be at least one region enabled at all times. This
// gives 3 valid states: US only, JP only, or JP and US combined." The
// at-least-one rule lives HERE rather than in each caller, because a control
// that can reach an impossible state and relies on two pages to refuse it is
// the same shape as two pages each deriving one value their own way. The
// only-on toggle is `disabled`, so the reason is where the click lands
// rather than in a message that arrives after the mistake.
//
// Kept import-free of the store (no ../store.js, no ../api.js) so a second
// caller -- the standards panel, the Scorecard -- can mount it unmodified:
// the same reason librarymodel.js and ui/exchange.js stay import-free of
// anything but their own inputs.
import { h } from "preact";
import htm from "htm";
import { REGIONS, RegionFlag, regionLabel } from "./regionflag.js";

const html = htm.bind(h);

function Note({ note }) {
  return typeof note === "string" && note.length
    ? html`<span class="version-switch-note">${note}</span>` : "";
}

/**
 * value    "jp" | "us" -- the segment currently pressed.
 * onChange (next) => void -- called with "jp"/"us" only when it differs
 *          from `value`; clicking the already-pressed segment is a no-op.
 * note     optional trailing text (e.g. "Viewing JP standards · you are
 *          graded on US") -- rendered only when it is a real, non-empty
 *          string, never for `null`/`undefined`/`""`.
 * label    the group's accessible name; callers with more than one switch
 *          on a page should pass something more specific than the default.
 */
export function VersionSwitch({ value, onChange, note = null, label = "Game version" }) {
  const pick = (next) => { if (next !== value) onChange(next); };
  return html`<div class="version-switch" role="group" aria-label=${label}>
    ${REGIONS.map((region) => html`<button key=${region} type="button"
        class="version-switch-seg" aria-pressed=${value === region}
        aria-label=${regionLabel(region)} title=${`${regionLabel(region)} standards`}
        onclick=${() => pick(region)}>
      <${RegionFlag} version=${region} size=${17} title="" />
    </button>`)}
    <${Note} note=${note} />
  </div>`;
}

/**
 * values   an array of "us"/"jp" -- every region currently ON. Order does
 *          not matter to the control; it always draws US then JP.
 * onChange (nextArray) => void -- the new set, in REGIONS order. Never
 *          called with an empty array: turning the last one off is refused
 *          in here (the button is disabled and the click is a no-op), so no
 *          caller has to defend against a state that means "show nothing".
 * note     as above.
 * label    the group's accessible name.
 */
export function RegionSwitch({ values, onChange, note = null, label = "Regions" }) {
  const on = new Set((values || []).map((value) => String(value).toLowerCase()));
  // An empty or unrecognised set reads as US -- the same fallback
  // `regionLabel` and `ratings.py` use, and the one state this control is
  // not allowed to render.
  if (![...on].some((value) => REGIONS.includes(value))) on.add("us");
  const toggle = (region) => {
    const next = REGIONS.filter((candidate) => (candidate === region
      ? !on.has(region) : on.has(candidate)));
    if (next.length) onChange(next);
  };
  return html`<div class="version-switch is-multi" role="group" aria-label=${label}>
    ${REGIONS.map((region) => {
      const pressed = on.has(region);
      const sole = pressed && on.size === 1;
      return html`<button key=${region} type="button" class="version-switch-seg"
          aria-pressed=${pressed} disabled=${sole}
          aria-label=${regionLabel(region)}
          title=${sole
            ? `${regionLabel(region)} only — at least one region stays on`
            : pressed ? `Hide ${regionLabel(region)}` : `Also show ${regionLabel(region)}`}
          onclick=${() => toggle(region)}>
        <${RegionFlag} version=${region} size=${17} title="" />
      </button>`;
    })}
    <${Note} note=${note} />
  </div>`;
}

/**
 * The region whose LADDER a surface draws when `values` may hold both --
 * `preferred` if it is on, otherwise the other one that is. A ladder table
 * and a set of rank bands are one region's cutoffs or they are nothing, so
 * "both" narrows to one HERE rather than in each surface's own head.
 */
export function primaryRegion(values, preferred = "us") {
  const on = (values || []).map((value) => String(value).toLowerCase());
  if (on.includes(preferred)) return preferred;
  return REGIONS.find((region) => on.includes(region)) || preferred;
}
