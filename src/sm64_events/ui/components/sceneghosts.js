// src/sm64_events/ui/components/sceneghosts.js -- the Scene viewer's ghost panel (M4 round 4, lab
// only; sceneviewer.js draws it below the player).
//
// His words (round 4, item 3): "we should also display on the bottom A PAGINATED LIST OF GHOSTS
// THAT WE COULD LOAD INTO THIS SCENE!!! ... If I add a ghost, it should immediately appear in the
// scene without needing to reload."
//
// Three lists of ghosts for the stage's star or segment, a page at a time from GET
// /api/scene-lab/ghosts (only complete recordings are listed): Your ghosts (fastest first, or
// newest first), Top ghosts (each player's best for a strategy, with its rank; its own strategy
// dropdown looks at another strategy of the same star), and Find a player (each matching player's
// best ghost; "N more" opens that player's slower ones, fastest first). Add and Remove change the
// scene's ghosts: the page sends the whole list over the scene socket (SceneLink.setGhosts) and the
// lab draws them into every picture after its answer. A ghost the lab cannot draw stays unselected
// with the lab's reason on its row. The stage's own run is the stage: its row says so instead of
// offering Add.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON } from "../api.js";
import { StratPicker } from "./stratpicker.js";
import { targetKey, targetQuery } from "./scenestage.js";

const html = htm.bind(h);
const ROOT = "/api/scene-lab";
// How long typing in Find a player waits before it asks (the rival search's own wait).
export const SEARCH_WAIT = { ms: 250 };

// One section's pages (the lab counts them from 1): `query` names the section and the target
// (null: nothing to ask). A new query starts on its first page; an answer to an older query or page
// is dropped, and the page before stays up while the next loads.
function useGhostPages(query) {
  const [paging, setPaging] = useState({ query: null, page: 1 });
  const page = paging.query === query ? paging.page : 1;
  const asking = query ? `${query}&page=${page}` : null;
  const [answered, setAnswered] = useState({ query: null, asking: null, answer: null, error: null });
  useEffect(() => {
    if (!asking) return undefined;
    let alive = true;
    getJSON(`${ROOT}/ghosts?${asking}`)
      .then((answer) => { if (alive) setAnswered({ query, asking, answer, error: null }); })
      .catch((problem) => { if (alive) setAnswered({ query, asking, answer: null, error: problem.message }); });
    return () => { alive = false; };
  }, [asking]);
  const fresh = answered.asking === asking, same = answered.query === query;
  return { page, answer: same ? answered.answer : null, error: fresh ? answered.error : null,
    setPage: (next) => setPaging({ query, page: next }) };
}

function Pager({ pages, label }) {
  const total = pages.answer?.pages || 1;
  if (total <= 1) return null;
  return html`<div class="scene-lab-ghost-pager" role="group" aria-label=${`${label} pages`}>
    <button type="button" disabled=${pages.page <= 1} onclick=${() => pages.setPage(pages.page - 1)}>Previous</button>
    <span>Page ${pages.page} of ${total}</span>
    <button type="button" disabled=${pages.page >= total} onclick=${() => pages.setPage(pages.page + 1)}>Next</button>
  </div>`;
}

// The day one of your ghosts was recorded, in local time ("Oct 12"), so Newest first reads as an order.
function recordedDay(utc) {
  const when = utc ? new Date(utc) : null;
  return when && !Number.isNaN(when.getTime()) ? when.toLocaleDateString(undefined, { month: "short", day: "numeric" }) : null;
}

