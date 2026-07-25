// Turns the server's payloads into the group shape components/picker.js
// renders. Pure and DOM-free — the same reason ui/group.js sits outside
// components/, and what lets these be unit-tested through node.
//
// NO FILTERING happens here. Which options a given control may offer is the
// CALL SITE's business (world topology in the segment builder, route scoping
// in the route editor) and is passed to GroupedPicker as `allow`.
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
// It lives HERE rather than in components/picker.js because this module
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
