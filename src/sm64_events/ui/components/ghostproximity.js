// Native world-space readability controls; writes share the ghost session's queue.
import { h } from "preact";
import htm from "htm";

const html = htm.bind(h);

// `range` is the server's limits.proximity: the distance slider and the floor
// an overlapping ghost keeps (ghost/settings.py), never restated here.
export function GhostProximity({ active, value, range, draft, write, queue }) {
  if (!active || !value || !range) return null;
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
        <input class="ghost-distance" type="range" min=${range.min} max=${range.max} step=${range.step}
          value=${shown.extra_distance} aria-label="Extra ghost fade distance"
          oninput=${(event) => queue({ proximity: { extra_distance: Number(event.target.value) } })} />
        <span class="ghost-field-value">${shown.extra_distance} units</span>
      </label>`}
      <p class="ghost-proximity-help">At the same position, the ghost keeps ${Math.round(range.floor * 100)}% of your Visibility. At zero extra
        distance, it reaches your Visibility setting just beyond Mario’s body.
        More distance makes the fade gentler. One Mario width is about 74 units.</p>
    </div>
  </details>`;
}
