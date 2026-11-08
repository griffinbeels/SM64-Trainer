// src/sm64_events/ui/components/sceneghosts.js -- the Ghost Lab's Ghosts tab (lab only; sceneviewer.js
// draws it in the tray under the player).
//
// His words (round 9, item 5): "load a different ghost's save states (e.g., I want to load Apeduden's
// ghost into the ghost viewer so that I can select one of his ghosts in the scene viewer --> save state
// --> then play from where he left off)", and on the first concepts: "the "add" and "open" are a bit
// confusing. I didn't understand the difference on first glance". So a ghost row has two parts, the
// layers pattern photo and video editors use: the row itself WATCHES that run (it becomes the scene:
// its world, its camera, the save states come from it; the row it is shows selected, with a star), and
// its eye SHOWS it beside the run you watch (a stage ghost: drawn into every picture, with its own
// sound). While a watched run is being made into a scene, its row and the play bar show the progress
// (scenebuild.js) and the old scene keeps playing.
//
// "In the scene" lists the watched run and every ghost shown beside it, each with its view (the stage
// seen from its own camera), its Mute and its eye. The list beside it is one at a time: Top (each
// player's best, first across All strategies, each row naming its strategy; round 9, item 7), Yours
// (fastest or newest first), or what Find a player found (each player's best; "N more" opens their
// slower ones), a page at a time from GET /api/scene-lab/ghosts (only complete recordings are listed).
// Every row wears the flag of the ROM its recording names (item 11), none where its record names none.
// A ghost the lab cannot draw stays hidden with the lab's reason on its row.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON } from "../api.js";
import { BuildProgress } from "./scenebuild.js";
import { Icon } from "./icons.js";
import { RegionFlag } from "./regionflag.js";
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

// The flag of the ROM a ghost's recording names ("us" or "jp", the lab's "version"), drawn by the
// app's one RegionFlag; nothing at all where its record names none (never a guess).
export function GhostFlag({ version, size = 16 }) {
  return version === "us" || version === "jp"
    ? html`<${RegionFlag} version=${version} size=${size} className="scene-lab-ghost-flag" />` : null;
}

// A listed row as the scene's ghost list holds it (SceneLink.setGhosts sends only ref and label).
export function sceneGhost(item) {
  return { ref: item.ref, label: item.player, player: item.player, time: item.time,
    version: item.version ?? null, strat: item.strat ?? null, attempt: item.attempt ?? null };
}

// Whether a listed row is the run being watched: the scene's own ghost (watched from a row) or its
// own attempt (an attempt scene of his).
export function isWatched(link, item) {
  const scene = link.scene;
  if (!scene) return false;
  if (scene.watching?.ref) return scene.watching.ref === item.ref;
  return scene.attempt != null && item.attempt === scene.attempt;
}

