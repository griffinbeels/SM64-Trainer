// src/sm64_events/ui/components/librarymodel.js
//
// Pure rules for the Library page. Import-free of Preact/the DOM ON PURPOSE:
// node drives these in tests/test_library_model_js.py without a browser,
// which is what pins section order, band order, the grid math and the trim
// mapping. `format.js` and `caps.js` are the two exceptions -- both are
// themselves Preact-free (node imports each directly in tests), so pulling in
// `fmtSeconds` and the division registry costs nothing this file exists to
// avoid. `fmtSeconds` buys `trayToImport` the SAME time notation the rest of
// this page already shows; `DIVISION_NUMERALS`/`DIVISIONS_PER_TIER` keep the
// subdivision vocabulary ONE door (caps.js owns it for every rank surface).

import { fmtSeconds } from "../format.js";
import { DIVISION_NUMERALS, DIVISIONS_PER_TIER } from "./caps.js";

// Slowest -> fastest, Library-only vocabulary: the Library bands times
// against a fitted or vetted LADDER (library/ladders.py::fit_payload,
// ranks/standards.py), which never carries Iron -- Iron is the classify.py
// floor tier with no threshold of its own (ranks/classify.py's own docstring:
// "Iron is the implicit floor; it carries no threshold in data"), so there is
// no Iron cutoff to band against. DUPLICATED from ranks/classify.py::RANK_NAMES
// on purpose (importing classify's JS twin, caps.js, would drag Preact in --
// this module has to stay import-free) and held together by
// tests/test_cross_language_parity.py, not by this comment: it asserts RANKS
// equals RANK_NAMES reversed with Iron dropped, and GAME_FPS equals
// core/timefmt.py's own constant.
export const RANKS = ["Bronze", "Silver", "Gold", "Platinum", "Diamond",
                      "Master", "Grandmaster", "Mario"];
// Usamune's clock, matching core/timefmt.py::GAME_FPS -- trim seconds are
// GAME frames on THIS clock, not the source video's own frame rate
// (storage/db.py: "in/out_frame are non-destructive sync bounds in GAME
// frames"; tracking/comparisons.py::master_seek_time divides by the same 30
// default).
export const GAME_FPS = 30;

// SECOND-DOOR RULING (task-4-caveats.md point 1, 2026-08-07): `ladderorder.js`
// already sorts strategies by the same Mario-cutoff idea (`slowestFirst`,
// used by standards.js's rank table), and its no-ladder rule is the OPPOSITE
// of this one -- there, an unproven strategy sorts FIRST ("it is not slow, it
// is unproven, and the left edge is where a run starts"). This is not an
// unpinned duplicate of that rule; it is a second caller asking a different
// question of the same fact. `slowestFirst` orders a TABLE's columns, read
// left-to-right as a run gets faster on ONE strategy already chosen. This
// orders a PAGE's sections, read top-to-bottom as a reader picks WHICH
// strategy to try next -- and there, an approach nobody has timed yet is not
// a beginner's first rung, it belongs at the bottom with the rest of "harder
// / less proven ways". Two rules stay ONE door each: this file owns "which
// section should a climbing reader meet first" (librarytarget.js is the only
// caller), `ladderorder.js` owns "which column is a run's own floor"
// (standards.js is the only caller). Neither reads the other's ladder key.
export function sectionOrder(approaches) {
  const keyed = approaches.map((a, i) => ({ a, i,
    mario: a.ladder && a.ladder.Mario != null ? a.ladder.Mario : -Infinity }));
  // beginner -> expert: slowest (most lenient) Mario cutoff first; no ladder
  // sinks last, keeping its published order (stable by original index).
  keyed.sort((x, y) => (y.mario - x.mario) || (x.i - y.i));
  return keyed.map((k) => k.a);
}

export function autoExpandName(ordered, selectedStrat) {
  if (!ordered.length) return null;
  if (selectedStrat) {
    const hit = ordered.find((a) => a.matched_strategy === selectedStrat
                                 || a.name === selectedStrat);
    if (hit) return hit.name;
  }
  return ordered[0].name;
}

