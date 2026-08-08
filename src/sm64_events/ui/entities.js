// Turns the server's payloads into the group shape components/entitymodal.js
// renders. Pure and DOM-free — the same reason ui/group.js sits outside
// components/, and what lets these be unit-tested through node.
//
// NO FILTERING happens here. Which options a given control may offer is the
// CALL SITE's business (world topology in the segment builder, route scoping
// in the route editor) and is passed to EntityPicker as `allow`.
//
// The taxonomy itself is never re-derived here: level_groups, course_groups
// and each segment's `origin` all come from the server, which has one home for
// them (tracking/segments.py).

// ":" does double duty on purpose: it joins a star's composite id ("8:2")
// AND an entity key's kind to its id ("star:8:2", "segment:6"). That is what
// makes a picker option's (kind, id) pair and the server's entity key the
// same string — see parseEntityKey / optionIcon below.
const STAR_ID_SEP = ":";

/** "8:1" -> { course: 8, star: 1 } — the composite id star pickers select. */
export function parseStarId(id) {
  const [course, star] = String(id).split(STAR_ID_SEP);
  return { course: Number(course), star: Number(star) };
}

export function starId(course, star) {
  return `${course}${STAR_ID_SEP}${star}`;
}

/** Levels grouped by castle region (vocab.level_groups). */
export function levelOptions(vocab) {
  return (vocab.level_groups || []).map((group) => ({
    key: group.key ?? "other",
    label: group.label,
    options: group.levels
      .filter((level) => vocab.levels[String(level)] !== undefined)
      .map((level) => ({ id: String(level), name: vocab.levels[String(level)] })),
  })).filter((group) => group.options.length > 0);
}

/** Courses grouped by castle region (vocab.course_groups). */
export function courseOptions(vocab) {
  return (vocab.course_groups || []).map((group) => ({
    key: group.key ?? "other",
    label: group.label,
    options: group.courses
      .filter((course) => vocab.courses[String(course)] !== undefined)
      .map((course) => ({ id: String(course), name: vocab.courses[String(course)] })),
  })).filter((group) => group.options.length > 0);
}

// Stars grouped by COURSE. The grid picker (2026-07-25) renders these as its
// two layers — a course cell, then that course's stars — so the grouping
// outlived the <optgroup> it was first written for. Two sources carry the same
// information, the builder vocab and the session catalog, so each gets a thin
// adapter over one core.

function starGroups(courseGroups, courseName, starNames) {
  return (courseGroups || []).flatMap((group) => group.courses
    .filter((course) => courseName(course) !== undefined)
    .map((course) => ({
      key: `course-${course}`,
      label: courseName(course),
      options: (starNames(course) || []).map((name, index) => ({
        id: starId(course, index), name,
      })),
    })))
    .filter((group) => group.options.length > 0);
}

export function starOptionsFromVocab(vocab) {
  return starGroups(vocab.course_groups,
                    (course) => vocab.courses[String(course)],
                    (course) => vocab.stars[String(course)]);
}

export function starOptionsFromCatalog(catalog) {
  const byId = new Map((catalog.courses || []).map((course) => [course.id, course]));
  return starGroups(catalog.course_groups,
                    (course) => (byId.get(course) || {}).name,
                    (course) => (byId.get(course) || {}).stars);
}

/** Segment definitions grouped by their origin region, in taxonomy order —
 *  the same grouping the segment library uses, so the picker beside it reads
 *  the same way. `taxonomy` is vocab.origins. */
