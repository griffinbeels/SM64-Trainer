// src/sm64_events/ui/components/overallstandards.js — the "Overall Rank
// Standards" section at the top of a Library target page.
//
// User, 2026-08-10: "This just displays the rank standards that determine the
// OVERALL RANK FOR THAT SEGMENT/STAR rather than the individual standards per
// strategy. The point is to make it very clear what it takes for you to rank
// up overall, versus rank up per strategy."
//
// The gap it fills is narrower than it sounds, and worth stating because the
// request assumed the surface already existed: the entity's own RANK has had
// a banner since 2026-07-25 (ranks.js::RankBanner over sec.entity_rank), but
// the LADDER behind it — the cutoffs that rank grades against — has never been
// drawn anywhere. standards.js is a per-STRATEGY table, one column per
// strategy, which is precisely the thing this is meant to be distinct from.
//
// The ladder is the entity's best-possible one: the pointwise best across
// every strategy, so a tier can be set by one way and the next tier by
// another. `overall_owners` names them, because "what does it take to rank up
// overall" is only half an answer without "and by doing what" — and that is
// the half that tells him which strategy to go and practice. Both fields are
// SERVER-derived (ranks/scoring.py::best_ladder + best_ladder_owners, served
// by ranks_api): a second pointwise-min written in JS would silently disagree
// with the banner sitting beside it the next time the Python side changed.
//
// ROUND 2 (2026-08-10) — it is the SAME SHAPE as the entry bands below it,
// and that is the whole of his correction: "displaying the table in the same
// order as the below tables — that is, Capless first at the top, then Toad,
// etc, all the way to mario. We also need to remember to include the capless
// times, as well as subdivision times. The subdivision times should be
// indented within each division. It should use the caps, just like the rank
// standard display below." So: easiest-first, the Capless floor included,
// every band opening into its five subdivision shells, and cap ART rather
// than the coloured name chips the first version drew. It shares the bands
// themselves with those tables (librarymodel.js::ladderBands) and both bracket
// labels (bandRangeLabel/divisionRangeLabel) rather than deriving a second
// set — a standards table and an entry table that disagreed about where
// Wario III starts would be the worst possible version of this feature.
//
// The one thing it does NOT share is entries: a rank standard is a fact about
// the ladder and holds whether or not anyone has published a time in that
// band, which is exactly why the Capless floor has a row here and is filtered
// out of the entry tables.
import { h } from "preact";
import { useState } from "preact/hooks";
import { useIdentityFetch } from "../refetch.js";
import htm from "htm";
import { Disclose } from "./collapsible.js";
import { Icon } from "./icons.js";
import { RankIcon } from "./rankicon.js";
import { getJSON } from "../api.js";
import { nounOfKey } from "../entitysection.js";
import { capName, divisionDigit, DIVISION_NUMERALS } from "./caps.js";
import {
  ladderBands, bandRangeLabel, divisionRangeLabel, standingOn,
} from "./librarymodel.js";

const html = htm.bind(h);
const enc = encodeURIComponent;

// Index 0 of the registry is the BOTTOM of any tier (caps.js), so this is
// Capless V -- the ladder floor a player with no saved time stands on.
const FLOOR_NUMERAL = DIVISION_NUMERALS[0];

function useOverallStandards(entity, version, standardsRevision) {
  const [data, setData] = useState(null);
  const [failed, setFailed] = useState(false);

  // Fetched the moment the section exists rather than on first open — the
  // same call standards.js was corrected to make on 2026-08-06, and for the
  // same reason: opening animates a box whose height is measured while its
  // contents are still in flight, so the first open of every panel looks
  // broken and every later one looks right.
  useIdentityFetch(`${entity}:${version}`, standardsRevision, (cleared) => {
    if (!entity) { setData(null); setFailed(false); return undefined; }
    let cancelled = false;
    if (cleared) setData(null);
    setFailed(false);
    // `version` is the Library page's own JP/US switch (2026-08-15): the
    // one ladder for the whole star must re-file with the sections below it,
    // or the page shows two versions at once with nothing saying why.
    const versionQuery = version ? `&version=${enc(version)}` : "";
    getJSON(`/api/ranks/standards?entity=${enc(entity)}${versionQuery}`)
      .then((result) => { if (!cancelled) setData(result); })
      .catch(() => { if (!cancelled) { setData(null); setFailed(true); } });
    return () => { cancelled = true; };
  });
  return { data, failed };
}

function progression(bands, you) {
  // "Next" is the very next SUBDIVISION he can reach, which is the finest
  // honest answer to "what does it take to rank up" — and with the bands
  // running easiest-first it is simply the step after his own.
  // Concatenated rather than templated, twice over: a `${band.tier}` reads to
  // tests/test_ui_cap_names.py as printing the raw key (it cannot tell a JS
  // string key from markup), and ui-core.md's own law forbids a backtick
  // inside an html`` template, which the sibling below sits in.
  const steps = bands.flatMap((band) =>
    (band.divisions || []).map((division) => band.tier + "/" + division.numeral));
  // With no saved time `standingOn` answers `{Iron, null}` — right for a chip
  // that just says "Capless", wrong for a LADDER, where every position is a
  // division and the floor is Capless V (caps.js: `rankPosition(tier, V, 0)`
  // is position 0). Resolved here rather than in `standingOn`, whose other
  // callers print a numeral-less cap on purpose. Without it the section's own
  // closing line ("the top row is where you are, and the row under it is the
  // first thing to beat") pointed at two rows carrying no mark at all —
  // caught by rendering the state the fixture's own star can never be in.
  const yourNumeral = you && you.division ? you.division : FLOOR_NUMERAL;
  const yourStep = you ? steps.indexOf(you.rank + "/" + yourNumeral) : -1;
  const nextStep = yourStep >= 0 && yourStep + 1 < steps.length
    ? steps[yourStep + 1] : null;
  return { yourNumeral, nextStep };
}

