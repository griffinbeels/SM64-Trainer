// src/sm64_events/ui/components/aboutpage.js — the About tab.
//
// Two views behind one switch: How it works (components/howitworks.js) and
// Credits (drawn here from ui/credits.js). The choice is a per-viewer
// convenience, remembered in localStorage.
import { h } from "preact";
import { useState } from "preact/hooks";
import htm from "htm";
import { Icon } from "./icons.js";
import { HowItWorks } from "./howitworks.js";
import { CREDIT_GROUPS, TESTERS, twitchUrl } from "../credits.js";

const html = htm.bind(h);

const VIEW_KEY = "about_view";
const VIEWS = [["how", "How it works"], ["credits", "Credits"]];

function storedView() {
  try {
    const v = localStorage.getItem(VIEW_KEY);
    return VIEWS.some(([id]) => id === v) ? v : "how";
  } catch { return "how"; }
}

function CreditRow({ item }) {
  return html`<li class="credit-row">
    <div class="credit-head">
      ${item.url
        ? html`<a href=${item.url} target="_blank" rel="noopener noreferrer">${item.name}</a>`
        : html`<b>${item.name}</b>`}
      ${item.by && html`<span class="credit-by">${item.by}</span>`}
    </div>
    ${item.what && html`<p>${item.what}</p>`}
  </li>`;
}

function Testers() {
  return html`<section class="practice-card credit-card credit-testers">
    <div class="workshop-card-heading">
      <div><span class="eyebrow">Thank you</span><h3>Testers</h3></div>
      <span class="count-badge">${TESTERS.length}</span>
    </div>
    <ul class="tester-list">
      ${TESTERS.map((tester) => html`<li key=${tester.twitch}>
        <a class="tester" href=${twitchUrl(tester.twitch)} target="_blank" rel="noopener noreferrer">
          <img src="/ui/assets/community/twitch-glitch-white.svg" alt="" />
          <b>${tester.name}</b>
          <span>twitch.tv/${tester.twitch}</span>
        </a>
      </li>`)}
    </ul>
  </section>`;
}

function Credits() {
  return html`<div class="credit-grid">
    <${Testers} />
    ${CREDIT_GROUPS.map((group) => html`<section key=${group.id}
        class=${`practice-card credit-card credit-${group.id}`}>
      <div class="workshop-card-heading">
        <div><span class="eyebrow">${group.eyebrow}</span><h3>${group.title}</h3></div>
      </div>
      ${group.lead && html`<p class="credit-lead">${group.lead}</p>`}
      <ul class=${`credit-list ${group.compact ? "is-compact" : ""}`}>
        ${group.items.map((item) => html`<${CreditRow} key=${item.name} item=${item} />`)}
      </ul>
    </section>`)}
  </div>`;
}

export function AboutPage({ t }) {
  const [view, setViewState] = useState(storedView);
  const setView = (id) => {
    setViewState(id);
    try { localStorage.setItem(VIEW_KEY, id); } catch { /* storage unavailable */ }
  };
  return html`<div class="workshop-page about-page">
    <header class="practice-card workshop-hero">
      <div class="workshop-title">
        <span class="workshop-title-icon"><${Icon} name="about" size=${22} /></span>
        <div>
          <span class="eyebrow">SM64 Trainer</span>
          <h2>About</h2>
        </div>
      </div>
      <div class="version-switch" role="group" aria-label="About view">
        ${VIEWS.map(([id, label]) => html`<button key=${id} type="button"
            class="version-switch-seg" aria-pressed=${view === id}
            onclick=${() => setView(id)}>${label}</button>`)}
      </div>
    </header>
    ${view === "how" ? html`<${HowItWorks} t=${t} showCredits=${() => setView("credits")} />`
      : html`<${Credits} />`}
  </div>`;
}
