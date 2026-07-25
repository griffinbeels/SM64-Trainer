# Shared Entity Picker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One grouped picker primitive behind every "choose a course / star / level / segment" control, so the region grouping reaches all of them and the next improvement is made once.

**Architecture:** A dumb `GroupedPicker` component renders `<optgroup>`-per-group and owns three behaviours (render groups, drop emptied groups, always keep the current value listed). Pure per-kind builders turn server payloads into its `groups` shape. Every domain filter stays at the call site and is passed in as `allow`.

**Tech Stack:** Preact + htm ESM served as-is (no build step), Python 3.12 / FastAPI / pytest, node for pure-JS unit tests.

**Spec:** `docs/superpowers/specs/2026-07-25-shared-entity-picker-design.md`

## Global Constraints

- Branch off current `main`; run everything from the repo root (or the feature worktree root if one was created).
- `uv run pytest -q` must pass before every commit. Never `pip`. **Baseline: 1515 passed.**
- UI components import Preact through index.html's **importmap** under BARE specifiers (`import { h } from "preact"`, `"preact/hooks"`, `"htm"`, then `const html = htm.bind(h)`). There is no `vendor/preact.js`.
- **Node needs a resolver hook to execute a UI module that imports `preact`/`htm`** (see `f38bdbd`) — bare specifiers only index.html's importmap resolves without one. We keep node-tested logic in import-free modules instead (`ui/group.js`, `ui/entities.js`); `visibleGroups` is there for exactly that reason and `picker.js` imports it.
- `node --check` on a file path is BLIND to ESM. Syntax-check with `node --input-type=module --check < file.js`, and verify behaviour by rendering — unit tests plus a syntax check once shipped an invisible feature here.
- Don't start `python -m sm64_events.main`; the user may be playing and it takes the recorder lock. UI verification uses a static harness on port **8137** (never 8064/8065/8066), deleted and killed in the same task.
- Run verification through the **Bash tool** — PowerShell mangles native exit codes.
- No single-letter variables, including JS callbacks. Match each file's comment density; this codebase carries the *why* at the point of use.
- Commit messages explain WHY (see `git log`).

---

## File Structure

| File | Responsibility | Owner |
|---|---|---|
| `src/sm64_events/ui/components/picker.js` (new) | `GroupedPicker` + the pure `visibleGroups` it renders | Task 1 |
| `src/sm64_events/ui/entities.js` (new) | Pure per-kind group builders | Task 2 |
| `src/sm64_events/tracking/views.py` | `_catalog()` gains `course_groups` | Task 3 |
| `src/sm64_events/ui/components/segments.js` | `ParamInput` renders through the picker; delete `groupedDropdown` | Task 4 |
| `src/sm64_events/ui/components/header.js` | Target modal: Course+Star → one star control | Task 5 |
| `src/sm64_events/ui/components/routes.js` | `ItemPicker`: one star control + grouped segments | Task 6 |
| `tests/test_ui_picker_parity.py` (new) + `.claude/rules/ui.md` | The parity gate + the change map | Task 7 |

**Deviation from the spec, deliberate:** the spec says the builders live "in the same module" as the picker. They are split into `ui/entities.js` because (a) they are pure data and node-testable without a DOM, exactly like `ui/group.js`, and (b) the split lets Tasks 1 and 2 run concurrently instead of contending on one file. Same contract, one more file.

## Waves

- **Wave 1 (parallel, disjoint):** Task 1 · Task 2 · Task 3
- **Wave 2 (parallel, disjoint):** Task 4 · Task 5 · Task 6 — each owns exactly one component file
- **Wave 3:** Task 7

---

### Task 1: `GroupedPicker` + `visibleGroups`

**Files:**
- Create: `src/sm64_events/ui/components/picker.js`
- Test: `tests/test_ui_picker.py` (new)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `visibleGroups(groups, allow, value) -> groups` (pure)
  - `GroupedPicker({ groups, value, onChange, allow, placeholder, disabled })`
  - group shape `{ key, label, options: [{ id, name }] }`; **ids are strings**
  - `onChange` receives the string id, or `null` when the placeholder is chosen

- [ ] **Step 1: Write the failing test**

Create `tests/test_ui_picker.py`:

