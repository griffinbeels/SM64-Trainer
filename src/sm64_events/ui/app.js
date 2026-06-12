// src/sm64_events/ui/app.js — root: header + tabs
import { h, render } from "preact";
import { useState } from "preact/hooks";
import htm from "htm";
import { useTracker } from "./store.js";
import { Header } from "./components/header.js";
import { Practice } from "./components/practice.js";
import { Feed } from "./components/feed.js";
import { Segments } from "./components/segments.js";

const html = htm.bind(h);
const TABS = ["Practice", "Segments", "Routes", "Live feed"];

function App() {
  const t = useTracker();
  const [tab, setTab] = useState("Practice");
  return html`
    <h1>SM64 Practice Tracker</h1>
    <${Header} t=${t} />
    <div class="tabs">
      ${TABS.map((name) => html`
        <div class="tab ${tab === name ? "on" : ""}"
             onclick=${() => name !== "Routes" && setTab(name)}
             title=${name === "Routes" ? "Phase 4" : ""}
             style=${name === "Routes" ? "opacity:.3;cursor:default" : ""}>${name}</div>`)}
    </div>
    <div class="pane">
      ${tab === "Practice" ? html`<${Practice} t=${t} />`
        : tab === "Segments" ? html`<${Segments} t=${t} />`
        : html`<${Feed} t=${t} />`}
    </div>`;
}

render(html`<${App} />`, document.getElementById("app"));
