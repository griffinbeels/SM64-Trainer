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
// literal `lib-band-${approachName}-${tier}` format for exactly this reason.
//
// FIX ROUND 1 (2026-08-07): `matched_strategy || name` is NOT unique on its
// own. Measured against the real bundled snapshot: 10 colliding section
// identities across 8 entities, and the MORE common half is the raw,
// UNMATCHED name — "100 coin star Xcam" alone collides on 6 different stars
// (every 100-coin entity with more than one sheet target), not just the
// matched-strategy case the original version of this file called the only
// one worth naming. `target.index` (stable within one loaded payload, and
// already how the picker/course-grid's own numeric door addresses a target)
// is what actually disambiguates, so every anchor/key is now scoped to the
// owning approach's OWN target as well: `_targetIndex`, stamped once when
// `approaches` is built (below), never re-derived per anchor call.
//
// The final format is
// `lib-band-<targetIndex>-<slug(matched_strategy||name)>-<slug(tier)>`
// (section anchors drop the `-<tier>` suffix). Recorded here for Task 7: it
// reaches these anchors through `focusStrat`/`focusTier` PROPS, resolved
// inside this file, never by reconstructing the id string itself — a name-
// only deep link is still inherently ambiguous between two sibling sections
// that share a `matched_strategy` (see the `focusStrat` effect below for why
// that specific ambiguity is not a bug to fix here).
function slug(text) {
  return String(text || "").trim().toLowerCase()
    .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "x";
}
// The single identity every open/closed state, React `key`, and anchor id in
// this file keys on — target index PLUS the display identity, so two
// sections that happen to share a name (matched or not) are still two
// independently addressable things.
const approachIdentity = (approach) =>
  `${approach._targetIndex}-${slug(approach.matched_strategy || approach.name)}`;
const sectionAnchorId = (approach) => `lib-section-${approachIdentity(approach)}`;
const bandAnchorId = (approach, tier) =>
  `lib-band-${approachIdentity(approach)}-${slug(tier)}`;
// The tray's own item identity (Task 5 fix round 1, then fix round 2). NOT
// `entry.video` alone: the controller measured it colliding across sibling
// ENTITIES on the real bundled snapshot -- 8 videos cited by more than one
// entity (e.g. JoSniffy's youtu.be/ANqWo4v9qfc evidences BOTH star:2:4 "Fall
// onto the Caged Island" and star:2:5 "Blast Away the Wall"), plus 605
// videos cited at more than one TIME. One long recording standing as
// evidence for two different stars is ordinary in this corpus, not a
// rarity -- so a video-keyed tray silently read the second star's
// identical-runner entry as "already added" the instant the first was,
// which is the tray's whole cross-entity use, not an edge case. Scoped to
// the owning approach the same way every other identity in this file
// already is (`approachIdentity`, target-scoped), plus the entry's own
// runner+time.
//
// FIX ROUND 2: `runner+time_cs` alone was still not unique -- measured over
// every video-bearing entry this page can reach (approaches only;
// subsections never render here), one real collision survived:
// star:16:0 "Xiah cycle pipe entry", approach "131-xiah-pipe", Benji, both
// at time_cs 5023, but TWO DIFFERENT recordings
// (youtube.com/watch?v=B9wXEVjv1WU and .../watch?v=U42IDMKO180) -- the same
// trick filmed twice. `video` is now a THIRD suffix. If a future reader's
// instinct is "we key on the video again, wasn't that the original bug" --
// it was NOT: the original bug was keying on video ALONE, with no
// target-scoped prefix. Appending it here to an already
// approach+runner+time key can only ever SPLIT a key that used to be
// shared, never merge two that used to differ, so it cannot reintroduce
// that collision.
const entryTrayKey = (approach, entry) =>
  `${approachIdentity(approach)}::${entry.runner}::${entry.time_cs}::${entry.video}`;

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