export function bandFor(ladder, timeCs) {
  // A time earns the FASTEST tier whose cutoff it beats (<=, displayed cs --
  // the same rule ranks/classify.py::rank_for applies). rank_for walks
  // RANK_NAMES hardest-first and returns on the FIRST tier a time beats;
  // this walks RANKS easiest-first and keeps OVERWRITING on every tier a
  // time beats, so the final value is the LAST (= hardest) one that
  // matched -- the two are the same answer read in opposite directions
  // (proved for a non-monotonic ladder, not just an ordinary one, by
  // test_band_for_agrees_with_a_hand_walked_non_monotonic_ladder).
  //
  // The floor is "Iron" -- the registry KEY, exactly what classify.rank_for
  // returns for a time that beats nothing, so `capName("Iron")` renders it
  // as "Capless" everywhere. It said "Below Bronze" until round 1
  // (2026-08-07), a third name for a tier this project already names twice:
  // "We shouldn't call it 'Below Bronze' -- it's the CAPLESS rank!"
  let earned = null;
  for (const tier of RANKS) {
    if (ladder[tier] == null) continue;
    if (timeCs <= Math.round(ladder[tier] * 100)) earned = tier;
  }
  return earned || "Iron";
}

// ---------------------------------------------------------------------------
// The scoring twin -- ranks/scoring.py's score curve, mirrored so the page
// can place an ENTRY in a subdivision ("Wario 3") the same way the server
// places an ATTEMPT (round 1: "further stratify each of the sections by
// subdivisions ... it's hard to tell apart Wario 1 from Wario 5").
//
// This is a deliberate second copy of a Python implementation the browser
// cannot round-trip for (44k entries, re-banded per JP/US mode flip), so it
// follows the standing rule for that case: the duplicate gets a test that
// COMPARES the two real implementations -- tests/test_cross_language_parity.py
// drives ranks/scoring.py and this file over the same real ladders and times
// and asserts identical (tier, division) for every pair. Never restate the
// curve in a test; the parity run IS the guard.
export const SCORE_ANCHORS = { Mario: 95, Grandmaster: 90, Master: 80,
                               Diamond: 70, Platinum: 60, Gold: 45,
                               Silver: 25, Bronze: 10 };
const TOP_SCORE = 100;
const TIERS_HARDEST_FIRST = [...RANKS].reverse();

// Python's round() is half-to-even; Math.round is half-up. The parity test
// feeds both sides real fitted ladders, where interpolated edges can land on
// exact halves, so the twin has to round the way the original does.
function pyRound(value) {
  const floor = Math.floor(value);
  const diff = value - floor;
  if (diff > 0.5) return floor + 1;
  if (diff < 0.5) return floor;
  return floor % 2 === 0 ? floor : floor + 1;
}

export function ladderCsOf(ladder) {
  const out = {};
  for (const tier of RANKS) {
    if (ladder && ladder[tier] != null) out[tier] = Math.round(ladder[tier] * 100);
  }
  return out;
}

function definedTiers(ladderCs) {
  return TIERS_HARDEST_FIRST.filter((tier) => ladderCs[tier] != null);
}

// Mirror of ranks/scoring.py::score_for -- piecewise linear in TIME through
// the anchors; faster than the hardest tier extrapolates (capped at 100),
// slower than the easiest decays asymptotically (the Iron tail).
export function scoreFor(ladderCs, timeCs) {
  const points = definedTiers(ladderCs).map((tier) => [ladderCs[tier], SCORE_ANCHORS[tier]]);
  if (!points.length) return null;
  const [hardestCs, hardestScore] = points[0];
  if (timeCs <= hardestCs) {
    if (points.length === 1) return hardestScore;
    const [nextCs, nextScore] = points[1];
    const slope = (nextScore - hardestScore) / (nextCs - hardestCs);
    return Math.min(TOP_SCORE, hardestScore + slope * (timeCs - hardestCs));
  }
  for (let seg = 0; seg + 1 < points.length; seg += 1) {
    const [fasterCs, fasterScore] = points[seg];
    const [slowerCs, slowerScore] = points[seg + 1];
    if (timeCs <= slowerCs) {
      const span = slowerCs - fasterCs;
      if (span <= 0) return slowerScore;
      return fasterScore + (slowerScore - fasterScore) * (timeCs - fasterCs) / span;
    }
  }
  const [easiestCs, easiestScore] = points[points.length - 1];
  return easiestScore * easiestCs / timeCs;
}

