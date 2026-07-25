# Progress ledger — segment origin categories

Branch: `feature/segment-origin-categories` · Plan:
`docs/superpowers/plans/2026-07-24-segment-origin-categories.md`

Trust this file + `git log` over recollection after any compaction. A task
marked complete is DONE — do not re-dispatch it.

| Wave | Task | State | Commits / notes |
|---|---|---|---|
| 1 | T1 addresses: `world_regions` BFS, MIPS table | **complete** | `20ee546`, 18 tests in test_addresses.py |
| 2 | T2 `ui/group.js` `buildTree` | **complete** | `5a4b734`, 3 node-driven tests (ran, not skipped) |
| 2 | T3 `start_origin` + taxonomy (needs T1) | **complete** | `9947b73`, 16 new tests |
| 2 | T4 `GroupedList` + `.lib-cat` CSS (needs T2) | **complete** | `dd678de`, 4 tests, node check exit 0 |
| 3 | T5 API stamp + override (needs T3) | **complete** | `7537c5a`, +docs/api.md (required — see watch items) |
| 3 | T6 routes.js migration (needs T2, T4) | **complete** | `63b9152`, net -35 lines, render-verified |
| 3 | T7 corpus origin coverage test (needs T3) | **complete** | `821b810`, all 65 seeded segments resolve |
| 4 | T8 segment library grouping (needs T4, T5) | **complete** | `c5c7c09` + controller fix `1ab2b66`, render-verified |
| 5 | T9 editor origin override (needs T8) | **complete** | `98e1d6e`, render-verified against REAL API fixtures |
| 6 | T10 docs (rule change maps) | dispatched | agent `t10-docs` |
| — | Final whole-branch review (Opus 5) | pending | NON-OPTIONAL |

## Watch items (predicted breakage + the sanctioned remedy)

- **`isolation: "worktree"` did NOT isolate (observed wave 1).** Both agents
  ran in THIS checkout, on this branch, sharing one git index — T1 watched its
  staged files get unstaged and a neighbour's untracked files appear staged
  alongside its own. Nothing was lost (both commits are clean), but the
  remedy is now mandatory in every dispatch: commit with an explicit PATHSPEC
  (`git commit -F - -- <exact paths>`), which takes the working-tree content of
  those paths and ignores whatever else sits in the shared index, then confirm
  with `git show --stat HEAD`. No merging, no branch creation — the branch is
  already correct.
- **The plan's Preact import line was WRONG** and Task 4 caught it: there is
  no `ui/vendor/preact.js`; components import bare specifiers through
  index.html's importmap (`preact`, `preact/hooks`, `htm`). Plan corrected +
  added to Global Constraints so Tasks 6/8/9 cannot repeat it.
- **Suite baseline is now 1508 passed** (1475 before wave 1); 16 commits on the branch.
- **The REST surface doc is `docs/api.md`, not the README** — the plan said
  README, which was wrong, and `tests/test_docs_cover_api.py` fails the suite
  for any undocumented `/api` route. T5 correctly wrote both entries itself
  rather than deferring to T10; T10 now only verifies them.
- **A test that cannot tell code from prose gets "fixed" by editing the prose.**
  My T8 assertion (`"WORLD_EDGES" not in segments.js`) forced an agent to reword
  an accurate, greppable comment citing `addresses.WORLD_EDGES_*`. Reverted in
  `1ab2b66`; the assertion now names the derivation symbols themselves. Watch
  for this shape in the final review.
- **T8's `originLevels` keys the region level on `String(origin.region)`**, so
  a null region becomes `"null"` and matches the taxonomy's trailing
  `{key: null}` entry. Intended; change both sides or neither.
- **`index.html` is owned by T4, then T9.** No other task may edit it.
- **Between T4 and T6 the route library is transiently unstyled** — T4 deletes
  the `.route-cat*` CSS while routes.js still emits those classes. T6 (same
  wave) is what closes it; do not ship the branch with T6 unlanded.
- **`segments.js` is owned by T8, then T9** — never concurrently.
