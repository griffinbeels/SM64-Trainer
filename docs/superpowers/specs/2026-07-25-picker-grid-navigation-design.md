# The picker becomes a grid you navigate, not a list you scroll

**Date:** 2026-07-25
**Status:** approved design, not yet implemented
**Supersedes:** the searchable-list interaction in
`2026-07-25-entity-picker-icons-design.md` §1 and §3. The icon chain, the asset
serving, and the "one picker everywhere" property from that spec all stand —
only the *interaction inside the dialog* changes.

## Problem

The icon modal shipped as one long list: 25 course groups, ~120 star rows,
scrolled. Live check confirmed it — reaching Bob-omb Battlefield, the FIRST
course in the game, took scrolling past five secret stages. The icons made rows
recognisable but did nothing about the distance between them.

The user's framing: *"instead of scrolling a ton to find everything, the user
clicks on the course → clicks on the star / segment. No scrolling."*

## Prior art — it already exists in this app

`ui/components/stagebanner.js`'s `PracticeCell` is exactly the cell this needs:
art, name, sub-line, rank medal, active/armed states, keyboard-activatable. The
practice banner already lays those out as a row of course stars, and the user
pointed straight at it: *"we could just reuse this directly! We already have
it!"*

It is currently **local** to `stagebanner.js`. Extracting it to a shared module
is the same move `GroupedList` and `EntityPicker` already made in this session:
one component, two consumers, no second copy to drift.

## Decisions (user, 2026-07-25)

1. **Grid everywhere, depth follows the data.** One grid component; how many
   layers depends on the kind, not on a per-call-site interaction choice.
2. **No search box.** Two clicks and no text entry. Dropping it also removes
   the filter + `aria-activedescendant` surface that made the custom control
   risky — arrow keys over a grid plus Enter/Escape is far less to get wrong.
3. **Layer 2 of the target picker is a UNION** of that course's stars *and* its
   segments — "just a union between the valid segments / stars for that course".
4. Special stages use their `star_icons` art (already shipped: `vanish`,
   `wing`, `metal`, `aqua`, `bitdw`/`bitfs`/`bits`, and PSS's real portrait).

## 1. Depth per kind

| Picker | Layer 1 | Layer 2 |
|---|---|---|
| level (segment builder) | every level as a cell, section-headed by castle region | — |
| course | every course as a cell | — |
| **star / practice target** | every course as a cell, main courses in game order then Bowser levels then the secret stages | that course's **stars + segments** |
| route segment | the five castle regions as cells | that region's segments |

Depth is a prop, not an inference: `depth={1}` renders one grid with a heading
per group; `depth={2}` renders group cells first, then the chosen group's
options. A caller that wants one layer never risks a stray drill-in.

## 2. The component

`ui/components/entitymodal.js` keeps its name, props, and the `groups` contract
— only the body changes:

```js
EntityPicker({ groups, value, onChange, allow, iconFor, title, depth = 1 })
```

- `depth = 1` → one grid; each group contributes a heading and its option cells.
- `depth = 2` → a grid of group cells; clicking one replaces the grid with that
  group's option cells, with a back affordance and the group's name in the
  dialog title.
- `visibleGroups(groups, allow, value)` still runs first, so the
  keep-the-current-value-listed invariant and its tests survive untouched.
- **Escape** closes from layer 1; from layer 2 it goes *back* to layer 1, which
  is what a two-step navigation makes people expect.
- Arrow keys move within the grid (left/right along a row, up/down between
  rows); Enter activates. `role="grid"`/`gridcell` replaces the listbox roles.

## 3. The shared cell

`PracticeCell` moves from `stagebanner.js` to
`ui/components/practicecell.js` and both import it. No behaviour change: the
banner keeps passing what it passes today, and the picker passes `iconSrc`,
`name`, `sub`, `active`, and `onPick`.

This is the third extraction of this shape in one session (`GroupedList`,
`EntityPicker`, now `PracticeCell`) and the reason is the same each time: a
second copy is where the two drift.

## 4. Layer 2's union (target picker only)

For a course, the options are its stars **plus** every segment whose origin
resolves to that course. The mapping already exists server-side: a segment's
`origin.key` is a world-node key, and `vocab.course_by_level` turns its level
into a course id. No new endpoint, no new field.

Picking a segment sets a **segment** target — `/api/target` is already
kind-dispatched, so the wire format needs nothing new either.

## 5. Testing

- `entities.js` gains a `courseUnionOptions(course, catalog, segments, vocab)`
  builder — pure, node-tested: a course's stars in slot order followed by its
  segments, and a segment whose origin is a castle subarea does NOT appear
  under a course.
- `visibleGroups` keeps its four existing tests unchanged.
- The parity gate keeps its shape scan and its domain-vocabulary guard,
  retargeted at nothing new (the component filename is unchanged).
- **Render checks** are the real gate, per project rule: two clicks reach a
  star with no scrolling; Escape from layer 2 returns to layer 1 rather than
  closing; the level picker shows one grid and never drills in; a segment
  appears alongside stars in its course's layer 2.

## 6. Risks

- **A grid of 25 courses must fit without scrolling** at the app's normal
  window size — that is the entire point. If it does not at some size, the
  answer is smaller cells, not a scrollbar creeping back.
- **Keyboard over a grid is 2-D**, so left/right must not fall off the row's
  end into the wrong cell. It is less surface than the filter+listbox it
  replaces, but it is not zero.
- **`PracticeCell` gains a second consumer**, so a banner-specific tweak can
  now leak into the picker. Its props are already the narrow part of the
  banner; keep it that way.
