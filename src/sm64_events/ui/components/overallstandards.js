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
// the half that tells him which strategy to go and practice.
//
// Both fields are SERVER-derived (ranks/scoring.py::best_ladder +
// best_ladder_owners, served by ranks_api). A second pointwise-min written in
// JS is exactly the divergence this project has a rule against, and it would
// silently disagree with the banner sitting beside it the next time the
// Python side changed.
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { Disclose } from "./collapsible.js";
import { Icon } from "./icons.js";
import { getJSON } from "../api.js";
import { fmtSeconds } from "../format.js";
import { RANK_NAMES, rankColor, capName, capGradient } from "./caps.js";
import { standingOn } from "./librarymodel.js";

const html = htm.bind(h);
const enc = encodeURIComponent;

// Hardest first, Iron excluded — the same list and the same order
// standards.js walks, so the two tables read as one vocabulary. Iron is the
// capless floor and carries no threshold anywhere (ranks/classify.py), so a
// row for it would be a row with no number in it.
const TIERS = RANK_NAMES.filter((tier) => tier !== "Iron");

/**
 * entity   entity key this page grades on ("star:c:s" / "segment:id"), or null
 *          when nothing grades this target yet (an unlinked castle movement).
 * label    what to call it in the explainer — the segment/star's own name.
 * pbCs     the viewer's best saved time on that entity across every strategy,
 *          or null. Graded through librarymodel's `standingOn`, the SAME walk
 *          every other ◀ you pin on this page uses, rather than a second
 *          grader that could put the header and the rows in different tiers.
 */
export function OverallStandards({ entity, label, pbCs = null }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState(null);
  const [failed, setFailed] = useState(false);

  // Fetched the moment the section exists rather than on first open — the
  // same call standards.js was corrected to make on 2026-08-06, and for the
  // same reason: opening animates a box whose height is measured while its
  // contents are still in flight, so the first open of every panel looks
  // broken and every later one looks right.
  useEffect(() => {
    if (!entity) { setData(null); setFailed(false); return undefined; }
    let cancelled = false;
    setFailed(false);
    getJSON(`/api/ranks/standards?entity=${enc(entity)}`)
      .then((result) => { if (!cancelled) setData(result); })
      .catch(() => { if (!cancelled) { setData(null); setFailed(true); } });
    return () => { cancelled = true; };
  }, [entity]);

  const overall = (data && data.overall) || {};
  const owners = (data && data.overall_owners) || {};
  const rows = TIERS.filter((tier) => overall[tier] != null);
  const you = entity ? standingOn(overall, pbCs) : null;
  // The tier ABOVE the one he is in is the whole question the section asks,
  // so it is marked as well as his own row. `TIERS` runs hardest-first, so
  // "up" is the PREVIOUS entry.
  const yourIndex = you ? rows.indexOf(you.rank) : -1;
  const nextTier = yourIndex > 0 ? rows[yourIndex - 1] : null;
  // Rule 11 in one word: a star and a segment are two kinds of the same
  // practiced thing, and only the noun differs on screen.
  const noun = entity && entity.startsWith("segment:") ? "segment" : "star";

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
        ${!entity ? html`<p class="library-overall-note">
          Nothing grades this target yet — it is a castle movement with no
          segment of its own. Link one above and the standards it ranks
          against appear here.
        </p>` : null}
        ${entity && failed ? html`<p class="library-overall-note">
          Could not load the standards for ${label || entity}.
        </p>` : null}
        ${entity && !failed && !data ? html`<div class="inline-state loading">
          <${Icon} name="updates" size=${16} />${" "}Loading standards…
        </div>` : null}
        ${entity && data && rows.length === 0 ? html`<p class="library-overall-note">
          ${label || entity}${" "}has no published rank standards, so it carries
          no overall rank yet.
        </p>` : null}
        ${rows.length ? html`
          <p class="library-overall-note">
            One ladder for the whole ${noun},
            built from the best time any strategy reaches at each tier — so
            beating a slow strategy's Mario is not the same as ranking up here.
          </p>
          <table class="library-overall-table">
            <thead><tr>
              <th>Rank</th><th>You need</th><th>Set by</th>
            </tr></thead>
            <tbody>
              ${rows.map((tier) => {
                const names = owners[tier] || [];
                const isYou = you && tier === you.rank;
                const isNext = tier === nextTier;
                return html`<tr class=${isYou ? "is-you" : (isNext ? "is-next" : "")}>
                  <td class="library-overall-cap"
                      style=${`background:${capGradient(tier) || rankColor(tier)}`}
                      title=${`${capName(tier)} on this ${noun} overall`}>${capName(tier)}</td>
                  <td class="library-overall-time">${fmtSeconds(overall[tier])}
                    ${isNext ? html`<span class="chip library-overall-next">next</span>` : null}
                    ${isYou ? html`<span class="chip library-overall-you">◀ you${
                      you.division ? ` · ${capName(tier)} ${you.division}` : ""}</span>` : null}
                  </td>
                  <td class="library-overall-by">${names.length
                    ? names.join(" · ")
                    : html`<span class="meta">—</span>`}</td>
                </tr>`;
              })}
            </tbody>
          </table>
          ${pbCs == null ? html`<p class="library-overall-note meta">
            No saved time here yet, so you sit at the capless floor — the
            slowest row above is the first thing to beat.
          </p>` : null}
        ` : null}
      </div>
    <//>
  </div>`;
}