// A ghost's view of the stage: its own camera, following it as the scene plays (the Camera tab has one
// per ghost too).
function TheirView({ link, ghostRef, name }) {
  const available = link.freeAvailable;
  return html`<button type="button" class="scene-lab-ghost-view icon-button" aria-pressed=${!link.free && link.view === ghostRef}
      disabled=${!available} aria-label=${`Their view: ${viewName(link.ghosts, ghostRef)}`}
      title=${available ? `See the stage from ${name}'s own camera` : link.scene.free_camera?.reason}
      onclick=${(event) => link.theirView(ghostRef, event.timeStamp)}><${Icon} name="camera" size=${15} /></button>`;
}

// A ghost in the scene plays its own sound effects beside the stage's, under the volume and Mute;
// this Mute silences that ghost alone (SceneSound.toggleGhost). Disabled, with why, when its sound
// cannot play.
function GhostMute({ link, ghostRef, name }) {
  const sound = link.sound, muted = sound.ghostMuted(ghostRef);
  const problem = !sound.available ? "This recording has no sound, so its ghosts play silent too."
    : sound.ghostProblems.has(ghostRef) ? "This ghost's sound could not be played." : null;
  const title = problem || (muted ? "Play this ghost's sound effects again" : "Mute this ghost's sound effects");
  return html`<button type="button" class="scene-lab-ghost-mute icon-button" aria-pressed=${muted} disabled=${!!problem}
      aria-label=${`Mute ${name}`} title=${title} onclick=${() => sound.toggleGhost(ghostRef)}>
    <${Icon} name=${muted ? "speakerMuted" : "speaker"} size=${15} /></button>`;
}

// What a row's eye shows: shown beside the scene, or not, or (while the lab has not answered the list
// it is in or out of: a ghost new to the lab may need a download first) on its way.
function eyeWords(shown, wanted, name) {
  if (wanted !== shown) return wanted ? `Showing ${name}...` : `Hiding ${name}...`;
  return shown ? `Hide ${name}` : `Show ${name} beside the run you watch`;
}

// The eye: shows this ghost beside the run you watch, or hides it again.
function Eye({ link, item, name, watched }) {
  const shown = link.ghosts.some((ghost) => ghost.ref === item.ref);
  const wanted = link.wantedGhosts.some((ghost) => ghost.ref === item.ref);
  const toggle = () => link.setGhosts(wanted ? link.wantedGhosts.filter((ghost) => ghost.ref !== item.ref)
    : [...link.wantedGhosts, sceneGhost(item)]);
  if (watched) return html`<span class="ghost-lab-eye is-watched" aria-hidden="true"></span>`;
  return html`<button type="button" class=${`ghost-lab-eye icon-button${wanted !== shown ? " is-waiting" : ""}`}
      aria-pressed=${shown} disabled=${wanted !== shown} aria-label=${eyeWords(shown, wanted, name)}
      title=${eyeWords(shown, wanted, name)} onclick=${toggle}>
    <${Icon} name=${wanted ? "eye" : "eyeOff"} size=${16} /></button>`;
}

// A row's name cell: its ROM's flag, the player (with the day, for one of yours) and, across All
// strategies, its strategy under the name.
function RowWho({ item, strat }) {
  const day = item.yours ? recordedDay(item.recorded_utc) : null;
  return html`<span class="scene-lab-ghost-who"><${GhostFlag} version=${item.version} />
    <span class="scene-lab-ghost-player" title=${item.player}>${item.player}${day && html`<small> · ${day}</small>`}</span>
    ${strat && item.strat && html`<small class="scene-lab-ghost-strat" title=${item.strat}>${item.strat}</small>`}</span>`;
}

// One ghost in a list: its rank where known, then the part that watches it (flag, player, strategy,
// time; pressed with a star while it is the scene), then its eye. `children` go under it (a player's
// slower ghosts); a refusal says why under it too.
function GhostRow({ item, link, watch, building, more = null, children = null, strat = false }) {
  const watched = isWatched(link, item), refused = link.refusedGhosts.get(item.ref);
  const shown = link.ghosts.some((ghost) => ghost.ref === item.ref), mine = building?.ref === item.ref;
  const marks = [watched && "is-watched", mine && "is-building", shown && "is-shown"].filter(Boolean);
  return html`<li class=${["scene-lab-ghost", ...marks].join(" ")} data-ref=${item.ref}>
    <span class="scene-lab-ghost-rank">${item.rank != null ? `#${item.rank}` : ""}</span>
    <${WatchButton} item=${item} watched=${watched} busy=${!!building} watch=${watch} strat=${strat} />
    <${Eye} link=${link} item=${item} name=${item.player} watched=${watched} />
    ${mine && html`<${BuildProgress} build=${building} compact />`}
    ${more}
    ${refused && !shown && html`<span class="scene-lab-ghost-reason" role="status">${refused}</span>`}
    ${children}
  </li>`;
}

// The part of a row that watches its run: flag, player, strategy and time, pressed with a star while it is
// the scene.
function WatchButton({ item, watched, busy, watch, strat }) {
  return html`<button type="button" class="ghost-lab-watch" aria-pressed=${watched} disabled=${busy || watched}
      aria-label=${`${watched ? "Watching" : "Watch"} ${item.player} ${item.time}`}
      title=${watched ? "This is the run you watch" : "Watch this run: it becomes the scene, and its save states"}
      onclick=${() => watch(item)}>
    <span class="ghost-lab-star" aria-hidden="true">${watched ? "★" : ""}</span>
    <${RowWho} item=${item} strat=${strat} />
    <span class="scene-lab-ghost-time">${item.time}</span>
  </button>`;
}

// A list's rows, or why there are none. The lab's "note" (why other players' ghosts are missing:
// signed out, offline, a source checkout) is one quiet line above them.
function GhostList({ pages, link, watch, building, empty, label, row = null, strat = false }) {
  const { answer, error } = pages;
  if (error) return html`<p class="scene-lab-ghost-note is-bad" role="status">${error}</p>`;
  if (!answer) return html`<p class="scene-lab-ghost-note" role="status">Loading...</p>`;
  const note = answer.note && html`<p class="scene-lab-ghost-note" role="note">${answer.note}</p>`;
  if (!answer.items?.length) return html`${note}<p class="scene-lab-ghost-note">${answer.reason || empty}</p>`;
  return html`${note}<ol class="scene-lab-ghost-list" aria-label=${label}>
      ${answer.items.map((item) => (row ? row(item)
        : html`<${GhostRow} key=${item.ref} item=${item} link=${link} watch=${watch} building=${building} strat=${strat} />`))}
    </ol>
    <${Pager} pages=${pages} label=${label} />`;
}

// The lab's question for Top ghosts: one strategy, or (null) every strategy at once. All strategies
// leaves the stage's strategy out, so changing the stage's strategy asks nothing new.
export function topQuery(target, strat) {
  return strat ? `section=top&${targetQuery({ ...target, strat })}`
    : `section=top&strategies=all&${targetQuery({ ...target, strat: null })}`;
}

// Top ghosts start on All strategies (his words, item 7: "across all strategies"); the dropdown's
// first choice is All strategies, then each strategy. A new target starts it over.
export const ALL_STRATEGIES = "All strategies";
function TopList({ link, target, strategies, watch, building }) {
  const [strat, setStrat] = useState({ from: null, value: null });
  const from = targetKey(target);
  const chosen = strat.from === from ? strat.value : null;
  const pages = useGhostPages(topQuery(target, chosen), link.ghostPages);
  return html`<section class="scene-lab-ghost-section" aria-label="Top ghosts">
    <header>${strategies?.length ? html`<${StratPicker} key=${from} entity=${from} strategies=${strategies}
        active=${chosen} allowNew=${false} highlightUnset=${false} label="Strategy for top ghosts"
        blankLabel=${ALL_STRATEGIES} submit=${(tag) => { setStrat({ from, value: tag || null }); return Promise.resolve(null); }}
        onChanged=${() => Promise.resolve()} />` : null}</header>
    <${GhostList} pages=${pages} link=${link} watch=${watch} building=${building} label="Top ghosts" strat=${!chosen}
      empty=${chosen ? "No shared ghosts for this strategy yet." : "No ghosts here yet."} />
  </section>`;
}

function YourList({ link, target, watch, building }) {
  const [sort, setSort] = useState("fastest");
  const pages = useGhostPages(`section=yours&${targetQuery(target)}&sort=${sort}`, link.ghostPages);
  return html`<section class="scene-lab-ghost-section" aria-label="Your ghosts">
    <header><select aria-label="Sort your ghosts" value=${sort} onchange=${(event) => setSort(event.currentTarget.value)}>
      <option value="fastest">Fastest first</option>
      <option value="newest">Newest first</option>
    </select></header>
    <${GhostList} pages=${pages} link=${link} watch=${watch} building=${building} label="Your ghosts"
      empty="No ghosts of yours for this strategy yet." />
  </section>`;
}

// A player's slower ghosts, fastest first, under their best.
function PlayerMore({ link, target, watch, building, player }) {
  const pages = useGhostPages(`section=player&player=${encodeURIComponent(player)}&${targetQuery(target)}`,
    link.ghostPages);
  return html`<div class="scene-lab-ghost-more-list">
    <${GhostList} pages=${pages} link=${link} watch=${watch} building=${building} label=${`${player}'s ghosts`}
      empty="No other ghosts." />
  </div>`;
}

// What Find a player found: each matching player's best, and "N more" for their slower ones.
function FoundList({ link, target, watch, building, asked }) {
  const [open, setOpen] = useState(() => new Set());
  useEffect(() => setOpen(new Set()), [asked]);
  const pages = useGhostPages(`section=search&q=${encodeURIComponent(asked)}&${targetQuery(target)}`, link.ghostPages);
  const flip = (player) => setOpen((was) => {
    const next = new Set(was);
    if (!next.delete(player)) next.add(player);
    return next;
  });
  const row = (item) => {
    const expanded = open.has(item.player);
    const more = item.more > 0 ? html`<button type="button" class="scene-lab-ghost-more" aria-expanded=${expanded}
        onclick=${() => flip(item.player)}>${expanded ? "Fewer" : `${item.more} more`}</button>` : null;
    return html`<${GhostRow} key=${item.ref} item=${item} link=${link} watch=${watch} building=${building} more=${more}>
      ${expanded && html`<${PlayerMore} link=${link} target=${target} watch=${watch} building=${building} player=${item.player} />`}
    <//>`;
  };
  return html`<section class="scene-lab-ghost-section" aria-label="Find a player">
    <${GhostList} pages=${pages} link=${link} watch=${watch} building=${building} label="Players found" row=${row}
      empty="No one by that name has a ghost here." />
  </section>`;
}

