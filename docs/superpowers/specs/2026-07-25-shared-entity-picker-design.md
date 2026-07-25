# One picker for every "choose a course / star / level / segment"

**Date:** 2026-07-25
**Status:** approved design, not yet implemented
**Follows:** `2026-07-24-segment-origin-categories-design.md` — this is the
"spec B" that spec deferred, now narrowed by what shipped in between.

## Problem

Three separate implementations of "pick a course, then pick a star" exist
today, sharing no code:

| Where | What it renders |
|---|---|
| `ui/components/header.js` (the practice-target modal) | Course `<select>` + Star `<select>` + Strategy |
| `ui/components/routes.js` (`ItemPicker`) | Mode + Course + Star, or a flat Segment list |
| `ui/components/segments.js` (`ParamInput`) | Course and Star clause params |

That duplication has a visible cost, not a theoretical one: the region grouping
that landed on 2026-07-24 reached the segment builder's level and course
dropdowns and **nothing else**, so the practice-target modal — the picker a
user touches most — still lists 15 courses and 7 stars as two flat lists (live
audit 2026-07-25). Any future improvement would have to be made three times,
and the odds all three stay identical are poor.

The segment picker in Routes has the same shape and the same gap: it lists 65
definitions flat, while the library right next to it groups them by castle
region.

## Prior art

- **In-repo, and the model for this spec.** `ui/group.js` + `ui/components/grouplist.js`
  did exactly this for the two libraries a day earlier: one dumb renderer, one
  grouping policy per consumer, and the shared piece owns only the mechanics
  everyone gets wrong (open-state persistence, indent, empty groups). This spec
  applies the same split to selection controls. `ui/components/stratpicker.js`
  is the other precedent — one component serving both stars and segments via a
  kind-dispatched body.
- **Dumb component + caller-supplied predicate** is the conventional resolution
  when several call sites need the same control under different constraints:
  the shared piece renders, the caller decides what is selectable. The
  alternative — pushing every consumer's rules into the component behind flags
  — is how shared components rot into a pile of booleans.
- **Deliberate deviation from the original idea.** The request that seeded this
  work said "we should probably have modals instead of dropdowns". We are
  keeping native `<select>` + `<optgroup>`. The target picker is *already* a
  modal whose contents are selects, so the modal question is orthogonal; native
  selects bring keyboard navigation, type-ahead, and screen-reader semantics
  for free, and a hand-rolled listbox would need all of that rebuilt before it
  broke even. Revisit only if type-ahead over ~120 grouped options proves
  insufficient in use.

## Decisions (user, 2026-07-25)

1. **The star picker collapses to ONE control** — an optgroup per course
   instead of Course + Star as two dependent selects.
2. **Filtering lives at the call sites.** "Different call sites might do
   different filtering under different conditions, but the picker is the same
   base."
3. **Everything of this kind comes from one place** — the duplication is the
   thing being fixed, not the styling.

## 1. The primitive

`ui/components/picker.js`:

```js
GroupedPicker({ groups, value, onChange, allow, placeholder, disabled })
// groups: [{ key, label, options: [{ id, name }] }]
// allow:  optional (id) => boolean — the CALLER's domain filter
// value:  the current id, or null
```

It renders one `<select>` with an `<optgroup>` per group and knows nothing
about levels, courses, stars, segments, world topology, or routes. It owns
exactly three behaviours, each of which has already been got wrong somewhere in
this codebase:

1. **Render the groups** (and their order — the caller supplies both).
2. **Drop a group whose options are all filtered out**, so a narrowed picker
   never shows an empty heading.
3. **Keep the current value listed even when `allow` rejects it.** A stored or
   legacy value fed to a filtered dropdown otherwise renders BLANK and reads as
   unset — the bug already fixed twice, independently, in `stratpicker.js` and
   in the segment builder's topology filtering. Centralising it here is the
   main reason this component exists at all.

Ids are **strings**, so a composite id (`"8:2"` = course 8, star 2) is as valid
as a level id. The caller encodes and decodes; the picker only passes them
through.

## 2. Group builders

Pure functions in the same module, one per kind, **no filtering in any of
them**:

| Builder | Groups | Options |
|---|---|---|
| `levelOptions(vocab)` | `vocab.level_groups` (castle regions) | level id → `LEVEL_NAMES` |
| `courseOptions(source)` | `course_groups` | course id → name |
| `starOptions(source)` | one per COURSE, courses ordered by region | `"<course>:<star>"` → star name |
| `segmentOptions(defs)` | by each def's `origin.region` | segment id → name |

`starOptions` is the collapsed control from decision 1: courses become the
optgroup headings, so region ordering survives one level up while the options
themselves are the stars. `segmentOptions` reads the `origin` stamp already on
`GET /api/segments`, so the Routes segment picker groups exactly like the
library beside it.

## 3. Call sites, and their filters

The filter is the caller's, always:

| Call site | Kind | Its filter |
|---|---|---|
| `segments.js` `ParamInput` | level, course, star | world topology (`allowedIds`) + `schema.enum` |
| `header.js` target modal | star (one control) | none — every practiceable star |
| `routes.js` `ItemPicker` | course/star, segment | none today; this is where route-scoped filtering would land |
| `routes.js` candidate editing | segment | none |

`segments.js`'s existing `groupedDropdown` helper is deleted — it was the
first draft of this component, written for one file.

## 4. Server: one field, not four

`tracking/views.py`'s `_CATALOG` gains `course_groups`, reusing
`tracking/segments.course_groups()`. Catalog-driven call sites (header, routes)
then group identically to vocab-driven ones (the segment builder) with no
second taxonomy and no new endpoint. Star names are already on the catalog.

## 5. Testing

- **Node-driven unit tests** for the group builders (`tests/test_ui_picker.py`,
  following `tests/test_ui_group.py`): composite ids round-trip, courses order
  by region, a def with a null origin lands in the trailing group.
- **The `allow` contract**, tested at the component level: a rejected option
  disappears, its group disappears when it empties, and **the current value
  survives rejection** — the invariant this component exists for.
- **A parity test** (`tests/test_ui_picker_parity.py`): every course/star/level/
  segment selection in `ui/` renders through `GroupedPicker`. This is the test
  that actually addresses the problem statement — without it, a fifth copy
  appears the next time someone needs a picker in a hurry.
- **Render checks** on the practice-target modal and the segment builder, per
  the project's UI norms (unit tests plus `node --check` once shipped an
  invisible feature here).

## 6. Out of scope

- The **render-glitch class** (`.planning/segment-origin-categories/flicker-diagnosis.md`)
  — its own branch, already diagnosed and ranked.
- A **custom listbox with search** — see Prior art. Native selects first.
- The **strategy picker** (`stratpicker.js`): it selects strings a user
  invents, not entities from a taxonomy, and it already owns write behaviour
  (dropped-write alerts, phantom-pick snap-back) that has nothing to do with
  grouping.

## 7. Risks

- **A ~120-option star select** is long. Native type-ahead searches option text
  within the list, and the modal keeps the current target visible, so this is
  judged acceptable — but it is the thing to watch in the human audit, and the
  reason Prior art leaves the custom-listbox door open.
- **Composite ids are a decode step at every call site.** Kept deliberate and
  explicit (`"8:2"`) rather than hidden behind an object, so the `<option
  value>` round-trip stays inspectable in devtools.
- **`_CATALOG` is a module-level constant.** Adding a derived field to it means
  the derivation runs at import; `course_groups()` is pure and cheap, but it
  must not grow a database dependency.
