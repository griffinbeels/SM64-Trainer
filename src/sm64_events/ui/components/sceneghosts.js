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
// lab draws them into every picture after its answer; each ghost in the scene plays its own sound
// effects (never music: ghost_sound.py) with a Mute of its own. A ghost the lab cannot draw stays unselected
// with the lab's reason on its row. The stage's own run is the stage: its row says so instead of
// offering Add.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON } from "../api.js";
import { Icon } from "./icons.js";
import { StratPicker } from "./stratpicker.js";
import { targetKey, targetQuery } from "./scenestage.js";

const html = htm.bind(h);
const ROOT = "/api/scene-lab";
// How long Find a player waits after typing stops before it asks; Enter asks at once. Every new
// name can cost the lab a community call, so it is never asked per keystroke.
export const SEARCH_WAIT = { ms: 400 };

// One section's pages (the lab counts them from 1): `query` names the section and the target
// (null: nothing to ask). Each page is asked once in a scene's life: `cache` (SceneLink.ghostPages)
// keeps every answer, and an ask still on its way, so paging back, reopening a section or coming
// back to the scene asks nothing. A new query starts on its first page; an answer to an older one is
// dropped, and the page before stays up while the next loads. Nothing here asks on a timer.
function useGhostPages(query, cache) {
  const [paging, setPaging] = useState({ query: null, page: 1 });
  const page = paging.query === query ? paging.page : 1;
  const asking = query ? `${query}&page=${page}` : null;
  const [answered, setAnswered] = useState({ query: null, asking: null, answer: null, error: null });
  useEffect(() => {
    const entry = asking ? cache.get(asking) : null;
    if (!asking || entry?.answer) return undefined;
    let alive = true;
    const pending = entry?.pending || getJSON(`${ROOT}/ghosts?${asking}`);
    if (!entry) {
      cache.set(asking, { pending });
      pending.then((answer) => cache.set(asking, { answer }), () => cache.delete(asking));   // a failure asks again
    }
    pending.then((answer) => { if (alive) setAnswered({ query, asking, answer, error: null }); },
      (problem) => { if (alive) setAnswered({ query, asking, answer: null, error: problem.message }); });
    return () => { alive = false; };
  }, [asking, cache]);
  const held = asking ? cache.get(asking)?.answer : undefined;
  const fresh = answered.asking === asking, same = answered.query === query;
  return { page, answer: held ?? (same ? answered.answer : null), error: fresh && !held ? answered.error : null,
    setPage: (next) => setPaging({ query, page: next }) };
}

// "pages" can count one page past what the lab holds (more top ghosts it fetches when asked), so it
// says whether there is a next page, not how many there are.
function Pager({ pages, label }) {
  const more = pages.page < (pages.answer?.pages || 1);
  if (!more && pages.page <= 1) return null;
  return html`<div class="scene-lab-ghost-pager" role="group" aria-label=${`${label} pages`}>
    <button type="button" disabled=${pages.page <= 1} onclick=${() => pages.setPage(pages.page - 1)}>Previous</button>
    <span>Page ${pages.page}</span>
    <button type="button" disabled=${!more} onclick=${() => pages.setPage(pages.page + 1)}>Next</button>
  </div>`;
}

// The day one of your ghosts was recorded, in local time ("Oct 12"), so Newest first reads as an order.
function recordedDay(utc) {
  const when = utc ? new Date(utc) : null;
  return when && !Number.isNaN(when.getTime()) ? when.toLocaleDateString(undefined, { month: "short", day: "numeric" }) : null;
}

// What a row's button says: Add or Remove, or, while the lab has not answered the list it is in or
// out of (a ghost new to the lab may need a download first), Adding or Removing.
function rowAction(shown, wanted) {
  if (wanted === shown) return shown ? "Remove" : "Add";
  return wanted ? "Adding..." : "Removing...";
}

// Whose view a ghost's camera is, in words: "Sully's view"; one of yours with no player name is
// "Your view" and its time, as the stage is usually a run of yours too; and when another ghost in the
// scene has the same name, its time, so two runs by one player stay apart.
export function viewName(ghosts, ref) {
  const ghost = ghosts.find((each) => each.ref === ref);
  if (!ghost) return "A ghost's view";
  const name = nameOf(ghost), owner = name === "You" ? "Your view" : `${name}'s view`;
  return timed(ghosts, ghost) ? `${owner} · ${ghost.time}` : owner;
}

