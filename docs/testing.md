# Testing

The suite is split so the local loop never waits on a browser. For the
`tools/verify.py quick|full` wrappers see [local verification](local-verification.md).

| Check | How | What it runs | Waits? |
| --- | --- | --- | --- |
| **Focused check** | `uv run python tools/run_tests.py tests/test_x.py` or `--changed` | the files you name (either lane), or testmon's pick inside the merge check | never |
| **Merge check** | `uv run python tools/run_tests.py` | every test that starts no UI fixture server and no browser (~10,400) | for one of two slots |
| **Browser run** | GitHub Actions, every push to main; `uv run python tools/browser_ci.py status` | every test that does (~700), in 8 parallel jobs, about 10 minutes | not on this machine |

The merge check gates a merge. The browser run gates only a release
(`tools/release.py` waits for a green one on the commit it releases).

Use the smallest check that can disprove the change, then stop. Run the merge
check once for the integrated change; repeat it only if code, test inputs,
dependencies or the integrated result changed. Record revision, command,
result and skips.

| Change | First check | Broaden when |
| --- | --- | --- |
| One Python module | the module's test file | a shared contract or a failure names another consumer |
| Python changes with a current coverage map | `--changed` | before integration; testmon cannot see subprocess-only Python |
| UI or API consumed by UI | the relevant behavior test and one render of the surface, by naming the browser file | layout changed: the responsive case; integration: merge check, then the browser run after the push |
| Docs, comments | the relevant doc/link/config test, if one applies | an executable configuration or documented contract changed |
| Test infrastructure, shared contracts, integration | the merge check | another change or unresolved failure |

Explicit targets are **focused**: serial by default (`--workers N` for a few
independent files), no testmon, no stamp. `--changed` cannot be combined with
targets and falls back to the whole merge check when any non-Python file
changed. `--dry-run` prints the choice without starting pytest.

## Which lane a test is in

`tools/test_lanes.py` reads each module's source, never importing it. A module
is in the **browser lane** when it imports `uilab` or `playwright`, names
`serve_ui` / `serve_ui_live`, or imports a `tests/` or `tools/` helper that does
so at module level. Everything else is the **merge check**. A new browser file
lands in the browser lane with no marker to remember.

Two guards keep it honest, both in `tests/test_test_lanes.py` and proved by
mutation: an independent token scan that fails if any merge-check module names
a browser entry point, and a tripwire. During the merge check a Chromium launch
or a fixture-server boot the classifier missed fails that test and names the
rule, instead of making the local loop slow again.

To run browser tests locally, name their files. `run_tests.py --browser` runs
the whole browser lane here; it takes a slot and is rarely worth it.

## The browser run

`.github/workflows/browser.yml` runs the browser lane on Windows runners:
`uv sync --frozen`, Playwright's Chromium, and uilab cloned at the commit pinned
in the workflow (bump `UILAB_REF` when a test needs a newer uilab; it must be
pushed to github.com/griffinbeels/uilab first). Each job takes `--shard K/N`:
whole files, viewport sweeps split along their bounded groups, balanced
longest-first by `tests/browser_durations.json`, two workers per 4-CPU runner.

Reading it, without raw logs:

```
uv run python tools/browser_ci.py status     # HEAD's run: one line, plus any job not green
uv run python tools/browser_ci.py wait       # block until it finishes
uv run python tools/browser_ci.py failures   # failing tests + first error line; artifacts in %TEMP%
uv run python tools/browser_ci.py durations  # rebalance the jobs from a run's JUnit times
gh workflow run browser.yml --ref <branch>   # a run for a branch before merging
```

**One retry, for setup errors only**, and only in this lane: fixture boot or
seeding timeouts, `WinError 10055` / `ERR_NO_BUFFER_SPACE`, a closed or crashed
browser (`SETUP_ERRORS` in `tools/test_lanes.py`). An `AssertionError` never
reruns. A test that needed the retry is printed and put in the job summary as
`FLAKY`; `failures` lists those too. The merge check has no retries.

## One budget across worktrees

A merge check (or `--browser`) takes one of two OS-lock slots before creating
any worker or browser; a third waits. Slot 0 is `%TEMP%\SM64Trainer_tests.lock`,
the lock older runners take, so a runner from a worktree without this change
still excludes and is excluded. Focused runs and `--changed` never queue. A
killed owner releases its slot. An unregistered outside test controller (a
very old runner) is waited for, never stopped.

On this 32-CPU desktop each run gets half the machine budget, so two merge
checks together fill it:

| Mode | Workers per run | CPUs all runs share |
| --- | ---: | ---: |
| Normal | 8 | 20 |
| OBS process open | 4 | 8 |
| GitHub runner (4 CPUs) | 2 | 4 |

Explicit `--workers`/`--reserve` may tighten these, never loosen them. Affinity
is set before spawning; OBS opening mid-run tightens the whole tree within two
seconds and stays latched. The runner contains pytest and every descendant in a
Windows job, closed on completion, interruption or runner death.
`--limit-minutes` stops a run that long after admission, so queued time never
cancels a run (`tools/verify_full.py` uses 30).

## Skips

A whole-lane run (either lane, or one browser job) fails on a skip whose reason
is not in `tests/skip_inventory.py`; a narrowed run may skip freely. Add a row
only for something the machine genuinely cannot run, saying what would lift it.
The browser lane refuses to start without uilab, because a missing uilab skips
every module and would otherwise look green.

## Browser-free component checks

`tests/test_ui_components.py` runs the real Preact components in jsdom through
Vitest, inside the merge check. It needs Node 24.13+ and
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
delete behavior coverage merely because it is slow: move it to the right lane.

Measurements behind the earlier single-lock runner, and the 2026-09-17 audit
examples, are in [the archived testing guide](history/testing-before-lanes-2026-09-21.md).
