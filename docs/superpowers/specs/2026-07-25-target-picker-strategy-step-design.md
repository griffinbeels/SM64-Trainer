# The target picker becomes one three-step flow that ends in a strategy

Status: approved 2026-07-25. Supersedes the header's inline `TargetEditor`
card.

**Base branch (decided 2026-07-26): `mario-cap-rank-icons`, not `main`.** That
branch was already implemented and in flight, and it DELETES `Medal` in favour
of `Hat` (`ui/components/hat.js`) plus a cap-name registry
(`ui/components/caps.js`). This feature renders a rank icon in two new places
and prints a rank string in one, so building on `main` would have written
imports for a symbol about to disappear — a break git merges clean and the
page dies on. Everywhere below that says "medal", read `Hat`; everywhere it
prints a tier name, read `capName(tier)` / `divisionDigit(numeral)`. The
implementation plan's Global Constraints carry the exact API.

## Problem

Setting a practice target from the header takes one click too many and ends
without the thing that actually decides how you practise.

Today (`ui/components/header.js`): the *Practice target* card opens an inline
`.context-editor` card holding two fields — a `Star` **trigger button** which
opens the real grid modal, and a `Strategy` `<select>` — plus Cancel / Set
target. So reaching the grid is two clicks, and the strategy is a bare
dropdown with no indication of how good you are at any of the options.

Three separate gaps fall out of that:

1. **The extra click.** The card exists only to host two controls, one of
   which is itself a trigger for a modal.
2. **The strategy is unranked and unexplained.** A `<select>` of names. The
   whole rank stack already knows your tier on each strategy; none of it
   reaches the moment you choose one.
3. **The star grid is rankless.** `courseUnionGroups` builds options with no
   `rank`, even though `PracticeCell` — the very cell the grid renders —
   already accepts a `rank` prop and draws a `Medal` with it. The banner uses
   that; the picker does not.

## Decisions (user, 2026-07-25)

- **Layer-2 tile medal = your BEST strategy's rank**, not the entity rank and
  not the active strategy's. At pick time no strategy is chosen yet, so "how
  good am I at this star" is the right question.
- **Nothing is written until a strategy card is clicked.** "If the user
  manages to close the window at this step or go back, then I think they would
  expect none of their settings to be changed from what they previously had…
  Upon selecting the strategy, the modal closes, everything writes, and the
  interaction is done."
