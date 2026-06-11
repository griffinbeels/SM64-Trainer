// src/sm64_events/ui/components/statmenu.js
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";

const html = htm.bind(h);
const keyOf = (s) => `${s.key}:${JSON.stringify(s.params || {})}`;

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
  const offers = registry.flatMap((d) => d.key === "avg_last_n"
    ? [10, 25, 50, 100].map((n) => ({ key: d.key, params: { n }, label: `Avg last ${n}` }))
    : [{ key: d.key, params: d.params, label: d.label }]);

  return html`<div class="popover" style="right:1rem">
    ${offers.map((o) => html`<label style="display:block">
      <input type="checkbox"
             checked=${selected.some((s) => keyOf(s) === keyOf(o))}
             onchange=${() => toggle({ key: o.key, params: o.params })} />
      ${o.label}</label>`)}
    <div style="margin-top:.5rem"><button onclick=${apply}>Apply</button>
      <button onclick=${close}>Cancel</button></div>
  </div>`;
}