```python
"""visibleGroups (ui/components/picker.js) driven through node.

It is the whole reason the shared picker exists: dropping emptied groups and
KEEPING THE CURRENT VALUE listed are behaviours that have been implemented —
and got wrong — separately in stratpicker.js and the segment builder.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

PICKER_JS = (Path(__file__).resolve().parent.parent / "src" / "sm64_events"
             / "ui" / "components" / "picker.js").as_uri()

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not on PATH")


def run_node(body: str):
    script = f"import {{ visibleGroups }} from {PICKER_JS!r};\n{body}"
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


GROUPS = """
const groups = [
  { key: "a", label: "Lobby", options: [{ id: "9", name: "BoB" }, { id: "24", name: "WF" }] },
  { key: "b", label: "Basement", options: [{ id: "8", name: "SSL" }] },
];
"""


def test_without_a_filter_every_group_survives():
    tree = run_node(GROUPS + 'console.log(JSON.stringify(visibleGroups(groups, null, null)));')
    assert [group["label"] for group in tree] == ["Lobby", "Basement"]
    assert [option["id"] for option in tree[0]["options"]] == ["9", "24"]


def test_a_group_emptied_by_the_filter_is_dropped():
    tree = run_node(GROUPS + 'console.log(JSON.stringify('
                    'visibleGroups(groups, (id) => id === "9", null)));')
    assert [group["label"] for group in tree] == ["Lobby"]
    assert [option["id"] for option in tree[0]["options"]] == ["9"]


def test_the_current_value_survives_a_filter_that_rejects_it():
    # A stored/legacy value fed to a filtered dropdown must never vanish — it
    # renders BLANK and reads as unset. Fixed twice before; pinned here.
    tree = run_node(GROUPS + 'console.log(JSON.stringify('
                    'visibleGroups(groups, (id) => id === "9", "8")));')
    assert [group["label"] for group in tree] == ["Lobby", "Basement"]
    assert [option["id"] for option in tree[1]["options"]] == ["8"]


def test_filtering_does_not_mutate_the_caller_s_groups():
    tree = run_node(GROUPS
                    + 'visibleGroups(groups, () => false, null);\n'
                    + 'console.log(JSON.stringify(groups.map((g) => g.options.length)));')
    assert tree == [2, 1]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_ui_picker.py -q`
Expected: FAIL — `ERR_MODULE_NOT_FOUND` for `picker.js`

- [ ] **Step 3: Implement**

Create `src/sm64_events/ui/components/picker.js`:

```js
import { h } from "preact";
import htm from "htm";

const html = htm.bind(h);

// THE picker behind every "choose a course / star / level / segment" control:
// the segment builder's clause params, the practice-target modal, and the
// route step editor. It knows NOTHING about levels, courses, stars, segments,
// world topology or routes — callers supply the groups (ui/entities.js builds
// them) and their own filter.
//
// It owns exactly three behaviours, each of which has been implemented
// separately, and wrongly, somewhere in this codebase before:
//   1. render one <optgroup> per group, in the caller's order;
//   2. drop a group the filter emptied, so no heading sits over nothing;
//   3. KEEP THE CURRENT VALUE listed even when the filter rejects it.
// (3) is the important one: a stored or legacy value fed to a filtered
// dropdown otherwise renders blank and reads as unset — fixed once in
// stratpicker.js (purged strategies) and again in the segment builder
// (out-of-topology stored defs) before this component existed.
//
// Ids are STRINGS, so a composite id ("8:2" = course 8, star 2) is as valid as
// a level id. The caller encodes and decodes; this file only passes them on.

/** Groups with the filter applied: emptied groups removed, current value kept.
 *  Pure — returns new objects, never mutates the caller's array. */
export function visibleGroups(groups, allow, value) {
  const keep = (option) => !allow || allow(option.id) || option.id === value;
  return (groups || [])
    .map((group) => ({ ...group, options: group.options.filter(keep) }))
    .filter((group) => group.options.length > 0);
}

/**
 * groups      [{ key, label, options: [{ id, name }] }]
 * value       current id (string) or null
 * onChange    (id | null) => void
 * allow       optional (id) => boolean — the CALLER's domain filter
 * placeholder optional leading option's label; omit for no placeholder
 */
export function GroupedPicker({ groups, value, onChange, allow, placeholder,
                               disabled = false }) {
  const shown = visibleGroups(groups, allow, value);
  return html`<select value=${value ?? ""} disabled=${disabled}
      onchange=${(event) => onChange(event.target.value === ""
        ? null : event.target.value)}>
    ${placeholder == null ? null
      : html`<option value="">${placeholder}</option>`}
    ${shown.map((group) => html`<optgroup key=${group.key} label=${group.label}>
      ${group.options.map((option) => html`<option key=${option.id}
        value=${option.id}>${option.name}</option>`)}
    </optgroup>`)}
  </select>`;
}
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_ui_picker.py -q`
Expected: PASS (4 tests)

Run: `node --input-type=module --check < src/sm64_events/ui/components/picker.js`
Expected: exit 0

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/ui/components/picker.js tests/test_ui_picker.py
git commit -F- <<'MSG'
feat(ui): one grouped picker for every entity selection

Three implementations of "pick a course, then a star" exist and share no code,
which is why yesterday's region grouping reached the segment builder and not
the practice-target modal.