// Mirror of ranks/scoring.py::time_for_score -- the algebraic inverse, used
// here to print each subdivision's own time bracket.
export function timeForScore(ladderCs, targetScore) {
  const points = definedTiers(ladderCs).map((tier) => [ladderCs[tier], SCORE_ANCHORS[tier]]);
  if (!points.length) return null;
  const [hardestCs, hardestScore] = points[0];
  if (targetScore >= hardestScore) {
    if (points.length === 1) return hardestCs;
    const [nextCs, nextScore] = points[1];
    const slope = (nextScore - hardestScore) / (nextCs - hardestCs);
    if (slope === 0) return hardestCs;
    return pyRound(hardestCs + (targetScore - hardestScore) / slope);
  }
  for (let seg = 0; seg + 1 < points.length; seg += 1) {
    const [fasterCs, fasterScore] = points[seg];
    const [slowerCs, slowerScore] = points[seg + 1];
    if (targetScore >= slowerScore) {
      const span = slowerCs - fasterCs;
      if (span <= 0) return slowerCs;
      const frac = (targetScore - fasterScore) / (slowerScore - fasterScore);
      return pyRound(fasterCs + frac * span);
    }
  }
  const [easiestCs, easiestScore] = points[points.length - 1];
  if (targetScore <= 0) return null;
  return pyRound(easiestScore * easiestCs / targetScore);
}

// Mirror of ranks/scoring.py::tier_band, `defined` hardest-first as there.
function tierBandRange(tier, defined) {
  if (tier === "Iron" || !defined.length) {
    return [0, defined.length ? SCORE_ANCHORS[defined[defined.length - 1]] : TOP_SCORE];
  }
  const index = defined.indexOf(tier);
  const high = index > 0 ? SCORE_ANCHORS[defined[index - 1]] : TOP_SCORE;
  return [SCORE_ANCHORS[tier], high];
}

// The rounded floor-edge time of each of `tier`'s five divisions, V-first.
// V's edge is the tier's own cutoff (exact -- an anchor); Iron V's is null
// (score 0, the asymptote).
function divisionEdges(tier, ladderCs, defined) {
  const [low, high] = tierBandRange(tier, defined);
  const width = (high - low) / DIVISIONS_PER_TIER;
  return DIVISION_NUMERALS.map((numeral, index) =>
    timeForScore(ladderCs, low + index * width));
}

// Which fifth of `tier`'s own band a time falls in -- by the SAME boundary
// rule ranks/scoring.py::progress_for_time applies when it grades HIM (round
// 3): a step is reached when the displayed time reaches the step's own
// DISPLAYED (rounded) cutoff, `<=`. The raw-score slice this used to mirror
// (division_for) disagrees with the banner for a time sitting exactly on a
// rounded division edge, which would file an entry one division below the
// rank his own banner shows for the identical time. Scanning fastest-first
// over the rounded edges is the walk's fixpoint (edges are monotone
// non-increasing, so the first `<=` hit is the fastest step reached).
export function divisionWithin(ladderCs, tier, timeCs) {
  const defined = definedTiers(ladderCs);
  if (!defined.length || !tier) return null;
  const edges = divisionEdges(tier, ladderCs, defined);
  for (let index = DIVISIONS_PER_TIER - 1; index > 0; index -= 1) {
    if (edges[index] != null && timeCs <= edges[index]) {
      return DIVISION_NUMERALS[index];
    }
  }
  return DIVISION_NUMERALS[0];
}

// The fastest displayed time `tier` still owns, PLUS ONE -- i.e. the
// exclusive bound: the next harder tier's cutoff belongs to THAT tier
// (reaching a cutoff earns the harder rank), so this tier's range stops one
// centisecond short of it. The hardest tier's bound is the score-100 cap,
// owned (no +1). Round 3: "each number should be distinct ... There
// shouldn't be overlap in that way."
function tierFastBoundCs(tier, ladderCs, defined) {
  if (tier === "Iron") {
    return ladderCs[defined[defined.length - 1]] + 1;
  }
  const index = defined.indexOf(tier);
  if (index === 0) return timeForScore(ladderCs, TOP_SCORE);
  return ladderCs[defined[index - 1]] + 1;
}