function ExampleCard({ entry, tier, hidden, trayKey, entityKey, inTray, foreignVersion, onAdd }) {
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
      ${/* FINAL REVIEW FIX (HIGH: JP/US band contamination). `.library-
           example-meta` is a fixed 4-column grid (icon/runner/time/+), so the
           badge nests INSIDE the runner cell rather than adding a 5th column
           -- a plain grid child would shift the time and "+" columns for
           every card that lacks one. This run's own `entry.version`
           disagrees with the ladder the band above it was fit from -- it
           still earns a real tier (the user's combined-unless-annotated rule
           permits that), but the screen must say so rather than let it read
           as a same-population time. */""}
      <span class="library-example-runner-wrap">
        <span class="library-example-runner">${entry.runner}</span>
        ${foreignVersion
          ? html`<span class="chip library-example-version"
              title=${`This run is ${entry.version === "jp" ? "JP" : "US"} -- graded here against a ladder fitted from ${entry.version === "jp" ? "US" : "JP"} times.`}>
              ${entry.version === "jp" ? "JP" : "US"}
            </span>`
          : ""}
      </span>
      <span class="library-example-time">${fmtSeconds(entry.time_cs / 100)}</span>
      <button type="button" class="library-example-plus"
          disabled=${!entry.video || inTray}
          title=${!entry.video ? "no video for this run"
            : inTray ? "already in the tray" : "add to the comparison tray"}
          onclick=${(clickEvent) => {
            clickEvent.stopPropagation();
            onAdd({ key: trayKey, runner: entry.runner, time_cs: entry.time_cs,
                     video: entry.video, entity_key: entityKey, strat: null, trim: null });
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
function Section({ approach, open, onOpen, query, stratInfo, trayKeys, entityKey, onAdd }) {
  const [jp, setJp] = useState(false);
  const hasJp = !!approach.ladder_jp;
  const ladder = (hasJp && jp ? approach.ladder_jp : approach.ladder) || {};
  // Which ROM population `ladder` was actually FIT from -- library/ladders.py
  // stamps `ladder_version` on the approach itself ("us"/"jp"/null), and
  // `ladder_jp` (when it exists) is only ever derived from JP times
  // (`fit_payload`'s own comment). null means the row's entries carry no
  // version distinction at all, so this project's own combined-unless-
  // annotated rule applies and no entry can be "foreign" against it.
  //
  // FINAL REVIEW FIX (HIGH): before this, every entry of an approach banded
  // against ONE ladder regardless of which ROM it was recorded on -- measured
  // on the shipped snapshot at 67 approaches / 4,659 entries banded against
  // the OTHER version's ladder (Blast to the Stone Pillar's own top band: 108
  // of 112 listed runs were JP times read against a US cutoff). The fix is
  // per-entry LABELLING, not re-sorting bands by version: the user's standing
  // rule is combined-unless-annotated, and where a row's data DOES annotate a
  // difference the screen must say so rather than pretend the mixed band is
  // uniform -- see the badge on ExampleCard below.
  const ladderVersion = hasJp && jp ? "jp" : (approach.ladder_version || null);
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
          ${/* FINAL REVIEW FIX (minor: ambiguous headers). Several approaches
               across sibling targets share one bare name -- "100 coin star
               Xcam" alone headed 6 real entity pages with no way to tell them
               apart (star:14:6's two came from "Stomp on the Thwomp + 100c"
               and "Thwomp + 100c w/ safety red"). `_target` (the owning
               target's own label, stamped once in LibraryTarget below) was
               already carried on every approach and never rendered --
               identity was correctly target-scoped internally the whole time
               (`approachIdentity`), only the VISIBLE header was ambiguous. */""}
          ${approach._target && approach._target !== approach.name
            ? html`<span class="meta library-section-target">${approach._target}</span>` : ""}
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
          </button>`
          : /* FINAL REVIEW FIX (medium: ladder_version read nowhere). A row
               with too few of the OTHER version's times to fit a second
               ladder still gets ONE, fitted entirely from whichever
               population it has -- 13 approaches fit from JP-only times, no
               `ladder_jp` companion (too few US runs to earn one), so no
               toggle ever existed to say so. `ladder_version` was stamped
               for exactly this and read by no JS at all until now. */
          approach.ladder_version
          ? html`<span class="chip library-ladder-version-chip"
              title=${`Fitted from ${approach.ladder_version === "jp" ? "JP" : "US"}-only community times -- not enough of the other version's runs to fit a second ladder.`}>
              ${approach.ladder_version === "jp" ? "JP" : "US"} ladder only
            </span>`
          : ""}
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
            ${band.entries.map((entry) => {
              const trayKey = entryTrayKey(approach, entry);
              // FIX (minor: duplicate Preact keys). `entryTrayKey` was
              // already computed here and is already scoped uniquely per
              // approach+runner+time+video (fix round 2's own reasoning,
              // above) -- reusing it as the React key retires the ad hoc
              // `entry.video || runner:time` fallback, which collided
              // whenever the same runner posted the same time on both the
              // JP and US row of a merged approach with neither carrying a
              // video (measured: 8 bands, most example cards are exactly
              // that kind of placeholder -- 34,264 of 40,974 entries have no
              // video at all).
              return html`<${ExampleCard} key=${trayKey}
                  entry=${entry} tier=${band.tier} trayKey=${trayKey} entityKey=${entityKey}
                  hidden=${!matchesRunner(entry, query)}
                  inTray=${trayKeys.has(trayKey)}
                  foreignVersion=${!!(ladderVersion && entry.version && entry.version !== ladderVersion)}
                  onAdd=${onAdd} />`;
            })}
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
export function LibraryTarget({ t, targets, onAdd, trayKeys, focusStrat, focusTier,
                               fallbackLabel = null }) {
  const [query, setQuery] = useState("");
  // The OPEN approach's `approachIdentity` — target-scoped, not just its
  // name, so two sibling targets whose approaches share a name (fix round 1)
  // can never accidentally open/close together.
  const [expanded, setExpanded] = useState(null);

  const rows = targets || [];
  const entityKey = (rows[0] && rows[0].entity_key) || null;
  // FINAL REVIEW FIX (important: blank-titled book mark). An entity the sheet
  // never mapped (78 of every 84 segments, measured against the shipped
  // corpus) returns `{targets: []}` from `/api/library/entity/{key}`, so
  // `rows[0]` never exists and `label` fell back to `""` -- a real page with
  // a real "No community times recorded here yet." paragraph, but under an
  // EMPTY `<h3>`, which reads as broken rather than honest (acceptance.md's
  // rule against a control that leads nowhere). `fallbackLabel` is the
  // opening card's own name (library.js resolves it off the current session
  // view, `entityLabel`, the same helper the mixed-entity Study note already
  // uses) -- carried only for the empty case; a real sheet label always wins.
  const label = (rows[0] && rows[0].label) || fallbackLabel || "";
  const missReason = rows.length === 1 ? rows[0].miss_reason : null;
  // The PAGE's own identity, for the auto-expand one-shot below -- never
  // `entityKey`. `rows[].index` is stable within one loaded payload (the
  // same numeric door the picker's own numeric branch already addresses a
  // target by; `library/store.py`'s `index()`/`for_entity()`/`target()` all
  // stamp it identically), so joining every row's own index names THIS page
  // uniquely whether or not it has an entity at all.
  const pageIdentity = rows.map((row) => row.index).join(",");

  const approaches = useMemo(() => sectionOrder(
    rows.flatMap((target) => (target.approaches || []).map((approach) => (
      { ...approach, _target: target.label, _targetIndex: target.index }))),
  ), [rows]);

  const activeStrat = activeStratFor(t && t.view, entityKey);
  const stratsData = useEntityStrategies(entityKey);
  const stratByName = useMemo(() => {
    const map = {};
    ((stratsData && stratsData.strategies) || []).forEach((entry) => { map[entry.name] = entry; });
    return map;
  }, [stratsData]);

  // Auto-expand once per PAGE — a deliberate one-shot, so a click the user
  // makes afterward is never silently reverted by a later render of the same
  // page (autoExpandName(ordered, t.view's active strat), brief step 1).
  // `autoExpandName` is Task 2's own contract and returns a bare `.name`
  // (never a target-scoped identity — that is not its job); resolved to the
  // FIRST approach carrying that name, matching `autoExpandName`'s own
  // internal `Array.find` semantics exactly, so this never picks a DIFFERENT
  // section than the one autoExpandName itself intended.
  //
  // FIX ROUND 3 (controller finding): keyed on `pageIdentity`, NOT
  // `entityKey`. Keying on `entityKey` broke two ways at once for the 129 of
  // 252 targets that have no entity at all (every Castle Movement, every
  // stage RTA, one not-a-target) -- `entityKey` is `null` for every one of
  // them, and the old guard's own sentinel started at `null` too, so the
  // very first comparison (`null === null`) was already true on mount and
  // the check returned before ever running: auto-expand never fired,
  // period, on more than half the library's target pages (190 approaches
  // across those 129 targets, including "CCM RTA"'s 4 approaches / 138
  // entries). Swapping only the sentinel's initial value would have fixed
  // the mount case and left navigation broken: `entityKey` stays `null` for
  // EVERY entity-less target, so hopping from one to another would still
  // read as "already opened this page" and never re-fire. Same failure
  // shape as the tray key two fix rounds ago -- an identity two genuinely
  // different things can share, here two different castle-movement targets
  // sharing the one value every entity-less target has.
  //
  // FIX ROUND 4 (controller finding, from the re-reviewer's own probe):
  // computed DURING RENDER, not in a `useEffect`. An effect commits one
  // FRAME AFTER the section headers this page draws already exist and are
  // clickable, so a click landing in that gap was silently reverted the
  // instant the effect caught up -- measured at 5 of 10 clicks reverted with
  // no artificial wait: a coin flip, not a narrow window, and worse on a
  // loaded machine than an idle one, since the gap is a TICK rather than a
  // fixed delay. This is React/Preact's own documented pattern for state
  // that must reset when a key changes but stay freely user-overridable in
  // between: compare against the identity the LAST RENDER saw, and adjust
  // `expanded` right here rather than scheduling a later commit. Calling
  // `setExpanded` during render makes Preact re-run this render with the new
  // value BEFORE anything paints, so there is no commit where a section is
  // clickable and auto-expand has not yet decided -- nothing runs AFTER a
  // click to revert it, because nothing runs after render at all for this.
  // `openedPage` starts at `null`, never at `pageIdentity` itself, because
  // `pageIdentity` is always a STRING (even `""` for an empty `rows`) and
  // `null` can never equal a string -- the same trap `entityKey`'s old
  // sentinel fell into, avoided this time by picking one no real value can
  // collide with, not merely one that starts out different from today's
  // data. WHAT this decides is unchanged from round 3 (still once per page,
  // still `autoExpandName`'s own resolution) -- only WHEN it decides moved.
  const [openedPage, setOpenedPage] = useState(null);
  if (openedPage !== pageIdentity) {
    setOpenedPage(pageIdentity);
    const wantedName = autoExpandName(approaches, activeStrat);
    const hit = approaches.find((approach) => approach.name === wantedName);
    setExpanded(hit ? approachIdentity(hit) : null);
  }

  // A deep link (Task 7: the standards ladder's own tier rows, and the
  // book mark) moves you once per LINK, then goes quiet — task-7-caveats.md
  // point 2's own ruling. The effect's dependency list includes `approaches`
  // (needed to resolve the `hit`), and `approaches` is a `useMemo` over
  // `rows` — so with no guard, ANY later `rows` change (a library refresh, a
  // re-fetch, a second intent landing on this same entity) re-runs this
  // effect and snaps `expanded` back to the SAME stale link, silently
  // reverting a click the user made in between. This project rejects exactly
  // that shape everywhere else (CLAUDE.md's rank-up-on-load ruling: an action
  // needs a gesture of his own, and "a page refetch happened to land while he
  // was browsing something else" is not one). `consumedFocusRef` is the same
  // "consume it once" treatment `library.js` already gives its own `intent`
  // prop (`clearIntent`, called the instant `openEntity` reads it) — here
  // expressed as a ref rather than a callback, since there is no owner above
  // this component to hand a clear-signal back to. Keyed on `pageIdentity`
  // too, not just `focusStrat`/`focusTier`: a DIFFERENT page that happens to
  // reuse the same strategy name is a fresh link, never "the same one
  // already consumed" (pageIdentity's own comment, above, has the reasoning
  // for why it — not `entityKey` — is what names a page).
  const consumedFocusRef = useRef(null);
  useEffect(() => {
    if (!focusStrat) return undefined;
    const focusId = `${pageIdentity}::${focusStrat}::${focusTier || ""}`;
    if (consumedFocusRef.current === focusId) return undefined;
    // `approaches.find` here still resolves by NAME alone and can still land
    // on the first of two sibling sections that share one `matched_strategy`
    // (the 100-coin case, caveat 4) — that is not this fix's bug to close:
    // the two sections show the IDENTICAL rank/PB for that strategy by
    // design ("your rank on a strategy is the same fact wherever it
    // appears"), so a strategy-named link has no third piece of information
    // to disambiguate WHICH sibling with, and landing on either is correct.
    const hit = approaches.find((approach) =>
      approach.matched_strategy === focusStrat || approach.name === focusStrat);
    if (!hit) return undefined;  // approaches not loaded yet -- stay
                                  // unconsumed, try again next render
    consumedFocusRef.current = focusId;
    setExpanded(approachIdentity(hit));
    // Disclose mounts the section body a tick after `open` flips — see its
    // own docstring ("Preact commits after the tick"). A short timer beats a
    // race against that mount rather than guessing a single rAF is enough.
    const timer = setTimeout(() => {
      const id = focusTier ? bandAnchorId(hit, focusTier) : sectionAnchorId(hit);
      document.getElementById(id)?.scrollIntoView({ block: "start", behavior: "smooth" });
    }, 80);
    return () => clearTimeout(timer);
  }, [focusStrat, focusTier, approaches, pageIdentity]);

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
    ${/* FINAL REVIEW FIX (minor: shared class). This box used to share
         `.library-search` with segments.js's own segment-library filter --
         one CSS change for either surface silently restyled the other, and
         it already broke the segments-editor uilab story once
         (tests/test_uilab_story_setup_scoping.py). Its own class now. */""}
    <input class="library-target-search" type="search" value=${query}
        placeholder="Search runners…" aria-label="Search runners"
        oninput=${(inputEvent) => setQuery(inputEvent.target.value)} />
    ${approaches.length === 0
      ? html`<p class="library-target-empty">
          ${missReason === "castle_movement" ? "Browse only — no segment adopts this movement yet."
            : missReason === "route" ? "Stage route — browse the sheet, no per-target ladder here."
            : "No community times recorded here yet."}
        </p>`
      : approaches.map((approach) => html`<${Section} key=${approachIdentity(approach)}
          approach=${approach} open=${expanded === approachIdentity(approach)}
          onOpen=${() => setExpanded(approachIdentity(approach))} query=${query}
          stratInfo=${approach.matched_strategy ? stratByName[approach.matched_strategy] : null}
          trayKeys=${trayKeys} entityKey=${entityKey} onAdd=${onAdd} />`)}
  </div>`;
}
