# Progress ledger — shared entity picker

Branch: `feature/shared-entity-picker` (from `main` @ 0579cf0) · Plan:
`docs/superpowers/plans/2026-07-25-shared-entity-picker.md` · Spec:
`docs/superpowers/specs/2026-07-25-shared-entity-picker-design.md`

Trust this file + `git log` over recollection after any compaction. A task
marked complete is DONE — do not re-dispatch it.

| Wave | Task | State | Commits / notes |
|---|---|---|---|
| 1 | T1 `components/picker.js` (`GroupedPicker`, `visibleGroups`) | dispatched | agent `p1-picker` |
| 1 | T2 `ui/entities.js` (pure group builders) | **complete** | `bf0cee2` |
| 1 | T3 `views.py` `catalog.course_groups` | **complete** | `ba455cd`, 2 tests |
| 2 | T4 segments.js clause params (needs T1, T2) | pending | |
| 2 | T5 header.js target modal (needs T1, T2, T3) | pending | |
| 2 | T6 routes.js step editor (needs T1, T2, T3) | pending | |
| 3 | T7 parity test + rules row | pending | |
| — | Whole-branch review (Opus 5) | pending | NON-OPTIONAL |
| — | Human audit (the ~120-option star list) | pending | only a human can judge type-ahead over it |

## Watch items (predicted breakage + the sanctioned remedy)

- **Agents share ONE checkout and ONE git index** (observed last branch:
  `isolation: "worktree"` did not isolate). Every dispatch commits with an
  explicit **pathspec** (`git commit -F - -- <paths>`) and confirms with
  `git show --stat HEAD`. Also verify `git branch --show-current` reads
  `feature/shared-entity-picker` before committing — a subagent has committed
  to the wrong checkout in this project before.
- **Ids are STRINGS inside the picker**; every call site converts at its own
  boundary (`Number(id)` / `parseStarId(id)`). A call site that forgets sends
  a string course_id to the API.
- **`segmentOptions(defs, taxonomy)` must tolerate a null taxonomy** — routes.js
  fetches `/api/segments/vocab` after first paint, so the first render has none.
- **`_CATALOG` is built at import.** `course_groups()` is pure; it must never
  grow a database dependency.
- **PLAN DEFECT, corrected mid-wave:** the plan put the pure `visibleGroups`
  inside `components/picker.js` and told its test to import it through node.
  Impossible — `picker.js` imports `preact`/`htm` as BARE specifiers, resolved
  only by index.html's importmap; node has no node_modules and no importmap, so
  it can never load that file. `group.js`/`entities.js` are node-testable
  precisely because they import nothing. `visibleGroups` therefore lives in
  `ui/entities.js`, and `picker.js` imports it. **Do not "tidy" it back into
  the component** — that silently deletes the only test of the
  keep-the-current-value invariant.
- Baseline before this branch: **1515 passed**; after T2+T3: **1526 passed**.