The component owns only what everyone gets wrong: dropping a group the filter
emptied, and KEEPING THE CURRENT VALUE listed when the filter rejects it. That
second one has already been fixed twice, independently — purged strategies in
stratpicker.js, out-of-topology stored defs in the segment builder. Third time
it lives in one place, with a test.

Domain rules stay OUT: the caller passes `allow`. Ids are strings so a
composite "8:2" (course 8, star 2) is as valid as a level id.
MSG
```

---

### Task 2: The per-kind group builders

**Files:**
- Create: `src/sm64_events/ui/entities.js`
- Test: `tests/test_ui_entities.py` (new)

**Interfaces:**
- Consumes: Task 1's group shape (`{ key, label, options: [{ id, name }] }`).
- Produces:
  - `levelOptions(vocab)` — from `vocab.level_groups` + `vocab.levels`
  - `courseOptions(vocab)` — from `vocab.course_groups` + `vocab.courses`
  - `starOptionsFromVocab(vocab)` — optgroup per course, ids `"<course>:<star>"`
  - `starOptionsFromCatalog(catalog)` — same shape from the session catalog
  - `segmentOptions(defs, taxonomy)` — grouped by each def's `origin.region`
  - `parseStarId(id) -> { course, star }`

- [ ] **Step 1: Write the failing test**

Create `tests/test_ui_entities.py`:

```python
"""The pure group builders (ui/entities.js), driven through node."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ENTITIES_JS = (Path(__file__).resolve().parent.parent / "src" / "sm64_events"
               / "ui" / "entities.js").as_uri()

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not on PATH")


def run_node(imports: str, body: str):
    script = f"import {{ {imports} }} from {ENTITIES_JS!r};\n{body}"
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


VOCAB = """
const vocab = {
  levels: { "6": "Castle Inside", "9": "Bob-omb Battlefield", "8": "Shifting Sand Land" },
  courses: { "1": "Bob-omb Battlefield", "8": "Shifting Sand Land" },
  stars: { "1": ["Big Bob-omb", "Footrace"], "8": ["In the Talons", "Shining Atop"] },
  level_groups: [
    { key: "6:1", label: "Lobby", levels: [6, 9] },
    { key: "6:3", label: "Basement", levels: [8] },
  ],
  course_groups: [
    { key: "6:1", label: "Lobby", courses: [1] },
    { key: "6:3", label: "Basement", courses: [8] },
  ],
};
"""


def test_level_options_carry_group_labels_and_string_ids():
    groups = run_node("levelOptions", VOCAB
                      + "console.log(JSON.stringify(levelOptions(vocab)));")
    assert [group["label"] for group in groups] == ["Lobby", "Basement"]
    assert groups[0]["options"] == [{"id": "6", "name": "Castle Inside"},
                                    {"id": "9", "name": "Bob-omb Battlefield"}]


def test_star_options_are_one_control_grouped_by_course():
    groups = run_node("starOptionsFromVocab", VOCAB
                      + "console.log(JSON.stringify(starOptionsFromVocab(vocab)));")
    # a group per COURSE, courses in region order (Lobby's BoB before SSL)
    assert [group["label"] for group in groups] == ["Bob-omb Battlefield",
                                                    "Shifting Sand Land"]
    assert groups[1]["options"] == [{"id": "8:0", "name": "In the Talons"},
                                    {"id": "8:1", "name": "Shining Atop"}]


def test_star_ids_round_trip():
    parsed = run_node("parseStarId",
                      'console.log(JSON.stringify(parseStarId("8:1")));')
    assert parsed == {"course": 8, "star": 1}


def test_catalog_and_vocab_produce_the_same_star_groups():
    catalog = """
const catalog = {
  course_groups: [
    { key: "6:1", label: "Lobby", courses: [1] },
    { key: "6:3", label: "Basement", courses: [8] },
  ],
  courses: [
    { id: 1, name: "Bob-omb Battlefield", stars: ["Big Bob-omb", "Footrace"] },
    { id: 8, name: "Shifting Sand Land", stars: ["In the Talons", "Shining Atop"] },
  ],
};
"""
    groups = run_node("starOptionsFromCatalog", catalog
                      + "console.log(JSON.stringify(starOptionsFromCatalog(catalog)));")
    assert [group["label"] for group in groups] == ["Bob-omb Battlefield",
                                                    "Shifting Sand Land"]
    assert groups[0]["options"][0] == {"id": "1:0", "name": "Big Bob-omb"}


def test_segments_group_by_origin_region_in_taxonomy_order():
    body = """