// A ghost's own name the same way ("Sully", or "You · 11"33"): the cinematic cameras' Follow list.
export function ghostName(ghosts, ref) {
  const ghost = ghosts.find((each) => each.ref === ref);
  if (!ghost) return "A ghost";
  return timed(ghosts, ghost) ? `${nameOf(ghost)} · ${ghost.time}` : nameOf(ghost);
}

const nameOf = (ghost) => ghost.player || ghost.label || "Ghost";
const timed = (ghosts, ghost) => !!ghost.time
  && (nameOf(ghost) === "You" || ghosts.filter((each) => nameOf(each) === nameOf(ghost)).length > 1);

// A ghost in the scene's row button for its view (the camera bar has one per ghost too).
function TheirView({ link, ghostRef }) {
  const available = link.freeAvailable;
  return html`<button type="button" class="scene-lab-ghost-view" aria-pressed=${!link.free && link.view === ghostRef}
      disabled=${!available} aria-label=${`Their view: ${viewName(link.ghosts, ghostRef)}`}
      title=${available ? "See the stage from this ghost's own camera, following it as the scene plays"
        : link.scene.free_camera?.reason}
      onclick=${(event) => link.theirView(ghostRef, event.timeStamp)}>Their view</button>`;
}

// One ghost: rank where known, player (with the day, for one of yours), time, and Add or Remove;
// once it is in the scene, a strip under it: Their view shows the stage from its own camera, Mute
// silences its sound. `children` go under it (a player's slower ghosts).
function GhostRow({ item, link, stage, more = null, children = null }) {
  const shown = link.ghosts.some((ghost) => ghost.ref === item.ref);
  const wanted = link.wantedGhosts.some((ghost) => ghost.ref === item.ref);
  const isStage = stage != null && item.attempt === stage;
  const refused = link.refusedGhosts.get(item.ref);
  const toggle = () => link.setGhosts(wanted ? link.wantedGhosts.filter((ghost) => ghost.ref !== item.ref)
    : [...link.wantedGhosts, { ref: item.ref, label: item.player, player: item.player, time: item.time }]);
  const name = `${item.player} ${item.time}`, day = item.yours ? recordedDay(item.recorded_utc) : null;
  const action = rowAction(shown, wanted), waiting = wanted !== shown;
  return html`<li class=${`scene-lab-ghost${shown ? " is-shown" : ""}${waiting ? " is-waiting" : ""}${isStage ? " is-stage" : ""}`}
      data-ref=${item.ref}>
    <span class="scene-lab-ghost-rank">${item.rank != null ? `#${item.rank}` : ""}</span>
    <span class="scene-lab-ghost-player" title=${item.player}>${item.player}${day && html`<small> · ${day}</small>`}</span>
    <span class="scene-lab-ghost-time">${item.time}</span>
    ${isStage ? html`<span class="scene-lab-ghost-stage" title="This run is the scene you are watching">The stage</span>`
      : html`<button type="button" disabled=${waiting} aria-label=${`${action.replace("...", "")} ${name}`}
          onclick=${toggle}>${action}</button>`}
    ${shown && html`<${InScene} link=${link} ghostRef=${item.ref} name=${name} isStage=${isStage} />`}
    ${more}
    ${refused && !shown && html`<span class="scene-lab-ghost-reason" role="status">${refused}</span>`}
    ${children}
  </li>`;
}

// A ghost in the scene's strip under its row: Their view (not for the stage's own run) and Mute.
function InScene({ link, ghostRef, name, isStage }) {
  return html`<div class="scene-lab-ghost-controls">
    ${!isStage && html`<${TheirView} link=${link} ghostRef=${ghostRef} />`}
    <${GhostMute} link=${link} ghostRef=${ghostRef} name=${name} />
  </div>`;
}

// A ghost in the scene plays its own sound effects beside the stage's, under the volume and Mute;
// this Mute silences that ghost alone (SceneSound.toggleGhost). Disabled, with why, when its sound
// cannot play.
function GhostMute({ link, ghostRef, name }) {
  const sound = link.sound, muted = sound.ghostMuted(ghostRef);
  const problem = !sound.available ? "This recording has no sound, so its ghosts play silent too."
    : sound.ghostProblems.has(ghostRef) ? "This ghost's sound could not be played." : null;
  const title = problem || (muted ? "Play this ghost's sound effects again" : "Mute this ghost's sound effects");
  return html`<button type="button" class="scene-lab-ghost-mute" aria-pressed=${muted} disabled=${!!problem}
      aria-label=${`Mute ${name}`} title=${title} onclick=${() => sound.toggleGhost(ghostRef)}>
    <${Icon} name=${muted ? "speakerMuted" : "speaker"} size=${14} /> Mute</button>`;
}

// A section's rows, or why there are none. The lab's "note" (why other players' ghosts are missing:
// signed out, offline, a source checkout) is one quiet line above them.
function GhostList({ pages, link, stage, empty, label, row = null }) {
  const { answer, error } = pages;
  if (error) return html`<p class="scene-lab-ghost-note is-bad" role="status">${error}</p>`;
  if (!answer) return html`<p class="scene-lab-ghost-note" role="status">Loading...</p>`;
  const note = answer.note && html`<p class="scene-lab-ghost-note" role="note">${answer.note}</p>`;
  if (!answer.items?.length) return html`${note}<p class="scene-lab-ghost-note">${answer.reason || empty}</p>`;
  return html`${note}<ol class="scene-lab-ghost-list" aria-label=${label}>
      ${answer.items.map((item) => (row ? row(item)
        : html`<${GhostRow} key=${item.ref} item=${item} link=${link} stage=${stage} />`))}
    </ol>
    <${Pager} pages=${pages} label=${label} />`;
}

function YourGhosts({ link, target, stage }) {
  const [sort, setSort] = useState("fastest");
  const query = `section=yours&${targetQuery(target)}&sort=${sort}`;
  const pages = useGhostPages(query, link.ghostPages);
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
  const pages = useGhostPages(`section=top&${targetQuery({ ...target, strat: chosen })}`, link.ghostPages);
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
  const pages = useGhostPages(`section=player&player=${encodeURIComponent(player)}&${targetQuery(target)}`,
    link.ghostPages);
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
  const submit = (value) => {
    clearTimeout(timer.current);
    if (value.trim() === asked) return;
    setAsked(value.trim());
    setOpen(new Set());
  };
  const type = (value) => {
    setTyped(value);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => submit(value), SEARCH_WAIT.ms);
  };
  const pages = useGhostPages(asked ? `section=search&q=${encodeURIComponent(asked)}&${targetQuery(target)}` : null,
    link.ghostPages);
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
        oninput=${(event) => type(event.currentTarget.value)}
        onkeydown=${(event) => { if (event.key === "Enter") submit(event.currentTarget.value); }} /></header>
    ${asked ? html`<${GhostList} pages=${pages} link=${link} stage=${stage} label="Players found" row=${row}
        empty="No one by that name has a ghost here." />`
      : html`<p class="scene-lab-ghost-note">Each player's fastest ghost; "more" shows their slower ones.</p>`}
  </section>`;
}

// `strategies`: the target's strategy names (the stage picker's list), for Top ghosts' own dropdown.
export function GhostPanel({ link, target, strategies }) {
  if (!link.scene || !targetKey(target)) return null;
  const stage = link.scene.attempt ?? null, count = link.ghosts.length, wanted = link.wantedGhosts.length;
  return html`<section class="practice-card scene-lab-ghosts" aria-label="Ghosts">
    <div class="scene-lab-ghosts-head">
      <h3>Ghosts</h3>
      <p class="scene-lab-hint">${count ? `${count} in the scene.` : "Add ghosts to watch them run beside this one."}</p>
      ${wanted ? html`<button type="button" onclick=${() => link.setGhosts([])}>Remove all</button>` : null}
    </div>
    ${link.ghostNote && html`<p class="scene-lab-ghost-note is-bad" role="status">${link.ghostNote}</p>`}
    <div class="scene-lab-ghost-sections">
      <${YourGhosts} link=${link} target=${target} stage=${stage} />
      <${TopGhosts} link=${link} target=${target} stage=${stage} strategies=${strategies} />
      <${FindPlayer} link=${link} target=${target} stage=${stage} />
    </div>
  </section>`;
}