// One ghost: rank where known, player (with the day, for one of yours), time, and Add or Remove.
// `children` go under it (a player's slower ghosts).
function GhostRow({ item, link, stage, more = null, children = null }) {
  const shown = link.ghosts.some((ghost) => ghost.ref === item.ref);
  const isStage = stage != null && item.attempt === stage;
  const refused = link.refusedGhosts.get(item.ref);
  const toggle = () => link.setGhosts(shown ? link.ghosts.filter((ghost) => ghost.ref !== item.ref)
    : [...link.ghosts, { ref: item.ref, label: item.player, player: item.player, time: item.time }]);
  const name = `${item.player} ${item.time}`, day = item.yours ? recordedDay(item.recorded_utc) : null;
  return html`<li class=${`scene-lab-ghost${shown ? " is-shown" : ""}${isStage ? " is-stage" : ""}`} data-ref=${item.ref}>
    <span class="scene-lab-ghost-rank">${item.rank != null ? `#${item.rank}` : ""}</span>
    <span class="scene-lab-ghost-player" title=${item.player}>${item.player}${day && html`<small> · ${day}</small>`}</span>
    <span class="scene-lab-ghost-time">${item.time}</span>
    ${isStage ? html`<span class="scene-lab-ghost-stage" title="This run is the scene you are watching">The stage</span>`
      : html`<button type="button" aria-label=${`${shown ? "Remove" : "Add"} ${name}`} onclick=${toggle}>
          ${shown ? "Remove" : "Add"}</button>`}
    ${more}
    ${refused && !shown && html`<span class="scene-lab-ghost-reason" role="status">${refused}</span>`}
    ${children}
  </li>`;
}

// A section's rows, or why there are none.
function GhostList({ pages, link, stage, empty, label, row = null }) {
  const { answer, error } = pages;
  if (error) return html`<p class="scene-lab-ghost-note is-bad" role="status">${error}</p>`;
  if (!answer) return html`<p class="scene-lab-ghost-note" role="status">Loading...</p>`;
  if (!answer.items?.length) return html`<p class="scene-lab-ghost-note">${answer.reason || empty}</p>`;
  return html`<ol class="scene-lab-ghost-list" aria-label=${label}>
      ${answer.items.map((item) => (row ? row(item)
        : html`<${GhostRow} key=${item.ref} item=${item} link=${link} stage=${stage} />`))}
    </ol>
    <${Pager} pages=${pages} label=${label} />`;
}

function YourGhosts({ link, target, stage }) {
  const [sort, setSort] = useState("fastest");
  const query = `section=yours&${targetQuery(target)}&sort=${sort}`;
  const pages = useGhostPages(query);
  return html`<section class="scene-lab-ghost-section" aria-label="Your ghosts">
    <header><h4>Your ghosts</h4>
      <select aria-label="Sort your ghosts" value=${sort} onchange=${(event) => setSort(event.currentTarget.value)}>
        <option value="fastest">Fastest first</option>
        <option value="newest">Newest first</option>
      </select></header>
    <${GhostList} pages=${pages} link=${link} stage=${stage} label="Your ghosts"
      empty="No ghosts of yours for this strategy yet." />
  </section>`;
}

// Top ghosts look at the stage's strategy until another is picked here; a new target or stage
// strategy starts it over.
function TopGhosts({ link, target, stage, strategies }) {
  const [strat, setStrat] = useState({ from: null, value: null });
  const from = `${targetKey(target)}|${target.strat ?? ""}`;
  const chosen = strat.from === from ? strat.value : target.strat ?? null;
  const pages = useGhostPages(`section=top&${targetQuery({ ...target, strat: chosen })}`);
  return html`<section class="scene-lab-ghost-section" aria-label="Top ghosts">
    <header><h4>Top ghosts</h4>
      ${strategies?.length ? html`<${StratPicker} key=${from} entity=${targetKey(target)} strategies=${strategies}
          active=${chosen} allowNew=${false} highlightUnset=${false} label="Strategy for top ghosts"
          blankLabel="— no strategy —" submit=${(tag) => { setStrat({ from, value: tag || null }); return Promise.resolve(null); }}
          onChanged=${() => Promise.resolve()} />` : null}</header>
    <${GhostList} pages=${pages} link=${link} stage=${stage} label="Top ghosts"
      empty="No shared ghosts for this strategy yet." />
  </section>`;
}

// A player's slower ghosts, fastest first, under their best.
function PlayerMore({ link, target, stage, player }) {
  const pages = useGhostPages(`section=player&player=${encodeURIComponent(player)}&${targetQuery(target)}`);
  return html`<div class="scene-lab-ghost-more-list">
    <${GhostList} pages=${pages} link=${link} stage=${stage} label=${`${player}'s ghosts`}
      empty="No other ghosts." />
  </div>`;
}

function FindPlayer({ link, target, stage }) {
  const [typed, setTyped] = useState("");
  const [asked, setAsked] = useState("");
  const [open, setOpen] = useState(() => new Set());
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);
  const type = (value) => {
    setTyped(value);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => { setAsked(value.trim()); setOpen(new Set()); }, SEARCH_WAIT.ms);
  };
  const pages = useGhostPages(asked ? `section=search&q=${encodeURIComponent(asked)}&${targetQuery(target)}` : null);
  const flip = (player) => setOpen((was) => {
    const next = new Set(was);
    if (!next.delete(player)) next.add(player);
    return next;
  });
  const row = (item) => {
    const expanded = open.has(item.player);
    const more = item.more > 0 ? html`<button type="button" class="scene-lab-ghost-more" aria-expanded=${expanded}
        onclick=${() => flip(item.player)}>${expanded ? "Fewer" : `${item.more} more`}</button>` : null;
    return html`<${GhostRow} key=${item.ref} item=${item} link=${link} stage=${stage} more=${more}>
      ${expanded && html`<${PlayerMore} link=${link} target=${target} stage=${stage} player=${item.player} />`}
    <//>`;
  };
  return html`<section class="scene-lab-ghost-section" aria-label="Find a player">
    <header><h4>Find a player</h4>
      <input type="search" maxlength="80" placeholder="Player name" aria-label="Find a player by name" value=${typed}
        oninput=${(event) => type(event.currentTarget.value)} /></header>
    ${asked ? html`<${GhostList} pages=${pages} link=${link} stage=${stage} label="Players found" row=${row}
        empty="No one by that name has a ghost here." />`
      : html`<p class="scene-lab-ghost-note">Each player's fastest ghost; "more" shows their slower ones.</p>`}
  </section>`;
}

// `strategies`: the target's strategy names (the stage picker's list), for Top ghosts' own dropdown.
export function GhostPanel({ link, target, strategies }) {
  if (!link.scene || !targetKey(target)) return null;
  const stage = link.scene.attempt ?? null, count = link.ghosts.length;
  return html`<section class="practice-card scene-lab-ghosts" aria-label="Ghosts">
    <div class="scene-lab-ghosts-head">
      <h3>Ghosts</h3>
      <p class="scene-lab-hint">${count ? `${count} in the scene.` : "Add ghosts to watch them run beside this one."}</p>
      ${count ? html`<button type="button" onclick=${() => link.setGhosts([])}>Remove all</button>` : null}
    </div>
    ${link.ghostNote && html`<p class="scene-lab-ghost-note is-bad" role="status">${link.ghostNote}</p>`}
    <div class="scene-lab-ghost-sections">
      <${YourGhosts} link=${link} target=${target} stage=${stage} />
      <${TopGhosts} link=${link} target=${target} stage=${stage} strategies=${strategies} />
      <${FindPlayer} link=${link} target=${target} stage=${stage} />
    </div>
  </section>`;
}
