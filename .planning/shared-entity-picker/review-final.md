# Whole-branch review — `feature/shared-entity-picker`

Reviewer: final whole-branch gate (Opus 5). 14 commits, `main..aa9eb09`.
`uv run pytest -q` on the merged branch state: **1539 passed, 3 warnings, 69.37s**
(matches the ledger). ESM syntax check (`node --input-type=module --check < file`)
passes on all five touched JS modules.

**Verdict: the refactor is sound and behaviour-preserving. The deliverable that
was supposed to prevent recurrence — `tests/test_ui_picker_parity.py` — does not
work.** All three of its tests are toothless or tautological, proven below by
execution, not by reading. That is the headline; everything else is small.

---

## Critical

None.

---

## Important

### I1. The parity test cannot detect a fourth hand-rolled picker
**Severity: Important · Confidence: High**
`tests/test_ui_picker_parity.py:14-24`

`ENTITY_PICKER_CALL_SITES` is a hardcoded three-element list, and the test only
asserts the string `"GroupedPicker"` appears in each of those three files. The
stated purpose — "without it a fifth hand-rolled course/star select appears the
next time someone needs one in a hurry" — is exactly the case it cannot catch:

- A **new** component (`ui/components/newthing.js`) containing
  `catalog.courses.map((c) => html\`<option value=${c.id}>${c.name}</option>\`)`
  is not in the list, so nothing is checked. Test passes.
- Inside an **existing** listed file, a hand-rolled `<select>` of courses added
  next to the shared one also passes, because `"GroupedPicker"` is still present
  somewhere in the file.

