// src/sm64_events/ui/components/resourcespage.js — the Resources tab.
//
// Draws ui/resources.js and nothing else: every card, link and rule lives in
// that registry, so changing what the tab lists never touches this file.
import { h } from "preact";
import htm from "htm";
import { Icon } from "./icons.js";
import { youtubeThumb } from "./librarymodel.js";
import { RESOURCE_SECTIONS, RULE_BLOCKS, STAGE_RTA_BANNED } from "../resources.js";

const html = htm.bind(h);

// One hit target per row: the whole row is the link.
export function ExternalRow({ url, mark, name, note, by }) {
  return html`<a class="res-row" href=${url} target="_blank" rel="noopener noreferrer">
    <span class="res-mark" aria-hidden="true">
      ${mark ? html`<img src=${mark} alt="" />` : html`<${Icon} name="link" size=${17} />`}
    </span>
    <span class="res-text">
      <b>${name}</b>
      ${(note || by) && html`<span>${by ? `by ${by}` : note}</span>`}
    </span>
    <${Icon} name="external" size=${15} className="res-out" />
  </a>`;
}

function VideoRow({ url, name, by }) {
  const thumb = youtubeThumb(url);
  return html`<a class="res-video" href=${url} target="_blank" rel="noopener noreferrer">
    <span class="res-thumb" aria-hidden="true">
      ${thumb && html`<img src=${thumb} alt="" loading="lazy"
        onerror=${(e) => { e.currentTarget.style.visibility = "hidden"; }} />`}
      <span class="res-play"><${Icon} name="play" size=${16} /></span>
    </span>
    <span class="res-text"><b>${name}</b><span>${by}</span></span>
  </a>`;
}

function Rules() {
  return html`<div class="rule-grid">
    ${RULE_BLOCKS.map((block) => html`<section class="rule-block">
      <h4>${block.title}</h4>
      <ul>${block.points.map((point) => html`<li>${point}</li>`)}</ul>
      ${block.source && html`<a class="rule-source" href=${block.source.url}
          target="_blank" rel="noopener noreferrer">${block.source.label}
        <${Icon} name="external" size=${13} /></a>`}
    </section>`)}
  </div>
  <details class="rule-banned">
    <summary>Banned in submitted Stage RTA runs</summary>
    <ul>${STAGE_RTA_BANNED.map((item) => html`<li>${item}</li>`)}</ul>
  </details>`;
}

function SectionBody({ section }) {
  if (section.kind === "rules") return html`<${Rules} />`;
  if (section.kind === "videos") {
    return html`<div class="res-videos">
      ${section.items.map((item) => html`<${VideoRow} key=${item.url} ...${item} />`)}
    </div>`;
  }
  return html`<div class="res-list">
    ${section.items.map((item) => html`<${ExternalRow} key=${item.url} ...${item} />`)}
  </div>`;
}

export function ResourcesPage() {
  return html`<div class="workshop-page resources-page">
    <header class="practice-card workshop-hero">
      <div class="workshop-title">
        <span class="workshop-title-icon"><${Icon} name="resources" size=${22} /></span>
        <div>
          <span class="eyebrow">Community</span>
          <h2>Resources</h2>
          <p>Where to learn, practice and compete with the SM64 community.</p>
        </div>
      </div>
    </header>
    <div class="res-grid">
      ${RESOURCE_SECTIONS.map((section) => html`<section key=${section.id}
          class=${`practice-card res-card res-${section.id} ${section.wide ? "is-wide" : ""}`}>
        <div class="workshop-card-heading">
          <div><span class="eyebrow">${section.eyebrow}</span><h3>${section.title}</h3></div>
        </div>
        <${SectionBody} section=${section} />
      </section>`)}
    </div>
  </div>`;
}
