// src/sm64_events/ui/components/versionswitch.js — the JP/US visual toggle.
//
// One small control shared by the Library hero and the Rank standards panel
// toolbar (spec 2026-08-15-game-version-design). Two segments, JP always on
// the LEFT, US on the RIGHT ("JP sits LEFT of US on every switch" — his
// ruling). Flipping it is VISUAL ONLY: it changes which version's standards
// a surface DISPLAYS, never what the player is actually GRADED on — that is
// the separate Game version setting (Auto-detect / JP / US) in the settings
// drawer, backed by core/modes.py. `note` is how a caller says so when the
// shown version differs from the grading one (explain, never dim — project
// rule); this component only renders whatever string it is handed.
import { h } from "preact";
import htm from "htm";

const html = htm.bind(h);

const SEGMENTS = [
  { value: "jp", text: "JP" },
  { value: "us", text: "US" },
];

export function VersionSwitch({ value, onChange, note = null, label = "Game version" }) {
  return html`<div class="version-switch" role="group" aria-label=${label}>
    ${SEGMENTS.map((segment) => html`<button type="button" key=${segment.value}
        class="version-switch-seg" aria-pressed=${value === segment.value}
        onclick=${() => { if (value !== segment.value) onChange(segment.value); }}>${segment.text}</button>`)}
    ${note ? html`<span class="version-switch-note">${note}</span>` : null}
  </div>`;
}