export function segmentOptions(defs, taxonomy) {
  // Before /api/segments/vocab resolves (first paint calls this with
  // `taxonomy` undefined), every def would bucket by region into SEPARATE
  // groups that all fall back to the same "Other" label — N identical
  // headings instead of one flat list (review M4). Return one ungrouped
  // bucket instead; it self-corrects once the taxonomy fetch lands.
  if (!taxonomy || taxonomy.length === 0) {
    return (defs || []).length === 0 ? [] : [{
      key: "all",
      label: "Segments",
      options: defs.map((def) => ({ id: String(def.id), name: def.name })),
    }];
  }
  const order = (taxonomy || []).map((region) => String(region.key));
  const labels = new Map((taxonomy || [])
    .map((region) => [String(region.key), region.label]));
  const buckets = new Map();
  for (const def of defs || []) {
    const region = String((def.origin || {}).region);
    if (!buckets.has(region)) buckets.set(region, []);
    buckets.get(region).push({ id: String(def.id), name: def.name });
  }
  const keys = [...buckets.keys()].sort((left, right) => {
    const leftIndex = order.indexOf(left), rightIndex = order.indexOf(right);
    return (leftIndex === -1 ? order.length : leftIndex)
      - (rightIndex === -1 ? order.length : rightIndex);
  });
  return keys.map((region) => ({
    key: region,
    label: labels.get(region) || "Other",
    options: buckets.get(region),
  }));
}

// Applying a caller's filter to picker groups: emptied groups dropped, and the
// CURRENT VALUE kept even when the filter rejects it (a stored value fed to a
// filtered dropdown otherwise renders blank and reads as unset — fixed twice
// before, separately, in stratpicker.js and the segment builder).
//
// It lives HERE rather than in the picker component because this module
// imports nothing: node can load it directly, so the invariant above is
// unit-testable. picker.js imports preact through the browser importmap, which
// node cannot resolve.

// Scope (review M1): this only rescues a value `allow` rejects. A caller that
// narrows `groups` itself BEFORE handing them to the picker — segments.js
// filters by schema.enum, and narrows the star groups to one course — is
// still responsible for its own value; visibleGroups never sees what got
// dropped, so it cannot inject it back.
/** Groups with the filter applied: emptied groups removed, current value kept
 *  when `allow` rejects it. Pure — returns new objects, never mutates the
 *  caller's array. */
export function visibleGroups(groups, allow, value) {
  const keep = (option) => !allow || allow(option.id) || option.id === value;
  return (groups || [])
    .map((group) => ({ ...group, options: group.options.filter(keep) }))
    .filter((group) => group.options.length > 0);
}

// --- Entity art -----------------------------------------------------------
// ONE chain, shared by the practice banner and the picker, so the same star
// never wears different art in two places. It lives here because this module
// imports nothing and is therefore node-testable; components/stagebanner.js
// imports these two registries rather than keeping its own copies.

// ui/assets/star_icons/{prefix}{slot+1}.png, one per main-course star
// INCLUDING the 100-coin 7th slot. Index = course_id - 1 (catalog order,
// pinned against the assets by tests/test_star_icons.py).
export const COURSE_ICON_PREFIXES = ["bob", "wf", "jrb", "ccm", "bbh", "hmc",
                                     "lll", "ssl", "ddd", "sl", "wdw", "ttm",
                                     "thi", "ttc", "rr"];

// The icon set has real art for the Bowser stages, keyed by both the course
// level (pipe-entry segments) and its fight arena.
export const LEVEL_ICONS = { 17: "bitdw", 19: "bitfs", 21: "bits",
                             30: "bitdw", 33: "bitfs", 34: "bits" };

// Default art for a SEEDED segment, keyed by the seed_key the corpus gives it
// (tools/corpus_*.py) rather than by name or id: a rename keeps the art, and
// every install agrees without a migration. Only the definitions whose art is
// a specific thing rather than a class live here — the class case is the
// category table below.
export const SEGMENT_SEED_ICONS = {
  "seg:lblj": "blj",
  "seg:lakitu-skip": "lakitu",
  "seg:mips-clip": "mips1",
};

// Default art for a whole CATEGORY of segment — the corpus's own grouping,
// persisted on `segment_defs.category` and shipped on every /api/segments row,
// so this is the seed's classification rather than a second one invented here.
// 63 of the 84 seeded definitions are Castle Movement; the ones among them
// that start in a Bowser stage keep that stage's art, because LEVEL_ICONS is
// consulted first (user, 2026-07-26: "update ALL castle movement segments to
// use the castle_movement picture").
export const SEGMENT_CATEGORY_ICONS = { "Castle Movement": "castle_movement" };

