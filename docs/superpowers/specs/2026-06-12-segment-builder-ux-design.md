# Segment Builder UX — sentence-style dynamic labels

Date: 2026-06-12 · Status: approved by user (brainstorming session)

## Problem

The segment builder (`ui/components/segments.js`) renders trigger params as
unlabeled dropdowns in registry order. For directional triggers this is
ambiguous: "You enter level [Castle Inside] [Castle Grounds]" gives no clue
which dropdown is the destination and which is the origin (it's `to` then
`from`). Three adjacent defects ride along:

1. **Overflow** — `.segclause` is `display: flex` with no wrapping; long
   level names push the ✕ button past the pane border.
2. **Area-clear bug** — the area `ParamInput` ignores `schema.required`:
   clearing an optional area (e.g. `attempt_anchor`'s) sends
   `Number("") === 0`, silently scoping the clause to area 0 instead of
   "any area".
3. **Bare number inputs** — `star_grabbed`'s `course`/`star` params have no
   `ParamInput` branch, so they render as raw number fields even though
   `COURSE_NAMES`/`STAR_NAMES` exist in `memory/addresses.py`.

## Decision

Sentence-style rows driven by **templates in the trigger registry**
(`tracking/segments.py`) — user-selected over two alternatives:

- *UI-side label map*: rejected — breaks the "adding a trigger type = one
  registry row, zero UI changes" property the registry promises.
- *Per-param label metadata*: rejected — labels could only render in
  params-dict order; "area {area} of {level}" needs word-order freedom.

## Design

### Registry (`tracking/segments.py`)

`TriggerType` and `GuardType` gain a `template: str` field — plain string
with `{param}` placeholders rendered after the type label. The full set:

| Key | Label (type dropdown) | Template |
|---|---|---|
| `level_enter` | You enter level | `{to} coming from {from}` |
| `level_exit` | You exit level | `{from} going to {to}` |
| `area_enter` | You enter area | `{area} of {level}` |
| `warp_entered` | You enter a warp/pipe | `in {level}` |
| `key_grabbed` | You grab a Bowser key | `in {level}` |
| `star_grabbed` | You grab a star | `in {course}, star {star}` |
| `spawned` | You spawn into the game | `in {level}` |
| `attempt_anchor` | Practice reset / savestate load *(label shortened — drops " in level")* | `in {level}, area {area}` |
| `prev_level` (guard) | Previous level was | `{level}` |
| `star_count_min` (guard) | Star count at least | `{n}` |
| `star_count_max` (guard) | Star count at most | `{n}` |

`vocab()` additionally serializes:

- `template` on every trigger and guard entry;
- `"courses"`: `{str(course_id): name}` from `COURSE_NAMES`;
- `"stars"`: `{str(course_id): [name, …]}` in `star_id` order, built with
  `star_name()` so courses 1–15 include "100 Coins" at `star_id` 6.

### UI rendering (`ui/components/segments.js`)

`ClauseRow` splits the template on `{param}` tokens (`/(\{\w+\})/`):
text tokens render as `<span class="segword">`, param tokens render the
existing `ParamInput` for that name. Defensive fallback: any param absent
from the template is appended after the templated content (unreachable
while the registry test below holds).

`ParamInput` changes, by kind:

- **level** — unchanged behavior; empty-option label becomes
  "(any level)" when optional (was "(any)").
- **area** — honors `required`: "— pick area —" when required,
  "(any area)" when optional; empty selection maps to `null`
  (fixes the area-clear bug).
- **course** *(new branch)* — dropdown from `vocab.courses`;
  "(any course)" → `null`.
- **star** *(new branch, dependent)* — dropdown from
  `vocab.stars[course]`, where `course` is the sibling param value on the
  same clause. **Disabled** whenever the course param is `null`
  ("(any course)" implies any star). Selecting or clearing the course
  resets the star param to `null` so a stale star id can never outlive its
  course. Disabled state shows "(any star)".
- **int** — unchanged (number input).

### CSS (`ui/index.html`)

- `.segclause` gains `flex-wrap: wrap` (rows wrap instead of bleeding).
- `.segclause select { max-width: 100%; }`.
- New `.segword` class for connector words, muted color consistent with
  the existing palette.

### Compatibility

No DB or saved-definition changes — params and their wire format are
untouched; this is presentation plus input affordances. The vocab endpoint
and UI ship together, so no schema versioning is needed.

## Testing

- `tests/test_segments.py`: every entry in `TRIGGERS`/`GUARDS` has a
  non-empty `template` whose placeholder set exactly equals its params
  keys (a registry typo fails CI, not the browser).
- `tests/test_api.py`: `/api/segments/vocab` carries `template`,
  `courses`, and `stars`; `stars` agrees with `star_name()` including the
  100-Coins entry.
- Frontend smoke test (chrome-devtools, mandatory gate): rebuild the LBLJ
  segment (level-enter with from/to, attempt-anchor with level+area, warp
  trigger), verify sentence rendering, wrap containment at narrow width,
  area clear → `null` in the PUT payload, and star-selector
  enable/disable/reset behavior on a star-grab trigger.
- Human audit (live look) after the smoke test passes.

## Out of scope

Matcher semantics, new trigger types, the segment list rows, and CSS
outside the builder. If a future trigger needs a param the sentence can't
express, extend the template format then — not now.
