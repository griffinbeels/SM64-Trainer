// Native world-space readability controls; writes share the ghost session's queue.
import { h } from "preact";
import htm from "htm";

const html = htm.bind(h);

export function GhostProximity({ active, view, value, draft, write, queue }) {
  if (!active || view !== "mario" || !value) return null;
  const shown = { ...value, ...draft };
  return html`<details class="ghost-proximity">
    <summary>Overlap fading</summary>
    <div class="ghost-proximity-body">
      <label class="ghost-field">
        <input type="checkbox" checked=${shown.enabled}
          onchange=${(event) => write({ proximity: { enabled: event.target.checked } })} />
        Fade the ghost near you
      </label>
      ${shown.enabled && html`<label class="ghost-field">
        <span class="ghost-field-name">Extra distance</span>
        <input class="ghost-distance" type="range" min="0" max="400" step="10"
          value=${shown.extra_distance} aria-label="Extra ghost fade distance"
          oninput=${(event) => queue({ proximity: { extra_distance: Number(event.target.value) } })} />
        <span class="ghost-field-value">${shown.extra_distance} units</span>
      </label>`}
      <p class="ghost-proximity-help">At the same position, the ghost disappears. At zero extra
        distance, it reaches your Visibility setting just beyond Mario’s body.
        More distance makes the fade gentler. One Mario width is about 74 units.</p>
    </div>
  </details>`;
}
