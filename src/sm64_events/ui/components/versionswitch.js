// src/sm64_events/ui/components/versionswitch.js — the shared JP/US game-
// version switch (spec 2026-08-15-game-version-design, "Shared primitive").
//
// One two-segment pill, JP left, US right, used identically wherever a page
// lets you EXPLORE the other version's ladder without touching what actually
// grades an attempt -- the spec's own Concepts section: "Version switch --
// the two visual JP/US toggles (Library page, standards panel). Default =
// effective version; changing one changes nothing graded." His 2026-08-15
// ruling retired the Library's old per-section `library-jp-toggle` chip in
// favour of exactly this: ONE page-level switch every section reads, "for
// fun exploration of the differences."
//
// Kept import-free of the store (no ../store.js, no ../api.js) so a second
// caller -- the standards panel -- can mount it unmodified: the same reason
// librarymodel.js and ui/exchange.js stay import-free of anything but their
// own inputs.
import { h } from "preact";
import htm from "htm";

const html = htm.bind(h);

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
    <button type="button" class="version-switch-seg" aria-pressed=${value === "jp"}
        onclick=${() => pick("jp")}>JP</button>
    <button type="button" class="version-switch-seg" aria-pressed=${value === "us"}
        onclick=${() => pick("us")}>US</button>
    ${typeof note === "string" && note.length
      ? html`<span class="version-switch-note">${note}</span>` : ""}
  </div>`;
}