// The category a definition the CORPUS does not know falls back to. A segment
// built by hand carries no `category` at all, so until 2026-07-27 it fell
// straight through to the generic star on the reasoning that "nothing knows
// what it is" — which was true of the field and false of the row: five
// hand-made movements predating the corpus ("Basement -> SSL", "WF ->
// Basement", …) sat in a v1.6.0 install drawing plain gold stars beside 47
// seeded rows wearing castle_movement, which reads as the feature not having
// shipped (live report 2026-07-27). What DOES know is where the segment
// starts: `origin.region` is the castle area its start node is reached
// through, present for every node with a place in the world graph and null
// only for a start with no place at all (a reset, an unscoped key grab, a
// Toad star). A segment that begins somewhere reachable is a movement between
// places, which is exactly what the 59 seeded Castle Movement rows are; one
// that begins nowhere still keeps the generic star and its ✎.
// This resolves to a CATEGORY, never to a stem: a second table pointing at the
// same picture is the second door this module exists to prevent, so the answer
// goes back through SEGMENT_CATEGORY_ICONS above. Pinned as a key of that
// table by tests/test_ui_entities.py.
export const UNCATEGORIZED_SEGMENT_CATEGORY = "Castle Movement";

/** A segment's effective category: the corpus's own when it has one, else what
 *  its start place implies. Exported for the test that pins it against the
 *  icon table; the chain below is its only production caller. */
export function segmentCategory(category, originRegion) {
  return category || (originRegion ? UNCATEGORIZED_SEGMENT_CATEGORY : null);
}

// Four main courses are not entered through a painting, so the game has NO
// portrait for them. These are hand-picked stand-ins (user, 2026-07-25) — the
// star art that reads as that course — rather than a positional star-1
// default, which would have given Hazy Maze Cave its first star's icon.
// This is the final answer for these four; there is no art to wait for.
export const COURSE_SUBSTITUTE_ICONS = { hmc: "hmc6", ssl: "ssl2",
                                         ddd: "ddd1", sl: "sl6" };

// The SPECIAL stages — Bowser levels, the caps, the slide, WMOTR, the aquarium
// — are courses 16-24, past the end of COURSE_ICON_PREFIXES (which is indexed
// by course_id-1 and only covers the 15 main courses). Without this map they
// fell through to the generic gold star even though real art exists for every
// one of them: the Bowser levels and the cap stages have star_icons entries,
// and PSS has an actual portrait (live check 2026-07-25 — the target picker
// showed a plain star for VCUtM, BitDW and PSS, which is what caught it).
// Values are course_icons stems where a portrait exists, star_icons stems
// otherwise; courseArt tries the portrait manifest first either way, so a
// portrait dropped in later wins with no code change.
export const SPECIAL_COURSE_ICONS = {
  16: "bitdw", 17: "bitfs", 18: "bits",
  19: "princess_secret",   // has a real portrait
  20: "metal", 21: "wing", 22: "vanish", 23: "sky", 24: "aqua",
};

const GENERIC_STAR_SLOTS = 6;   // ui/assets/star_1.png … star_6.png
export const genericStarSrc = (slot = 0) =>
  `/ui/assets/star_${Math.min(slot + 1, GENERIC_STAR_SLOTS)}.png`;
const genericStar = genericStarSrc;
// generic gold-star art vs "real" art (a bundled split icon or an uploaded
// user icon) — the latter gets the opaque-square `courseicon` treatment
export const isGenericArt = (src) => /\/assets\/star_\d+\.png$/.test(src);
const starIconSrc = (stem) => `/ui/assets/star_icons/${stem}.png`;

/** THE stem -> img-src rule: "user:<file>" is an icon the user uploaded,
 *  served out of the data dir; anything else is a bundled split-icon stem.
 *  It lives here rather than in components/iconpicker.js (which re-exports it
 *  for its own callers) because entityIcon below has to resolve an OVERRIDE
 *  stem too, and this module imports nothing — so the one rule stays where
 *  node can test it. Resolving an override through starIconSrc instead asks
 *  for `/ui/assets/star_icons/user:foo.png`: a 404 that falls back to a plain
 *  star, which is what the picker did to every uploaded icon until 2026-07-26. */
export function iconSrcFromStem(stem) {
  const raw = String(stem);
  if (raw.startsWith("user:"))
    return `/api/icons/file/${encodeURIComponent(raw.slice(5))}`;
  return starIconSrc(raw);
}