This is not hypothetical drift-proofing: `segments.js` already contains one
remaining hand-rolled filtered `<select>` (`ParamInput`'s `subarea` branch,
`segments.js:107`) with its own keep-current-value logic, and the parity test is
blind to it. That branch is a deliberate, documented exception ("A 3-item list
has nothing to group") — the point is that the test would be equally blind if it
weren't.

**Fix:** invert the test. Scan **every** `.js` under `ui/` and fail on the
*shape* of a hand-rolled entity select, e.g. a regex for `<option` in a file
that also references `catalog.courses`, `vocab.courses`, `vocab.stars`,
`vocab.levels`, or `.origin.region`, with an explicit allowlist of the files
permitted to do so (today: `segments.js` for `castle_areas` only). The allowlist
is what a future author has to consciously edit — a hardcoded call-site list is
something they never even see.

### I2. The "no domain vocabulary in the picker" guard inspects 5 % of the file
**Severity: Important · Confidence: High**
`tests/test_ui_picker_parity.py:41`

```python
assert domain_word not in picker.lower().split("//")[0], domain_word
```

`split("//")[0]` truncates at the **first** `//` in the file. The first `//` in
`picker.js` is the header comment on line 7, so the guarded region is lines 1-6:

```
=== CHECKED REGION (127 chars of 2472) ===
import { h } from "preact";
import htm from "htm";
import { visiblegroups } from "../entities.js";

const html = htm.bind(h);
```

The entire component body (lines 26-50) is never examined. Demonstrated: patching
`const COURSE_NAMES = {1: "BoB"}; // star/level/segment topology route` into the
body of `GroupedPicker` leaves the guard flagging **nothing** — the test still
passes.

It is also fragile in the direction the dispatch asked about: any `//` comment
added above the current header shrinks the region further, and deleting the
header block entirely expands it to the whole file (a silent, unrelated change in
what the test means).

**Fix:** strip comments properly and check what remains — e.g.
`re.sub(r"/\*.*?\*/", "", src, flags=re.S)` then drop `^\s*//.*$` lines with
`re.MULTILINE`, and assert against the residue. Or drop the guard: it is trying
to police a design principle that the `entities.js` / `picker.js` split already
enforces structurally, and a broken guard is worse than none.

### I3. The "no call site rebuilds the grouping" test can only fail on copy-pasted Python
**Severity: Important · Confidence: High**
`tests/test_ui_picker_parity.py:32`

The three guarded strings — `world_regions`, `CASTLE_REGION`, `region_for_node`
— are Python identifiers that live only in `src/sm64_events/memory/addresses.py`
(verified: zero occurrences anywhere under `ui/`, and none in any `.js` in the
repo). A JS author re-deriving region membership would write
`const REGIONS = {...}` or `groupByRegion()`, not a Python snake_case symbol. The
assertion is therefore vacuously true and will stay vacuously true.

**Fix:** guard the *inputs* a JS re-derivation would need rather than the names
it might use — e.g. assert no listed call site contains a hardcoded level-id or
course-id array literal of length > 3, or fold this into the shape-based scan of
I1 and delete it.

### I4. The `id`/`name` fold-in landed as a dead parameter; the advisory it was meant to fix still fires
**Severity: Important · Confidence: High**
`src/sm64_events/ui/components/picker.js:36-39`, all three call sites

The ledger records this as *"Controller-approved fold-in for T7 (not scope
creep): `GroupedPicker`'s `<select>` sets no `id`/`name`, so Chrome logs a
form-field advisory at every call site now (found by T5's render check). The fix
belongs in the shared component, not in any one caller."*

What shipped is an optional pass-through: `GroupedPicker({..., id, name })` →
`<select id=${id} name=${name}>`. **No caller passes either prop** (grepped: the
only occurrences of `GroupedPicker` in `header.js:265`, `routes.js:160,164`,
`segments.js:97,111,123` pass `groups`/`value`/`onChange`/`allow`/`placeholder`/
`disabled` only). Preact omits the attribute for `undefined`, so all three call
sites still render a `<select>` with neither `id` nor `name` and Chrome still
logs the advisory. The recorded fix did not fix anything, and the branch gained a
speculative API surface (global CLAUDE.md principle 7: "No speculative
architecture. YAGNI").

**Fix:** either have the component derive a stable fallback itself (the ledger's
own reasoning — "the fix belongs in the shared component"), e.g.
`name=${name ?? "grouped-picker"}` / a `useId`-style counter, or drop the two
props and record the advisory as accepted. Do not leave a parameter that exists
only to make a bug report look addressed.

### I5. `.claude/rules/ui.md` advertises the broken guard as protection
**Severity: Important · Confidence: High**
`.claude/rules/ui.md`, "Entity pickers" row, final clause

> *"…a second guard in the same file asserts the picker's code above its header
> comment never gains a domain word (course/star/level/segment/topology/route),
> **so filtering logic can't migrate in**"*

The mechanism description is honest ("above its header comment"), but the
conclusion is false — per I2 the guarded region is the import block, and
filtering logic absolutely can migrate in. This file is the project's cross-
session memory; a future session reads that clause and trusts a guard that does
not exist. Per the dispatch: a wrong row is worse than a missing one.

**Fix:** land I2 and then restate the clause to match what the fixed guard
checks; or, if the guard is deleted, delete the clause with it.

---

## Minor

### M1. `visibleGroups` does not centralise the bug class the spec says it does
**Severity: Minor · Confidence: High**
`src/sm64_events/ui/entities.js:115-120`; spec §1.3

`visibleGroups` keeps the current value listed when **`allow`** rejects it. It
cannot keep a value the **caller dropped from `groups` before the picker saw
them** — it filters, it never injects. Two of the shipped call sites do exactly
that caller-side narrowing:

- `segments.js:92-96` — the `schema.enum` filter is applied to `groups`, not
  passed as `allow`. A stored level outside the enum blanks.
- `segments.js:121-122` — `.filter((group) => group.key === \`course-${clause.course}\`)`
  narrows to one course before the picker. A star index the narrowed course
  doesn't have blanks.

Neither is a **regression** — I traced old-vs-new set-and-order equality for all
three branches and they are identical (old `groupedDropdown` applied `inEnum` at
the same point; the old star branch mapped `vocab.stars[clause.course]` the same
way). And I confirmed both narrowings are currently unreachable in practice:
`course_groups()` covers all 25 of `COURSE_NAMES` (leftovers group), and
`vocab.courses` / `vocab.stars` / `vocab.levels` are each fully covered by their
group lists (verified by running the vocab builder). So this is an **accuracy**
finding against the spec's claim that centralising this here is "the main reason
this component exists at all", and against `.claude/rules/ui.md`'s "**ALWAYS**
keeps the current value listed" — accurate about the function, misleading about
the system.

**Fix (doc-only):** scope the claim — "keeps the current value listed when
`allow` rejects it; a caller that narrows `groups` itself is still responsible
for its own value." One sentence in `entities.js` above the function and a word
change in the rules row.

### M2. `routes.js` sends `segment_id: 0` where it used to send `null`
**Severity: Minor · Confidence: High**
`src/sm64_events/ui/components/routes.js:145,151`

`useState(segs[0] ? String(segs[0].id) : null)` seeds `segId` at mount only (the
never-re-seeds bug is pre-existing and correctly deferred to the render-glitch
branch). What is **new** is the exit conversion: `Number(segId)` with `segId ===
null` yields **`0`**, not `null` (verified in node). Concrete path: `ItemPicker`
mounts while `getJSON("/api/segments")` is still in flight → `segId = null` →
segments arrive → the Add button un-disables and the `<select>` visually shows
the first option → click "Add option" → POST body carries
`{type: "segment", segment_id: 0}`.

Consequence is bounded: `_validate_item` (`tracking/routes.py:33`) accepts `0` as
an integer, but `TrackerService._check_segment_refs`
(`tracking/service.py:629,643`) rejects the nonexistent id → 409. So no silent
route corruption (rule 12 is safe). The user-visible cost is a confusing error
about segment 0 instead of "nothing selected". Before this branch the same path
sent `segment_id: null` and failed at the earlier, clearer structural check.

**Fix:** `segment_id: segId == null ? null : Number(segId)`, one line.

### M3. `routes.js` star pick can serialise `course: null` / `star: null`
**Severity: Minor · Confidence: Medium**
`src/sm64_events/ui/components/routes.js:141-149`, with `routes.js:289`

`const catalog = (t.view && t.view.catalog) || { courses: [] };` — the fallback
has **no `course_groups`**, so `starOptionsFromCatalog(fallback)` returns `[]`,
`firstStar` is `null`, and `useState` freezes `star = null` for that mount.
Clicking "Add step" then runs `parseStarId(null)` → `{course: NaN, star: NaN}` →
`JSON.stringify` turns NaN into `null` → POST `{"type":"star","course":null,
"star":null}` (all verified in node). `_validate_item` 409s with "star candidate
needs integer course and star", so again no corruption — but the old code seeded
`useState(catalog.courses[0] ? catalog.courses[0].id : 0)` and would have added a
(wrong but valid) course-0 star.

Reachability is low: `ItemPicker` only mounts after the user selects or creates a
route, by which time `t.view` is normally loaded. Confidence Medium on
reachability, High on the mechanics.

**Fix:** either give the fallback a `course_groups: []` and derive `star` from
`starGroups` on every render instead of at mount, or guard `pick()` with
`if (star == null) return;`.

### M4. Before `/api/segments/vocab` resolves, every segment group is labelled "Other"
**Severity: Minor · Confidence: High**
`src/sm64_events/ui/components/routes.js:144`, `entities.js:81-101`

`vocab` starts `null` (`routes.js:283`), so the first paint calls
`segmentOptions(segs, undefined)`. `order` and `labels` are both empty, so every
bucket resolves `labels.get(region) || "Other"`. Verified in node — two segments
in different regions produce **two separate optgroups both labelled "Other"**,
not one flat list and not one merged group. It self-corrects when the fetch
lands.

The ledger's watch item ("must tolerate a null taxonomy") is satisfied in the
sense that nothing throws, but the tolerated output is repeated identical
headings. Both `ItemPicker` call sites (`routes.js:221,500`) do receive `vocab`,
as required.

**Fix:** when `taxonomy` is empty, return a single ungrouped bucket (or render
the flat list) rather than N groups sharing one label.

### M5. Test pinned to a variable name
**Severity: Minor · Confidence: High**
`tests/test_ui_grouplist.py` (new `test_route_segment_picker_groups_like_the_library`)

`assert "segmentOptions(segs" in ROUTES` breaks if `segs` is renamed, if the
call is wrapped in `useMemo`, or if prettier reflows the arguments across lines
— none of which change behaviour. Pin the behaviour (`"segmentOptions("` plus
the `vocab` origins argument) or drop it; the sibling
`test_route_item_picker_uses_the_shared_picker` already covers the import.

### M6. `test_header_ui.py` doesn't guard the thing this branch put at risk
**Severity: Minor · Confidence: High**
`tests/test_header_ui.py:16-17`

`test_target_modal_still_posts_course_and_star_separately` asserts
`"course_id:" in HEADER_JS and "star_id:" in HEADER_JS`. It would pass unchanged
if `apply()` sent `course_id: course` with `course` holding the string `"8"` —
which is precisely the string/number boundary this refactor introduced. (The
shipped code is correct: `header.js:249` wraps both in `Number()`, and
`parseStarId` at `header.js:272` already returns numbers, so nothing string-y
reaches the API. I traced all three call sites' edges and they all convert —
see CLEAN below.) The test just doesn't check it.

**Fix:** assert `"course_id: Number(course)"` / `"star_id: Number(star)"`, or
delete the test as covered by the node-level `parseStarId` round-trip test.

### M7. Plan doc's bolded claim contradicts its own parenthetical
**Severity: Minor · Confidence: High**
`docs/superpowers/plans/2026-07-25-shared-entity-picker.md:18` (commit `b20e095`)

> **"Node cannot execute a UI module that imports `preact`/`htm`"** … *"(A
> `node:module` resolver hook is the alternative — see the superseded commit
> `f38bdbd` — but the simpler layout won.)"*

The bold sentence is false in the absolute; the parenthetical says so. A future
session grepping for the bold line gets the wrong fact. I verified the escape
hatch is real: `ui/vendor/{preact,hooks,htm}.module.js` all exist, and `f38bdbd`
does register a `node:module` resolver from a `data:` URL mapping the three bare
specifiers to those files. The controller's flip-flop (`8d74f54` → `cd36e63` →
`b20e095`) landed on a defensible engineering choice recorded with its evidence
— only the phrasing needs fixing.