// The five subdivision shells of one tier band, slowest (V) first, each
// carrying the range of displayed times it OWNS under the boundary rule
// above: slow end its own rounded floor edge, fast end one centisecond
// slower than the next unit's -- so no number ever appears on two adjacent
// rows. A shell whose range holds no whole centisecond (`empty`) is real on
// tight ladders: this corpus has vetted divisions 2-3cs wide, thinner than
// one 3.33cs frame.
function divisionShells(tier, ladderCs, defined) {
  const edges = divisionEdges(tier, ladderCs, defined);
  const tierBound = tierFastBoundCs(tier, ladderCs, defined);
  return DIVISION_NUMERALS.map((numeral, index) => {
    const slowCs = edges[index];
    const fastCs = index + 1 < DIVISIONS_PER_TIER ? edges[index + 1] + 1 : tierBound;
    return { numeral, slowCs, fastCs,
             empty: slowCs != null && fastCs != null && fastCs > slowCs,
             entries: [] };
  });
}

export function bandsOf(ladder, entries) {
  const ladderCs = ladderCsOf(ladder);
  const defined = definedTiers(ladderCs);
  // No ladder at all: one honest catch-all with no tier and no divisions --
  // "Unranked", never Capless, because there is nothing to be capless AT.
  if (!defined.length) {
    return [{ tier: null, cutoffCs: null, divisions: null,
              entries: [...(entries || [])].sort((a, b) => b.time_cs - a.time_cs) }];
  }
  const bands = [{ tier: "Iron", cutoffCs: null,
                   fastCs: tierFastBoundCs("Iron", ladderCs, defined),
                   entries: [], divisions: divisionShells("Iron", ladderCs, defined) }];
  for (const tier of RANKS) {
    if (ladderCs[tier] != null) {
      bands.push({ tier, cutoffCs: ladderCs[tier],
                   fastCs: tierFastBoundCs(tier, ladderCs, defined),
                   entries: [], divisions: divisionShells(tier, ladderCs, defined) });
    }
  }
  const byTier = Object.fromEntries(bands.map((band) => [band.tier, band]));
  for (const entry of entries || []) {
    const band = byTier[bandFor(ladder, entry.time_cs)];
    band.entries.push(entry);
    const numeral = divisionWithin(ladderCs, band.tier, entry.time_cs);
    band.divisions[DIVISION_NUMERALS.indexOf(numeral)].entries.push(entry);
  }
  for (const band of bands) {
    band.entries.sort((a, b) => b.time_cs - a.time_cs);
    for (const division of band.divisions) {
      division.entries.sort((a, b) => b.time_cs - a.time_cs);
    }
  }
  return bands.filter((band) => band.entries.length || band.cutoffCs != null);
}

// Your standing on an associated row (round 6): the reader's segment PB
// graded by the ROW's own displayed ladder -- the SAME walk that files every
// sheet entry (bandFor + divisionWithin), so the ◀ you pin and the rank chip
// can never disagree on one surface. No PB → Capless, verbatim his ruling:
// "if there are no times, it's capless; if there are, we automatically
// calculate the rank." The practice tab keeps grading by the segment's own
// ladder; where the sheet's span differs, this page's answer is "where your
// time sits among THESE times."
export function standingOn(ladder, pbCs) {
  if (pbCs == null) return { rank: "Iron", division: null };
  const tier = bandFor(ladder || {}, pbCs);
  return { rank: tier, division: divisionWithin(ladderCsOf(ladder || {}), tier, pbCs) };
}

// Which rows offer the link-to-segment button (round 5). A star's approaches
// auto-adopt at scrape time, so only approaches on an ENTITY-LESS target
// (castle movements, stage routes) are linkable; subsections never auto-adopt
// -- the user builds the segment first (his 2026-08-05 ruling) -- so every
// one is, on star and movement targets alike. A row without `row_key` (an
// old snapshot) gets no button: a click that cannot name its row cannot be
// honest about failing.
export function linkable(target, item, kind) {
  if (!item || !item.row_key) return false;
  if (kind === "subsection") return true;
  return !target.entity_key;
}

