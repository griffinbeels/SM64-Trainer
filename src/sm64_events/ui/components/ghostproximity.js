// Native world-space readability controls; writes share the ghost session's queue.
import { h } from "preact";
import htm from "htm";
import { SWITCH_LABELS } from "../ghostsession.js";

const html = htm.bind(h);

// `range` is the server's limits.proximity: the distance slider and the floor
// an overlapping ghost keeps (ghost/settings.py), never restated here.
// `switches` are the on/off rows whose words say `section: "overlap"`
// (ghostsession.js SWITCH_LABELS): they follow this fade, so they sit under it
// and are omitted while it is off. Unknown is drawn unknown and cannot be pressed.
export function GhostProximity({ active, view, value, range, draft, write, queue, switches = [] }) {
  if (!active || view !== "mario" || !value || !range) return null;
  const shown = { ...value, ...draft };
  const rows = switches.map((row) => ({ setting: row.setting, words: SWITCH_LABELS[row.setting],
    on: row.draft == null ? row.value : Boolean(row.draft) }));
  const sound = rows.some((row) => row.setting === "overlap_sound" && row.on);
  return html`<details class="ghost-proximity">
    <summary>Overlap fading</summary>
    <div class="ghost-proximity-body">
      <label class="ghost-field ghost-overlap-fade">
        <input type="checkbox" checked=${shown.enabled}
          onchange=${(event) => write({ proximity: { enabled: event.target.checked } })} />
        Fade the ghost near you
      </label>
      ${shown.enabled && html`<label class="ghost-field">
        <span class="ghost-field-name">Extra distance</span>
        <input class="ghost-distance" type="range" min=${range.min} max=${range.max} step=${range.step}
          value=${shown.extra_distance} aria-label="Extra ghost fade distance"
          oninput=${(event) => queue({ proximity: { extra_distance: Number(event.target.value) } })} />
        <span class="ghost-field-value">${shown.extra_distance} units</span>
      </label>`}
      ${shown.enabled && rows.map(({ setting, on, words }) => html`<label key=${setting}
          class="ghost-field ${words.className}" title=${on == null ? words.waiting : words.group}>
        <input type="checkbox" checked=${Boolean(on)} disabled=${on == null}
          onchange=${(event) => write({ [setting]: event.target.checked })} />
        ${words.label}
      </label>`)}
      <p class="ghost-proximity-help">At the same position, the ghost keeps ${Math.round(range.floor * 100)}% of your Visibility. At zero extra
        distance, it reaches your Visibility setting just beyond Mario’s body.
        More distance makes the fade gentler. One Mario width is about 74 units.
        ${shown.enabled && sound && " Its sound fades the same way, and a sound you both make on the same frame plays once."}</p>
    </div>
  </details>`;
}