**Fix:** "Node needs a resolver hook to execute a UI module that imports
`preact`/`htm` (see `f38bdbd`); we keep node-tested logic in import-free modules
instead."

### M8. The keep-current-value rationale is now written out in four places
**Severity: Minor · Confidence: High**

`picker.js:13-21`, `entities.js:103-111`, `tests/test_ui_picker.py:1-11`
docstring, and the `.claude/rules/ui.md` row all narrate the same
"fixed twice before, in stratpicker.js and the segment builder" history at
length. The DoD says "one fact, one authoritative place — link, don't
duplicate". Pick `entities.js` (where the function lives) as the home and have
the other three point at it in one line each.

### M9. Group builders re-run on every poll tick
**Severity: Minor · Confidence: Medium**

`header.js:266` (`starOptionsFromCatalog(v.catalog)`), `routes.js:141,144`, and
`segments.js:121` all rebuild their group arrays on every render. The full star
set is 25 groups × up to 7 options ≈ 175 fresh objects per render, in components
that re-render on the ~1 s view poll (the reason the file's `seconds` branch uses
`oninput`). Old code built one course's stars. Absolutely small in isolation, but
this repo has an open over-hours RAM/lag investigation, so it is worth a
`useMemo` on `catalog`/`vocab` identity rather than leaving it as new per-tick
churn. Not a correctness issue.

