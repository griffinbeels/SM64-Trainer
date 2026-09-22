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
| **Full run** | GitHub Actions, every push to main; `uv run python tools/full_run.py status` | the whole suite, browser tests included, split across parallel jobs | not on this machine |

The merge check gates a merge; the full run gates a release (`tools/release.py`
waits for a green one on the commit it releases). **Wrap runs the merge check
once; don't run it yourself first.** A direct `run_tests.py` run leaves no
receipt, so wrap runs the same tests again (5m42s twice, 2026-09-22). To see
it before wrap, run `python tools/verify.py full`: its receipt is keyed on the
tree's content, so wrap reuses it from any checkout, unless merging main
changed the tree. While working, run focused checks; `--why --dry-run` shows
what the merge check would pick without running it. A failure reruns as a focused
check: `tools/full_run.py failures` prints the ready-to-paste command for a red
full run, and the merge check reruns last run's failures by itself.

## The blast radius

`tools/blast_radius.py` diffs the working tree (uncommitted and untracked files
included) against the **baseline**: the newest ancestor of HEAD whose full run
on main passed, else the merge-base with main (the output says which). Then:

| Changed | Selects |
| --- | --- |
| Python | pytest-testmon's rule, read off a recorded map: the tests that executed a code block that is no longer as it was. The map is the baseline run's, else the newest nightly one, else a local testmon database; a file no map has seen selects the tests importing it |
| UI script | the module plus everything importing it up to the app shell; then every test naming one of those files, a CamelCase export, a class one of them renders, or the tab (`title="Rank"`) whose page it is |
| Stylesheet | the **rules** that changed, not the file: their class names, then the components rendering them, as above. Only an element selector, `:root` tokens, `@font-face` or widely used keyframes select every browser test |
| Test file | itself |
| Anything else | tests and modules whose code (not comments) names the file |
| Runner, lock file, `conftest.py` | every test that starts no browser; GitHub covers the rest (the selector itself is not one: it changes which tests run, not how they behave) |

Plus every test that failed in this checkout's last run, unless that record is
older than a baseline with a green full run, which supersedes it. The viewport sweeps
(`tests/test_responsive*.py`) are never picked: every case sweeps every page
at every width. The full run covers them; name one to run it locally.

`tests/test_blast_radius.py` pins the rules against the real tree (a Rank
component and a Rank class reach the Rank page and not the Library, a global
rule reaches every page), each proved by mutation. If the radius missed
something, the full run finds it after the push; widen the rule, not the habit.

## The full run

`.github/workflows/full.yml` splits the suite across `JOBS` Windows runners,
one number (`jobs` on a dispatch). It is 20, every runner the account has, so
a second run waits for the first. A plan job turns it into the job list
(`tools/test_lanes.py matrix N`): browser jobs run two workers, the rest
four, and the lanes split the jobs so both finish together (recorded work
over each lane's measured speedup, `LANE_SPEEDUP`). Each job runs
`run_tests.py --all --lane L --shard K/N`, balanced by
`tests/test_durations.json`, the runners' own times (`full_run.py durations
--run <id>` refreshes it). A file is one unit, except that a file over 120 s
whose tests share no fixture splits into its tests: one long file on one
worker set the whole run's wall.

| Jobs (browser + rest) | Units | Wall | Suite step per job |
| --- | --- | ---: | ---: |
| 8 (6 + 2) | files whole, four sweep groups | 11.8 min | 7.4-10.8 min |
| 12 (10 + 2) | same | 10.3 min | 4.7-9.2 min |
| 16 (12 + 4) | long files split | 7.3 min | 2.8-6.1 min |
| 20 (15 + 5) | same, lanes by speedup | 5.5 min | 2.7-4.3 min |

Measured 2026-09-22 (runs 35682161165, 35682940350, 35684469976,
35687662916); setup was 30-60 s a job throughout. Three browser workers
instead of two cut the median browser job by 11% and not the wall
(35687167501). The app's faster start (one rank calibration per start) took
20 jobs to 5.0 minutes (35689782910). What remains is the largest whole
files, whose tests share a page (about three minutes each), plus setup.

Per job: `uv sync --frozen` from uv's cache, Node 24 and the pinned Node tools
(cached), ffmpeg (the gyan.dev build the desktop app bundles, pinned by
`FFMPEG_BUILD`, cached: a newer build broke the CFR sink's audio), and on
browser jobs only Chromium (cached) and uilab at `UILAB_REF` (bump it after
pushing uilab). A test running past five minutes prints every thread's stack.
A newer push to main cancels an older run on main; nightly runs and other
branches never cancel. Each job uploads JUnit XML, its flaky list and failure
screenshots; a wait that timed out also leaves a note beside its picture:
the requests still unanswered, every failed request, error status, console
error and uncaught exception, and the start of the page's body. The nightly run (or a dispatch with `record_coverage`) also
records the coverage map the blast radius reads; recording doubles a job's
time, so a push's run does not.

```
uv run python tools/full_run.py status     # HEAD's run: one line, plus any job not green
uv run python tools/full_run.py wait       # block until it finishes
uv run python tools/full_run.py failures   # failing tests, first error line, rerun command
gh workflow run full.yml --ref <branch>    # a run for a branch before merging
```

What a runner cannot do skips there with an inventory reason: the GL
witnesses need an OpenGL 3.3+ driver (`tests/gl_probe.py`), hardware encoders
need their vendor's GPU, and the GPU witnesses are opt-in everywhere. The
`RUNNER_ONLY` rows in `tests/skip_inventory.py` (the shared harness, the
knowledge repo's chain checker, NVENC, a live journal) count only on a
runner, so the same skip on this desktop still fails the audit.

**One retry, in the full run only.** A job's suite runs with no retries. If
it fails, a second step reruns exactly the tests that failed (read from its
JUnit report), alone and one at a time, after the suite (`tools/full_run.py
retry`). The job is green when they all pass there; a real break fails twice
and stays red, and a job with more than 20 failures, or a red suite with no
failing test, is not retried. A test that passed only alone is `FLAKY`: in the
job summary, a warning annotation and a small `flaky-*` artifact, and
`full_run.py status` lists it, then names any test `FLAKY` in two or more of
the branch's last ten runs as needing a fix. Before this every run went red on
one different browser wait, about one test in 700 (runs 35684991413 to
35687167501). Local runs have no retries.

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
| GitHub runner (4 CPUs, one job each) | the job's: 2 browser, 4 other | 4 |

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
