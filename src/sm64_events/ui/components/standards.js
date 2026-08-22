// src/sm64_events/ui/components/standards.js — collapsible, view-by-default
// rank-standards table for one entity (star:c:s or segment:id). Each cutoff time
// links to the fastest example video that RANKS that tier (server-resolved
// cutoff_videos: auto band from xcams clips + library entries + the user's
// per-cell overrides); the strat header links to the Mario-row video (= the
// overall fastest). Each tier row EXPANDS into its five subdivision threshold
// rows (task 0098), whose brackets and example filing come from
// librarymodel.js::bandsOf — the SAME walk the Library page files entries
// with, so this table and that page cannot disagree about where a division
// starts. Edit mode adds a ▶ button per cell to paste/clear an override, and
// the section links out to the xcams Daily Star page for browsing every
// example.
import { h } from "preact";
import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import { Disclose } from "./collapsible.js";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { fmtIgtShort, fmtSeconds } from "../format.js";
import { nounOfKey } from "../entitysection.js";
import { TimeFields } from "./timefields.js";
import { ceilingOf, slowestFirst } from "../ladderorder.js";
import { RANK_NAMES, rankColor } from "./ranks.js";
import { capName, capGradient, divisionDigit, DIVISION_NUMERALS } from "./caps.js";
import { bandsOf, divisionRangeLabel, ladderBands } from "./librarymodel.js";
import { RankIcon } from "./rankicon.js";
import { disclosurePlan } from "../disclosure.js";
import { feedTuning } from "../feedtuning.js";
import { StratModal } from "./stratmodal.js";
import { Modal } from "./modal.js";
import { Icon } from "./icons.js";
import { VersionSwitch } from "./versionswitch.js";
const html = htm.bind(h);
const enc = encodeURIComponent;

const reducedMotion = () => typeof matchMedia === "function"
  && matchMedia("(prefers-reduced-motion: reduce)").matches;

// Slowest first, top to bottom — the Library's own direction, applied here in
// round 3 (his words: "it gives a sense of progression as the player reads
// from top to bottom"). Rows run Capless → Toad → … → Mario, and within an
// expanded tier the subdivisions run 5 → 1, so the whole table is monotone
// slow→fast down the page. DIVISION_NUMERALS is already bottom-first (V at
// index 0; caps.js mirrors scoring.py), so it is used as-is.
const ROW_ORDER = ["Iron", ...RANK_NAMES.filter((r) => r !== "Iron").reverse()];

// The five subdivision rows one expanded tier inserts (task 0098 item 3).
// Real `<tr>`s in the SAME table, never a nested one, so the columns stay
// aligned with the tier rows by construction. They mount on expand and
// unmount after the close lands (Disclose's own mounted/settle contract —
// that component animates a DIV's height and cannot wrap table rows, so the
// pattern is reimplemented here at row grain: each cell's `.std-sub-clip`
// animates height with the SAME disclosurePlan/feedTuning numbers, and the
// padding lives inside the clip so a closed row is genuinely 0px tall).
// SHEET BEST -- the last row of the standards table, always, and the only one
// that is not a rank. The top of a ladder is not the top of the sport: he
// expanded Mario into its divisions, read Mario 1 and said "there actually ARE
// faster times than this" (2026-08-15). One cell per strategy column carrying
// the fastest time on the Ultimate Sheet for that strategy, its runner, and a
// link where the run was filmed (server/ranks_api.py -> library/examples.py::
// sheet_best). No cap icon, by his instruction -- it grades nothing, so wearing
// a rank's art would say it does. A strategy with no sheet row gets an empty
// cell rather than a guess, and the row never takes a "you are here" bracket:
// markerPosition walks the LADDER, which this is not part of. A real <tr> in
// the same <tbody> as the ladder rows, so the columns stay aligned by
// construction -- the same reason StdSubRows works the way it does.
function SheetBestRow({ strats, sheetBest }) {
  if (!sheetBest || !strats.some((strat) => sheetBest[strat])) return null;
  return html`<tr class="std-sheet-best">
    <td class="std-tier">
      <span class="std-tier-label"><span
        class="std-sheet-best-name">Sheet Best</span></span>
    </td>
    ${strats.map((strat) => {
      const best = sheetBest[strat];
      if (!best) return html`<td class="std-sheet-best-cell">—</td>`;
      const label = fmtSeconds(best.time_cs / 100);
      const runner = best.runner || "";
      return html`<td class="std-sheet-best-cell">
        ${best.video
          ? html`<a href=${best.video} target="_blank" rel="noopener"
              title=${`${runner || "the fastest run"} on the Ultimate Sheet`}
              >${label}</a>`
          : html`<span>${label}</span>`}
        ${runner ? html`<span class="std-sheet-best-runner"
          >${runner}</span>` : null}</td>`;
    })}
  </tr>`;
}

