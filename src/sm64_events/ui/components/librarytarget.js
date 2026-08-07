// src/sm64_events/ui/components/librarytarget.js — the progression-first
// target page: one section per strategy, beginner -> expert, each a
// rank-standards TOC over community examples banded slowest -> fastest.
// Scrolling down IS the climb (spec 2026-08-07-library-page, section 3).
//
// SECOND-DOOR RULING (task-4-caveats.md point 1): sections are ordered by
// `librarymodel.js::sectionOrder`, not `ladderorder.js::slowestFirst` --
// deliberately, not by omission. The two rules disagree about where an
// unproven (no-ladder) strategy belongs, and the reasoning for keeping them
// as two doors rather than unifying them lives on `sectionOrder`'s own
// docstring, where the next person choosing between them will look first.
import { h } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON } from "../api.js";
import { fmtSeconds } from "../format.js";
import { entityKey as sectionEntityKey } from "../entitysection.js";
import { entityIconSrc, genericStarSrc } from "./entityicons.js";
import { RankIcon } from "./rankicon.js";
import { capName } from "./caps.js";
import { Disclose } from "./collapsible.js";
import { Icon } from "./icons.js";
import {
  sectionOrder, autoExpandName, bandsOf, matchesRunner, youtubeThumb, youtubeEmbed,
} from "./librarymodel.js";

const html = htm.bind(h);
const enc = encodeURIComponent;

// DOM-id-safe identity for a section/band. Approach names carry spaces, `+`,
// `·` and `(JP)` — none legal (or at least none SAFE) inside a bare `id`, and
// `matched_strategy` is worse (it is the qualified vetted name, e.g.
// "100c + Slide · Open"). caveat 7 grants latitude to change the brief's
// literal `lib-band-${approachName}-${tier}` format for exactly this reason;
// the final format is `lib-band-<slug(matched_strategy||name)>-<slug(tier)>`,
// recorded in the task report for Task 7 (it reaches these anchors through
// `focusStrat`/`focusTier` PROPS, resolved inside this file, never by
// reconstructing the id string itself — see the report for why that is safe
// even though two approaches on a 100-coin star's sibling targets can share a
// literal unmatched name).
function slug(text) {
  return String(text || "").trim().toLowerCase()
    .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "x";
}
const sectionAnchorId = (approach) =>
  `lib-section-${slug(approach.matched_strategy || approach.name)}`;
const bandAnchorId = (approach, tier) =>
  `lib-band-${slug(approach.matched_strategy || approach.name)}-${slug(tier)}`;

// `t.view`'s active strategy for this entity — `entitysection.js::entityKey`
// is the read-side identity every section already carries, and `last_strat`
// is the same field practicelog.js reads for the SAME question
// (`activeStrat=${sec.last_strat}` feeding StandardsPanel). Not a new rule,
// a new caller of an existing one.
function activeStratFor(view, entityKey) {
  if (!entityKey || !view) return null;
  const sections = [...(view.stars || []), ...(view.segments || [])];
  const hit = sections.find((sec) => sectionEntityKey(sec) === entityKey);
  return (hit && hit.last_strat) || null;
}

// Every strategy this entity knows, each carrying its own rank + PB —
// GET /api/target/strategies?entity=, the picker's step-3 payload
// (views.py::build_entity_strategies) and the only source for "your rank on
// a strategy you have matched but never made active" (the section banner's
// own sec.rank/sec.pb answer only for the ACTIVE strategy, which is a
// different question). One fetch per entity, not per approach — several
// approaches can share one matched_strategy name across sibling 100-coin
// targets (caveat 4) and must read the identical standing.
function useEntityStrategies(entityKey) {
  const [data, setData] = useState(null);
  useEffect(() => {
    if (!entityKey) { setData(null); return undefined; }
    let cancelled = false;
    getJSON(`/api/target/strategies?entity=${enc(entityKey)}`)
      .then((result) => { if (!cancelled) setData(result); })
      .catch(() => { if (!cancelled) setData(null); });
    return () => { cancelled = true; };
  }, [entityKey]);
  return data;
}