function OverallDivision({ band, division, you, yourNumeral, nextStep }) {
  const key = band.tier + "/" + division.numeral;
  const isYou = you && you.rank === band.tier && yourNumeral === division.numeral;
  const isNext = key === nextStep;
  return html`<div class=${`library-overall-division${isYou ? " is-you" : ""}${
      isNext ? " is-next" : ""}`}>
    <span class="library-overall-division-label">
      <span class="rank-icon-slot" style="--icon-size: 16px">
        <${RankIcon} tier=${band.tier} division=${division.numeral} size=${16} /></span>
      ${" "}${capName(band.tier)}${" "}${divisionDigit(division.numeral)}
    </span>
    <span class="meta">${divisionRangeLabel(division)}</span>
    ${isNext ? html`<span class="chip library-overall-next">next</span>` : ""}
    ${isYou ? html`<span class="chip library-overall-you">◀ you</span>` : ""}
  </div>`;
}

function OverallBand({ band, owners, noun, you, yourNumeral, nextStep }) {
  const names = owners[band.tier] || [];
  return html`<div class="library-overall-band" data-tier=${band.tier}>
    <div class=${`library-overall-band-head${
        you && you.rank === band.tier ? " is-you" : ""}`}>
      <span class="rank-icon-slot" style="--icon-size: 18px">
        <${RankIcon} tier=${band.tier} division=${"I"} size=${18} /></span>
      ${" "}<b>${capName(band.tier)}</b>
      <span class="meta library-overall-range">${bandRangeLabel(band)}</span>
      ${names.length ? html`<span class="meta library-overall-by"
          title=${`the strategy that sets this cutoff on the ${noun}'s own ladder`}>
          set by ${names.join(" · ")}</span>` : ""}
    </div>
    <div class="library-overall-divisions">
      ${(band.divisions || []).map((division) => html`<${OverallDivision}
        key=${division.numeral} band=${band} division=${division} you=${you}
        yourNumeral=${yourNumeral} nextStep=${nextStep} />`)}
    </div>
  </div>`;
}

function OverallState({ entity, label, failed, data, bands }) {
  if (!entity) return html`<p class="library-overall-note">
    Nothing grades this target yet — it is a castle movement with no
    segment of its own. Link one above and the standards it ranks
    against appear here.
  </p>`;
  if (failed) return html`<p class="library-overall-note">
    Could not load the standards for ${label || entity}.
  </p>`;
  if (!data) return html`<div class="inline-state loading">
    <${Icon} name="updates" size=${16} />${" "}Loading standards…
  </div>`;
  if (!bands.length) return html`<p class="library-overall-note">
    ${label || entity}${" "}has no published rank standards, so it carries
    no overall rank yet.
  </p>`;
  return null;
}

/**
 * entity   entity key this page grades on ("star:c:s" / "segment:id"), or null
 *          when nothing grades this target yet (an unlinked castle movement).
 * label    what to call it in the explainer — the segment/star's own name.
 * pbCs     the viewer's best saved time on that entity across every strategy,
 *          or null. Graded through librarymodel's `standingOn`, the SAME walk
 *          every other ◀ you pin on this page uses, rather than a second
 *          grader that could put this table and those in different tiers.
 */
export function OverallStandards({ entity, label, pbCs = null, version = null,
    standardsRevision = 0 }) {
  const [open, setOpen] = useState(false);
  const { data, failed } = useOverallStandards(entity, version, standardsRevision);
  const overall = (data && data.overall) || {};
  const owners = (data && data.overall_owners) || {};
  const bands = ladderBands(overall);
  const you = entity ? standingOn(overall, pbCs) : null;
  const noun = nounOfKey(entity);
  const { yourNumeral, nextStep } = progression(bands, you);

  return html`<div class="library-overall">
    <button type="button" class="disc library-overall-toggle"
        aria-expanded=${open} onclick=${() => setOpen(!open)}>
      <${Icon} name="rank" size=${16} />
      <span>Overall Rank Standards</span>
      <span class="meta">${" "}· ${entity
        ? `what the ${noun} itself grades on, across every strategy`
        : "nothing grades this target yet"}</span>
      <${Icon} name="chevron" size=${16} className="library-overall-chevron" />
    </button>
    <${Disclose} open=${open} className="library-overall-disclose">
      <div class="library-overall-body">
        <${OverallState} entity=${entity} label=${label} failed=${failed}
          data=${data} bands=${bands} />
        ${bands.length ? html`
          <p class="library-overall-note">
            One ladder for the whole ${noun}, built from the best time any
            strategy reaches at each tier — so beating a slow strategy's Mario
            is not the same as ranking up here.
          </p>
          <div class="library-overall-ladder">
            ${bands.map((band) => html`<${OverallBand} key=${"overall-" + band.tier}
              band=${band} owners=${owners} noun=${noun} you=${you}
              yourNumeral=${yourNumeral} nextStep=${nextStep} />`)}
          </div>
          ${pbCs == null ? html`<p class="library-overall-note meta">
            No saved time here yet, so you sit at the capless floor — the top
            row is where you are, and the row under it is the first thing to
            beat.
          </p>` : null}
        ` : null}
      </div>
    <//>
  </div>`;
}
