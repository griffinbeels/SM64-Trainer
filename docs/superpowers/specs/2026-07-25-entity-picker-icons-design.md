# Icons in the entity picker — a modal that shows what you're choosing

**Date:** 2026-07-25
**Status:** approved design, not yet implemented
**Supersedes:** the native-`<select>` decision in
`2026-07-25-shared-entity-picker-design.md` §Prior art. That spec kept native
selects and named the condition that would reopen the question. This is that
condition, arriving from a direction it did not predict.

## Problem

Reading "Shifting Sand Land" is slower than seeing the pyramid. Every picker in
the app currently identifies a course, level or star by **name alone**, so a
user scanning for one is doing text comparison in a list of 15 courses or ~120
stars.

The blocker is not styling: **`<option>` and `<optgroup>` cannot contain
images.** The browser renders them as text in a native popup, and no CSS
changes that. Icons therefore require replacing the native control.

## Prior art

- **In-repo, and the reason this is cheap.** `ui/components/iconpicker.js` is
  already a modal grid of image tiles on the shared `Modal` shell, fed by
  `GET /api/icons` which *globs* the asset directory so a newly dropped file
  appears with zero code changes. `ui/components/stagebanner.js` already
  resolves per-entity art through an override → level-fallback → generic chain
  and renders it in `PracticeCell`. This design reuses both rather than
  inventing an icon system.