- **Strategy card = medal + tier·division + name + PB.** Not name-only (too
  little to choose on), not with the next-rank gap (that belongs on the
  practice card's rank banner, where there is room for it).
- **The stage-banner quick-select row is out of scope** — it keeps its
  one-click behaviour. Inserting a modal into the "I am standing in the level
  right now" path fights what that row is for.

## Prior art — most of this already exists

- `ui/components/entitymodal.js` already does course-grid → star/segment-grid
  at `depth=2`, with Back, an Escape that backs out before it closes, and a
  focus move on drill-in.
- `ui/components/practicecell.js` already renders art / rank medal / name /
  sub-line, and is already shared by the picker grid and the practice banner.
- `views.py::_strat_rank` + `grading_basis` + `_graded_progress` are already
  THE grading path shared by `rank_by_star`, `segment_targets[].rank`, the
  section banners and the route medals.
- `.needs-strat` in `index.html` is already the red blink for an unset
  strategy.
- `stratmodal.js` is already the strategy-creation modal, already opened from
  three places including this header.

What does not exist: rank **per strategy** for an arbitrary entity, and any
rank at all for an entity that is not the current target.

## 1. The flow

| Step | Shows | Leaving it |
|---|---|---|
| 1 | Course cells (existing layer 1) | Escape / backdrop closes, nothing written |
| 2 | That course's stars ∪ segments, each with a rank badge | Back / Escape → step 1 |
| 3 | Strategy cards for the picked entity | Back / Escape → step 2 |

The header's *Practice target* card opens the dialog at step 1 directly. The
inline `TargetEditor` is deleted; its two fields **are** steps 2 and 3.

Escape walks the stack 3 → 2 → 1 → closed, extending the rule
`entitymodal.js` already implements for step 2.

A cell pick at step 2 does **not** write. It advances to step 3. The only
write in the whole flow happens on a step-3 card click, and it closes the
dialog.

### 1a. The step-2 medal must cost zero height

`index.html` currently carries, with its reason:

> `.entity-grid .starrank { display: none; }` — No rank slot in the picker:
> nothing grades a cell here, so it rendered a column of "–" that cost a line
> per ROW — 4 rows of it was most of the 94px that made the grid scroll on a
> 900px-tall window (live audit 2026-07-25).

Simply un-hiding that slot re-introduces a bug this app has already audited
and fixed. Grading the cells does not remove the cost: a course where you
have practised two of seven stars still renders five "–" and the grid rows
still grow.

So the medal renders as an **out-of-flow corner badge over the cell art**,
and only when a rank exists. `PracticeCell` gains one boolean LOOK flag,
`rankBadge` — the same kind of call-site look flag `dimIdle` already is —
which swaps the in-flow `.starrank` row for an absolutely-positioned
`.starrank-badge` and renders nothing at all when `rank` is falsy.
`.starcell` is already `position: relative` (its hover ✎ depends on it), so
this needs no new positioning context. `.entity-grid .starrank { display:
none }` **stays**, so a future call site cannot silently restore the
scrolling row.

The banner is untouched: it passes no `rankBadge` and keeps its in-flow slot,
where the "–" is meaningful because every banner cell is graded.

## 2. Step 3 anatomy

A grid of cards, siblings of the step-2 cells:

```
┌──────────────────┐  ┌──────────────────┐
│      ( ★ )       │  │      ( ★ )       │
│    Platinum II   │  │     Gold IV      │
│    Sign Clip     │  │    Standard      │
│   PB  0:21.53    │  │   PB  0:23.10    │
│    ● current     │  │                  │
└──────────────────┘  └──────────────────┘
```

Two cards are always present:

- **No strategy** — commits the target with an explicit null strategy. Wears
  `.needs-strat` (the existing red blink) when the entity has no previous
  selection, which is the "blinking no-strat display" this spec was asked
  for. **Omitted** when `allow_blank` is false — a segment whose definition
  carries a `default_strat`, where the server falls back to the default
  anyway (`projection.py` caveat 17), so offering the option would be a lie.
- **+ New strategy…** — opens the existing `StratModal`. On save, that name
  becomes the pick and commits, exactly as the header's dropdown does today.

An entity with no strategies at all renders only those two, above a one-line
`.stable-empty compact` note — the same treatment the attempt-timeline panel
uses, rather than the full `emptystate.js` cast art, which is too heavy for a
modal step.

Modal title at step 3 is the picked entity's own name; the picker already
holds the option object, so no extra prop is needed to say it.

## 3. Server data — two lazy endpoints

Both are computed **on demand and never added to the session view.** The view
rebuilds on every WebSocket event, and per-strategy grading in the avg rank
modes is O(history) per strategy per entity. This work belongs off that path.

### `GET /api/target/ranks`

```
{ "star:8:2":   {"rank": "Platinum", "division": "II", "strat": "Sign Clip"},
  "segment:12": {"rank": "Gold", "division": "IV", "strat": "Standard"} }
```

Every entity with a gradeable time; entities with none are simply absent and
their tile shows `–` ("no rank if never attempted yet"). Fetched when the
modal opens.

**Best strategy** = the highest `scoring.score_for` across the entity's
strategies, each graded on **its own** ladder. Ties break on the strategy
name (`min`), the same deterministic convention `_fastest_strategy` uses.

`strat` rides the payload so the tile's tooltip can name it. This matters:
the SAME `PracticeCell` on the practice banner shows the **active**
strategy's rank, so the medal can legitimately change after you pick. Naming
the strategy is what stops that reading as a rendering fault.

### `GET /api/target/strategies?entity=star:8:2`

```
{ "entity": "star:8:2", "current": "Sign Clip", "allow_blank": true,
  "strategies": [ {"name": "Sign Clip", "rank": "Platinum", "division": "II",
                   "score": 74.2, "pb_display": "0:21.53"}, … ] }
```

`allow_blank` is false exactly when the entity is a segment whose definition
carries a `default_strat` — the same rule `stratpicker.js` already applies
from `sec.default_strat`.

### Grading path and clock

Both builders route through `grading_basis` → `_graded_progress`, so no
medal here can disagree with `rank_by_star`, a section banner, or a route
medal.

Clock comes from `ranks.clock_for(ek)` — igt for stars, rta for segments,
per-entity overridable. That is the authoritative answer;
`rank_by_star` and `segment_targets` each hardcode one literal instead, which
happens to agree today. Do not copy the literals.

### The strategy list itself

Reuses `_strategies_for` (stars) / `_seg_strategies` (segments): registered ∪
observed-on-attempts ∪ rank-standards, tombstoned names filtered.

**This fixes a live bug.** Today's `TargetEditor` reads `v.strategies` — the
RAW registered ui_state map — so a strategy you have attempts on but never
registered is missing from the header's dropdown while the practice card
offers it.

## 4. The explicit-null fix (required by the commit semantics)

`tracking/service.py::set_target` documents its own gap:

> KNOWN GAP (found 2026-07-23, not yet fixed): a None strat_tag is omitted
> from the payload rather than journaled as an explicit clear, so picking
> "(no strategy)" in the header target editor leaves an already-set strat in
> place.

So the **No strategy** card cannot commit correctly today. Root-cause fix,
scoped to two files:

- `server/api.py` — `TargetBody` distinguishes *absent* from *explicitly
  null* via `model_fields_set`, the same pattern `routes.js` category
  clearing already relies on (`model_dump(exclude_unset=True)`).
- `tracking/service.py::set_target` — on an explicit clear, publish
  `target_set` as it does today, then call `set_strat(course, star, None)`,
  which **does** journal the null.

This mirrors what `set_target_segment` already does (it delegates to
`set_strat_segment`). The `target_set` payload shape is **unchanged**, so the
consumer audit that docstring warns about does not apply — no `target_set`
consumer sees anything new.

## 5. Component structure

| File | Change |
|---|---|
| `ui/components/entitymodal.js` | ONE new optional prop `nextStep` (a component). When given, a cell pick sets internal pending state and renders `nextStep` inside the SAME `Modal` (title = the picked cell's name) instead of calling `onChange` and closing. Absent → today's behaviour byte-for-byte, so the segment builder and route editor are untouched. The clear cell still emits `null` and closes. |
| `ui/components/strategystep.js` | **new** — fetches `/api/target/strategies`, renders the cards, owns the commit write and the `StratModal`. |
| `ui/components/header.js` | The target card opens the picker dialog directly; `TargetEditor` deleted. |
| `ui/components/practicecell.js` | One boolean look flag `rankBadge` (§1a): out-of-flow corner medal, nothing when unranked. Default false — the banner is byte-for-byte unchanged. |
| `ui/entities.js` | `courseUnionGroups` takes an optional rank map and stamps `rank` on options. |
| `ui/index.html` | `.starrank-badge` (§1a) and `.strat-grid` / `.strat-card` CSS in the one design-system block. |
| `tracking/views.py` | `build_entity_ranks` + `build_entity_strategies`. |
| `server/api.py` | the two GETs + the `model_fields_set` change. |
| `tracking/service.py` | the explicit-null clear. |

`strategystep.js` parses the picked id with `entities.js`'s `parseStarId` /
`parseSegmentId` and posts the kind-dispatched body itself, so the generic
picker never learns a domain rule — the same division of labour `allow`
already has.

## 6. Testing

- `views.py` builders: best-strategy selection (including the name tie-break),
  the absent-entity case, `allow_blank` for a defaulted segment, and that the
  ranks agree with `_section_banner` for the same entity+strategy.
- The explicit-null clear: `POST /api/target` with `strat_tag: null` present
  clears; with the key absent it leaves the existing strat alone. Both
  directions, or the fix is unpinned.
- `entitymodal.js`: a call site passing no `nextStep` still closes on pick
  (the three existing call sites depend on it).
- §1a: `.entity-grid .starrank { display: none }` is still present in
  `index.html`, and `practicecell.js` renders no in-flow rank element under
  `rankBadge`. Both directions — the whole point is that a later change
  cannot quietly put the scrolling row back.
- `test_header_ui.py::test_target_modal_still_posts_course_and_star_as_numbers`
  **moves** to the strategy step — the string→number boundary at the API edge
  is still real, just relocated. Do not delete it.
- Verification is a headless render against captured API fixtures (the
  fixture-server recipe in `.claude/rules/ui.md`), not unit tests plus
  `node --check` — that combination has shipped an invisible feature here
  before.

## 7. Risks

- **The medal means two things.** Picker = best strategy, banner = active
  strategy, in the same `PracticeCell`. Mitigated by the tooltip naming the
  strategy; if it still reads wrong live, the fallback is to show the active
  strategy's rank on the tile and move "best" into the tooltip.
- **`GET /api/target/ranks` cost.** Bounded by entities-with-attempts ×
  strategies-with-ladders, once per modal open. If it ever bites, the answer
  is a cache keyed on the rank mode and the journal head, not moving it into
  the session view.
- **Deleting `TargetEditor` removes the Cancel/Set target affordance.** That
  is intended — the flow is now cancel-by-default and commit-on-last-click —
  but it is the one habit an existing user has to unlearn.
