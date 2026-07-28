// src/sm64_events/ui/components/statmenu.js
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";

const html = htm.bind(h);
// Selection identity — MUST match selection_id() in stats/registry.py, and
// tests/test_cross_language_parity.py is what makes that "must" real (it
// extracts this very declaration and runs it against the Python one). Disagree
// and ticking a chip's checkbox adds a duplicate instead of toggling the chip
// already on the card. Detail:
// avg_last_n is parameterized by n (each N its own chip); every other stat
// is identified by key alone, so a stored param variant (e.g. a legacy
// custom failures set) still matches its checkbox and unchecking removes
// ALL stored variants of that stat.
const keyOf = (s) =>
  s.key === "avg_last_n" ? `${s.key}:${(s.params || {}).n}` : s.key;

// Dust-trick stats (stats/registry.py `_dust_rate` family). Hidden from the
// menu, and their chips from the practice cards, unless the Settings
// "Dust-trick counts" toggle (t.showDust) is on — detection is still being
// tuned (2026-07-24). Stored selections are untouched; they reappear when
// the toggle comes back on.
export const DUST_STAT_KEYS = new Set(["dustless_rate", "dustless_jump_rate"]);

export function StatMenu({ t, close }) {
  const [registry, setRegistry] = useState([]);
  const [selected, setSelected] = useState(t.view.stat_menu);
  useEffect(() => { getJSON("/api/stats/registry").then(setRegistry); }, []);

  function toggle(entry) {
    const k = keyOf(entry);
    setSelected((sel) => sel.some((s) => keyOf(s) === k)
      ? sel.filter((s) => keyOf(s) !== k) : [...sel, entry]);
  }

  async function apply() {
    await send("PUT", "/api/statmenu", { selections: selected });
    close(); t.refresh();
  }

  // offer avg_last_n at a few useful Ns plus every parameterless stat
  const offers = registry
    .filter((d) => t.showDust || !DUST_STAT_KEYS.has(d.key))
    .flatMap((d) => d.key === "avg_last_n"
    ? [10, 25, 50, 100].map((n) => ({ key: d.key, params: { n }, label: `Avg last ${n}` }))
    : [{ key: d.key, params: d.params, label: d.label }]);

  return html`<div class="popover">
    ${offers.map((o) => html`<label style="display:block">
      <input type="checkbox"
             checked=${selected.some((s) => keyOf(s) === keyOf(o))}
             onchange=${() => toggle({ key: o.key, params: o.params })} />
      ${o.label}</label>`)}
    <div style="margin-top:.5rem"><button onclick=${apply}>Apply</button>
      <button onclick=${close}>Cancel</button></div>
  </div>`;
}
