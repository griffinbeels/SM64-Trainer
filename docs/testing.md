# Testing

Griffin, 2026-09-21: *"if we're just changing one or two files when something
fails, we SHOULD NOT be rerunning the whole suite. A single browser test fails,
we rerun that test (and, if it requires code changes, any other tests
impacted)... we ALREADY TESTED THE WHOLE SUITE. NO NEED TO RERUN ANY TESTS
OTHER THAN THE BLAST RADIUS FOR OUR CHANGES. The \*FULL\* test suite should be
run on GitHub, but during iteration / feature development / merging into main,
we're testing \*what we changed\* / \*what is impacted by the change\*."*

So there is no local full-suite run. For the `tools/verify.py quick|full`
wrappers see [local verification](local-verification.md).

| Check | How | What it runs | Waits? |
| --- | --- | --- | --- |
| **Focused check** | `uv run python tools/run_tests.py tests/test_x.py "tests/test_y.py::test_z"` | exactly what you name, browser tests included | never |
| **Merge check** | `uv run python tools/run_tests.py` (`--why` shows the selection) | the **blast radius**: the tests this change can affect | for one of two slots |
| **Full run** | GitHub Actions, every push to main; `uv run python tools/full_run.py status` | the whole suite, browser tests included, in 12 parallel jobs | not on this machine |

The merge check gates a merge; the full run gates a release (`tools/release.py`
waits for a green one on the commit it releases). A failure reruns as a focused
check: `tools/full_run.py failures` prints the ready-to-paste command for a red
full run, and the merge check reruns last run's failures by itself.

## The blast radius

`tools/blast_radius.py` diffs the working tree (uncommitted and untracked files
included) against the **baseline**: the newest ancestor of HEAD whose full run
on main passed, else the merge-base with main (the output says which). Then:

| Changed | Selects |
| --- | --- |
| Python | the tests whose recorded coverage executed that file: the full run's published map, else the best local pytest-testmon database, else the tests importing it |
| UI script | the module plus everything importing it up to the app shell; then every test naming one of those files, a CamelCase export, a class one of them renders, or the tab (`title="Rank"`) whose page it is |
| Stylesheet | the **rules** that changed, not the file: their class names, then the components rendering them, as above. Only an element selector, `:root` tokens, `@font-face` or widely used keyframes select every browser test |
| Test file | itself |
| Anything else | tests and modules whose code (not comments) names the file |
| Runner, lock file, `conftest.py` | every test that starts no browser; GitHub covers the rest |

Plus every test that failed in this checkout's last run. The viewport sweeps
(`tests/test_responsive*.py`) are never picked: every case sweeps every page
at every width. The full run covers them; name one to run it locally.

`tests/test_blast_radius.py` pins the rules against the real tree (a Rank
component and a Rank class reach the Rank page and not the Library, a global
rule reaches every page), each proved by mutation. If the radius missed
something, the full run finds it after the push; widen the rule, not the habit.

## The full run

`.github/workflows/full.yml` runs `run_tests.py --all --shard K/12
--record-coverage` on Windows runners: `uv sync --frozen`, Node 24, ffmpeg,
Playwright's Chromium, and uilab cloned at the commit pinned in the workflow
(bump `UILAB_REF` after pushing uilab). Jobs are balanced by
`tests/test_durations.json` (files whole, sweeps by their bounded groups);
`full_run.py durations` refreshes it. Each job uploads JUnit XML, the rerun
list, failure screenshots, and its piece of the coverage map the next blast
radius reads.

```
uv run python tools/full_run.py status     # HEAD's run: one line, plus any job not green
uv run python tools/full_run.py wait       # block until it finishes
uv run python tools/full_run.py failures   # failing tests, first error line, rerun command
gh workflow run full.yml --ref <branch>    # a run for a branch before merging
```

**One retry, for setup errors only**, and only in the full run: fixture boot or
seeding timeouts, `WinError 10055` / `ERR_NO_BUFFER_SPACE`, a closed or crashed
browser (`SETUP_ERRORS` in `tools/test_lanes.py`). An `AssertionError` never
reruns. A retried test is printed and put in the job summary as `FLAKY`. Local
runs have no retries.

## Browser tests

`tools/test_lanes.py` reads each module's source and calls it a browser module
when it imports `uilab` or `playwright`, names `serve_ui`/`serve_ui_live`, or
imports a helper that does so at module level. A test in any other module that
launches Chromium or boots the fixture server fails, naming the rule (the
tripwire in `tests/conftest.py`); `tests/test_test_lanes.py` checks the
classification against an independent token scan. A merge check whose radius
holds browser tests refuses to start without uilab.

## One budget across worktrees

A merge check (or `--all`) takes one of two OS-lock slots before creating any
worker or browser; a third waits. Slot 0 is `%TEMP%\SM64Trainer_tests.lock`,
the lock older runners take, so a runner from a worktree without this change
still excludes and is excluded. Focused checks never queue; an empty radius
takes no slot. A killed owner releases its slot. An unregistered outside test
controller is waited for, never stopped.

| Mode | Workers per run | CPUs all runs share |
| --- | ---: | ---: |
| Normal (32-CPU desktop) | 8 | 20 |
| OBS process open | 4 | 8 |
| GitHub runner (4 CPUs) | 2 | 4 |

Explicit `--workers`/`--reserve` may tighten these, never loosen them. Affinity
is set before spawning; OBS opening mid-run tightens the whole tree within two
seconds. The runner contains pytest and every descendant in a Windows job.
`--limit-minutes` stops a run that long after admission, so queued time never
cancels it (`tools/verify_full.py` uses 30).

## Skips

A whole-suite run (each full-run job) fails on a skip whose reason is not in
`tests/skip_inventory.py`; a narrowed run may skip freely. Add a row only for
something the machine genuinely cannot run, saying what would lift it.

## Browser-free component checks

`tests/test_ui_components.py` and its siblings run the real Preact components
in jsdom through Vitest. They need Node 24.13+ and
`npm ci --prefix tests/frontend --ignore-scripts` once per checkout; a missing
install fails with the setup command rather than skipping. Layout, hit testing
and paint stay browser tests. [Pilot findings](testing-pilot.md).

## What makes a test worth keeping

These tests exist to stop an agent changing behaviour nobody asked it to
change. Griffin, 2026-09-17: *"make sure that we are not over-testing certain
behaviors / that everything we have is genuinely valuable for preventing
agentic drift. That's the purpose of the tests."* A test earns its place by
answering all four:

1. **What change turns it red?** Name it. If you cannot, it is decoration.
2. **Is it the only thing that catches that change?** If a sibling fails
   identically, one of them is redundant; keep the one that reads better.
3. **Does it pin a decision or an accident?** "The time comes from the Usamune
   clock" is a decision. A pixel height or a shipped default's contents is
   where we happen to be this week -- see contract 7 in CLAUDE.md.
4. **Does it fail only when the behaviour is wrong?** A test that goes red for
   load, a port collision or a missing tool teaches everyone to ignore red.

Delete a test when its contract has been retired, or when another test proves
the same failure and a mutation shows the duplicate adds no protection. Do not
delete behavior coverage merely because it is slow.

Measurements behind the earlier single-lock runner, and the 2026-09-17 audit
examples, are in [the archived testing guide](history/testing-before-lanes-2026-09-21.md).