function StdSubRows({ rank, open, strats, bandFor, cellClass, cellStyle,
    labelStyle, exampleLink }) {
  const rowRefs = useRef([]);
  const running = useRef([]);
  const wasOpen = useRef(open);
  const [mounted, setMounted] = useState(open);
  useLayoutEffect(() => { if (open) setMounted(true); }, [open]);
  useLayoutEffect(() => {
    if (open && !mounted) return undefined;      // contents not in the DOM yet
    if (open === wasOpen.current) return undefined;
    wasOpen.current = open;
    const clips = rowRefs.current.filter(Boolean).flatMap(
      (row) => [...row.querySelectorAll(".std-sub-clip")]);
    running.current.forEach((animation) => animation.cancel());
    running.current = [];
    const settle = () => {
      running.current.forEach((animation) => animation.cancel());
      running.current = [];
      if (!wasOpen.current) setMounted(false);
    };
    if (reducedMotion() || !clips.length
        || typeof clips[0].animate !== "function") {
      settle();
      return undefined;
    }
    const tuning = feedTuning();
    let plan = null;
    running.current = clips.map((clip) => {
      // Each clip measures ITSELF (a cancelled fill has already been cleared,
      // so scrollHeight is the natural height); duration and easing are the
      // shared disclosure numbers, so every cell of every row moves as one.
      const cellPlan = disclosurePlan(open, clip.scrollHeight, tuning);
      plan = plan || cellPlan;
      return clip.animate(
        [{ height: `${cellPlan.from}px` }, { height: `${cellPlan.to}px` }],
        { duration: cellPlan.durationMs, easing: cellPlan.easing, fill: "both" });
    });
    if (!plan || plan.durationMs <= 0) { settle(); return undefined; }
    running.current[0].onfinish = settle;
    return () => {
      running.current.forEach((animation) => animation.cancel());
      running.current = [];
    };
  }, [open, mounted]);
  if (!mounted) return null;
  return html`${DIVISION_NUMERALS.map((numeral, rowIndex) => html`<tr
      class="std-sub" key=${numeral}
      ref=${(row) => { rowRefs.current[rowIndex] = row; }}>
    <td class="std-sub-label" style=${labelStyle}>
      <div class="std-sub-clip"><div class="std-sub-pad std-sub-name">
        <span class="std-tier-label std-sub-indent">
          <span class="rank-icon-slot" style="--icon-size: 14px">
            <${RankIcon} tier=${rank} division=${numeral} size=${14} /></span>
          <span>${capName(rank)} ${divisionDigit(numeral)}</span>
        </span>
      </div></div>
    </td>
    ${strats.map((strat) => {
      const band = bandFor(strat, rank);
      const division = band
        ? band.divisions[DIVISION_NUMERALS.indexOf(numeral)] : null;
      const cutoff = division && !division.empty && division.slowCs != null
        ? division.slowCs : null;
      const label = cutoff != null ? fmtSeconds(cutoff / 100) : "—";
      // bandsOf sorts a division's entries slowest first, so the FASTEST
      // example within the subdivision — his band rule, one level finer —
      // is the last one; every clip entry carries a video by construction.
      const example = division && division.entries.length
        ? division.entries[division.entries.length - 1] : null;
      return html`<td class=${cellClass(strat)} style=${cellStyle(strat)}>
        <div class="std-sub-clip"><div class="std-sub-pad">
          ${example && cutoff != null
            ? html`<a href=${example.video} target="_blank" rel="noopener"
                onclick=${exampleLink(strat, rank, numeral, example)}
                title=${`example ${capName(rank)} ${divisionDigit(numeral)} run (${divisionRangeLabel(division)})`}>${label}</a>`
            : label}
        </div></div>
      </td>`;
    })}
  </tr>`)}`;
}

// Each exit-star variant gets its OWN hue from the site palette, so the
// columns under one heading read as one group instead of as a run of
// strategies that happen to sit near each other (user, 2026-08-03: "better
// delineate which columns belong to which overarching exit star strategy").
// THE single owner of the cycle -- the stylesheet never names a band colour,
// it only reads the `--std-band` this hands it, so adding a hue is a row here.
// Six is one more than any course can have exit stars, so a repeat needs the
// unfiled-strategy "Other" band on a fully-defined course; even then two bands
// in one hue cannot be ADJACENT until there are ten of them.
const BAND_TINTS = ["--blue", "--gold", "--green", "--coral", "--violet", "--caveat"];

// Your time is essentially never AT a cutoff, so "you are here" is not a
// cell: it is a point BETWEEN two rows in one column. This returns that
// point as a 0..1 fraction of the gap between the two cutoffs it falls
// between, which is also exactly your division within the tier.
export function markerPosition(ladderSeconds, timeCs) {
  const rows = Object.entries(ladderSeconds)
    .map(([tier, seconds]) => [tier, Math.round(seconds * 100)])
    .sort((left, right) => left[1] - right[1]);               // fastest first
  if (!rows.length || timeCs == null) return null;
  if (timeCs <= rows[0][1]) return { above: null, below: rows[0][0], frac: 1 };
  for (let index = 0; index < rows.length - 1; index += 1) {
    const [fastTier, fastCs] = rows[index], [slowTier, slowCs] = rows[index + 1];
    if (timeCs <= slowCs) {
      const span = slowCs - fastCs;
      return { above: fastTier, below: slowTier,
        frac: span > 0 ? (slowCs - timeCs) / span : 1 };
    }
  }
  return { above: rows[rows.length - 1][0], below: null, frac: 0 };
}

// score is NOT computed here. It comes from sectionRank.score, which
// _section_banner (tracking/views.py) already grades against the active
// strategy's own ladder via ranks/scoring.py::score_for -- a JS copy of that
// curve would silently disagree the next time the Python side changes (the
// Iron tail moved 2026-07-25; a standards.js copy would have drifted).

const fmtScore = (score) => (score == null ? "—" : score.toFixed(1));