/** An entity KEY -> {kind, id}: "star:8:2" -> {kind:"star", id:"8:2"},
 *  "segment:6" -> {kind:"segment", id:"6"}. The key is the vocabulary the
 *  server already speaks (icon_overrides, /api/target/ranks, the MARELO
 *  breakdown), and a picker option's own (kind, id) pair joined by ":" IS
 *  that key — which is why optionIcon below is a one-line wrapper. */
export function parseEntityKey(entityKey) {
  const raw = String(entityKey);
  const cut = raw.indexOf(STAR_ID_SEP);
  return cut < 0 ? { kind: raw, id: "" }
                 : { kind: raw.slice(0, cut), id: raw.slice(cut + 1) };
}

/** The generic-star slot an entity falls back to: a star's own slot (so the
 *  seven per-course stars keep distinct gold art), 0 for everything else.
 *  Shared by the chain's own fallback and by every caller's img onerror, so a
 *  load failure lands on the same art the resolver would have chosen. */
export function fallbackSlotForEntityKey(entityKey) {
  const { kind, id } = parseEntityKey(entityKey);
  if (kind !== "star") return 0;
  const { star } = parseStarId(id);
  return Number.isFinite(star) ? star : 0;
}

/**
 * THE art chain: an entity key -> an image URL. Every surface that draws a
 * practiceable thing goes through this one function — the practice banner,
 * the target picker, the route and segment editors, the Rank tab's coverage
 * tiles — so the same star or segment can never wear different art in two
 * places. ALWAYS returns a URL: a cell with no art collapses its own layout,
 * so every branch ends at the generic star.
 *
 * entityKey  "star:<course>:<slot>" | "segment:<id>" | "course:<id>"
 *            | "level:<id>"
 * context    { courseIcons     stem -> filename, from GET /api/icons/courses
 *              starIconsMode   "course" | "classic", the user's setting
 *              iconOverrides   view.icon_overrides, per-entity user picks
 *              courseByLevel   vocab.course_by_level
 *              segmentLevels   segment id -> its start levels
 *              segmentMeta     segment id -> {seedKey, category}, the corpus's
 *                              own identity + grouping off /api/segments }
 *            Built ONCE, by components/entityicons.js::iconContext(t) — never
 *            per call site. Three hand-built contexts is exactly how the Rank
 *            tab ended up drawing a plain gold star for BitFS Pipe Entry
 *            while the banner drew Bowser (live report 2026-07-26).
 *
 * Two rules the whole chain rests on:
 * - an explicit OVERRIDE wins for every kind and in either star-icon mode.
 *   It is the user naming this entity's art, not a default to re-derive.
 * - "classic" is a STAR setting (its control is labelled "Star icons", and
 *   the generic gold star is a star's own fallback). A segment and a Bowser
 *   stage keep their real art in classic mode.
 *
 * Four main courses (HMC, SSL, DDD, SL) have no portrait because the game has
 * no painting for them; they resolve to a hand-picked stand-in star icon,
 * which is the final answer, not a placeholder.
 */