function ExampleCard({ entry, tier, hidden, inTray, onAdd }) {
  const [playing, setPlaying] = useState(false);
  const embed = entry.video ? youtubeEmbed(entry.video) : null;
  const thumb = entry.video ? youtubeThumb(entry.video) : null;
  const canOpen = !!embed;
  const openExternal = !!entry.video && !embed;

  function toggle() {
    if (canOpen) setPlaying((prev) => !prev);
  }

  return html`<div class="library-example ${hidden ? "hidden" : ""}">
    <div class="library-example-media ${canOpen ? "is-clickable" : ""}"
        onclick=${canOpen ? toggle : null}
        title=${canOpen ? (playing ? "Close" : "Play inline") : ""}>
      ${playing && embed
        ? html`<iframe class="library-embed" src=${embed}
            title=${`${entry.runner} — ${fmtSeconds(entry.time_cs / 100)}`}
            allow="autoplay; encrypted-media" allowfullscreen></iframe>`
        : thumb
          ? html`<img class="library-example-thumb" src=${thumb} alt=""
              loading="lazy" />`
          : html`<div class="library-example-thumb library-example-placeholder">
              <${Icon} name="play" size=${20} />
            </div>`}
      ${openExternal
        ? html`<a class="library-example-external" href=${entry.video}
            target="_blank" rel="noopener" title="open on the runner's site">
            <${Icon} name="upload" size=${13} /></a>` : null}
    </div>
    <div class="library-example-meta">
      <span class="library-example-tier"><${RankIcon} tier=${tier} size=${16} /></span>
      <span class="library-example-runner">${entry.runner}</span>
      <span class="library-example-time">${fmtSeconds(entry.time_cs / 100)}</span>
      <button type="button" class="library-example-plus"
          disabled=${!entry.video || inTray}
          title=${!entry.video ? "no video for this run"
            : inTray ? "already in the tray" : "add to the comparison tray"}
          onclick=${(clickEvent) => {
            clickEvent.stopPropagation();
            onAdd({ key: entry.video, runner: entry.runner, time_cs: entry.time_cs,
                     video: entry.video, strat: null, trim: null });
          }}>+</button>
    </div>
  </div>`;
}

// One tier row of the TOC. `cutoffCs` is null for "Below Bronze" (no
// cutoff — the catch-all for anything that has not beaten one yet).
function TocRow({ band, count, isYou, onJump }) {
  return html`<tr class="library-toc-row" onclick=${onJump}>
    <td class="library-toc-tier">
      <${RankIcon} tier=${band.tier} size=${16} />
      ${capName(band.tier)}${isYou ? html`<span class="library-toc-you" title="your current tier on this strategy"> ◀ you</span>` : ""}
    </td>
    <td class="library-toc-cutoff">${band.cutoffCs != null ? fmtSeconds(band.cutoffCs / 100) : "—"}</td>
    <td class="library-toc-count">${count}</td>
  </tr>`;
}

/**
 * One strategy's section: header (identity, community best, fill rate, your
 * standing), an optional JP/US toggle, the TOC, and the banded example
 * cards. `open` is owned by the PARENT (single-open accordion, so "exactly
 * one section open" is a property of the parent's own state rather than
 * something every section has to negotiate).
 */
function Section({ approach, open, onOpen, query, stratInfo, trayKeys, onAdd }) {
  const [jp, setJp] = useState(false);
  const hasJp = !!approach.ladder_jp;
  const ladder = (hasJp && jp ? approach.ladder_jp : approach.ladder) || {};
  const bands = useMemo(() => bandsOf(ladder, approach.entries),
    [ladder, approach.entries]);
  const marioKey = approach.ladder && approach.ladder.Mario != null
    ? approach.ladder.Mario : -1;   // presentational echo of sectionOrder's own key; -1 (not -Infinity) so it survives JSON round-trips a render probe takes

  return html`<div class="library-section ${open ? "open" : ""}" data-mario=${marioKey}
      id=${sectionAnchorId(approach)}>
    <button type="button" class="library-section-head" onclick=${onOpen}
        aria-expanded=${open}>
      <div class="library-section-text">
        <div class="library-section-identity">
          <span class="library-section-name">${approach.name}</span>
          ${approach.matched_strategy
            ? html`<span class="chip library-matched-chip">= your "${approach.matched_strategy}"</span>` : ""}
        </div>
        <div class="library-section-facts">
          ${approach.best_cs != null
            ? html`<span class="meta">Best ${fmtSeconds(approach.best_cs / 100)} · ${approach.best_runner}</span>` : ""}
          ${approach.fill_rate != null
            ? html`<span class="meta">Fill ${Math.round(approach.fill_rate * 100)}%</span>` : ""}
          ${stratInfo
            ? html`<span class="meta library-your-standing">
                ${stratInfo.rank ? html`<${RankIcon} tier=${stratInfo.rank} division=${stratInfo.division} size=${16} />` : "Not yet ranked"}
                ${stratInfo.pb_display ? html` · ${stratInfo.pb_display}` : ""}
              </span>` : ""}
        </div>
      </div>
      <${Icon} name="chevron" size=${16} className="library-section-chevron" />
    </button>
    <${Disclose} open=${open} className="library-section-disclose">
      <div class="library-section-body">
        ${hasJp ? html`<button type="button" class="chip chip-button library-jp-toggle"
            aria-pressed=${jp} onclick=${() => setJp((prev) => !prev)}>
            ${jp ? "JP ladder" : "US ladder"} · switch
          </button>` : ""}
        <table class="library-toc"><tbody>
          ${bands.map((band) => html`<${TocRow} key=${bandAnchorId(approach, band.tier)} band=${band}
              count=${band.entries.filter((entry) => matchesRunner(entry, query)).length}
              isYou=${!!(stratInfo && stratInfo.rank === band.tier)}
              onJump=${() => document.getElementById(bandAnchorId(approach, band.tier))
                ?.scrollIntoView({ block: "start", behavior: "smooth" })} />`)}
        </tbody></table>
        ${bands.map((band) => html`<div class="library-band" key=${bandAnchorId(approach, band.tier)}
            data-tier=${band.tier} id=${bandAnchorId(approach, band.tier)}>
          <div class="library-band-head">
            <${RankIcon} tier=${band.tier} size=${18} /> <b>${capName(band.tier)}</b>
            <span class="meta">${band.cutoffCs != null ? fmtSeconds(band.cutoffCs / 100) : "unranked"}</span>
          </div>
          <div class="library-examples">
            ${band.entries.map((entry) => html`<${ExampleCard} key=${entry.video || `${entry.runner}:${entry.time_cs}`}
                entry=${entry} tier=${band.tier}
                hidden=${!matchesRunner(entry, query)}
                inTray=${!!(entry.video && trayKeys.has(entry.video))}
                onAdd=${onAdd} />`)}
          </div>
        </div>`)}
      </div>
    <//>
  </div>`;
}