// Deliberate mirror of ranks/classify.py::display_cs (pinned by
// tests/test_cross_language_parity.py, same as fmtIgt mirrors format.js): frames ->
// DISPLAYED centiseconds (30fps quantized), so the marker's position never
// disagrees with the time fmtIgt shows for the same frame count (project
// rule 7 / Usamune IGT clock). This is pure quantization arithmetic, not a
// curve -- unlike the score, it has had no reason to drift.
function displayCs(frames) {
  return Math.floor(frames / 30) * 100 + Math.floor(((frames % 30) * 100) / 30);
}

// A Bowser Reds star and its paired seg:reds->pipe:<abbrev> segment share
// ONE rank-standards entity (the star's -- tracking/activestrat.py)
// but must never show each other's half: `family` ("Star" | "Pipe" | null)
// filters the COLUMN list to names ending " (<family>)" -- the fetched
// `data.strategies` is the community store's raw entity data (every
// strategy on that entity, both families mixed), unlike `sec.strategies`,
// which views.py already family-filters server-side for the session view.
// A plain string suffix check, not a second "which times count" resolver
// (that stays server-side, grading_basis/valid_frames) -- there is nothing
// here to disagree with.
function inFamily(name, family) {
  return !family || name.endsWith(` (${family})`);
}

export function StandardsPanel({ entity, activeStrat, strategies, onChanged,
    defaultOpen = false, sectionRank = null, sectionPb = null, family = null,
    openLibrary = null, gradingVersion = null }) {
  const [open, setOpen] = useState(defaultOpen);
  const [data, setData] = useState(null);
  const [editing, setEditing] = useState(false);
  const [showAdd, setShowAdd] = useState(false);
  const [videoEdit, setVideoEdit] = useState(null);
  // Which tier's subdivision rows are showing — single-open, like the Library
  // target page's accordion; null = all collapsed (the default).
  const [expandedTier, setExpandedTier] = useState(null);
  // The version switch's own state — VISUAL only. null means "don't ask",
  // which is also what the switch opens on: the server then resolves the
  // GRADING version itself, so a fresh panel always agrees with what the
  // player is actually rated on. Flipping this never touches grading_version
  // (spec 2026-08-15-game-version-design).
  const [shownVersion, setShownVersion] = useState(null);
  // Strategies ticked "JP differs" THIS session that have no JP time on the
  // server yet — local only, never sent anywhere by itself. The write
  // happens the moment a JP TimeFields actually commits.
  const [jpOpen, setJpOpen] = useState(() => new Set());
  async function load() {
    const qs = shownVersion ? `&version=${enc(shownVersion)}` : "";
    setData(await getJSON(`/api/ranks/standards?entity=${enc(entity)}${qs}`));
  }
  // When opened by default (or when the card remounts for a new entity while
  // open), fetch on mount — toggle() only loads on a user click, so an
  // open-by-default panel would otherwise sit on "Loading standards…" forever.
  // PREFETCHED THE MOMENT THE PANEL EXISTS, not on first open. Griffin,
  // 2026-08-06: "when opening the rank standards, it seems like it loads the
  // rank standards from disk (or a cold cache, whatever), which causes there
  // to be a brief lag before the rank standards loads -- then, every
  // open/close going forward looks as expected. I think the second the card
  // appears in the list, we should load those rank standards and prepare them
  // to be opened later."
  //
  // The panel used to fetch on open, which was a deliberate "no cost per card"
  // when it moved inside the cards -- and the cost it avoided was invisible
  // while the cost it created was not: the FIRST open of each panel animated
  // a box whose contents arrived mid-flight, so the height it measured was
  // the loading state's and the table appeared afterwards. Every later open
  // looked right, which is exactly the shape of a cold-cache stall.
  //
  // `load()` is idempotent and this component is only mounted for cards the
  // log is actually rendering, so the traffic is one request per visible card
  // per entity change, not per card in the corpus.
  // Refetches on the entity changing (unchanged) AND on the shown version
  // changing (the switch) — one effect, one `load()` call site, so the two
  // triggers can never race each other into two in-flight requests. Only an
  // ACTUAL entity change resets the subdivision/JP-toggle state; re-running
  // it for a version flip would collapse a tier the user has open just to
  // look at its JP times.
  // `gradingVersion` (the session view's `game_version.effective`, passed
  // by the card) is a THIRD trigger: flipping the Game version setting
  // re-grades every card through the view refetch, and an already-fetched
  // panel whose switch is untouched (shownVersion null) would otherwise keep
  // showing the old version's ladder under a rank that has moved.
  const prevEntityRef = useRef(entity);
  useEffect(() => {
    if (prevEntityRef.current !== entity) {
      prevEntityRef.current = entity;
      setExpandedTier(null);
      setJpOpen(new Set());
    }
    load();
  }, [entity, shownVersion, gradingVersion]);
  // Reload on EVERY open, not just the first: a strat created from the
  // practice dropdown or header picker while this panel sat cached would
  // otherwise show empty cells forever (its data is fetched out-of-band,
  // not via the session view). Old data stays visible until replaced.
  function toggle() { const n = !open; setOpen(n); if (n) load(); }
  async function put(strat, rank, seconds, version = "us") {
    const qs = version === "jp" ? "?version=jp" : "";
    await send("PUT", `/api/ranks/standards/${enc(entity)}/${enc(strat)}/${enc(rank)}${qs}`, { seconds });
    await load(); onChanged && onChanged();
  }
  // Ticking "JP differs" only opens the local JP sub-column (jpOpen) — no
  // write happens until a JP time actually commits through the second
  // TimeFields. Unticking a strategy that already HAS server-side JP times
  // is destructive, so it confirms first; unticking one that never got a JP
  // time typed just closes the column back up with no request at all.
  // Disabled entirely for a sheet-fitted JP ladder (checked, per the header
  // below) since the DELETE below is a no-op for those server-side.
  async function toggleJp(strat, checked) {
    if (checked) {
      setJpOpen((prev) => new Set(prev).add(strat));
      return;
    }
    if ((data.clearable_jp_strategies || []).includes(strat)) {
      if (!window.confirm("Clear this strategy's JP times? US times stay.")) {
        // The native checkbox already flipped itself unchecked before this
        // handler ran; a fresh Set (same contents) forces a re-render so
        // Preact reconciles the controlled `checked` prop back to true.
        setJpOpen((prev) => new Set(prev));
        return;
      }
      await send("DELETE", `/api/ranks/standards/${enc(entity)}/${enc(strat)}/jp`);
      setJpOpen((prev) => { const next = new Set(prev); next.delete(strat); return next; });
      await load(); onChanged && onChanged();
      return;
    }
    setJpOpen((prev) => { const next = new Set(prev); next.delete(strat); return next; });
  }
  async function delStrat(s) {
    // Dual-meaning x (user-picked): seeded strats are community data —
    // clear-only; custom strats fully delete (tombstone hides attempt-
    // observed occurrences server-side; re-creating the name restores).
    const msg = isSeeded(s)
      ? `Clear rank standards for "${s}"? (The column stays while the strategy is in use.)`
      : `Delete strategy "${s}"?\nRemoves it from all dropdowns and clears its rank `
        + `standards. Past attempts keep their recorded times; re-creating the same `
        + `name restores them.`;
    if (!window.confirm(msg)) return;
    const qs = isSeeded(s) ? "" : "?purge=true";
    await send("DELETE", `/api/ranks/standards/${enc(entity)}/${enc(s)}${qs}`);
    await load(); onChanged && onChanged();
  }
  function editVideo(strat, rank) {
    setVideoEdit({ strat, rank, url: userVid(strat, rank) || "", saving: false, error: null });
  }
  async function saveVideo(nextUrl = videoEdit.url) {
    const { strat, rank } = videoEdit;
    const url = nextUrl.trim();
    const path = `/api/ranks/standards/${enc(entity)}/${enc(strat)}/${enc(rank)}/video`;
    setVideoEdit({ ...videoEdit, saving: true, error: null });
    try {
    await send(url ? "PUT" : "DELETE", path, url ? { url } : undefined);
      await load(); onChanged && onChanged(); setVideoEdit(null);
    } catch (e) {
      setVideoEdit({ ...videoEdit, saving: false, error: String(e) });
    }
  }
  async function reset() {
    if (!window.confirm("Reset this entity to community defaults?")) return;
    await send("POST", `/api/ranks/standards/${enc(entity)}/reset`);
    await load(); setEditing(false); onChanged && onChanged();
  }
  // per-(strat,rank) video accessors (resolved auto+override vs raw user override)
  const cutoffVid = (s, rank) =>
    (data.cutoff_videos && data.cutoff_videos[s] && data.cutoff_videos[s][rank]) || null;
  const userVid = (s, rank) =>
    (data.user_videos && data.user_videos[s] && data.user_videos[s][rank]) || null;
  const headVid = (s) => cutoffVid(s, "Mario") || (data.videos && data.videos[s]) || null;
  const isSeeded = (s) => (data.seeded || []).includes(s);
  // What the last fetch actually resolved on — echoes shownVersion once it
  // has landed, and the grading version on a fresh panel that never asked
  // (shownVersion still null). The version switch reads THIS, not
  // shownVersion directly, so it never shows a version the table has not
  // actually drawn yet.
  const version = data ? data.version : "us";
  // A strategy's JP column is showing when the SERVER already carries a JP
  // ladder for it, or the user ticked "JP differs" this session and has not
  // typed a time yet (jpOpen).
  const jpFlagged = data
    ? new Set([...(data.jp_strategies || []), ...jpOpen])
    : new Set();
  // Whose JP overlay is HIS to clear: the server's own list (a strategy
  // whose JP times sit in his file -- the vetted seed's annotations and every
  // typed JP time both do). A sheet-fitted JP ladder is not in it, so its
  // checkbox is disabled with the reason where the click lands, rather than
  // offering a clear that would no-op. Read from the payload, not derived
  // here from `fitted_strategies` (which is about the BASE ladder and gave a
  // false "cannot be cleared" the moment a JP time was typed onto a fitted
  // strategy -- whole-branch review, 2026-08-15).
  const clearableJp = data ? new Set(data.clearable_jp_strategies || []) : new Set();
  const lockedJp = (strat) => jpFlagged.has(strat) && !clearableJp.has(strat) && !jpOpen.has(strat);

  // Columns = store strategies (community order first) + every other strat
  // this section knows (registered / used on attempts — sec.strategies from
  // views.py). A known strat with no store entry renders an empty column, so
  // custom strats are fillable the moment they exist. Object.hasOwn (not
  // `in`): a strat named e.g. "constructor" must not vanish via the proto
  // chain.
  const allStrats = data
    ? [...Object.keys(data.strategies).filter((s) => inFamily(s, family)),
       ...(strategies || []).filter((s) => inFamily(s, family)
         && !Object.hasOwn(data.strategies, s))]
    : [];
  // A 100-coin star is timed separately per EXIT star, so its columns are
  // BANDED by variant with the leaf name in the column head — "Standard"
  // under "100c + Race" (spec 2026-08-03-hundred-coin-exit-variants). Bands
  // come from the server's own `strategy_groups`; nothing here works out which
  // variant a strategy belongs to. `bands` is empty for every ordinary entity
  // and the table renders exactly as it did before.
  const bands = ((data && data.strategy_groups) || [])
    .map((group) => ({
      label: group.label,
      names: group.strategies.map((s) => s.name).filter((s) => allStrats.includes(s)),
    }))
    .filter((band) => band.names.length);
  const banded = new Set(bands.flatMap((band) => band.names));
  const loose = allStrats.filter((s) => !banded.has(s));
  if (bands.length && loose.length) bands.push({ label: "Other", names: loose });
  // SLOWEST on the left, FASTEST on the right — the table is a PATH, read
  // bottom-left to top-right as you improve (user, 2026-08-03; the rule and
  // its tie-breaks live in ui/ladderorder.js). Applied WITHIN each exit-star
  // band, and to the bands themselves by their own fastest column, so the
  // progression still reads left-to-right across a banded 100-coin table
  // without a column leaving its heading.
  const ladders = (data && data.strategies) || {};
  for (const band of bands) band.names = slowestFirst(band.names, ladders);
  bands.sort((a, b) => {
    const fastest = (band) => Math.min(
      ...band.names.map((name) => ceilingOf(ladders[name])));
    const one = fastest(a), other = fastest(b);
    return one === other ? 0 : other - one;
  });
  // Banded order, so a column always sits under its own heading.
  const strats = bands.length
    ? bands.flatMap((band) => band.names)
    : slowestFirst(allStrats, ladders);
  // Colour is assigned AFTER the bands are ordered, so the leftmost band is
  // always the first hue and the table looks the same every time it opens.
  bands.forEach((band, index) => {
    band.tint = `var(${BAND_TINTS[index % BAND_TINTS.length]})`;
  });
  const tintOf = new Map(bands.flatMap(
    (band) => band.names.map((name) => [name, band.tint])));
  // Where one band ENDS and the next begins: the wash says which columns
  // belong together, this says where the boundary is, and it runs the full
  // height of the table rather than stopping at the heading.
  const bandStart = new Set(bands.map((band) => band.names[0]));
  const bandClass = (strat, ...rest) => [...rest,
    bandStart.has(strat) ? "std-band-start" : ""].filter(Boolean).join(" ");
  const bandStyle = (strat) => (tintOf.has(strat)
    ? `--std-band:${tintOf.get(strat)}` : null);
  const leafOf = new Map(((data && data.strategy_groups) || [])
    .flatMap((group) => group.strategies.map((s) => [s.name, s.leaf])));
  const colHead = (strat) => leafOf.get(strat) || strat;
  // Subdivision brackets + example filing, per strategy, through the Library
  // page's OWN walk (librarymodel.js::bandsOf) — the payload's `clips` are the
  // raw [time_cs, url] pool the server resolved the tier links from, so a
  // subdivision example and its tier cell can never come from different pools,
  // and this table and the Library cannot disagree about where a division
  // starts (task 0098 item 3).
  const stratBands = {};
  for (const strat of strats) {
    const ladder = (data && data.strategies[strat]) || {};
    if (!Object.keys(ladder).length) continue;
    const clipEntries = (((data && data.clips) || {})[strat] || [])
      .map(([timeCs, url]) => ({ time_cs: timeCs, video: url }));
    stratBands[strat] = {};
    for (const band of bandsOf(ladder, clipEntries)) {
      stratBands[strat][band.tier] = band;
    }
    // bandsOf drops an ENTRY-LESS Capless band (no cutoff, nothing to show
    // on the Library's entry tables); this table's Capless row shows the
    // subdivision EDGE TIMES regardless — "capless times first" (round 3) —
    // so the empty structure comes back from ladderBands, the same door.
    if (!stratBands[strat].Iron) {
      const iron = ladderBands(ladder).find((band) => band.tier === "Iron");
      if (iron) stratBands[strat].Iron = iron;
    }
  }
  const subBandFor = (strat, rank) => (stratBands[strat] || {})[rank] || null;
  // Round 3: a time link DEEP-LINKS to its exact library entry instead of
  // opening the raw video ("if I clicked on the 28"91 time for Mario 4, I
  // would be brought to that exact ranked entry video in the Library page").
  // Only URLs the server names as library entries can land anywhere
  // (`library_urls`) — a vetted-only xcams URL has no entry card to arrive
  // at and keeps the plain external behaviour. The href stays the real video
  // either way, so a middle-click still opens it directly.
  const libraryUrls = new Set((data && data.library_urls) || []);
  const exampleLink = (strat, rank, numeral, example) => {
    if (!openLibrary || !libraryUrls.has(example.video)) return null;
    return (clickEvent) => {
      clickEvent.preventDefault();
      openLibrary({ kind: "target", entity, strat, tier: rank,
                    division: numeral, entryUrl: example.video,
                    entryCs: example.time_cs });
    };
  };
  // The TIER cell's own example is filed in whichever subdivision holds it —
  // resolved here so the tier link can carry the same precise landing the
  // subdivision links do.
  const tierExample = (strat, rank, url) => {
    const band = subBandFor(strat, rank);
    if (!band) return { numeral: null, entry: { video: url, time_cs: null } };
    for (const division of band.divisions || []) {
      const entry = division.entries.find((one) => one.video === url);
      if (entry) return { numeral: division.numeral, entry };
    }
    return { numeral: null, entry: { video: url, time_cs: null } };
  };
  // "You are here": the grading basis under the ACTIVE strategy. Avg rank
  // modes carry it on sectionRank.basis; pb mode carries none (the same
  // split _section_banner already encodes server-side), so it falls back to
  // the saved PB row for this entity's clock ON THAT STRATEGY. That last
  // clause is the 2026-08-15 fix: the fallback used to read the
  // strategy-BLIND PB, so a time run on 3x LJ planted a "you are here" badge
  // in the Standard column and his own progress looked misattributed. A
  // strategy he has never run now simply has no marker, which is the honest
  // state. Interpolated against THIS
  // strategy's own ladder (the column actually on screen) rather than the
  // entity's best-possible ladder, so the marker's bracketed cutoffs can
  // never disagree with the rows it sits between.
  //
  // `data.strategies` is now VERSION-RESOLVED (the switch above), so the
  // marker moves to sit between whichever cutoffs the shown version drew —
  // that is intended, not a bug: it is answering "where do I sit against
  // THESE rows". `entityScore` below stays the server-GRADED score
  // regardless of what is shown, on purpose — flipping the switch changes
  // what you are looking at, never what you are rated on.
  // The one strategy that sets EVERY Overall cutoff, or null -- resolved
  // SERVER-side (ranks/scoring.py::sole_overall_owner, which carries the
  // rule and its gates) so this panel and the Library page cannot disagree.
  const soleOverallOwner = (data && data.sole_overall_owner) || null;
  const activeLadder = data && activeStrat ? (data.strategies[activeStrat] || {}) : {};
  const basisFrames = data && sectionRank && sectionRank.basis
    ? sectionRank.basis.frames
    : (data && sectionPb && sectionPb[data.clock] ? sectionPb[data.clock].frames : null);
  const timeCs = basisFrames != null ? displayCs(basisFrames) : null;
  const hasActiveLadder = Object.keys(activeLadder).length > 0;
  const marker = timeCs != null && hasActiveLadder ? markerPosition(activeLadder, timeCs) : null;
  // The server-graded score for the active strategy's own ladder, or absent
  // when sectionRank is one of _section_banner's sentinel states (no_strat /
  // no_ladder / unranked -- e.g. pb mode with no saved PB on THIS strategy,
  // even while the basisFrames fallback above still finds an entity-wide PB
  // to position the marker with). No client-side fallback curve: a missing
  // score is a real state to show honestly, not something to paper over.
  const entityScore = sectionRank && sectionRank.score != null ? sectionRank.score : null;
  return html`<div class="stdpanel">
    <button class="disc standards-toggle" onclick=${toggle} aria-expanded=${open}>
      <${Icon} name="rank" size=${16} />
      <span>Rank standards</span>
      ${activeStrat ? html`<span class="meta"> · active: ${activeStrat}</span>` : null}
      <${Icon} name="chevron" size=${16} className="standards-chevron" />
    </button>
    <${Disclose} open=${open} className="stdpanel-disclose">
    ${!data ? html`<div class="stdbody"><div class="inline-state loading">
      <${Icon} name="updates" size=${16} /> Loading standards…
    </div></div>` : null}
    ${data ? html`<div class="stdbody">
      <div class="stdtools">
        <button class=${editing ? "is-selected" : ""} onclick=${() => setEditing(!editing)}>
          <${Icon} name=${editing ? "check" : "edit"} size=${15} /> ${editing ? "Done editing" : "Edit"}
        </button>
        ${editing ? html`<button onclick=${() => setShowAdd(true)}>
          <${Icon} name="plus" size=${15} /> Strategy
        </button>` : null}
        <button class="quiet-button" onclick=${reset}>
          <${Icon} name="restart" size=${15} /> Community defaults
        </button>
        <${VersionSwitch} value=${version}
            onChange=${setShownVersion}
            note=${data.version !== data.grading_version
              ? `Viewing ${data.version.toUpperCase()} standards · you are graded on ${data.grading_version.toUpperCase()}`
              : null} />
        ${data.xcams_url ? html`<a class="meta" href=${data.xcams_url} target="_blank" rel="noopener"
            title="browse every example run for this star on the xcams Daily Star page">Examples on xcams ↗</a>` : null}
      </div>
      ${/* WHY the active strategy's ladder can BE the Overall one -- the
           "correct but unexplained reads as a bug" shape (his report,
           2026-08-15: "it also still seems a bit weird that the 'Standard'
           strategy is the exact progression for the 'Overall' ranking"). The
           sentence is the only thing that lives here; when it is true is the
           server's call. */""}
      ${soleOverallOwner ? html`<p class="std-overall-note">
        <b>${soleOverallOwner}</b> is the fastest strategy at every rank, so its
        times are also the Overall standard for this ${nounOfKey(entity)}.</p>` : null}
      <table class="stdtable"><thead>
        ${bands.length ? html`<tr class="std-variant-row"><th></th>
          ${bands.map((band) => html`<th class="std-variant std-band-start"
              colspan=${band.names.length} style=${`--std-band:${band.tint}`}
              title="the star this 100-coin run ends on">${band.label}</th>`)}</tr>` : null}
        <tr><th>Strat</th>
        ${strats.map((strat) => html`<th
          class=${bandClass(strat, tintOf.has(strat) ? "std-banded" : "",
            strat === activeStrat ? "col-active" : "")}
          style=${bandStyle(strat)}>${headVid(strat)
          ? html`<a href=${headVid(strat)} target="_blank" rel="noopener" title="fastest-time video">${colHead(strat)}</a>`
          : colHead(strat)}${editing ? html` <button class="candx" title=${isSeeded(strat) ? "clear this strategy's standards" : "delete this strategy"} onclick=${() => delStrat(strat)}>×</button>
          <label class="std-jp-toggle" title=${lockedJp(strat)
              ? "this strategy's JP ladder comes from the Ultimate Sheet and cannot be cleared here"
              : "US and JP timed differently for this strategy"}>
            <input type="checkbox" checked=${jpFlagged.has(strat)}
                disabled=${lockedJp(strat)}
                onchange=${(e) => toggleJp(strat, e.target.checked)} /> JP differs
          </label>` : ""}
          ${marker && strat === activeStrat ? html`<span class="std-you-badge"
              title=${data.version !== data.grading_version
                ? `your current time, placed on the ${data.version.toUpperCase()} ladder shown here · the score is your graded (${data.grading_version.toUpperCase()}) one`
                : "your current time and score on this ladder"}>◀ you · ${fmtIgtShort(basisFrames)}${entityScore != null ? ` · ${fmtScore(entityScore)}` : ""}</span>` : ""}</th>`)}</tr></thead>
        <tbody>
        ${ROW_ORDER.map((rank) => html`<tr key=${rank}>
          <!-- Large flat surface -> the tier's own gradient where it has
               one (Toadsworth/Toad, addendum 2, 2026-07-25): a flat fill
               here is a lie for a cap that's actually two-tone, and a
               white slab (old Toad) was the live complaint that started
               this. capGradient falls back to null for a flat tier. -->
          <td class="std-tier-cell"
              style=${`background:${capGradient(rank) || rankColor(rank)}`}
              title=${rank === "Iron"
                ? `${capName(rank)} — slower than every cutoff`
                : `${capName(rank)} · ${rank} on xcams`}>
            <!-- ONE full-cell button (round 3: "everything clearly feels
                 like one cohesive card" — the library glyph is gone, the
                 time links deep-link instead). The label block has a FIXED
                 width and centres as a group, which is what keeps every cap
                 on one vertical line while the pair sits mid-cell ("CENTERED
                 in the middle, but still aligned so that all of the hats are
                 aligned vertically"); the chevron pins to the cell's right
                 edge. The cap is the division-I cap, the Library TOC's own
                 round-2 ruling. -->
            <button type="button" class="std-tier-btn"
                aria-expanded=${expandedTier === rank}
                title=${`${expandedTier === rank ? "hide" : "show"} ${capName(rank)} subdivision standards`}
                onclick=${() => setExpandedTier(
                  (prev) => (prev === rank ? null : rank))}>
              <span class="std-tier-label">
                <span class="rank-icon-slot" style="--icon-size: 15px">
                  <${RankIcon} tier=${rank} division=${"I"} size=${15} /></span>
                <span class="std-tier-name">${capName(rank)}</span>
              </span>
              <${Icon} name="chevron" size=${16} className="std-tier-chevron" />
            </button>
          </td>
          ${strats.map((strat) => {
            const v = (data.strategies[strat] || {})[rank];
            const vid = rank === "Iron" ? null : cutoffVid(strat, rank);
            // Every rank standard reads in the Usamune display format the practice
            // log and every PB already use -- 1'21"32, and 23"00 under a minute
            // (user, 2026-08-03: "This is important because that matches the
            // format we actually display in the practice log"). Raw seconds
            // beside a formatted PB is two vocabularies for one quantity.
            const label = v != null ? fmtSeconds(v) : "—";
            // "You are here" — a cell highlight would be a lie (your time is
            // essentially never AT a cutoff), so instead the two cutoffs
            // bracketing your interpolated position get their own mark, and
            // every EASIER tier below that (a cutoff you are, by definition,
            // already running faster than) reads as already-beaten.
            const isBracket = marker && strat === activeStrat
              && (rank === marker.above || rank === marker.below);
            const beaten = marker && marker.below && strat === activeStrat
              && RANK_NAMES.indexOf(rank) > RANK_NAMES.indexOf(marker.below);
            const cellClass = bandClass(strat,
              strat === activeStrat ? "col-active" : "",
              isBracket ? "std-marker-bracket" : (beaten ? "std-beaten" : ""));
            // The Capless row carries no threshold of its own and no editor —
            // the floor has no cutoff to set (`set_threshold` rejects Iron
            // server-side). Round 4, his named EXCEPTION: the collapsed row
            // shows the CAPLESS 1 time — the floor's best subdivision edge
            // stands in for the cutoff it does not have, behaving exactly
            // like its own Capless 1 cell (same value, same example link), so
            // collapsing never changes what the number means.
            if (rank === "Iron") {
              const band = subBandFor(strat, "Iron");
              const one = band
                ? band.divisions[DIVISION_NUMERALS.indexOf("I")] : null;
              const capOneCs = one && !one.empty && one.slowCs != null
                ? one.slowCs : null;
              const capOneLabel = capOneCs != null
                ? fmtSeconds(capOneCs / 100) : "—";
              const example = one && one.entries.length
                ? one.entries[one.entries.length - 1] : null;
              return html`<td class=${cellClass} style=${bandStyle(strat)}>
                ${example && capOneCs != null
                  ? html`<a href=${example.video} target="_blank" rel="noopener"
                      onclick=${exampleLink(strat, "Iron", "I", example)}
                      title=${`example ${capName("Iron")} 1 run`}>${capOneLabel}</a>`
                  : capOneLabel}</td>`;
            }
            // Only a JP-FLAGGED strategy grows the second sub-column — every
            // other strategy keeps the single field it always had. The
            // editor reads the payload's EXPLICIT per-version ladders
            // (`strategies_us` / `strategies_jp`), never the version-resolved
            // `strategies`, so the switch stays live while editing and the
            // US field can never quietly show a JP-resolved time (whole-
            // branch review, 2026-08-15). An unflagged strategy is one
            // ladder for both versions, so its single field reads US.
            const usSeconds = ((data.strategies_us || {})[strat] || {})[rank];
            const jpSeconds = jpFlagged.has(strat)
              ? ((data.strategies_jp || {})[strat] || {})[rank] : null;
            return html`<td class=${cellClass} style=${bandStyle(strat)}>
              ${editing
                ? html`<span class=${`stdcell${jpFlagged.has(strat) ? " stdcell-versions" : ""}`}>
                    ${jpFlagged.has(strat)
                      ? html`<span class="stdcell-version">
                          <span class="stdcell-version-label">US</span>
                          <${TimeFields} seconds=${usSeconds} compact
                              label=${`${capName(rank)} ${strat} US`}
                              onCommit=${(next) => { if (next != null) put(strat, rank, next); }} />
                        </span>
                        <span class="stdcell-version">
                          <span class="stdcell-version-label">JP</span>
                          <${TimeFields} seconds=${jpSeconds} compact
                              label=${`${capName(rank)} ${strat} JP`}
                              onCommit=${(next) => { if (next != null) put(strat, rank, next, "jp"); }} />
                        </span>`
                      : html`<${TimeFields} seconds=${usSeconds} compact
                          label=${`${capName(rank)} ${strat}`}
                          onCommit=${(next) => { if (next != null) put(strat, rank, next); }} />`}
                    <button class="vidbtn" title=${`${userVid(strat, rank) ? "edit" : "add"} ${capName(rank)} example video`}
                      onclick=${() => editVideo(strat, rank)}>${userVid(strat, rank) ? "▶✎" : "▶＋"}</button></span>`
                : (vid
                    ? html`<a href=${vid} target="_blank" rel="noopener"
                        onclick=${(() => {
                          const found = tierExample(strat, rank, vid);
                          return exampleLink(strat, rank, found.numeral, found.entry);
                        })()}
                        title=${`example ${capName(rank)} run`}>${label}</a>`
                    : label)}</td>`;
          })}</tr>
          <${StdSubRows} key=${"sub-" + rank} rank=${rank}
              open=${expandedTier === rank} strats=${strats}
              bandFor=${subBandFor} exampleLink=${exampleLink}
              cellClass=${(strat) => bandClass(strat,
                strat === activeStrat ? "col-active" : "")}
              cellStyle=${bandStyle}
              labelStyle=${`background:color-mix(in srgb, ${rankColor(rank)} 18%, transparent)`} />`)}
        <${SheetBestRow} strats=${strats} sheetBest=${data.sheet_best} />
        </tbody></table>
    </div>` : null}
    <//>
    ${showAdd ? html`<${StratModal} entity=${entity} existing=${strats}
        onSaved=${async () => { setShowAdd(false); await load(); onChanged && onChanged(); }}
        onClose=${() => setShowAdd(false)} />` : null}
    ${videoEdit ? html`<${Modal} title="Example video" icon="play"
        description=${`${capName(videoEdit.rank)} rank · ${videoEdit.strat}`}
        onClose=${videoEdit.saving ? null : () => setVideoEdit(null)}
        footer=${html`
          <button onclick=${() => setVideoEdit(null)} disabled=${videoEdit.saving}>Cancel</button>
          ${videoEdit.url ? html`<button class="danger-text"
              onclick=${() => saveVideo("")} disabled=${videoEdit.saving}>
            <${Icon} name="trash" size=${15} /> Clear video
          </button>` : null}
          <button class="primary-button" onclick=${() => saveVideo()}
              disabled=${videoEdit.saving || !videoEdit.url.trim()}>
            <${Icon} name="save" size=${15} />
            ${videoEdit.saving ? "Saving…" : "Save video"}
          </button>`}>
      <label class="modal-field">
        <span class="field-label">Video URL</span>
        <input type="url" autofocus placeholder="https://…"
            value=${videoEdit.url}
            oninput=${(e) => setVideoEdit({ ...videoEdit, url: e.target.value })} />
        <small>Use a direct video or YouTube URL for this rank example.</small>
      </label>
      ${videoEdit.error ? html`<div class="modal-error">${videoEdit.error}</div>` : null}
    <//>` : null}
  </div>`;
}