export function entityIcon(entityKey, context = {}) {
  const { courseIcons = {}, starIconsMode = "course", iconOverrides = {},
          courseByLevel = {}, segmentLevels = {}, segmentMeta = {} } = context;
  const { kind, id } = parseEntityKey(entityKey);
  const override = iconOverrides[String(entityKey)];
  if (override) return iconSrcFromStem(override);
  const prefixFor = (course) => COURSE_ICON_PREFIXES[Number(course) - 1] || null;
  // A special stage's art is a COMPLETE stem ("vanish"), not a per-slot prefix
  // — those courses have one icon, not seven, so `${prefix}${slot+1}` would ask
  // for a vanish1.png that does not exist.
  const specialFor = (course) => SPECIAL_COURSE_ICONS[Number(course)] || null;
  const courseArt = (course) => {
    const prefix = prefixFor(course);
    const special = specialFor(course);
    const stem = prefix || special;
    if (!stem) return genericStar();
    // The portrait manifest wins for either kind, so PSS uses its real
    // painting and a portrait dropped in later needs no code change.
    if (courseIcons[stem]) return `/ui/assets/course_icons/${courseIcons[stem]}`;
    if (special) return starIconSrc(special);
    if (COURSE_SUBSTITUTE_ICONS[prefix])
      return starIconSrc(COURSE_SUBSTITUTE_ICONS[prefix]);
    return starIconSrc(`${prefix}1`);
  };

  if (kind === "course") return courseArt(id);
  if (kind === "star") {
    const { course, star } = parseStarId(id);
    if (starIconsMode !== "course") return genericStar(star);
    const prefix = prefixFor(course);
    if (prefix) return starIconSrc(`${prefix}${star + 1}`);
    // Special stages have ONE icon rather than one per slot, so every star in
    // them wears the stage's own art instead of a generic gold star.
    const special = specialFor(course);
    return special ? starIconSrc(special) : genericStar(star);
  }
  if (kind === "level") {
    const level = Number(id);
    if (LEVEL_ICONS[level]) return starIconSrc(LEVEL_ICONS[level]);
    const course = courseByLevel[String(level)];
    return course ? courseArt(course) : genericStar();
  }
  if (kind === "segment") {
    // NOT gated on starIconsMode: see the "classic is a STAR setting" rule
    // above. Most specific first — this definition's own art, then the stage
    // it starts in, then what its whole category wears. A Bowser pipe entry is
    // categorised Castle Movement AND starts in a Bowser stage, and the stage
    // is the more useful thing to show, which is why LEVEL_ICONS outranks the
    // category table. A row the corpus never seeded has no category of its
    // own, so `segmentCategory` infers one from where it starts — see the
    // registry comments above.
    const { seedKey, category, originRegion } = segmentMeta[String(id)] || {};
    const stem = SEGMENT_SEED_ICONS[seedKey]
      || (segmentLevels[String(id)] || [])
           .map((level) => LEVEL_ICONS[level]).find(Boolean)
      || SEGMENT_CATEGORY_ICONS[segmentCategory(category, originRegion)];
    return stem ? starIconSrc(stem) : genericStar();
  }
  return genericStar(fallbackSlotForEntityKey(entityKey));
}

/** The same chain, addressed the way a PICKER addresses an option: a kind
 *  plus that kind's own id ("8:2" for a star, "6" for a segment). Joined by
 *  ":" they ARE the entity key, so this is a rename, not a second chain. */
export function optionIcon(kind, id, context = {}) {
  return entityIcon(`${kind}${STAR_ID_SEP}${id}`, context);
}

// --- Grid picker layers ---------------------------------------------------
// The target picker's layer 2 is a UNION: a course's stars AND the segments
// that begin in it (user, 2026-07-25 — "just a union between the valid
// segments / stars for that course"). Both are practiceable things and
// /api/target is already kind-dispatched, so picking either is one click.
//
// A segment belongs to a course when its ORIGIN's level maps to one. Segments
// that start in the castle itself (a lobby BLJ, a basement movement) have no
// course, so they appear in no course's layer 2 — the route editor groups
// those by region instead.

/** The LEVEL a segment starts in, from its origin node key ("30" or "6:1"),
 *  or null. /api/segments carries `origin`, never `start_levels` — that field
 *  is on the session view's segment_targets, and assuming otherwise made every
 *  segment row fall back to a plain star (2026-07-25, twice). */
export function segmentLevel(segment) {
  const originKey = (segment.origin || {}).key;
  if (originKey == null) return null;
  const level = Number(String(originKey).split(":")[0]);
  return Number.isNaN(level) ? null : level;
}

/** The icon context's segmentLevels map, built the one right way. */
export function segmentLevelsOf(segments) {
  return Object.fromEntries((segments || []).map((segment) => {
    const level = segmentLevel(segment);
    return [String(segment.id), level == null ? [] : [level]];
  }));
}

/** Course id a segment belongs to, or null. `courseByLevel` is vocab's.
 *  Module-local: courseUnionGroups is its only consumer (review M9). */
function segmentCourse(segment, courseByLevel) {
  const level = segmentLevel(segment);
  if (level == null) return null;
  const course = (courseByLevel || {})[String(level)];
  return course == null ? null : Number(course);
}

