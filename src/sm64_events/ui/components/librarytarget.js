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
import { getJSON, send } from "../api.js";
import { fmtSeconds } from "../format.js";
import { entityKey as sectionEntityKey } from "../entitysection.js";
import { entityIconSrc, genericStarSrc } from "./entityicons.js";
import { RankIcon } from "./rankicon.js";
import { capName, divisionDigit } from "./caps.js";
import { Disclose } from "./collapsible.js";
import { Icon } from "./icons.js";
import { SearchMenu } from "./searchselect.js";
import { OverallStandards } from "./overallstandards.js";
import { SegmentTimeline } from "./segmenttimeline.js";
import {
  sectionOrder, autoExpandName, bandsOf, bandRangeLabel, divisionRangeLabel,
  matchesRunner, videoSource, linkable, standingOn, bandFor, divisionWithin,
  ladderCsOf,
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
// A PIECE keys on its `row_key`, not its name: 19 targets repeat a row name
// ("Warp fadeout" appears twice under Big Bob-omb) and the audit's own
// uniqueness guarantee is on row keys, never names. Approaches keep the
// display identity — their name-sharing case (matched_strategy across
// siblings) is deliberately unresolved (see the focusStrat effect).
const approachIdentity = (approach) =>
  approach._piece && approach.row_key
    ? `${approach._targetIndex}-piece-${slug(approach.row_key)}`
    : `${approach._targetIndex}-${slug(approach.matched_strategy || approach.name)}`;
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

// Round 6: the PB behind every ASSOCIATED row (linked or name-matched) --
// the same step-3 payload useEntityStrategies reads, one fetch per distinct
// segment, reduced to the entity's best saved PB across its strategies.
// Name-agnostic on purpose: "it reads whatever the data was for that
// segment", and his practice may sit under any strategy name.
function useAssocStandings(entityKeys) {
  const [standings, setStandings] = useState({});
  const requested = useRef(new Set());
  useEffect(() => {
    let cancelled = false;
    entityKeys.forEach((key) => {
      if (requested.current.has(key)) return;
      requested.current.add(key);
      getJSON(`/api/target/strategies?entity=${enc(key)}`)
        .then((result) => {
          if (cancelled) return;
          const timed = (result.strategies || [])
            .filter((strategy) => strategy.pb_cs != null);
          const best = timed.length
            ? timed.reduce((a, b) => (a.pb_cs <= b.pb_cs ? a : b)) : null;
          setStandings((prev) => ({
            ...prev,
            [key]: { pbCs: best ? best.pb_cs : null,
                     pbDisplay: best ? best.pb_display : null } }));
        })
        .catch(() => {
          // Loaded-with-nothing, not stuck-loading: the row still shows an
          // honest Capless rather than waiting on a fetch that failed.
          if (!cancelled) setStandings((prev) => ({
            ...prev, [key]: { pbCs: null, pbDisplay: null } }));
        });
    });
    return () => { cancelled = true; };
  }, [entityKeys.join(",")]);
  return standings;
}

// Which segment a row is associated with, if any: an explicit assignment
// (linked, unlink lives on the row) outranks the target's name-match.
function assocFor(row, standings, resolveLabel) {
  const matched = row._matchedSegment;
  const entity = row.adopted || (matched && matched.entity) || null;
  if (!entity) return null;
  const standing = standings[entity];
  return { entity,
           name: row.adopted ? resolveLabel(row.adopted) : matched.name,
           source: row.adopted ? "linked" : "matched",
           loaded: !!standing,
           pbCs: standing ? standing.pbCs : null,
           pbDisplay: standing ? standing.pbDisplay : null };
}

// One video-bearing entry, as a card big enough to watch (round 1: "each grid
// element should be much bigger ... I also can't see the times very easily,
// nor the names for each of the players"). Videoless entries never reach this
// component any more -- they render as PlainEntry rows, because a play button
// on a run nobody filmed is what "the videos don't load for most of them"
// turned out to be (37,268 of 44,701 entries carry no video at all).
//
// `videoSource` (librarymodel.js) names the player per format: YouTube shows
// its real thumbnail and swaps to its embed on click; Twitch/X/Bluesky/
// Streamable/Drive show a labelled tile that swaps to their own embed on
// click; a direct media file plays natively; anything unembeddable is an
// honest "watch on <site>" door that opens a new tab -- never a dead play
// button. The version pill this card used to wear is GONE (round 1 item 4
// superseded it: the section's mode now filters entries, so every visible
// run is the mode's own version).
function ExampleCard({ entry, tier, division, trayKey, entityKey, inTray, onAdd }) {
  const [playing, setPlaying] = useState(false);
  // Bluesky's embed host takes a DID, and most sheet links carry a handle --
  // resolved with ONE public-API fetch on the first click (videoSource's own
  // bsky comment has the measurement). Failure degrades to the link-out door.
  const [bskyEmbed, setBskyEmbed] = useState(null);
  const [bskyFailed, setBskyFailed] = useState(false);
  const src = videoSource(entry.video,
    typeof location !== "undefined" ? location.hostname : null);
  const embedSrc = src && (src.embed
    || (src.kind === "bsky" && !bskyFailed ? bskyEmbed : null));
  const canEmbed = !!(src && src.kind !== "file" && src.kind !== "image"
    && src.kind !== "link"
    && (embedSrc || (src.kind === "bsky" && !bskyFailed)));
  const label = `${entry.runner} — ${fmtSeconds(entry.time_cs / 100)}`;

  function startPlaying() {
    if (src.kind === "bsky" && !src.embed && !bskyEmbed && !bskyFailed) {
      fetch("https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle"
            + `?handle=${enc(src.actor)}`)
        .then((resp) => resp.json())
        .then((body) => {
          if (!body || !body.did) throw new Error("no did");
          setBskyEmbed(`https://embed.bsky.app/embed/${body.did}/app.bsky.feed.post/${src.rkey}`);
        })
        .catch(() => setBskyFailed(true));
    }
    setPlaying(true);
  }

  function media() {
    if (src.kind === "file") {
      return html`<video class="library-example-thumb" src=${src.embed}
          controls muted preload="metadata" title=${label}></video>`;
    }
    if (src.kind === "image") {
      return html`<img class="library-example-thumb" src=${src.thumb} alt=${label}
          loading="lazy" />`;
    }
    if (src.kind === "link" || bskyFailed) {
      return html`<a class="library-example-thumb library-example-placeholder"
          href=${entry.video} target="_blank" rel="noopener"
          title="opens in a new tab">
        <${Icon} name="play" size=${22} />
        <span class="library-example-site">watch on ${src.site}</span>
      </a>`;
    }
    if (playing && embedSrc) {
      return html`<iframe class="library-embed" src=${embedSrc} title=${label}
          allow="autoplay; encrypted-media" allowfullscreen></iframe>`;
    }
    // The labelled placeholder always renders; a thumbnail (YouTube only)
    // stacks OVER it and removes itself if the host never answers -- a dead
    // or still-loading thumb must degrade to the branded tile, never to a
    // black box that reads as a broken player.
    return html`<div class="library-example-thumb library-example-placeholder is-clickable"
        onclick=${startPlaying}>
      <${Icon} name="play" size=${30} />
      <span class="library-example-site">Play on ${src.site}</span>
      ${src.thumb
        ? html`<img class="library-example-thumb library-example-thumb-img"
            src=${src.thumb} alt="" loading="lazy"
            onerror=${(errorEvent) => errorEvent.target.remove()} />`
        : ""}
    </div>`;
  }

  return html`<div class="library-example" data-video=${entry.video}>
    <div class="library-example-media ${canEmbed ? "is-clickable" : ""}"
        onclick=${canEmbed && playing ? () => setPlaying(false) : null}
        title=${canEmbed ? (playing ? "Close" : "Play inline") : ""}>
      ${media()}
      ${src.kind !== "youtube" && src.kind !== "file" && src.kind !== "image"
        ? html`<a class="library-example-external" href=${entry.video}
            target="_blank" rel="noopener" title=${`open on ${src.site}`}>
            <${Icon} name="upload" size=${13} /></a>` : null}
    </div>
    <div class="library-example-meta">
      <span class="rank-icon-slot library-example-tier" style="--icon-size: 20px">
        ${tier ? html`<${RankIcon} tier=${tier} division=${division} size=${20} />` : "–"}</span>
      <span class="library-example-runner-wrap">
        <span class="library-example-runner">${entry.runner}</span>
      </span>
      <span class="library-example-time">${fmtSeconds(entry.time_cs / 100)}</span>
      <button type="button" class="library-example-plus"
          disabled=${inTray}
          title=${inTray ? "already in the tray" : "add to the comparison tray"}
          onclick=${(clickEvent) => {
            clickEvent.stopPropagation();
            onAdd({ key: trayKey, runner: entry.runner, time_cs: entry.time_cs,
                     video: entry.video, entity_key: entityKey, strat: null, trim: null });
          }}>+</button>
    </div>
  </div>`;
}

// A run nobody filmed: still evidence (a real runner, a real time, a real
// subdivision), never a video card. No "+" -- the tray imports videos, and a
// row with nothing to import must not offer the gesture.
function PlainEntry({ entry, tier, division }) {
  return html`<span class="library-plain-entry">
    ${tier ? html`<span class="rank-icon-slot" style="--icon-size: 15px">
      <${RankIcon} tier=${tier} division=${division} size=${15} /></span>` : ""}
    <span class="library-plain-runner">${entry.runner}</span>
    <span class="library-plain-time">${fmtSeconds(entry.time_cs / 100)}</span>
  </span>`;
}

// One subdivision of one tier band -- "Wario 3" -- collapsed by default
// (round 1: "have each subdivision be collapsable by default, and collapsed.
// So, I would go through and be able to find the exact subdivision I want to
// look at"). The header is the browse surface: the winged, numbered cap, the
// name, the time bracket, the count, and YOU when this is where you stand.
// An empty subdivision renders as a plain dimmed row, NOT a button -- a
// control that expands to nothing is a dead control, and the reason ("no
// examples") sits where the click would land.
function DivisionGroup({ approach, band, division, query, isYou, trayKeys,
                         entityKey, onAdd, autoOpen = false }) {
  const [open, setOpen] = useState(false);
  // A standards-table deep link names THIS subdivision (round 3, task 0098):
  // open once per arrival, during render (the openedPage pattern below — an
  // effect would land a frame after the header is already clickable), and
  // re-arm when the mark moves elsewhere so a later link can land again. The
  // user's own toggle stays free in between.
  const [autoConsumed, setAutoConsumed] = useState(false);
  if (autoOpen && !autoConsumed) { setAutoConsumed(true); setOpen(true); }
  if (!autoOpen && autoConsumed) setAutoConsumed(false);
  const visible = division.entries.filter((entry) => matchesRunner(entry, query));
  // ROUND 4: a live query filters the STRUCTURE, not just the cards --
  // "hide the subdivisions / ranks that don't show up for the search ...
  // and open any dropdowns to show it". A subdivision with no match
  // disappears; one with matches is FORCED open for the query's duration.
  // The manual open/closed choice survives underneath (`open` still
  // toggles) and returns the moment the query clears.
  const searching = !!query;
  if (searching && !visible.length) return null;
  const effectiveOpen = searching || open;
  const label = `${capName(band.tier)} ${divisionDigit(division.numeral)}`;
  const bracket = divisionRangeLabel(division);
  const you = isYou
    ? html`<span class="library-toc-you" title="your current rank on this strategy"> ◀ you</span>` : "";

  if (!division.entries.length) {
    return html`<div class="library-division is-empty">
      <span class="library-division-label">
        <span class="rank-icon-slot" style="--icon-size: 16px">
          <${RankIcon} tier=${band.tier} division=${division.numeral} size=${16} /></span>
        ${label}</span>
      <span class="meta">${bracket}</span>
      <span class="meta library-division-count">no examples</span>${you}
    </div>`;
  }
  const withVideo = visible.filter((entry) => entry.video);
  const plain = visible.filter((entry) => !entry.video);
  return html`<div class="library-division ${effectiveOpen ? "open" : ""}">
    <button type="button" class="library-division-head" aria-expanded=${effectiveOpen}
        onclick=${() => setOpen((prev) => !prev)}>
      <span class="library-division-label">
        <span class="rank-icon-slot" style="--icon-size: 18px">
          <${RankIcon} tier=${band.tier} division=${division.numeral} size=${18} /></span>
        ${label}</span>
      <span class="meta">${bracket}</span>${you}
      <span class="meta library-division-count">
        ${withVideo.length ? `${withVideo.length} video${withVideo.length === 1 ? "" : "s"}` : ""}
        ${withVideo.length && plain.length ? " · " : ""}
        ${plain.length ? `${plain.length} time${plain.length === 1 ? "" : "s"}` : ""}
      </span>
      <${Icon} name="chevron" size=${14} className="library-division-chevron" />
    </button>
    <${Disclose} open=${effectiveOpen} className="library-division-disclose">
      <div class="library-division-body">
        ${withVideo.length ? html`<div class="library-examples">
          ${withVideo.map((entry) => {
            // Same identity as before the subdivision split: approach-scoped
            // plus runner+time+video (see entryTrayKey's own history above).
            const trayKey = entryTrayKey(approach, entry);
            return html`<${ExampleCard} key=${trayKey}
                entry=${entry} tier=${band.tier} division=${division.numeral}
                trayKey=${trayKey} entityKey=${entityKey}
                inTray=${trayKeys.has(trayKey)}
                onAdd=${onAdd} />`;
          })}
        </div>` : ""}
        ${plain.length ? html`<div class="library-plain-rows">
          ${plain.map((entry) => html`<${PlainEntry}
              key=${`${entry.runner}:${entry.time_cs}`}
              entry=${entry} tier=${band.tier} division=${division.numeral} />`)}
        </div>` : ""}
      </div>
    <//>
  </div>`;
}

// The range of displayed times one tier band owns, as a string -- round 3:
// "the header for each section should be adjusted to show the RANGE for that
// rank", and the TOC's number column "should also show what the range is ...
// Capless included (e.g., 59"65+ (the slowest time) to 11"93". Capless has
// no cutoff, so its slow end is the slowest VISIBLE entry with his own "+"
// notation; a Capless band with no entries never renders (bandsOf filters
// it). The fast end is the exclusive bound (librarymodel.js::tierFastBoundCs),
// so no number repeats between adjacent tiers.
// `bandRangeLabel` / `divisionRangeLabel` moved to librarymodel.js on
// 2026-08-10 -- the Overall Rank Standards section prints the same spans, and
// two copies of "the fast end is one centisecond slower than the next unit's
// slow end" is exactly the divergence this project has a rule against.

// One tier row of the TOC. `cutoffCs` is null for the Capless floor (no
// cutoff — the catch-all for anything that has not beaten one yet) and for
// the tier-less Unranked band a no-ladder approach shows. `you` is the
// stratInfo of the reader when this row is their tier, so the marker can name
// the exact subdivision (round 1: "the 'YOU' display should consider the
// subdivision").
function TocRow({ band, count, you, onJump }) {
  // Tier-level surfaces wear the DIVISION-I cap (round 2: "the OVERALL
  // symbol for each overall tier should be the highest level cap symbol ...
  // the empty cap kinda looks weird") -- numeral + full wings, with Capless
  // keeping its own no-wings law. Inside a `.rank-icon-slot` so the wings
  // reserve their spill instead of crowding the name (ui-ranks.md).
  return html`<tr class="library-toc-row" onclick=${onJump}>
    <td class="library-toc-tier">
      ${band.tier ? html`<span class="rank-icon-slot" style="--icon-size: 16px">
        <${RankIcon} tier=${band.tier} division=${"I"} size=${16} /></span>` : ""}
      ${band.tier ? capName(band.tier) : "Unranked"}${you
        ? html`<span class="library-toc-you" title="your current rank on this strategy">
            ${" "}◀ you${you.division ? ` · ${capName(you.rank)} ${divisionDigit(you.division)}` : ""}</span>`
        : ""}
    </td>
    <td class="library-toc-cutoff">${band.tier ? bandRangeLabel(band) : "—"}</td>
    <td class="library-toc-count">${count}</td>
  </tr>`;
}

/**
 * ROUND 5 -- the link door. `library/adoptions.py` and its adopt/unadopt
 * routes shipped with the backend and had NO UI caller (the audit tool was
 * the only assign surface), which made linking a movement or a subsection to
 * a segment a capability that did not exist for the user. This is the
 * one-click door: pick one of YOUR segments and the row's community ladder
 * becomes that segment's strategy, gradeable like any other.
 *
 * Refusals surface VERBATIM where the click landed -- `validate()` already
 * names every reason (no ladder, exit-star variants, a vetted name
 * collision) and pre-filtering rows client-side would be a second door on
 * that judgement. A linked row shows the segment's name and an Unlink, so a
 * mis-click never dead-ends into the audit tool.
 */
function LinkDoor({ linkedName, linkedNote, offerNote, doAdopt, doUnlink,
                    segments, segmentsError, onRecord = null }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function act(request) {
    setBusy(true); setError(null);
    try { await request(); setOpen(false); }
    catch (err) { setError(err.message || String(err)); }
    finally { setBusy(false); }
  }

  if (linkedName) {
    return html`<div class="library-link-state is-linked">
      <${Icon} name="link" size=${14} />
      <span>Linked to your segment <b>${linkedName}</b>${linkedNote}</span>
      <button type="button" class="quiet-button library-unlink" disabled=${busy}
          onclick=${() => act(doUnlink)}>Unlink</button>
      ${error ? html`<span class="library-link-error">${error}</span>` : ""}
    </div>`;
  }

  // The menu is the SHARED filterable popup (searchselect.js, round 9 --
  // "we should basically reuse this anywhere we have a dropdown selector"),
  // overlaid below the button, which stays rendered: his standing ruling on
  // transient controls (2026-07-26). This door keeps its own open state so
  // a failed adopt shows the server's reason in the still-open panel.
  return html`<div class="library-link-state">
    ${offerNote ? html`<span class="meta">${offerNote}</span>` : ""}
    <button type="button" class="quiet-button library-link-button"
        aria-expanded=${open}
        onclick=${() => setOpen((prev) => !prev)}>
      <${Icon} name="link" size=${14} /> Link a segment…
    </button>
    ${/* Task 0096: the row that has no segment yet should not send him to
         the Segments tab to make one — the SAME recorder opens right here,
         pre-named after the row, and the save links itself. One recorder
         implementation (segmenttimeline.js), so the two doors cannot
         drift. */""}
    ${onRecord ? html`<button type="button"
        class="quiet-button library-record-button" onclick=${onRecord}>
      <${Icon} name="bookmark" size=${14} /> Record a segment…
    </button>` : ""}
    ${open ? html`<${SearchMenu}
        title="Link to one of your segments"
        groups=${segments && segments.length
          ? [{ label: null,
               options: segments.map((segment) =>
                 ({ value: segment.id, label: segment.name })) }]
          : []}
        emptyNote=${segmentsError ? "Could not load your segments."
          : segments == null ? "Loading your segments…"
          : "You have no segments yet — build one in the segment editor first."}
        busy=${busy} error=${error || segmentsError}
        onPick=${(segmentId) => act(() => doAdopt(segmentId))}
        onClose=${() => setOpen(false)} />` : ""}
  </div>`;
}

// A PIECE's own row-level door (round 5, narrowed round 7: approaches no
// longer carry one -- the whole-target door in the page header covers them).
function LinkControl({ row, kind, entityKey, adoptable, segments, segmentsError,
                       onRelink, resolveLabel, openRecorder }) {
  const linked = !!row.adopted;
  if (!linked && !(adoptable && linkable({ entity_key: entityKey }, row, kind))) {
    return null;
  }
  return html`<${LinkDoor}
      linkedName=${linked ? resolveLabel(row.adopted) : null}
      linkedNote=${" — it grades there now."}
      offerNote="Not graded yet — link one of your segments to grade it."
      doAdopt=${async (segmentId) => {
        await send("POST", "/api/library/adopt",
          { row_key: row.row_key, entity_key: `segment:${segmentId}` });
        await onRelink();
      }}
      doUnlink=${async () => {
        await send("POST", "/api/library/unadopt", { row_key: row.row_key });
        await onRelink();
      }}
      segments=${segments} segmentsError=${segmentsError}
      onRecord=${openRecorder ? () => openRecorder({
        key: `piece-${row.row_key}`,
        // The row's own name IS the segment's name — "it gets automatically
        // named and associated with that star" (task 0096) — and the target's
        // entity pre-fills `parents`, which is what makes the recording a
        // [[subsection]] of the star he is looking at.
        name: row.name,
        parents: entityKey ? [entityKey] : [],
        link: async (segmentId) => {
          await send("POST", "/api/library/adopt",
            { row_key: row.row_key, entity_key: `segment:${segmentId}` });
        } }) : null} />`;
}

/**
 * ROUND 7 -- the whole-target door, beside the target's own name: "the link
 * a segment button should be moved near the top, next to the name ... as an
 * 'overarching' thing. If we link a segment, then it should automatically
 * load ALL strategies for that segment." One click adopts every laddered
 * approach onto the segment (`POST /api/library/adopt_target`), so the
 * segment's picker gains every documented way at once; Unlink reverses the
 * whole batch. A name-matched target wears the chip instead -- the match
 * already IS the association.
 */
function TargetLinkControl({ rows, approaches, linkCtx }) {
  if (!linkCtx) return null;
  const target = rows.length === 1 ? rows[0] : null;
  // Entity pages (stars) never link -- their approaches auto-adopt.
  if (!target || target.entity_key || !target.adoptable) return null;
  const linkedEntity = (approaches.find((approach) => approach.adopted) || {}).adopted
    || null;
  if (!linkedEntity && target.matched_segment) {
    return html`<span class="chip library-matched-chip"
        title="This target matches a segment you already have, by name — no linking needed.">
        = your segment "${target.matched_segment.name}"</span>`;
  }
  if (!linkedEntity && !approaches.some((approach) =>
      approach.row_key && approach.ladder)) {
    return null;                 // nothing gradeable to link
  }
  return html`<${LinkDoor}
      linkedName=${linkedEntity ? linkCtx.resolveLabel(linkedEntity) : null}
      linkedNote=${" — every strategy here grades there now."}
      offerNote=${null}
      doAdopt=${async (segmentId) => {
        await send("POST", "/api/library/adopt_target",
          { target_index: target.index, entity_key: `segment:${segmentId}` });
        await linkCtx.onRelink();
      }}
      doUnlink=${async () => {
        await send("POST", "/api/library/unadopt_target",
          { target_index: target.index });
        await linkCtx.onRelink();
      }}
      segments=${linkCtx.segments} segmentsError=${linkCtx.segmentsError}
      onRecord=${linkCtx.openRecorder ? () => linkCtx.openRecorder({
        key: `target-${target.index}`,
        // A whole movement, not a piece of anything: name it after the
        // target, leave `parents` empty (a castle movement is as high as
        // the castle goes — round 14's own ruling), link the whole target.
        name: target.label,
        parents: [],
        link: async (segmentId) => {
          await send("POST", "/api/library/adopt_target",
            { target_index: target.index, entity_key: `segment:${segmentId}` });
        } }) : null} />`;
}

/**
 * ROUND 5: the sheet's subsections -- stretches inside a run that no
 * strategy section owns -- rendered nowhere until the link door needed a
 * place to click them. They were compact rows ("the full display can come
 * later if browsing pieces turns out to matter") until task 0100, when it
 * did: "each piece should still have the same exact card layout +
 * information as a normal star strategy". Each piece is now the SAME
 * `Section` a strategy renders through -- full band TOC, subdivisions,
 * example cards, JP/US mode -- joining the page's single-open accordion.
 * The one difference is the door: a piece keeps its own link/record strip
 * (a strategy's linking is target-level, round 7).
 */
function PiecesList({ pieces, query, expanded, onOpen, trayKeys, entityKey,
                      onAdd, linkCtx }) {
  if (!pieces.length) return null;
  return html`<div class="library-pieces">
    <h4 class="library-pieces-head">Pieces of this run</h4>
    ${pieces.map((piece) => html`<${Section} key=${approachIdentity(piece)}
        approach=${piece} open=${expanded === approachIdentity(piece)}
        onOpen=${() => onOpen(approachIdentity(piece))}
        query=${query} stratInfo=${null}
        trayKeys=${trayKeys}
        entityKey=${piece.entity_key || entityKey} onAdd=${onAdd}
        linkCtx=${linkCtx}
        door=${html`<${LinkControl} row=${piece} kind="subsection"
            entityKey=${piece._entityKey} adoptable=${piece._adoptable}
            ...${linkCtx} />`} />`)}
  </div>`;
}

/**
 * One strategy's section: header (identity, community best, fill rate, your
 * standing), an optional JP/US toggle, the TOC, and the banded example
 * cards. `open` is owned by the PARENT (single-open accordion, so "exactly
 * one section open" is a property of the parent's own state rather than
 * something every section has to negotiate).
 */
function Section({ approach, open, onOpen, query, stratInfo, trayKeys, entityKey, onAdd,
                   linkCtx, door = null, focusMark = null }) {
  // ROUND 1 (2026-08-07), superseding the round-2 version-badge ruling: the
  // JP/US control is a MODE now, and a mode FILTERS -- "We should have 2
  // modes: JP (shows only JP entries), US (shows only US entries)." An entry
  // tagged with the other version disappears; an entry never annotated with a
  // version shows in both modes (the combined-unless-annotated rule, applied
  // to display). With every visible run being the mode's own version, the
  // per-entry version pill that used to badge mixed sections had nothing
  // left to say and is deleted. Bands and counts are computed AFTER the
  // filter, so every number on screen describes what is actually shown.
  const [version, setVersion] = useState("us");
  const hasJp = !!approach.ladder_jp;
  const mixedVersions = useMemo(() => new Set(
    (approach.entries || []).map((entry) => entry.version).filter(Boolean),
  ).size > 1, [approach.entries]);
  const versioned = hasJp || mixedVersions;
  const ladder = (hasJp && version === "jp" ? approach.ladder_jp : approach.ladder) || {};
  const visibleEntries = useMemo(() => (versioned
    ? (approach.entries || []).filter((entry) => !entry.version || entry.version === version)
    : (approach.entries || [])), [approach.entries, versioned, version]);
  const bands = useMemo(() => bandsOf(ladder, visibleEntries),
    [ladder, visibleEntries]);
  // ROUND 4: a live query hides every band with no matching runner -- TOC
  // row and band section both -- so the page shows "only the actual
  // results". The full structure returns when the query clears.
  const shownBands = query
    ? bands.filter((band) => band.entries.some((entry) => matchesRunner(entry, query)))
    : bands;
  const marioKey = approach.ladder && approach.ladder.Mario != null
    ? approach.ladder.Mario : -1;   // presentational echo of sectionOrder's own key; -1 (not -Infinity) so it survives JSON round-trips a render probe takes

  // Round 6: an associated row's standing -- his segment PB graded by THIS
  // section's displayed ladder (the JP/US-resolved one the entries file
  // under), Capless when no PB. A star's matched strategy keeps its served
  // standing (there the displayed ladder IS the grading ladder); the one
  // named difference is that an association's ladder may not be, so it
  // walks the displayed one. Rendered only once loaded -- a Capless flash
  // that flips to Gold reads as the page changing its mind.
  const assoc = linkCtx
    ? assocFor(approach, linkCtx.assocStandings, linkCtx.resolveLabel) : null;
  const assocInfo = assoc && assoc.loaded
    ? { ...standingOn(ladder, assoc.pbCs),
        pb_display: assoc.pbDisplay, noTimes: assoc.pbCs == null }
    : null;
  const standing = stratInfo || assocInfo;

  return html`<div class=${`library-section ${open ? "open" : ""}`
        + (approach._piece ? " library-piece-section" : "")}
      data-mario=${marioKey}
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
          ${/* Round 7: the "= your segment" chip lives beside the page's
               own name now (TargetLinkControl) -- the association is
               target-level and repeating it per section was noise. The
               section keeps only its STANDING. */""}
        </div>
        <div class="library-section-facts">
          ${approach.best_cs != null
            ? html`<span class="meta">Best ${fmtSeconds(approach.best_cs / 100)} · ${approach.best_runner}</span>` : ""}
          ${approach.fill_rate != null
            ? html`<span class="meta">Fill ${Math.round(approach.fill_rate * 100)}%</span>` : ""}
          ${standing
            ? html`<span class="meta library-your-standing"
                title=${standing.noTimes ? "Capless — no recorded time on this segment yet." : undefined}>
                ${standing.rank ? html`<${RankIcon} tier=${standing.rank} division=${standing.division} size=${16} />` : "Not yet ranked"}
                ${standing.pb_display ? html` · ${standing.pb_display}`
                  : standing.noTimes ? " · no times yet" : ""}
              </span>` : ""}
        </div>
      </div>
      <${Icon} name="chevron" size=${16} className="library-section-chevron" />
    </button>
    ${/* A PIECE's link/record door (task 0100) — OUTSIDE the fold, because
         "Linked to your segment X" and the record offer are the row's
         at-a-glance state, and outside the head <button>, because a button
         may not contain a button. Approaches pass no door (round 7: the
         whole-target control owns theirs). */""}
    ${door ? html`<div class="library-section-door">${door}</div>` : ""}
    <${Disclose} open=${open} className="library-section-disclose">
      <div class="library-section-body">
        ${/* Round 7: approaches carry NO row-level door -- the whole-target
             control beside the page's name owns linking now ("moved near
             the top ... as an 'overarching' thing"). Pieces keep theirs. */""}
        ${versioned ? html`<button type="button" class="chip chip-button library-jp-toggle"
            aria-pressed=${version === "jp"}
            title=${hasJp
              ? `Showing only ${version === "jp" ? "JP" : "US"} entries, banded against the ${version === "jp" ? "JP" : "US"} ladder.`
              : `Showing only ${version === "jp" ? "JP" : "US"} entries. This approach has one ladder for both versions.`}
            onclick=${() => setVersion((prev) => (prev === "jp" ? "us" : "jp"))}>
            ${version === "jp" ? "JP" : "US"} mode · switch
          </button>` : ""}
        ${/* FINAL REVIEW FIX (medium: ladder_version read nowhere). A row
             with too few of the OTHER version's times to fit a second ladder
             still gets ONE, fitted entirely from whichever population it has
             -- 13 approaches fit from JP-only times, no `ladder_jp` companion
             (too few US runs to earn one). The chip renders INDEPENDENTLY of
             the mode toggle since round 1: an approach can mix entry versions
             (so the toggle shows) while its one ladder is still fitted from a
             single version's times -- both facts stay on screen. */""}
        ${!hasJp && approach.ladder_version
          ? html`<span class="chip library-ladder-version-chip"
              title=${`Fitted from ${approach.ladder_version === "jp" ? "JP" : "US"}-only community times -- not enough of the other version's runs to fit a second ladder.`}>
              ${approach.ladder_version === "jp" ? "JP" : "US"} ladder only
            </span>`
          : ""}
        <table class="library-toc"><tbody>
          ${shownBands.map((band) => html`<${TocRow} key=${bandAnchorId(approach, band.tier || "unranked")} band=${band}
              count=${band.entries.filter((entry) => matchesRunner(entry, query)).length}
              you=${standing && standing.rank === band.tier ? standing : null}
              onJump=${() => document.getElementById(bandAnchorId(approach, band.tier || "unranked"))
                ?.scrollIntoView({ block: "start", behavior: "smooth" })} />`)}
        </tbody></table>
        ${shownBands.map((band) => html`<div class="library-band" key=${bandAnchorId(approach, band.tier || "unranked")}
            data-tier=${band.tier || "unranked"} id=${bandAnchorId(approach, band.tier || "unranked")}>
          <div class="library-band-head">
            ${band.tier ? html`<span class="rank-icon-slot" style="--icon-size: 18px">
              <${RankIcon} tier=${band.tier} division=${"I"} size=${18} /></span>` : ""}
            ${" "}<b>${band.tier ? capName(band.tier) : "Unranked"}</b>
            <span class="meta">${bandRangeLabel(band)}</span>
          </div>
          ${band.divisions
            ? band.divisions.map((division) => html`<${DivisionGroup}
                key=${`${bandAnchorId(approach, band.tier)}-${division.numeral}`}
                approach=${approach} band=${band} division=${division} query=${query}
                autoOpen=${!!(focusMark && focusMark.tier === band.tier
                              && focusMark.division === division.numeral)}
                isYou=${!!(standing && standing.rank === band.tier
                           && standing.division === division.numeral)}
                trayKeys=${trayKeys} entityKey=${entityKey} onAdd=${onAdd} />`)
            : html`<div class="library-division-body">
                <div class="library-examples">
                  ${band.entries.filter((entry) => matchesRunner(entry, query) && entry.video)
                    .map((entry) => {
                      const trayKey = entryTrayKey(approach, entry);
                      return html`<${ExampleCard} key=${trayKey}
                          entry=${entry} tier=${band.tier} division=${null}
                          trayKey=${trayKey} entityKey=${entityKey}
                          inTray=${trayKeys.has(trayKey)} onAdd=${onAdd} />`;
                    })}
                </div>
                <div class="library-plain-rows">
                  ${band.entries.filter((entry) => matchesRunner(entry, query) && !entry.video)
                    .map((entry) => html`<${PlainEntry}
                        key=${`${entry.runner}:${entry.time_cs}`}
                        entry=${entry} tier=${band.tier} division=${null} />`)}
                </div>
              </div>`}
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
                               focusDivision = null, focusEntryUrl = null,
                               focusRow = null,
                               fallbackLabel = null, onRelink = () => {},
                               resolveEntityLabel = null }) {
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

  // FINAL REVIEW FIX (minor: ambiguous headers), RULED ROUND 2: `_target`
  // only disambiguates when this ENTITY PAGE holds more than one target --
  // gated on `rows.length > 1` here rather than left to Section's own
  // `_target !== name` check, which fired on 257 of 509 approaches (any
  // strategy name differing from its star's label, the ordinary case) when
  // only 9 of 112 entity pages actually hold more than one target to
  // disambiguate between. On the other 103 pages it repeated the page's own
  // `<h3>` verbatim on 178 of 281 sections -- noise dressed as a label.
  const approaches = useMemo(() => sectionOrder(
    rows.flatMap((target) => (target.approaches || []).map((approach) => (
      { ...approach, _target: rows.length > 1 ? target.label : null,
        _targetIndex: target.index,
        // Round 5: what the link door needs from the OWNING row -- whether
        // this target has an entity (a star's approaches auto-adopt, so
        // they never link) and whether this instance mounts the adopt
        // routes at all. Round 6 adds the target's name-matched segment,
        // stamped per row so a section needs no second lookup.
        _entityKey: target.entity_key || null,
        _adoptable: !!target.adoptable,
        _matchedSegment: target.matched_segment || null }))),
  ), [rows]);

  // Round 5: the sheet's subsections, stamped the same way -- every one is
  // linkable (they never auto-adopt; the user builds the segment first, his
  // 2026-08-05 ruling), so they finally render, as the Pieces list below.
  // NO `_matchedSegment` here on purpose: a piece is a PART of the movement,
  // so inheriting the whole segment's association would grade a
  // whole-segment PB against a part's ladder. Pieces associate only through
  // their own explicit link.
  const pieces = useMemo(() => rows.flatMap((target) =>
    (target.subsections || []).map((piece) => (
      { ...piece, _target: rows.length > 1 ? target.label : null,
        _targetIndex: target.index,
        _piece: true,
        _entityKey: target.entity_key || null,
        _adoptable: !!target.adoptable }))), [rows]);

  // One fetch serves both halves of the link door: the menu's options and
  // the linked chip's segment NAME (the session view only names segments
  // with activity, so `resolveEntityLabel` alone can leave a chip reading
  // "segment:42"). Fetched only when the page actually shows the door.
  const wantsSegments = pieces.some((piece) => piece.row_key)
    || approaches.some((approach) => approach.adopted
        || (approach._adoptable && linkable({ entity_key: approach._entityKey },
                                            approach, "approach")));
  const [segments, setSegments] = useState(null);
  const [segmentsError, setSegmentsError] = useState(null);
  useEffect(() => {
    if (!wantsSegments || segments != null) return;
    getJSON("/api/segments")
      .then((list) => { setSegments(list); setSegmentsError(null); })
      .catch((err) => setSegmentsError(err.message || String(err)));
  }, [wantsSegments, segments]);

  const resolveLabel = (key) => {
    const match = /^segment:(\d+)$/.exec(key || "");
    if (match && segments) {
      const segment = segments.find((seg) => seg.id === Number(match[1]));
      if (segment) return segment.name;
    }
    return (resolveEntityLabel && resolveEntityLabel(key)) || key;
  };

  // Round 6: one standings fetch per distinct associated segment feeds every
  // linked/matched row's rank chip and ◀ you pin.
  const assocEntities = useMemo(() => {
    const keys = new Set();
    approaches.forEach((approach) => {
      const entity = approach.adopted
        || (approach._matchedSegment && approach._matchedSegment.entity);
      if (entity) keys.add(entity);
    });
    pieces.forEach((piece) => { if (piece.adopted) keys.add(piece.adopted); });
    return [...keys].sort();
  }, [approaches, pieces]);
  const assocStandings = useAssocStandings(assocEntities);

  // Task 0096: the record door's intent — `{key, name, parents, link}`.
  // Held here (not in the door) because the recorder is ONE page-level
  // mount, and `link` is what the door wants done with the id the save
  // mints (adopt this row / adopt the whole target).
  const [recording, setRecording] = useState(null);

  const linkCtx = { segments, segmentsError, onRelink, resolveLabel,
                    assocStandings, openRecorder: setRecording };

  const activeStrat = activeStratFor(t && t.view, entityKey);
  const stratsData = useEntityStrategies(entityKey);
  const stratByName = useMemo(() => {
    const map = {};
    ((stratsData && stratsData.strategies) || []).forEach((entry) => { map[entry.name] = entry; });
    return map;
  }, [stratsData]);

  // WHICH entity this page's overall rank belongs to, resolved once. A star
  // target grades on itself; an entity-less movement grades on whatever
  // segment it is linked to, else on the one it name-matches -- the exact
  // precedence `assocFor` already applies per row (an explicit assignment
  // outranks a name match), lifted to the page because the overall rank is a
  // fact about the TARGET, not about any one approach. Null is a real answer:
  // an unlinked castle movement is graded by nothing.
  const gradingEntity = useMemo(() => {
    if (entityKey) return entityKey;
    const linked = approaches.find((approach) => approach.adopted);
    if (linked) return linked.adopted;
    const matched = approaches.find((approach) => approach._matchedSegment);
    return matched ? matched._matchedSegment.entity : null;
  }, [entityKey, approaches]);
  const gradingLabel = gradingEntity === entityKey
    ? label : (gradingEntity ? resolveLabel(gradingEntity) : null);
  // His best SAVED time on that entity across every strategy -- the same
  // reduction `useAssocStandings` performs, reused verbatim for a linked
  // segment and repeated over the star page's own strategy payload, which is
  // the one place that number is already on hand.
  const gradingPbCs = useMemo(() => {
    if (gradingEntity && gradingEntity !== entityKey)
      return (assocStandings[gradingEntity] || {}).pbCs ?? null;
    const timed = ((stratsData && stratsData.strategies) || [])
      .filter((strategy) => strategy.pb_cs != null);
    return timed.length
      ? timed.reduce((left, right) => (left.pb_cs <= right.pb_cs ? left : right)).pb_cs
      : null;
  }, [gradingEntity, entityKey, assocStandings, stratsData]);

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
  // 2026-08-14: arriving through a LINK ("once there's a connection to a
  // library page, we should be brought there automatically"). `focusRow` is
  // the piece the entity's link points at, resolved server-side by the
  // entity door. Open that piece's own section and center him on the band
  // his standing sits in — the same consume-once shape as `focusStrat`
  // below, with one extra wait: the standing rides `useAssocStandings`,
  // which loads async, and scrolling before it lands would center the
  // section head instead of his rank. A piece with no band for his standing
  // (Capless — the entry tables filter that floor out) centers the section.
  const consumedRowRef = useRef(null);
  useEffect(() => {
    if (!focusRow) return undefined;
    const focusId = `${pageIdentity}::${focusRow}`;
    if (consumedRowRef.current === focusId) return undefined;
    const piece = pieces.find((candidate) => candidate.row_key === focusRow);
    if (!piece) return undefined;      // rows not loaded yet -- retry next render
    const assoc = assocFor(piece, assocStandings, resolveLabel);
    if (assoc && !assoc.loaded) return undefined;   // standing still in flight
    consumedRowRef.current = focusId;
    setExpanded(approachIdentity(piece));
    const standing = assoc ? standingOn(piece.ladder, assoc.pbCs) : null;
    const timer = setTimeout(() => {
      const band = standing && standing.rank
        ? document.getElementById(bandAnchorId(piece, standing.rank)) : null;
      (band || document.getElementById(sectionAnchorId(piece)))
        ?.scrollIntoView({ block: "center", behavior: "smooth" });
    }, 80);
    return () => clearTimeout(timer);
  }, [focusRow, pieces, assocStandings, pageIdentity]);

  const consumedFocusRef = useRef(null);
  // Round 3 (task 0098): a standards-table time link names an exact
  // subdivision and entry. The mark rides to the hit approach's Section,
  // which auto-opens that DivisionGroup; once its Disclose has mounted, the
  // second timer below finds the entry card, centers it, and plays the
  // arrival blink ("hey it's me, i'm the one that loaded!").
  const [focusMark, setFocusMark] = useState(null);
  const rootRef = useRef(null);
  useEffect(() => {
    if (!focusStrat) return undefined;
    const focusId = `${pageIdentity}::${focusStrat}::${focusTier || ""}`
      + `::${focusDivision || ""}::${focusEntryUrl || ""}`;
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
    // WHERE the entry sits is resolved HERE, against the approach's own
    // displayed ladder — never trusted from the intent. The standards table
    // banded the clip against the VETTED ladder; this page files entries by
    // the row's own (usually sheet-fitted) ladder, and the two legitimately
    // disagree about a tier/division (round 6's ruling: "where the sheet's
    // span differs, this page's answer is where your time sits among THESE
    // times"). Landing must open the division the card is actually IN. The
    // intent's tier/division survive only as the fallback when the URL
    // matches no entry (a vetted-only example, a JP-filtered one).
    let mark = null;
    if (focusEntryUrl) {
      const entry = (hit.entries || []).find(
        (one) => one.video === focusEntryUrl);
      if (entry) {
        const ladder = hit.ladder || {};
        const tier = bandFor(ladder, entry.time_cs);
        mark = { approachId: approachIdentity(hit), tier,
                 division: divisionWithin(ladderCsOf(ladder), tier, entry.time_cs),
                 entryUrl: focusEntryUrl };
      }
    }
    if (!mark && focusTier && focusDivision) {
      mark = { approachId: approachIdentity(hit), tier: focusTier,
               division: focusDivision, entryUrl: focusEntryUrl || null };
    }
    setFocusMark(mark);
    // Disclose mounts the section body a tick after `open` flips — see its
    // own docstring ("Preact commits after the tick"). A short timer beats a
    // race against that mount rather than guessing a single rAF is enough.
    const timer = setTimeout(() => {
      const id = mark ? bandAnchorId(hit, mark.tier)
        : focusTier ? bandAnchorId(hit, focusTier) : sectionAnchorId(hit);
      document.getElementById(id)?.scrollIntoView({ block: "start", behavior: "smooth" });
    }, 80);
    // The entry card exists only after the auto-opened DivisionGroup's own
    // Disclose mounts, so the refinement runs on a later look: center the
    // exact card and blink it. Scoped to THIS page's root — a bare selector
    // would find a hidden mounted copy (ui-core's scoping rule). A card the
    // mode filter hides (a JP entry in US mode) is simply not found, and the
    // band scroll above has already landed somewhere honest.
    const helloTimer = setTimeout(() => {
      const root = rootRef.current;
      const card = root && focusEntryUrl
        ? root.querySelector(`[data-video="${CSS.escape(focusEntryUrl)}"]`)
        : null;
      if (!card) return;
      card.scrollIntoView({ block: "center", behavior: "smooth" });
      card.classList.add("library-arrival");
      setTimeout(() => card.classList.remove("library-arrival"), 2200);
    }, 550);
    return () => { clearTimeout(timer); clearTimeout(helloTimer); };
  }, [focusStrat, focusTier, focusDivision, focusEntryUrl, approaches,
      pageIdentity]);

  const iconSrc = entityKey ? entityIconSrc(t, entityKey) : genericStarSrc();
  const activeStratInfo = activeStrat ? stratByName[activeStrat] : null;

  return html`<div class="library-target" ref=${rootRef}>
    <div class="library-target-header">
      <img class="library-target-icon" src=${iconSrc} alt="" draggable="false" />
      <div class="library-target-heading">
        <div class="library-target-titleline">
          <h3>${label}</h3>
          <${TargetLinkControl} rows=${rows} approaches=${approaches}
              linkCtx=${linkCtx} />
        </div>
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
    ${/* Round 11 (2026-08-10): the ladder the star/segment ITSELF grades on,
         above every per-strategy section, because "what it takes to rank up
         overall" is a different question from "what it takes to rank up on
         this strategy" and only the second had a surface. The entity is
         resolved once, here, through the same ladder every other standing on
         this page reads: the target's own key, else whatever segment it is
         linked or name-matched to. An entity-less, unlinked movement passes
         null and the section says so rather than not rendering — a page that
         looks identical after a change reads as the change not working. */""}
    <${OverallStandards} entity=${gradingEntity} label=${gradingLabel}
        pbCs=${gradingPbCs} />
    ${approaches.length === 0
      ? html`<p class="library-target-empty">
          ${missReason === "castle_movement" ? "Browse only — no segment adopts this movement yet."
            : missReason === "route" ? "Stage route — browse the sheet, no per-target ladder here."
            : "No community times recorded here yet."}
        </p>`
      : approaches.map((approach) => html`<${Section} key=${approachIdentity(approach)}
          approach=${approach} open=${expanded === approachIdentity(approach)}
          onOpen=${/* Round 3: clicking the OPEN section's header closes it --
               the single-open accordion allowed everything-closed on mount
               but never by gesture ("It seems like we're being forced to
               have at least one open at a given time"). */
            () => setExpanded((prev) =>
              prev === approachIdentity(approach) ? null : approachIdentity(approach))}
          query=${query}
          focusMark=${focusMark && focusMark.approachId === approachIdentity(approach)
            ? focusMark : null}
          stratInfo=${approach.matched_strategy ? stratByName[approach.matched_strategy] : null}
          trayKeys=${trayKeys} entityKey=${entityKey} onAdd=${onAdd}
          linkCtx=${linkCtx} />`)}
    <${PiecesList} pieces=${pieces} query=${query}
        expanded=${expanded}
        onOpen=${(identity) => setExpanded((prev) =>
          prev === identity ? null : identity)}
        trayKeys=${trayKeys} entityKey=${entityKey} onAdd=${onAdd}
        linkCtx=${linkCtx} />
    ${/* Task 0096: the record door's recorder — the IDENTICAL surface the
         Segments tab opens (one implementation, his own requirement), seeded
         with the row's name and its target's entity. The save's own
         `onSaved` is awaited by the recorder, so a link refusal lands
         beside Save (where the click is) and the recorder stays open; the
         segment itself is already saved, and a second Save re-uses its id.
         On success the fresh /api/segments fetch lands BEFORE the rows
         reload, so the linked chip never flashes a raw `segment:<id>`. */""}
    ${recording && html`<${SegmentTimeline} t=${t} key=${recording.key}
        prefill=${{ name: recording.name, parents: recording.parents }}
        onCancel=${() => setRecording(null)}
        onSaved=${async (segmentId) => {
          await recording.link(segmentId);
          setRecording(null);
          setSegments(await getJSON("/api/segments"));
          await onRelink();
        }} />`}
  </div>`;
}