---

## Checked and CLEAN

**String/number boundary (dispatch item 1)** — traced all three edges; no string
reaches an API or a stored definition.
- `header.js:272-273` — `parseStarId(id)` returns `{course: Number, star:
  Number}` (`entities.js:16-19`), passed to `pickStar`; `apply()` at
  `header.js:249` additionally wraps both in `Number()`. Payload is numeric.
  `StratModal`'s `entity` at `header.js:290` also uses `Number()`.
- `routes.js:148-151` — star: `parseStarId(star)` → numbers. Segment:
  `Number(segId)` → number (see M2 for the `null` edge).
- `segments.js:100,114,127` — every `onChange` converts: `Number(id)` for level
  and course, `parseStarId(id).star` (a Number) for star. `value` going *in* is
  stringified symmetrically (`String(value)`, `starId(clause.course, value)`).
  The clause params stored in a segment definition stay integers.

**Star branch edits exactly one param (dispatch item 3)** — `segments.js:127`
calls `onChange(parseStarId(id).star)` → `ClauseRow.setParam("star", idx)`
(`segments.js:160`), which builds `{...clause, star: idx}` and never touches
`course`. The consistency sweep at `segments.js:170-179` cannot clear `course`
either: it only nulls a sibling when `allowedIds` returns non-null, which
requires a `flow` annotation — and I confirmed no `course` or `star` param in the
trigger registry carries one (ran the vocab builder over every trigger). Clearing
the course is also sane: `segments.js:163` explicitly nulls `star` on a course
change, and the star control's `value` becomes `null` with `disabled=true`.