// The rank map (GET /api/target/ranks) is keyed "star:8:2" / "segment:12",
// but a star OPTION's id is the bare composite "8:2" -- courseUnionGroups
// builds it via starId(), same as every other star id in this file -- while
// a segment option's id already IS "segment:12". Get this translation wrong
// and every star silently shows no rank while every segment works, which
// reads as missing data rather than as a bug.
export const entityKeyForOption = (optionId) => {
  const id = String(optionId);
  // "segment:12" and "area:6:1" already ARE entity keys; a bare composite
  // ("8:1") is a star's. An `area:` id is the recorder's terminal castle
  // tile (round 14) — prefixing it with star: would save a parent no row
  // could ever resolve.
  return id.startsWith("segment:") || id.startsWith("area:") ? id
                                                             : `star:${id}`;
};
// The rank map's key IS the entity key -- one name for the translation, used
// for the badge lookup here and by the recorder to say which entity a new
// subsection is a piece of (`SegmentDef.parent`, the sheet's own mapping key).
// Two hand-built copies of a key format is how they drift.
const rankMapKey = entityKeyForOption;

// build_entity_ranks (GET /api/target/ranks) answers per entity with a FLAT
// {rank, division, strat} -- the endpoint's own docstring names this shape.
// PracticeCell, after mario-cap-rank-icons (task 8, 2026-07-26), wants `rank`
// NESTED as {rank, division} -- the same shape rank_by_star/segment_targets
// already carry, so RankIcon has one prop contract everywhere it's used
// (banner cells and picker cells alike). Reshaping happens HERE, at the
// builder, rather than changing the server payload: build_entity_ranks' flat
// shape is also its own OWN documented contract (other pure builders may key
// off it directly), and this is the one place a star id gets translated to
// the map's key already, so the reshape rides the same lookup.

/**
 * Layer-1 course cells with layer-2 options = that course's stars followed by
 * its segments. Courses keep the catalog's order, which is game order.
 *   catalog       session view catalog ({courses:[{id,name,stars:[]}]})
 *   segments      GET /api/segments rows (each carrying `origin`)
 *   courseByLevel vocab.course_by_level
 *   ranksByKey    GET /api/target/ranks' {entity_key: {rank,division,strat}},
 *                 optional -- a caller that has not fetched it yet (header.js,
 *                 until a later task wires the fetch) keeps working unchanged.
 *                 An option's own `rank` comes back NESTED {rank,division} --
 *                 see withRank below for why.
 *   origins       vocab.origins (the server's ordered region taxonomy),
 *                 optional -- with it the castle segments split into one tile
 *                 per REGION (round 12 item 2: "we should show all of the
 *                 castle areas (lobby, grounds, courtyard, basement,
 *                 upstairs)" against ONE "Castle" tile holding 20 movements);
 *                 without it they stay one trailing Castle group, byte-
 *                 identical to before the parameter existed.
 */