const taxonomy = [
  { key: "16", label: "Castle Grounds", children: [] },
  { key: "6:1", label: "Lobby", children: [] },
  { key: null, label: "Anywhere", children: [] },
];
const defs = [
  { id: 3, name: "LBLJ", origin: { region: "6:1", region_label: "Lobby" } },
  { id: 7, name: "Lakitu Skip", origin: { region: "16", region_label: "Castle Grounds" } },
  { id: 9, name: "Reset split", origin: { region: null, region_label: "Anywhere" } },
];
console.log(JSON.stringify(segmentOptions(defs, taxonomy)));
"""
    groups = run_node("segmentOptions", body)
    assert [group["label"] for group in groups] == ["Castle Grounds", "Lobby",
                                                    "Anywhere"]
    assert groups[0]["options"] == [{"id": "7", "name": "Lakitu Skip"}]
    assert groups[2]["options"] == [{"id": "9", "name": "Reset split"}]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_ui_entities.py -q`
Expected: FAIL — `ERR_MODULE_NOT_FOUND` for `entities.js`

- [ ] **Step 3: Implement**

Create `src/sm64_events/ui/entities.js`:

```js
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
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_ui_entities.py -q`
Expected: PASS (5 tests)

Run: `node --input-type=module --check < src/sm64_events/ui/entities.js`
Expected: exit 0

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/ui/entities.js tests/test_ui_entities.py
git commit -F- <<'MSG'
feat(ui): pure builders turning server payloads into picker groups

One per kind, no filtering in any of them — which options a control may offer
is the call site's business, not the builder's.

The star builder is the collapsed control the user asked for: the optgroup is
the COURSE, so region ordering survives one level up while the options are the
stars, and one control replaces the old course+star pair. Its id is composite
("8:1"), kept explicit rather than hidden behind an object so the <option
value> round-trip stays inspectable in devtools.

Vocab and the session catalog carry the same information in different shapes,
so each gets a thin adapter over one core rather than two implementations.
segmentOptions groups by the origin stamp the server already ships, so the
route editor's segment picker reads exactly like the library beside it.
MSG
```

---

### Task 3: `course_groups` on the session catalog

**Files:**
- Modify: `src/sm64_events/tracking/views.py` (`_catalog`, ~line 158)
- Test: `tests/test_views.py`

**Interfaces:**
- Consumes: `tracking/segments.course_groups()` (already on main).
- Produces: session view `catalog.course_groups` — `[{key, label, courses: [ids]}]`, identical to `vocab()["course_groups"]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_views.py`:

```python
def test_catalog_carries_the_same_course_groups_as_vocab():
    # The header and route pickers read the CATALOG; the segment builder reads
    # VOCAB. They must group identically or the same star sits under different
    # headings in different pickers.
    from sm64_events.tracking.segments import course_groups, vocab
    from sm64_events.tracking.views import _CATALOG

    assert _CATALOG["course_groups"] == course_groups()
    assert _CATALOG["course_groups"] == vocab()["course_groups"]


def test_catalog_course_groups_cover_every_catalog_course():
    from sm64_events.tracking.views import _CATALOG

    grouped = {course for group in _CATALOG["course_groups"]
               for course in group["courses"]}
    assert grouped == {course["id"] for course in _CATALOG["courses"]}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_views.py -q -k catalog`
Expected: FAIL — `KeyError: 'course_groups'`

- [ ] **Step 3: Implement**

In `src/sm64_events/tracking/views.py`, extend the existing import from
`sm64_events.tracking.segments` with `course_groups`, then change `_catalog`:

```python
def _catalog() -> dict:
    courses = []
    for cid, cname in COURSE_NAMES.items():
        # max(..., 1): the catalog always shows at least one star row even
        # for course 0 (display fallback); the count itself lives in
        # addresses.star_count
        n = max(star_count(cid), 1)
        courses.append({"id": cid, "name": cname,
                        "stars": [star_name(cid, s) for s in range(n)]})
    # The SAME grouping vocab ships, so a catalog-driven picker (the practice
    # target modal, the route step editor) files a star under the same castle
    # region a vocab-driven one does (the segment builder). Pure and cheap, so
    # computing it at import with the rest of the catalog is fine — but it must
    # never grow a database dependency.
    return {"courses": courses, "course_groups": course_groups()}
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_views.py -q`
Expected: PASS

- [ ] **Step 5: Run the full suite** (the session-view payload is asserted in several files)

Run: `uv run pytest -q`
Expected: PASS, baseline 1515 + your 2

- [ ] **Step 6: Commit**

```bash
git add src/sm64_events/tracking/views.py tests/test_views.py
git commit -F- <<'MSG'
feat(views): ship course_groups on the session catalog

The header and route pickers read the catalog; the segment builder reads vocab.
Without this they would group by different rules and the same star would sit
under different headings in different pickers — the exact drift the shared
picker exists to stop.

Reuses tracking/segments.course_groups() rather than deriving a second time:
the taxonomy keeps one home.
MSG
```

---

### Task 4: The segment builder's clause params

**Files:**
- Modify: `src/sm64_events/ui/components/segments.js` (`ParamInput`, ~lines 66-150)
- Test: `tests/test_segments_editor_ui.py`

**Interfaces:**
- Consumes: `GroupedPicker` (Task 1); `levelOptions`, `courseOptions`, `starOptionsFromVocab`, `parseStarId` (Task 2).
- Produces: nothing new.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_segments_editor_ui.py`:

```python
def test_clause_params_render_through_the_shared_picker():
    assert "GroupedPicker" in SEGMENTS_JS_SOURCE
    assert "levelOptions" in SEGMENTS_JS_SOURCE
    # the file-local first draft is gone, not merely unused
    assert "groupedDropdown" not in SEGMENTS_JS_SOURCE


def test_the_topology_filter_stays_at_this_call_site():
    # Domain rules never move into the picker: allowedIds is computed here and
    # handed over as `allow`.
    assert "allowedIds" in SEGMENTS_JS_SOURCE
    assert "allow=${" in SEGMENTS_JS_SOURCE
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_segments_editor_ui.py -q`
Expected: FAIL — `assert "GroupedPicker" in SEGMENTS_JS_SOURCE`

- [ ] **Step 3: Implement**

Add to the imports in `segments.js`:

```js
import { GroupedPicker } from "./picker.js";
import { courseOptions, levelOptions, parseStarId, starId,
         starOptionsFromVocab } from "../entities.js";
```

Replace the whole `groupedDropdown` helper (added 2026-07-25, this component's
first draft) and the `level` / `course` / `star` branches of `ParamInput` with:

```js
  // The topology filter is THIS call site's rule, so it is computed here and
  // handed to the picker as `allow` — the picker never learns about world
  // edges. `permittedId` takes the string ids the picker deals in.
  const allowed = allowedIds(schema, clause, vocab.connections);
  const permitted = ([id]) => !allowed || allowed.has(Number(id))
    || Number(id) === value;
  const permittedId = (id) => permitted([id]);

  if (schema.kind === "level") {
    // schema.enum restricts the choices (area_enter offers only the castle
    // hubs); absent enum = every level.
    const groups = levelOptions(vocab).map((group) => ({
      ...group,
      options: group.options.filter((option) =>
        !schema.enum || schema.enum.includes(Number(option.id))),
    })).filter((group) => group.options.length > 0);
    return html`<${GroupedPicker} groups=${groups} allow=${permittedId}
      value=${value == null ? null : String(value)}
      placeholder=${schema.required ? "— pick level —" : "(any level)"}
      onChange=${(id) => onChange(id == null ? null : Number(id))} />`;
  }
  if (schema.kind === "course")
    return html`<${GroupedPicker} groups=${courseOptions(vocab)}
      value=${value == null ? null : String(value)}
      placeholder=${schema.required ? "— pick course —" : "(any course)"}
      onChange=${(id) => onChange(id == null ? null : Number(id))} />`;
```

The `star` branch keeps its dependence on the sibling `course` param — this
control edits ONE param, so it must not set the course too:

```js
  if (schema.kind === "star") {
    // Dependent on the sibling course param: with no course picked, any star
    // matches, so the control is disabled rather than lying about a choice.
    // The shared star groups carry composite ids, so this branch narrows them
    // to the picked course and unpacks the star index on the way out.
    const groups = starOptionsFromVocab(vocab)
      .filter((group) => group.key === `course-${clause.course}`);
    return html`<${GroupedPicker} groups=${groups}
      disabled=${clause.course == null}
      value=${value == null ? null : starId(clause.course, value)}
      placeholder=${schema.required ? "— pick star —" : "(any star)"}
      onChange=${(id) => onChange(id == null ? null : parseStarId(id).star)} />`;
  }
```

Leave the `subarea`, `seconds`, and fallback branches exactly as they are — a
3-item subarea list has nothing to group, and both numeric inputs must keep
their `oninput` (a poll re-render wipes uncommitted `onchange` values).

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q`
Expected: PASS

Run: `node --input-type=module --check < src/sm64_events/ui/components/segments.js`
Expected: exit 0

- [ ] **Step 5: Verify by rendering (mandatory)**

Harness `ParamInput` with a stubbed vocab (as `_harness_picker.html` did on
2026-07-24 — see `git log --grep="grouped pickers"` for the pattern): serve
`python -m http.server 8137 --directory src/sm64_events/ui`, open with
`chrome --headless=new --dump-dom`, and confirm:
1. the level control renders one `<optgroup>` per region;
2. with `clause.from = 16` (coming from Castle Grounds) it narrows to Castle
   Inside + VCUtM and **still shows both headings**;
3. the star control is disabled with no course picked, and lists that course's
   stars once one is;
4. no `JS ERROR:` in the console log.
Delete the harness and kill port 8137 in this same task.

- [ ] **Step 6: Commit**

```bash
git add src/sm64_events/ui/components/segments.js tests/test_segments_editor_ui.py
git commit -F- <<'MSG'
refactor(ui): segment builder selects render through the shared picker

Deletes groupedDropdown — this component's first draft, written for one file a
day earlier. Same rendering, one implementation.

The topology filter stays HERE, as `allow`: which levels are reachable from the
other side of a move is this call site's rule, and the picker never learns
about world edges. The star branch narrows the shared course-grouped list to
the sibling course param and unpacks the composite id on the way out, so it
still edits exactly one param.
MSG
```

---

### Task 5: The practice-target modal

**Files:**
- Modify: `src/sm64_events/ui/components/header.js` (target editor, ~lines 253-275)
- Test: `tests/test_header_ui.py` (new — verified: no header test file exists today)

**Interfaces:**
- Consumes: `GroupedPicker` (Task 1); `starOptionsFromCatalog`, `parseStarId`, `starId` (Task 2); `catalog.course_groups` (Task 3).
- Produces: nothing new.

- [ ] **Step 1: Write the failing test**

Create `tests/test_header_ui.py`:

```python
from pathlib import Path

HEADER_JS = (Path(__file__).resolve().parent.parent / "src" / "sm64_events"
             / "ui" / "components" / "header.js").read_text(encoding="utf-8")


def test_target_modal_picks_a_star_with_one_control():
    # Course + Star collapsed into one grouped control (user decision
    # 2026-07-25): the optgroup is the course, the option is the star.
    assert "starOptionsFromCatalog" in HEADER_JS
    assert "GroupedPicker" in HEADER_JS
    assert "parseStarId" in HEADER_JS


def test_target_modal_still_posts_course_and_star_separately():
    # The API contract is unchanged — only the control collapsed.
    assert "course_id:" in HEADER_JS and "star_id:" in HEADER_JS
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_header_ui.py -q`
Expected: FAIL — `assert "starOptionsFromCatalog" in HEADER_JS`

- [ ] **Step 3: Implement**

Add to `header.js`'s imports:

```js
import { GroupedPicker } from "./picker.js";
import { parseStarId, starId, starOptionsFromCatalog } from "../entities.js";
```

Replace the Course and Star `<label>` pair in the target editor with one
control, leaving Strategy untouched:

```js
      <label>Star<${GroupedPicker}
        groups=${starOptionsFromCatalog(v.catalog)}
        value=${starId(Number(course), Number(star))}
        placeholder=${null}
        onChange=${(id) => {
          // One control, still two fields on the wire: unpack and reuse the
          // existing pickStar so the strategy list re-resolves for the new star.
          const picked = parseStarId(id);
          pickStar(picked.course, picked.star);
        }} /></label>
```

`apply()` is unchanged — it already posts `course_id` / `star_id` from the same
two state values `pickStar` sets.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q`
Expected: PASS

Run: `node --input-type=module --check < src/sm64_events/ui/components/header.js`
Expected: exit 0

- [ ] **Step 5: Verify by rendering (mandatory)**

Harness the target editor with a stubbed `v.catalog` carrying `course_groups`.
Confirm: one Star control; optgroups are courses in region order; picking
"Shifting Sand Land → Shining Atop the Pyramid" leaves the Strategy list
re-resolved for that star; Set target still posts `{course_id: 8, star_id: ...}`
(log `fetch` in the stub). Delete the harness and kill port 8137 in this task.

- [ ] **Step 6: Commit**

```bash
git add src/sm64_events/ui/components/header.js tests/test_header_ui.py
git commit -F- <<'MSG'
feat(ui): the practice target picks a star with one grouped control

Course + Star were two dependent selects, flat, listing 15 courses and then 7
stars — the picker a user touches most and the one yesterday's region grouping
never reached.

Now one control whose optgroup is the course, courses in castle-region order.
The wire format is untouched: the composite id unpacks into the same course_id
and star_id the endpoint already takes, through the same pickStar that
re-resolves the strategy list.
MSG
```

---

### Task 6: The route step editor

**Files:**
- Modify: `src/sm64_events/ui/components/routes.js` (`ItemPicker`, ~lines 131-165)
- Test: `tests/test_ui_grouplist.py` (extend — it already holds routes.js source assertions)

**Interfaces:**
- Consumes: `GroupedPicker` (Task 1); `starOptionsFromCatalog`, `segmentOptions`, `parseStarId` (Task 2); `catalog.course_groups` (Task 3).
- Produces: nothing new.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ui_grouplist.py`:

```python
def test_route_item_picker_uses_the_shared_picker():
    assert "GroupedPicker" in ROUTES
    assert "starOptionsFromCatalog" in ROUTES
    assert "segmentOptions" in ROUTES


def test_route_segment_picker_groups_like_the_library():
    # The segment list beside it groups by origin region; this one did not.
    assert "segmentOptions(segs" in ROUTES
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_ui_grouplist.py -q`
Expected: FAIL — `assert "GroupedPicker" in ROUTES`

- [ ] **Step 3: Implement**

Add to `routes.js`'s imports:

```js
import { GroupedPicker } from "./picker.js";
import { parseStarId, segmentOptions, starId,
         starOptionsFromCatalog } from "../entities.js";
```

`ItemPicker` gains the taxonomy for segment grouping and collapses its star
pair. Replace the component with:

```js
// Star/segment picker shared by "add step" and "add option to a group".
function ItemPicker({ catalog, segs, vocab, onPick, label }) {
  const [mode, setMode] = useState("star");
  const starGroups = starOptionsFromCatalog(catalog);
  const firstStar = starGroups[0] ? starGroups[0].options[0].id : null;
  const [star, setStar] = useState(firstStar);
  const segGroups = segmentOptions(segs, (vocab || {}).origins);
  const [segId, setSegId] = useState(segs[0] ? String(segs[0].id) : null);
  const pick = () => {
    if (mode === "star") {
      const picked = parseStarId(star);
      onPick({ type: "star", course: picked.course, star: picked.star });
    } else {
      onPick({ type: "segment", segment_id: Number(segId) });
    }
  };
  return html`<div class="routepick">
    <select value=${mode} onchange=${(e) => setMode(e.target.value)}>
      <option value="star">Star</option>
      <option value="segment">Segment</option>
    </select>
    ${mode === "star"
      ? html`<${GroupedPicker} groups=${starGroups} value=${star}
          placeholder=${null} onChange=${(id) => setStar(id)} />`
      : segs.length === 0
        ? html`<span class="meta">no segments defined</span>`
        : html`<${GroupedPicker} groups=${segGroups} value=${segId}
            placeholder=${null} onChange=${(id) => setSegId(id)} />`}
    <button disabled=${mode === "segment" && segs.length === 0} onclick=${pick}>
      <${Icon} name="plus" size=${15} /> ${label || "Add"}
    </button>
  </div>`;
}
```

Then pass `vocab` at both `ItemPicker` call sites, verified against the file:

- `routes.js:494` sits inside `Routes`, where `vocab` is already state
  (`routes.js:277`) — pass `vocab=${vocab}` directly.
- `routes.js:216` sits inside **`StepRow`** (`routes.js:169`), which takes
  `{ step, view, idx, total, catalog, segs, onChange, onMove, onRemove, weakest }`.
  Add `vocab` to that destructure, forward it to `ItemPicker`, and pass
  `vocab=${vocab}` at `StepRow`'s own call site inside `Routes`.

`segmentOptions` tolerates a null taxonomy (it falls back to one "Other" group),
so a first paint before `/api/segments/vocab` resolves still renders.

Note the bug this also closes: the old `useState(segs[0] ? ... : null)` captured
an empty list at mount and never re-seeded when `segs` arrived. Seeding from
the same expression is unchanged, but `segId` is now a string, matching the
picker's contract; `pick()` converts once, at the boundary.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q`
Expected: PASS

Run: `node --input-type=module --check < src/sm64_events/ui/components/routes.js`
Expected: exit 0

- [ ] **Step 5: Verify by rendering (mandatory)**

Harness `ItemPicker` with a stubbed catalog + `segs` carrying `origin` stamps +
`vocab.origins`. Confirm: the star control is one grouped select; switching to
Segment shows segments grouped by castle region in taxonomy order; Add reports
`{type: "star", course, star}` and `{type: "segment", segment_id}` as NUMBERS.
Delete the harness and kill port 8137 in this task.

- [ ] **Step 6: Commit**

```bash
git add src/sm64_events/ui/components/routes.js tests/test_ui_grouplist.py
git commit -F- <<'MSG'
refactor(ui): route step editor picks through the shared picker

The star pair collapses to one grouped control, and the segment picker now
groups by castle region — it sat beside a library that groups exactly that way
and listed 65 definitions flat.

Ids are strings inside the picker and convert once at the boundary, in pick(),
so the step candidates on the wire stay numeric.
MSG
```

---

### Task 7: The parity gate and the change map

**Files:**
- Create: `tests/test_ui_picker_parity.py`
- Modify: `.claude/rules/ui.md`

**Interfaces:** none.

- [ ] **Step 1: Write the test**

```python
"""Every entity selection renders through the shared picker.