**Keep-the-current-value invariant through `GroupedPicker`** — `picker.js:38` →
`visibleGroups(groups, allow, value)`; `entities.js:117`
`keep = (o) => !allow || allow(o.id) || o.id === value`, and the emptied-group
drop follows. Pinned by four node-driven tests in `tests/test_ui_picker.py`
including the non-mutation one. The string-vs-string comparison is right: callers
pass `value` already stringified. (Scope caveat is M1.)

**`routes.js` before vocab loads (dispatch item 4)** — `(vocab || {}).origins`
does not throw; both `ItemPicker` mount points receive `vocab`
(`routes.js:221` inside `StepRow`, `routes.js:500` for "Add a step"), and
`StepRow` receives it from `routes.js:489`. Output quality caveat is M4.

**`header.js` behaviour parity (dispatch item 5)** — every prior behaviour
survives:
- Strategy list re-resolves per star: `onChange` routes through the unchanged
  `pickStar`, which calls `setStrat(lastStratFor(c, s))`; `options =
  stratsFor(course, star)` recomputes on render (`header.js:236,255`).
- `stratNonce` remount is untouched (`header.js:275`, bumped only at
  `header.js:292` on modal close) — the picker change does not interact with it.
- `apply()` payload identical: `{course_id, star_id, strat_tag}`, both numeric.
- Initial value on an existing target: `starId(Number(tgt.course_id ?? 1),
  Number(tgt.star_id ?? 0))`. I verified **every** course is reachable —
  `course_groups()` groups 24 courses by region and sweeps the remainder
  (course 0, the castle secret stars) into an "Other" leftovers group, giving
  exact coverage of all 25 `COURSE_NAMES` entries, which is also what
  `_CATALOG["courses"]` is built from. Pinned server-side by the new
  `test_catalog_course_groups_cover_every_catalog_course`. No star became
  unreachable when the Course select was removed. Ordering changed (region order
  now, so course 0 moved from first to last) — intended per spec decision 1.