// The search field: asks once typing stops (SEARCH_WAIT) or on Enter, never per keystroke; empty
// returns to the list before it.
function useSearch() {
  const [typed, setTyped] = useState("");
  const [asked, setAsked] = useState("");
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);
  const submit = (value) => {
    clearTimeout(timer.current);
    if (value.trim() !== asked) setAsked(value.trim());
  };
  const type = (value) => {
    setTyped(value);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => submit(value), SEARCH_WAIT.ms);
  };
  return { typed, asked, type, submit };
}

const LISTS = [["top", "Top"], ["yours", "Yours"]];

// The list beside "In the scene": Top or Yours, or what the search found while it holds a name.
function FindGhosts({ link, target, strategies, watch, building }) {
  const [list, setList] = useState("top");
  const search = useSearch();
  const showing = search.asked ? "found" : list;
  return html`<div class="ghost-lab-lists">
    <div class="ghost-lab-lists-head">
      <div class="ghost-lab-segments" role="group" aria-label="Ghost list">
        ${LISTS.map(([key, words]) => html`<button type="button" key=${key} aria-pressed=${showing === key}
            onclick=${() => { setList(key); search.type(""); search.submit(""); }}>${words}</button>`)}
      </div>
      <label class="ghost-lab-find"><${Icon} name="search" size=${14} />
        <input type="search" maxlength="80" placeholder="Find a player" aria-label="Find a player by name"
          value=${search.typed} oninput=${(event) => search.type(event.currentTarget.value)}
          onkeydown=${(event) => { if (event.key === "Enter") search.submit(event.currentTarget.value); }} /></label>
    </div>
    ${showing === "top" && html`<${TopList} link=${link} target=${target} strategies=${strategies} watch=${watch}
      building=${building} />`}
    ${showing === "yours" && html`<${YourList} link=${link} target=${target} watch=${watch} building=${building} />`}
    ${showing === "found" && html`<${FoundList} link=${link} target=${target} watch=${watch} building=${building}
      asked=${search.asked} />`}
  </div>`;
}