export function courseUnionGroups(catalog, segments, courseByLevel,
                                  ranksByKey = {}, origins = null) {
  const byCourse = new Map();
  for (const segment of segments || []) {
    const course = segmentCourse(segment, courseByLevel);
    if (course == null) continue;
    if (!byCourse.has(course)) byCourse.set(course, []);
    byCourse.get(course).push(segment);
  }
  // Grid ORDER (user, 2026-07-25: "a grid of the courses, in order. At the end,
  // it also shows the bowser levels / secret stars"): the 15 main courses in
  // game order, then the Bowser levels, then the secret stages, and the castle
  // secret stars last. The catalog's own order puts course 0 FIRST, which put
  // "Castle Secret" where Bob-omb Battlefield belongs (live check 2026-07-25).
  const gridRank = (courseId) => {
    if (courseId >= 1 && courseId <= 15) return [0, courseId];   // main courses
    if (courseId >= 16 && courseId <= 18) return [1, courseId];  // Bowser levels
    if (courseId >= 19) return [2, courseId];                    // secret stages
    return [3, courseId];                                        // course 0
  };
  const inGridOrder = [...(catalog.courses || [])].sort((left, right) => {
    const [leftBand, leftId] = gridRank(left.id);
    const [rightBand, rightId] = gridRank(right.id);
    return leftBand - rightBand || leftId - rightId;
  });
  // Segments that start in the castle itself (a lobby BLJ, a basement
  // movement) belong to no course. They used to be dropped, which made the
  // union read as complete while there was NO deliberate way to target them —
  // the banner only offers them while you are standing there in-game (review
  // I2). They get their own trailing group instead.
  const castleSegments = (segments || []).filter((segment) =>
    segmentCourse(segment, courseByLevel) == null);
  // An unmapped option gets NO `rank`/`strat` keys at all -- returning it
  // untouched rather than spreading `{rank: undefined, strat: undefined}` --
  // so a caller that never passes `ranksByKey` gets back the exact same
  // option shape as before this parameter existed (JSON.stringify would drop
  // the undefined-valued keys either way, but this needs no round-trip to
  // prove it). `rank` is NESTED as `{rank, division}` -- PracticeCell's prop
  // of the same name, which now feeds RankIcon exactly like `rank_by_star`/
  // `segment_targets` do (mario-cap-rank-icons task 8, 2026-07-26) -- while
  // `build_entity_ranks` itself keeps answering FLAT `{rank, division,
  // strat}` per its own contract; the reshape happens at this one call site.
  // `strat` names the strategy the `rank` medal was earned WITH (the
  // best-scoring one, `build_entity_ranks`' answer) -- practicecell.js needs
  // it on the badge's tooltip, since the SAME cell later shows the ACTIVE
  // strategy's medal on the practice banner, often a different one (spec §3
  // risk 1; final review I2, 2026-07-26 -- this was the one piece of that
  // mitigation the client dropped).
  const withRank = (option) => {
    const entry = ranksByKey[rankMapKey(option.id)];
    if (!entry) return option;
    return { ...option, rank: { rank: entry.rank, division: entry.division },
             strat: entry.strat };
  };
  const courseCells = inGridOrder.map((course) => ({
    key: `course-${course.id}`,
    label: course.name,
    options: [
      ...(course.stars || []).map((name, slot) => withRank({
        id: starId(course.id, slot), name,
      })),
      ...(byCourse.get(course.id) || []).map((segment) => withRank({
        id: `segment:${segment.id}`, name: segment.name, sub: "segment",
      })),
    ],
  })).filter((group) => group.options.length > 0);
  if (castleSegments.length === 0) return courseCells;
  const segmentOption = (segment) => withRank({
    id: `segment:${segment.id}`, name: segment.name, sub: "segment",
  });
  if (!origins) {
    return [...courseCells, {
      key: "castle-segments", label: "Castle",
      options: castleSegments.map(segmentOption),
    }];
  }
  // One tile per castle REGION, grouped by each segment's own server-stamped
  // `origin.region` and ordered by the taxonomy the library already reads --
  // never a second hand-written region table (the duplicated-domain-fact
  // class this file exists to prevent). A segment whose origin names no
  // region (an "Anywhere" definition) keeps the trailing Castle tile.
  const regionRank = new Map(origins.map((region, index) => [region.key, index]));
  const byRegion = new Map();
  for (const segment of castleSegments) {
    const region = (segment.origin || {}).region || null;
    const key = regionRank.has(region) ? region : null;
    if (!byRegion.has(key)) byRegion.set(key, []);
    byRegion.get(key).push(segment);
  }
  const regionLabel = new Map(origins.map((region) => [region.key, region.label]));
  const regionCells = [...byRegion.entries()]
    .filter(([region]) => region !== null)
    .sort(([left], [right]) => regionRank.get(left) - regionRank.get(right))
    .map(([region, members]) => ({
      key: `castle-${region}`, label: regionLabel.get(region),
      options: members.map(segmentOption),
    }));
  const homeless = byRegion.get(null) || [];
  return [...courseCells, ...regionCells,
          ...(homeless.length ? [{
            key: "castle-segments", label: "Castle",
            options: homeless.map(segmentOption),
          }] : [])];
}

/** "segment:12" -> 12, or null for a star id. The target picker's two kinds
 *  share one control, so the id says which endpoint shape to post. */
export function parseSegmentId(id) {
  const text = String(id);
  return text.startsWith("segment:") ? Number(text.slice(8)) : null;
}