export function gridShape(n) {
  if (n <= 1) return { rows: 1, cols: 1 };
  if (n === 2) return { rows: 1, cols: 2 };
  if (n <= 4) return { rows: 2, cols: 2 };
  if (n <= 6) return { rows: 2, cols: 3 };
  return { rows: 3, cols: 3 };
}

export function youtubeId(url) {
  const m = (url || "").match(
    /(?:youtu\.be\/|youtube\.com\/(?:watch\?[^#]*v=|embed\/|shorts\/))([\w-]{6,})/);
  return m ? m[1] : null;
}
export function youtubeThumb(url) {
  const id = youtubeId(url);
  return id ? `https://i.ytimg.com/vi/${id}/hqdefault.jpg` : null;
}
export function youtubeEmbed(url, startS) {
  const id = youtubeId(url);
  if (!id) return null;
  const m = (url || "").match(/[?&]t=(\d+)/);
  const start = Math.floor(startS != null ? startS : (m ? +m[1] : 0));
  // mute=1 (round 4): every player that can start muted does -- and this is
  // the ONE door for YouTube embeds, so the tray's vibes grid inherits it.
  return `https://www.youtube-nocookie.com/embed/${id}?start=${start}&mute=1&enablejsapi=1`;
}

// ---------------------------------------------------------------------------
// Which player a video URL gets -- one registry, because "the videos don't
// load" (round 1) was three different truths at once: 83% of entries carry NO
// video at all, and of the 7,433 that do, ~12% are not YouTube (census
// 2026-08-07: YouTube 6,579 · Twitch 397 · X/Twitter 384 · Bluesky 22 ·
// Discord 21 · Imgur 13 · Streamable 5 · Drive 5 · tail ~8). Every format
// gets a named handler or an honest link-out -- never a dead play button.
//
// `parentHost` is the EMBEDDING page's hostname (Twitch's players refuse to
// load without a matching `parent=` param); the caller passes
// location.hostname so this file stays DOM-free.
export function videoSource(url, parentHost) {
  if (!url) return null;
  if (youtubeId(url)) {
    return { kind: "youtube", site: "YouTube",
             embed: youtubeEmbed(url), thumb: youtubeThumb(url) };
  }
  const host = parentHost || "localhost";
  let match;
  if ((match = url.match(/clips\.twitch\.tv\/([\w-]+)/))
      || (match = url.match(/twitch\.tv\/\w+\/clip\/([\w-]+)/))) {
    return { kind: "twitch-clip", site: "Twitch", thumb: null,
             embed: `https://clips.twitch.tv/embed?clip=${match[1]}&parent=${host}&autoplay=false&muted=true` };
  }
  if ((match = url.match(/twitch\.tv\/videos\/(\d+)/))) {
    return { kind: "twitch", site: "Twitch", thumb: null,
             embed: `https://player.twitch.tv/?video=${match[1]}&parent=${host}&autoplay=false&muted=true` };
  }
  if ((match = url.match(/(?:x|twitter|vxtwitter|fxtwitter)\.com\/\w+\/status\/(\d+)/))) {
    return { kind: "tweet", site: "X", thumb: null,
             embed: `https://platform.twitter.com/embed/Tweet.html?id=${match[1]}&theme=dark&dnt=true` };
  }
  if ((match = url.match(/bsky\.app\/profile\/([^/]+)\/post\/([\w.]+)/))) {
    // embed.bsky.app accepts only a DID, never a handle -- measured live
    // ("Invalid DID: DID syntax didn't validate via regex", 2026-08-07). A
    // URL already carrying one embeds directly; a handle URL ships
    // `embed: null` plus the pieces, and the CARD resolves the DID with one
    // public-API fetch on the first click (ExampleCard's own bsky branch).
    const [, actor, rkey] = match;
    return { kind: "bsky", site: "Bluesky", thumb: null, actor, rkey,
             embed: actor.startsWith("did:")
               ? `https://embed.bsky.app/embed/${actor}/app.bsky.feed.post/${rkey}`
               : null };
  }
  if ((match = url.match(/streamable\.com\/(?:e\/)?(\w+)/))) {
    return { kind: "streamable", site: "Streamable", thumb: null,
             embed: `https://streamable.com/e/${match[1]}?muted=1` };
  }
  if ((match = url.match(/drive\.google\.com\/file\/d\/([^/?#]+)/))) {
    return { kind: "gdrive", site: "Google Drive", thumb: null,
             embed: `https://drive.google.com/file/d/${match[1]}/preview` };
  }
  if (/\.(mp4|webm|mov|m4v)(\?|#|$)/i.test(url)) {
    return { kind: "file", site: siteLabel(url), thumb: null, embed: url };
  }
  if (/\.(png|jpe?g|gif|webp)(\?|#|$)/i.test(url)) {
    return { kind: "image", site: siteLabel(url), thumb: url, embed: null };
  }
  return { kind: "link", site: siteLabel(url), thumb: null, embed: null };
}

function siteLabel(url) {
  const match = (url || "").match(/^https?:\/\/(?:www\.)?([^/]+)/);
  const host = match ? match[1].toLowerCase() : "";
  if (host.includes("discord")) return "Discord";
  if (host.includes("imgur")) return "Imgur";
  if (host.includes("tiktok")) return "TikTok";
  if (host.includes("twitch")) return "Twitch";
  if (host.includes("catbox")) return "Catbox";
  return host || "the runner's site";
}

export function matchesRunner(entry, query) {
  return !query || entry.runner.toLowerCase().includes(query.toLowerCase());
}

export function lastPracticed(view) {
  // views.py::build_session_view ships `stars` and `segments` as two SEPARATE
  // top-level arrays -- never one merged `sections` list (ui/focustarget.js,
  // ui/components/compare.js and ui/components/practicelog.js already read
  // it this way). Compares `journal_id`, never the raw `id`: a reattributed
  // 100-coin attempt keeps a segment-namespace id (~7.5e11) that would
  // outrank every native id forever -- the exact bug
  // tracking-storage.md's "Attempt ordering must use journal_id" law and
  // focustarget.js::newestJournalId both already exist to avoid.
  let best = null, bestId = -1;
  const consider = (sec, key) => {
    for (const attempt of sec.attempts || []) {
      if (attempt.journal_id != null && attempt.journal_id > bestId) {
        bestId = attempt.journal_id;
        best = key;
      }
    }
  };
  for (const sec of (view && view.stars) || [])
    consider(sec, `star:${sec.course_id}:${sec.star_id}`);
  for (const sec of (view && view.segments) || [])
    consider(sec, `segment:${sec.segment_id}`);
  return best;
}

// TASK 6 RULING (task-6-caveats.md point 6): `entityKey` used to be a second
// parameter here. It is gone -- a tray item carries its OWN `entity_key`
// (Task 5 fix round 1, `librarytray.js`'s own header comment), stamped at
// the moment it was added, and import dedupe is scoped to (entity_key,
// strat). A caller handing in a DIFFERENT entity than the item's own would
// import it into the wrong bucket; reading `item.entity_key` makes that
// impossible rather than merely undocumented.
export function trayToImport(item) {
  const trim = item.trim || {};
  const edit = (trim.start_s != null || trim.end_s != null) ? {
    in_frame: trim.start_s != null ? Math.round(trim.start_s * GAME_FPS) : null,
    out_frame: trim.end_s != null ? Math.round(trim.end_s * GAME_FPS) : null,
  } : null;
  // body matches server/compare_api.py::ImportBody's five fields exactly
  // (entity_key, strat, name, source_kind, source_ref) -- name is a
  // PRE-FILLED default for an editable text input (compare.js's `.cmp-save`
  // name field, "name this comparison"), never a fixed label, so exactness
  // matters less than continuity: `fmtSeconds` is what the Library card this
  // item came from already showed for the same time_cs, so the number the
  // user just read on the "+" button matches the number waiting for them in
  // Compare ("Kally 43"80", not a bare "43.80" the rest of the page never
  // writes). TASK 5 RULING (task-5-caveats.md point 2): this was an
  // unpinned `.toFixed(2)` until now: pinned below and in
  // test_library_model_js.py.
  return { body: { entity_key: item.entity_key, strat: item.strat || "Standard",
                   name: `${item.runner} ${fmtSeconds(item.time_cs / 100)}`,
                   source_kind: "youtube", source_ref: item.video },
           edit };
}