/**
 * `targets` — every FULL library target for the entity (several for a
 * 100-coin star's exit variants); `library.js` resolves both the
 * entity door (`/api/library/entity/{key}`) and the numeric-index door
 * (`/api/library/target/{index}`, owed to this task by task-3-caveats.md
 * point 4) to this same full shape before mounting this component, so it
 * never has to branch on which door it came through.
 */
export function LibraryTarget({ t, targets, onAdd, trayKeys, focusStrat, focusTier }) {
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState(null);   // the OPEN approach's .name

  const rows = targets || [];
  const entityKey = (rows[0] && rows[0].entity_key) || null;
  const label = (rows[0] && rows[0].label) || "";
  const missReason = rows.length === 1 ? rows[0].miss_reason : null;

  const approaches = useMemo(() => sectionOrder(
    rows.flatMap((target) => (target.approaches || []).map((approach) => (
      { ...approach, _target: target.label }))),
  ), [rows]);

  const activeStrat = activeStratFor(t && t.view, entityKey);
  const stratsData = useEntityStrategies(entityKey);
  const stratByName = useMemo(() => {
    const map = {};
    ((stratsData && stratsData.strategies) || []).forEach((entry) => { map[entry.name] = entry; });
    return map;
  }, [stratsData]);

  // Auto-expand once per entity — a deliberate one-shot, so a click the user
  // makes afterward is never silently reverted by a later render of the same
  // page (autoExpandName(ordered, t.view's active strat), brief step 1).
  const openedEntity = useRef(null);
  useEffect(() => {
    if (openedEntity.current === entityKey) return;
    openedEntity.current = entityKey;
    setExpanded(autoExpandName(approaches, activeStrat));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entityKey]);

  // A deep link (Task 7: the standards ladder's own tier rows, and the
  // book mark) re-fires on every change, unlike the auto-open above — the
  // whole point of a link is to move you even while the page is already
  // open on something else.
  useEffect(() => {
    if (!focusStrat) return undefined;
    const hit = approaches.find((approach) =>
      approach.matched_strategy === focusStrat || approach.name === focusStrat);
    if (!hit) return undefined;
    setExpanded(hit.name);
    // Disclose mounts the section body a tick after `open` flips — see its
    // own docstring ("Preact commits after the tick"). A short timer beats a
    // race against that mount rather than guessing a single rAF is enough.
    const timer = setTimeout(() => {
      const id = focusTier ? bandAnchorId(hit, focusTier) : sectionAnchorId(hit);
      document.getElementById(id)?.scrollIntoView({ block: "start", behavior: "smooth" });
    }, 80);
    return () => clearTimeout(timer);
  }, [focusStrat, focusTier, approaches]);

  const iconSrc = entityKey ? entityIconSrc(t, entityKey) : genericStarSrc();
  const activeStratInfo = activeStrat ? stratByName[activeStrat] : null;

  return html`<div class="library-target">
    <div class="library-target-header">
      <img class="library-target-icon" src=${iconSrc} alt="" draggable="false" />
      <div class="library-target-heading">
        <h3>${label}</h3>
        ${activeStratInfo && activeStratInfo.rank
          ? html`<span class="chip library-reminder-chip">
              <${RankIcon} tier=${activeStratInfo.rank} division=${activeStratInfo.division} size=${14} />
              your ${activeStrat}
            </span>` : ""}
      </div>
    </div>
    <input class="library-search" type="search" value=${query}
        placeholder="Search runners…" aria-label="Search runners"
        oninput=${(inputEvent) => setQuery(inputEvent.target.value)} />
    ${approaches.length === 0
      ? html`<p class="library-target-empty">
          ${missReason === "castle_movement" ? "Browse only — no segment adopts this movement yet."
            : missReason === "route" ? "Stage route — browse the sheet, no per-target ladder here."
            : "No community times recorded here yet."}
        </p>`
      : approaches.map((approach) => html`<${Section} key=${approach.matched_strategy || approach.name}
          approach=${approach} open=${expanded === approach.name}
          onOpen=${() => setExpanded(approach.name)} query=${query}
          stratInfo=${approach.matched_strategy ? stratByName[approach.matched_strategy] : null}
          trayKeys=${trayKeys} onAdd=${onAdd} />`)}
  </div>`;
}
