// src/sm64_events/ui/app.js — root: header + tabs
import { h, render } from "preact";
import { useState } from "preact/hooks";
import htm from "htm";
import { useTracker } from "./store.js";
import { Header } from "./components/header.js";
import { Practice } from "./components/practice.js";
import { Feed } from "./components/feed.js";
import { Segments } from "./components/segments.js";
import { Routes } from "./components/routes.js";
import { Run } from "./components/runview.js";
import { Compare } from "./components/compare.js";
import { UpdatePopup } from "./components/update.js";

const html = htm.bind(h);
const TABS = ["Practice", "Segments", "Routes", "Run", "Compare", "Live feed"];

function App() {
  const t = useTracker();
  const [tab, setTab] = useState("Practice");
  const [compareIntent, setCompareIntent] = useState(null);
  const openCompare = (intent) => { setCompareIntent(intent); setTab("Compare"); };
  return html`
    <h1>SM64 Trainer</h1>
    <${Header} t=${t} />
    <div class="tabs">
      ${TABS.map((name) => html`
        <div class="tab ${tab === name ? "on" : ""}"
             onclick=${() => setTab(name)}>${name}</div>`)}
    </div>
    <div class="pane">
      ${/* Compare stays MOUNTED across tab switches (hidden when inactive) so
           the loaded videos + sync survive leaving and returning; `active`
           drives its feed/availability refresh. */""}
      <div style=${tab === "Compare" ? "" : "display:none"}>
        <${Compare} t=${t} intent=${compareIntent}
          clearIntent=${() => setCompareIntent(null)} active=${tab === "Compare"} />
      </div>
      ${tab === "Practice" ? html`<${Practice} t=${t} openCompare=${openCompare} />`
        : tab === "Segments" ? html`<${Segments} t=${t} />`
        : tab === "Routes" ? html`<${Routes} t=${t} />`
        : tab === "Run" ? html`<${Run} t=${t} />`
        : tab === "Live feed" ? html`<${Feed} t=${t} />`
        : null}
    </div>
    <${UpdatePopup} t=${t} />`;
}

render(html`<${App} />`, document.getElementById("app"));