- **The pattern.** A trigger button plus a searchable, grouped, keyboard-driven
  list in a dialog is the command-palette / listbox-in-dialog shape (VS Code's
  quick-open, Slack's switcher). The keyboard contract is the part
  hand-rolled versions habitually miss, so §3 specifies it explicitly rather
  than leaving it to implementation taste.
- **Why a dialog and not an anchored dropdown.** The workshop panes now scroll
  internally under a measured height cap (`ui/viewport.js`, 2026-07-25). An
  anchored popup inside a clipped, internally-scrolling pane is the classic
  place custom dropdowns break, and escaping it means a portal plus
  position-on-scroll tracking. A centered dialog has none of that.

## Decisions (user, 2026-07-25)

1. **Modal picker**, not an inline anchored dropdown.
2. **All four kinds** — level, course, star and segment — so there is one
   control everywhere, which was the point of the previous spec.
3. **Stars nest under their course**: the course is a heading carrying the
   portrait, its stars are rows beneath. The portrait appears once per course
   rather than seven times.
4. **Star row art follows the existing `starIcons` preference** — per-star
   course icons or the classic gold star, the user's existing setting.
5. **Four courses have no portrait, and never will.** HMC, SSL, DDD and SL are
   not entered through a painting in the game, so no portrait art exists to
   find (user, 2026-07-25 — they run this game; do not go looking for these
   files or file a TODO to source them). They resolve to their star-1 icon,
   which is real art. This is the permanent answer, not a placeholder.
   (Which four they are moved during design: `sl.webp` was mislabelled and is
   actually CCM. That the correction was a file RENAME and not a code change is
   the fallback chain doing its job.)

## 1. The component

`ui/components/entitymodal.js`:

```js
EntityPicker({ groups, value, onChange, allow, label, kind })
```

Renders a **trigger button** — current option's icon, its name, a caret — and
opens `EntityPickerModal` on click. The modal is the shared `Modal` shell
containing a search input and the grouped list.

It keeps `visibleGroups(groups, allow, value)` from `ui/entities.js` unchanged,
so the invariant that already has tests carries over: a value the caller's
filter rejects **stays listed**, and a group emptied by filtering disappears.

`groups` gains one optional field per option and one per group:

```js
{ key, label, icon?, options: [{ id, name, icon? }] }
```

`icon` is a URL or null. Nothing else about the group contract changes, so the
`entities.js` builders and their tests keep working.

## 2. Icon resolution — one chain, one home

A single `optionIcon(kind, id, context)` in `ui/entities.js`, beside the
builders that produce the options:

| Kind | Chain |
|---|---|
| course | course portrait → that course's star-1 icon → generic star |
| star | **preference-driven**: `starIcons === "course"` → that star's split icon (`{prefix}{slot+1}.png`); `"classic"` → the generic gold star art. Mirrors `stagebanner.js` exactly. |
| level | `COURSE_BY_LEVEL` → the course chain; Bowser stages use the existing `bitdw`/`bitfs`/`bits` art; castle levels (6/16/26) have no art and fall through to generic |
| segment | the banner's existing chain: per-entity icon override → `LEVEL_ICONS` start-level match → generic |

The star row therefore shows the same art the practice banner shows for that
star, under the same setting, and the segment picker shows the same art the
banner shows for that segment. No second source of truth for entity art.

**The four courses without a portrait (HMC, SSL, DDD, SL) resolve to their
star-1 icon** — real art, so they read as intentional, which they are: those
courses are not entered through a painting, so the portrait does not exist to
ship (§Decisions 5). The fallback is the final state for them.

## 3. Keyboard and focus — specified, not left to taste

This is what native gave us for free and what a custom control must earn back:

- **Type** in the search field filters rows; a group whose rows all filter out
  disappears, headings included.
- **↑ / ↓** move the active row across group boundaries; the list scrolls to
  keep it visible.
- **Enter** picks the active row; **Escape** closes without changing anything.
- **Focus returns to the trigger button** on close, either way.
- `role="listbox"` on the list, `role="option"` on rows, `aria-activedescendant`
  on the list pointing at the active row, `aria-selected` on the current value.
- The modal shell already traps focus and handles backdrop dismissal.

## 4. Assets and serving

`assets/course_icons/` (13 files — 11 main-course portraits plus BitFS and
PSS; currently **untracked** and outside the
served tree) moves to `src/sm64_events/ui/assets/course_icons/`, which
`tools/build_exe.py` already bundles as part of the `ui/` tree.

Extensions are mixed (`.webp` and `.png`), so the client must not guess:
**`GET /api/icons/courses`** returns the directory listing, exactly as
`GET /api/icons` already does for `star_icons`. Consequence: re-art, a
higher-resolution rip, or a portrait for a course that gains one appears by
dropping the file in the folder, with no code change. That property is the
reason for the endpoint; a hardcoded extension map would forfeit it. It is NOT
there to await HMC/SSL/DDD/SL — those four have no painting in the game.

## 5. What this replaces

`GroupedPicker` (`ui/components/picker.js`, shipped 2026-07-25) is **replaced**
at all four call sites and deleted. `visibleGroups` stays in `entities.js` with
its four tests. The parity gate
(`tests/test_ui_picker_parity.py`) switches its target from "imports
`GroupedPicker`" to "imports `EntityPicker`", keeping the shape scan that
catches a hand-rolled `<option>` list and the guard that keeps domain
vocabulary out of the shared component.

Said plainly: the native picker was not a stepping stone to this. It was the
right call under a constraint that then changed, and it is being removed rather
than layered over.

## 6. Testing

- **Node-driven** (`tests/test_ui_entities.py`): `optionIcon` for each kind,
  including the four portrait-less courses falling through to star-1 art, both
  values of the `starIcons` preference, and a segment with an icon override.
- **`visibleGroups`** keeps its existing four tests unchanged — the invariant
  survives the control swap, which is the point of it living in `entities.js`.
- **Parity gate** retargeted per §5, and re-probed the way the current one was:
  a hand-rolled `<option>` list in a new file must turn it red.
- **Render checks** (mandatory — unit tests plus `node --check` once shipped an
  invisible feature here): open the modal from the practice target; confirm
  portraits render and HMC shows its fallback rather than a broken image; type
  to filter and confirm empty groups vanish; ↑/↓/Enter/Escape; focus returns to
  the trigger; and the modal renders correctly inside the height-capped,
  internally-scrolling workshop panes.

## 7. Risks

- **~120 rows and ~135 images in one modal.** No virtualization (YAGNI at this
  size), but rows carry `loading="lazy"` so only what scrolls into view fetches.
  If it stutters, the fix is to render a group's rows only when it is expanded —
  noted, not built.
- **A custom control is a permanent keyboard-accessibility liability** in a way
  a native select is not. §3 is the mitigation, and the render checks exercise
  it; regressions here are silent for mouse users, so the keyboard path needs a
  test that clicks nothing.
- **Two icon systems could drift** — the banner's and the picker's. Mitigated by
  §2 reusing the banner's chain rather than defining a parallel one; a future
  change to entity art must land in one place.