**Dead code and leftovers (dispatch item 7)** — `groupedDropdown` exists nowhere
in `src/`; the only live reference is the negative assertion in
`test_segments_editor_ui.py:114`. `numOrNull` and `dropdown` are still used by
the `subarea` and free-number branches, so neither is orphaned. `routes.js` has
exactly one export (`Routes`) — the temporary `ItemPicker` export was reverted.
`git status --porcelain` is empty: no harness pages, no stray files.

**Behavioural parity of the three `segments.js` branches** — I compared old and
new option sets and orders directly:
- *level*: old `inEnum` filter on group levels + `names[id] !== undefined` +
  `permitted` ≡ new `levelOptions` (which does the `names` check) + the
  `schema.enum` map + `allow=permittedId`. Same set, same order, empty groups
  dropped in both.
- *course*: old passed `permitted`, new passes no `allow`. Verified harmless —
  `allowedIds` returns `null` for any schema without `flow`, and no `course`
  param has one, so `permitted` was unconditionally true.
- *star*: old mapped `vocab.stars[clause.course]`; new narrows
  `starOptionsFromVocab` to `course-${clause.course}`. Equivalent given
  `vocab.courses` ⊇ `vocab.stars` and `course_groups` ⊇ both — all three
  verified by running the vocab builder (zero courses in `stars` unreachable via
  `course_groups ∩ courses`).
- Removed pre-groups fallbacks (`vocab.level_groups`/`course_groups` absent):
  the server ships both keys unconditionally (`vocab()` keys verified), and
  server and JS ship together, so the dead branch removal is correct.

**Server change** — `views.py:167-172` adds `course_groups()` to `_CATALOG`.
`course_groups` (`tracking/segments.py:686`) is pure: it reads `level_groups()`,
`COURSE_BY_LEVEL`, `COURSE_NAMES` and touches no database, so the import-time
derivation is safe (the ledger's watch item holds). The import edit at
`views.py:46-48` is a clean addition. Both new tests in `test_views.py` are real
behaviour tests — the catalog↔vocab identity check is the one that would actually
catch a future divergence, and it is the best test on the branch.

**Test suite** — `tests/test_ui_entities.py` (5 tests) and
`tests/test_ui_picker.py` (4 tests) are genuine node-executed behaviour tests
with meaningful assertions, including the subtle `String(null) === "null"`
coercion that makes a null-region segment land in the "Anywhere" group. Neither
is tautological. `tests/test_views.py` additions likewise. The weak ones are I1-I3,
M5, M6.

**Project rules** — Rule 10 (browser↔GUI parity): the change is entirely
`ui/` + server, `desktop/` untouched, so both surfaces get it. Rule 11
(star↔segment parity): one component now serves both kinds at every call site,
which strengthens the rule rather than testing it. Rule 12 (route step order):
untouched — `ItemPicker` still appends in the same place, and both new bad-input
paths (M2, M3) are rejected server-side rather than persisted. Rule 6
(read-only memory), rules 1-9: not in scope, not touched.

**Rule-file row** — apart from I5 and the "ALWAYS" wording in M1, every claim in
the new `.claude/rules/ui.md` "Entity pickers" row matches the shipped code: the
five builder names, the string/composite-id contract, `visibleGroups`'s location
and its stated reason, "filtering lives at the CALL SITE and is passed as
`allow`", "groups come from the server, never re-derived in JS", and the three
listed call sites. The pre-existing "Segments builder UI" row's topology
description is still accurate after the refactor.

**Controller commits** — `5f7696d` (ledger), `8d74f54`/`cd36e63`/`b20e095`
(doc corrections), `74820c2`/`aa9eb09` (ledger updates) all reviewed. The
flip-flop on node-executability is recorded honestly with the superseded commit
hash and I confirmed the escape hatch it points at is real and recoverable. Only
M7's phrasing is wrong. Test counts stated in the ledger (1515 → 1536 → 1539)
match: I measured 1539.
