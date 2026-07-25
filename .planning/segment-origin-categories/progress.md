# Progress ledger — segment origin categories

Branch: `feature/segment-origin-categories` · Plan:
`docs/superpowers/plans/2026-07-24-segment-origin-categories.md`

Trust this file + `git log` over recollection after any compaction. A task
marked complete is DONE — do not re-dispatch it.

| Wave | Task | State | Commits / notes |
|---|---|---|---|
| 1 | T1 addresses: `world_regions` BFS, MIPS table | dispatched | agent `t1-addresses` |
| 1 | T2 `ui/group.js` `buildTree` | dispatched | agent `t2-grouptree` |
| 2 | T3 `start_origin` + taxonomy (needs T1) | pending | |
| 2 | T4 `GroupedList` + `.lib-cat` CSS (needs T2) | pending | |
| 3 | T5 API stamp + override (needs T3) | pending | |
| 3 | T6 routes.js migration (needs T2, T4) | pending | |
| 3 | T7 corpus origin coverage test (needs T3) | pending | |
| 4 | T8 segment library grouping (needs T4, T5) | pending | |
| 5 | T9 editor origin override (needs T8) | pending | serialized after T8 — same file |
| 6 | T10 docs (rules + README) | pending | |
| — | Final whole-branch review (Opus 5) | pending | NON-OPTIONAL |

## Watch items (predicted breakage + the sanctioned remedy)

- **Agent worktrees branch from `main`, not this branch** — the plan doc lives
  only here. Every dispatch starts with
  `git merge feature/segment-origin-categories --no-edit`, then cherry-pick
  their task commits back (clean: tracks own disjoint files).
- **T8's `originLevels` keys the region level on `String(origin.region)`**, so
  a null region becomes `"null"` and matches the taxonomy's trailing
  `{key: null}` entry. Intended; change both sides or neither.
- **`index.html` is owned by T4, then T9.** No other task may edit it.
- **`segments.js` is owned by T8, then T9** — never concurrently.