This is the test that addresses the actual complaint (2026-07-25: "feels like
we're redoing a lot of the same work over and over again"). Without it a fifth
hand-rolled course/star select appears the next time someone needs one in a
hurry, and the grouping silently stops being universal.
"""
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "src" / "sm64_events" / "ui"

# Call sites that select a level, course, star or segment. Each must import
# the shared picker. Add a row when a new one appears — do NOT add an exception.
ENTITY_PICKER_CALL_SITES = [
    "components/segments.js",   # clause params: level / course / star
    "components/header.js",     # practice-target modal: star
    "components/routes.js",     # route step editor: star / segment
]


def test_every_entity_selection_uses_the_shared_picker():
    for relative in ENTITY_PICKER_CALL_SITES:
        source = (UI / relative).read_text(encoding="utf-8")
        assert "GroupedPicker" in source, relative


def test_no_call_site_rebuilds_the_grouping_itself():
    # The taxonomy has one home (tracking/segments.py). A call site computing
    # its own region membership is the drift this whole change removes.
    for relative in ENTITY_PICKER_CALL_SITES:
        source = (UI / relative).read_text(encoding="utf-8")
        for derivation in ("world_regions", "CASTLE_REGION", "region_for_node"):
            assert derivation not in source, f"{relative}: {derivation}"


def test_the_picker_owns_no_domain_vocabulary():
    # The inverse guard: domain rules must not migrate INTO the picker.
    picker = (UI / "components" / "picker.js").read_text(encoding="utf-8")
    for domain_word in ("course", "star", "level", "segment", "topology",
                        "route"):
        assert domain_word not in picker.lower().split("//")[0], domain_word
```

**Note for the implementer:** the last test only inspects the code above the
first comment marker, so the explanatory header may (and does) mention those
words. If that split proves brittle, assert on the identifiers instead —
`"vocab" not in picker` and `"catalog" not in picker` — but do not delete the
guard: it is what keeps the picker dumb.

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_ui_picker_parity.py -q`
Expected: PASS (3 tests) once Tasks 4-6 have landed

- [ ] **Step 3: Update the change map**

In `.claude/rules/ui.md`, add above the "Library grouping" row:

```markdown
| Entity pickers (level / course / star / segment) | `ui/components/picker.js` (`GroupedPicker` + pure `visibleGroups` — renders one `<optgroup>` per group, DROPS a group the filter emptied, and ALWAYS keeps the current value listed; that last one had already been fixed twice separately — purged strats in stratpicker.js, out-of-topology defs in the segment builder) + `ui/entities.js` (pure builders: `levelOptions`/`courseOptions`/`starOptionsFromVocab`/`starOptionsFromCatalog`/`segmentOptions`, ids are STRINGS and a star's is composite `"8:1"`). **Filtering lives at the CALL SITE** and is passed as `allow` — the segment builder passes its world-topology filter; the picker never learns a domain rule. Groups come from the server (`vocab.level_groups`/`course_groups`, `catalog.course_groups`, each segment's `origin`), never re-derived in JS. Call sites: segments.js clause params, header.js target modal (Course+Star collapsed to ONE control), routes.js step editor. Pinned by `tests/test_ui_picker_parity.py` — add a row there for a new call site, never an exception |
```

- [ ] **Step 4: Full suite**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_ui_picker_parity.py .claude/rules/ui.md
git commit -F- <<'MSG'
test(ui): pin every entity selection to the shared picker

The complaint that started this work was "we're redoing a lot of the same work
over and over again" — so the durable fix is not the component, it is the test
that stops a fifth copy appearing. It also guards the inverse: domain
vocabulary must not migrate INTO the picker, which is how a shared component
rots into a pile of booleans.

Rule row records where the pieces live and, more importantly, that filtering
belongs to the call site.
MSG
```

---

## Final gate

- [ ] `uv run pytest -q` green on the branch
- [ ] Whole-branch review (`superpowers:requesting-code-review`, Opus 5) — three components changed in parallel, so the cross-cutting read is the one that matters
- [ ] Human audit: open the practice-target modal, the segment builder, and the route step editor. The ~120-option star list is the risk the spec flags; only a human can say whether type-ahead over it feels right.

## Self-review notes

- **Spec coverage:** §1 primitive → Task 1; §2 builders → Task 2; §3 call sites + filters → Tasks 4, 5, 6; §4 server field → Task 3; §5 testing → tests in every task plus Task 7; §6 out-of-scope respected (no glitch work, no custom listbox, `stratpicker.js` untouched); §7 risks → the star-list risk is the human-audit item, composite ids are converted at one boundary per call site, `_CATALOG`'s import-time derivation is called out in Task 3's comment.
- **Type consistency:** the group shape `{key, label, options:[{id, name}]}` is produced by every Task 2 builder and consumed by Task 1's `visibleGroups`/`GroupedPicker`; `id` is a **string** everywhere, converted with `Number(...)`/`parseStarId(...)` at each call site's boundary (Tasks 4, 5, 6); `starId(course, star)` builds what `parseStarId(id)` reads.
- **Verified while planning, not assumed:** `tests/test_header_ui.py` does not exist — Task 5 creates it. `ItemPicker`'s two call sites are `routes.js:494` (inside `Routes`, where `vocab` is state) and `routes.js:216` (inside `StepRow`, which must forward it) — spelled out in Task 6 rather than left as "search for it".