// One row of "In the scene": the run you watch (a star, nothing to press), or a ghost shown beside it
// with its view, its Mute and its eye.
function SceneRow({ link, ghost, watched }) {
  const name = ghost.player || ghost.label || "Ghost";
  return html`<li class=${`ghost-lab-in-scene${watched ? " is-watched" : ""}`} data-ref=${ghost.ref || "stage"}>
    <span class="ghost-lab-star" aria-hidden="true">${watched ? "★" : ""}</span>
    <span class="scene-lab-ghost-who"><${GhostFlag} version=${ghost.version} />
      <span class="scene-lab-ghost-player" title=${name}>${name}</span></span>
    <span class="scene-lab-ghost-time">${ghost.time || ""}</span>
    ${watched ? html`<span class="ghost-lab-in-scene-tools"></span>` : html`<span class="ghost-lab-in-scene-tools">
      <${TheirView} link=${link} ghostRef=${ghost.ref} name=${name} />
      <${GhostMute} link=${link} ghostRef=${ghost.ref} name=${`${name} ${ghost.time || ""}`.trim()} />
      <${Eye} link=${link} item=${ghost} name=${name} watched=${false} /></span>`}
  </li>`;
}

// The run you watch, then every ghost shown beside it (and one on its way, dashed).
function InScene({ link, watching }) {
  const shown = link.wantedGhosts;
  return html`<section class="ghost-lab-scene-list" aria-label="In the scene">
    <header><h4>In the scene</h4>
      ${shown.length ? html`<button type="button" class="ghost-lab-hide-all" onclick=${() => link.setGhosts([])}>Hide all</button>` : null}
    </header>
    <ol class="scene-lab-ghost-list">
      <${SceneRow} link=${link} ghost=${watching} watched=${true} />
      ${shown.map((ghost) => html`<${SceneRow} key=${ghost.ref} link=${link} ghost=${ghost} watched=${false} />`)}
    </ol>
    ${link.ghostNote && html`<p class="scene-lab-ghost-note is-bad" role="status">${link.ghostNote}</p>`}
  </section>`;
}

// The Ghosts tab. `watch(item)` makes a listed run the scene; `building` is the run being made into one
// ({ref, name, progress}); `watching` the run on screen ({player, time, version}); `strategies` the
// target's strategy names (the stage picker's list), for Top ghosts' dropdown.
export function GhostPanel({ link, target, strategies, watch, building = null, watching }) {
  if (!link.scene || !targetKey(target)) {
    return html`<p class="scene-lab-ghost-note">Choose a course and star to see its ghosts.</p>`;
  }
  return html`<div class="ghost-lab-ghosts scene-lab-ghosts" aria-label="Ghosts">
    <${InScene} link=${link} watching=${watching} />
    <${FindGhosts} link=${link} target=${target} strategies=${strategies} watch=${watch} building=${building} />
  </div>`;
}
