# Progress ledger — entity picker icons

Branch: `feature/picker-icons` (from `main` @ 85cf88b) · Plan:
`docs/superpowers/plans/2026-07-25-entity-picker-icons.md` · Spec:
`docs/superpowers/specs/2026-07-25-entity-picker-icons-design.md`

Trust this file + `git log` over recollection after any compaction. A task
marked complete is DONE — do not re-dispatch it.

| Wave | Task | State | Commits / notes |
|---|---|---|---|
| 1 | T1 serve portraits + `course_by_level` | dispatched | agent `i1-assets` |
| 1 | T2 `optionIcon` + move the two registries | dispatched | agent `i2-icons` |
| 1 | T3 `EntityPicker` + CSS + store manifest | dispatched | agent `i3-modal` |
| 2 | T4 segments.js clause params | pending | |
| 2 | T5 header.js target modal | pending | |
| 2 | T6 routes.js step editor | pending | |
| 3 | T7 delete picker.js, retarget the gate | pending | |
| — | Whole-branch review (Opus 5) | pending | NON-OPTIONAL |
| — | Human audit — **the keyboard path** | pending | mouse-only testing cannot see these regressions |

## Watch items (predicted breakage + the sanctioned remedy)

- **Agents share ONE checkout and ONE git index** (observed on the last two
  branches: `isolation: "worktree"` does not isolate). Every dispatch verifies
  `git branch --show-current` reads `feature/picker-icons` and commits with an
  explicit **pathspec**, then confirms with `git show --stat HEAD`.
- **`t.courseIcons` is created by T3 and read by T4/T5/T6.** If T3's store
  change is missed, every course row silently falls back to star art — which
  looks like the fallback working, not like a missing fetch. Check the manifest
  is non-empty in wave 2's render checks.
- **Four courses have no portrait and never will** (HMC, SSL, DDD, SL — not
  entered through a painting). They use hand-picked substitutes `hmc6`,
  `ssl2`, `ddd1`, `sl6`. Do not add a TODO, do not hunt for portrait files.
- **Ids are STRINGS inside the picker**; each call site converts at its own
  boundary (`Number(id)` / `parseStarId(id)`).
- **`optionIcon` must never return null** — a row with no art collapses its own
  layout. Every branch ends at the generic star.
- Baseline before this branch: **1539 passed**.
