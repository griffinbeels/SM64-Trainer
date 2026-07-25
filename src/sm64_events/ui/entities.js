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

// The star picker is ONE control (user decision 2026-07-25): the optgroup is
// the COURSE, so region order survives one level up while the options are the
// stars themselves. Two sources carry the same information — the builder vocab
// and the session catalog — so each gets a thin adapter over one core.

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

// Four main courses are not entered through a painting, so the game has NO
// portrait for them. These are hand-picked stand-ins (user, 2026-07-25) — the
// star art that reads as that course — rather than a positional star-1
// default, which would have given Hazy Maze Cave its first star's icon.
// This is the final answer for these four; there is no art to wait for.
export const COURSE_SUBSTITUTE_ICONS = { hmc: "hmc6", ssl: "ssl2",
                                         ddd: "ddd1", sl: "sl6" };

const GENERIC_STAR_SLOTS = 6;   // ui/assets/star_1.png … star_6.png
const genericStar = (slot = 0) =>
  `/ui/assets/star_${Math.min(slot + 1, GENERIC_STAR_SLOTS)}.png`;
const starIconSrc = (stem) => `/ui/assets/star_icons/${stem}.png`;

/**
 * Art for one picker row. ALWAYS returns a URL — a row with no icon would
 * collapse its own layout, so every branch ends at the generic star.
 *
 * kind    "course" | "star" | "level" | "segment"
 * id      the option id (a star's is composite, "8:2")
 * context { courseIcons     stem -> filename, from GET /api/icons/courses
 *           starIconsMode   "course" | "classic", the user's setting
 *           iconOverrides   view.icon_overrides, per-entity user picks
 *           courseByLevel   vocab.course_by_level
 *           segmentLevels   segment id -> its start levels }
 *
 * Four main courses (HMC, SSL, DDD, SL) have no portrait because the game has
 * no painting for them; they resolve to their star-1 icon, which is the final
 * answer, not a placeholder.
 */
export function optionIcon(kind, id, context = {}) {
  const { courseIcons = {}, starIconsMode = "course", iconOverrides = {},
          courseByLevel = {}, segmentLevels = {} } = context;
  const prefixFor = (course) => COURSE_ICON_PREFIXES[Number(course) - 1] || null;
  const courseArt = (course) => {
    const prefix = prefixFor(course);
    if (!prefix) return genericStar();
    if (courseIcons[prefix]) return `/ui/assets/course_icons/${courseIcons[prefix]}`;
    if (COURSE_SUBSTITUTE_ICONS[prefix])
      return starIconSrc(COURSE_SUBSTITUTE_ICONS[prefix]);
    return starIconSrc(`${prefix}1`);
  };

  if (kind === "course") return courseArt(id);
  if (kind === "star") {
    const { course, star } = parseStarId(id);
    if (starIconsMode !== "course") return genericStar(star);
    const prefix = prefixFor(course);
    return prefix ? starIconSrc(`${prefix}${star + 1}`) : genericStar(star);
  }
  if (kind === "level") {
    const level = Number(id);
    if (LEVEL_ICONS[level]) return starIconSrc(LEVEL_ICONS[level]);
    const course = courseByLevel[String(level)];
    return course ? courseArt(course) : genericStar();
  }
  if (kind === "segment") {
    const override = iconOverrides[`segment:${id}`];
    if (override) return starIconSrc(override);
    const stem = (segmentLevels[String(id)] || [])
      .map((level) => LEVEL_ICONS[level]).find(Boolean);
    return stem ? starIconSrc(stem) : genericStar();
  }
  return genericStar();
}
