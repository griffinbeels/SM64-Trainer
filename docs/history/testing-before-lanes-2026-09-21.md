# Testing before the lanes split (archived 2026-09-21)

Historical evidence only: docs/testing.md as it stood before the suite was
split into the local merge check and the GitHub browser run. The single
machine-wide lock, the 16-worker budget and the measurements below describe
that earlier runner. Current guidance: [testing](../testing.md).

For the local draft and wrap commands, see [local verification](../local-verification.md).
The shared `quick` lane runs pinned offline lint checks; `full` retains this
document's integration runner and requires its rendered-test dependencies.

Use the smallest check that can disprove the change, then stop when it passes.
Run the full suite once for the integrated change, not after every edit, commit,
review, and merge. Repeat it only if code, test inputs, dependencies, or the
integrated result changed. A merge of the already-tested identical tree does
not justify another full run. Record the revision, command, result, and skips.

## Choose the scope before running

| Change | First check | Broaden when |
| --- | --- | --- |
| One Python module | `uv run python tools/run_tests.py tests/test_<module>.py` | A shared contract or a failure names another consumer |
| Python changes with a current coverage map | `uv run python tools/run_tests.py --changed` | Before integration; testmon cannot see subprocess-only Python |
| UI or API consumed by UI | Relevant behavior test and one headless render of the affected surface | Layout changed: relevant responsive cases; integration: full suite |
| Docs, task notes, comments | Relevant doc/link/config check if one applies | Executable configuration or a documented contract changed |
| Test infrastructure, shared contracts, final integration | `uv run python tools/run_tests.py` | Another change or unresolved failure warrants another run |

Explicit pytest targets/options select **focused** mode: serial by default,
no testmon instrumentation, no full-run stamp. `--workers N` opts into bounded
parallelism for several independent files. `--dry-run` explains selection
without starting pytest. `--changed` and explicit targets cannot be combined.
`--changed` still falls back to the full suite for any non-Python change; use an
explicit focused scope for docs/UI work rather than pretending coverage sees it.

Do not rerun responsive checks separately after a full run already executed
them against the same inputs. Do not run five full suites to test queueing:
the admission regression uses five tiny real processes instead.

## Browser-free component checks

Install Node 24.13+ on the 24.x line (or Node 26+) and run
`npm ci --prefix tests/frontend --ignore-scripts` once per checkout. These are
development dependencies only. Missing dependencies fail the component gate
with the setup command; they never silently remove its coverage.

Run `uv run python tools/run_tests.py tests/test_ui_components.py -s` for the
TimeFields pilot. The pytest bridge is included in a normal full run, so there
is no separate suite to remember. It invokes Vitest once in run mode, with one
worker, no retries and no browser. It inherits the shared budget; on Windows,
a nested job also closes its worker on completion, failure or timeout. Do not
start standalone Vitest/watch processes outside the resource-owning runner.

Vitest resolves the application's import map to its actual vendored Preact,
hooks and htm modules, including inside the testing helpers. The real component
runs in jsdom; props and DOM events are exercised without an app server.
Use this for field edits, state changes and callback contracts. Layout, hit
testing, paint, browser permissions and full application wiring remain real
browser checks. Passing this lane does not prove desktop smoothness.

Pure logic can be cheaper still: `tests/test_ui_time_format.py` retains its
eight Python assertions and batches their real JavaScript outputs through one
short-lived Node process. It needs no npm packages. Do not move already-cheap
logic into a DOM environment merely to use one framework everywhere.

See [the pilot findings](../testing-pilot.md) for measured costs, inventory limits
and the next candidates. No browser workflow coverage was removed by this pilot.

## One budget across worktrees

All updated runners and direct pytest controllers share an OS lock in the user
temp directory. Waiting controllers sleep before creating workers or browsers.
There is one active allocation regardless of whether one or five agents request
tests. Admission is mutual exclusion, not FIFO; queued runs report that they
are waiting. Killing a lock owner releases its lock automatically.

On this 32-logical-CPU desktop:

| Mode | Maximum workers | CPUs available to tests |
| --- | ---: | ---: |
| Normal | 16 | 20 |
| OBS process open | 8 | 8 |
| Focused check, either mode | Serial | Same mode's CPU allocation |

Budgets scale down on smaller/pre-restricted machines. Explicit worker counts
and reserves may reduce these limits, never increase them. The runner sets
affinity **before** spawning, then checks its descendants every two seconds.
Opening OBS mid-run tightens existing processes; closing OBS does not expand
that run again. xdist's worker count stays fixed until the next run. A child's
deliberately narrower affinity is preserved.

OBS detection checks the process name, including idle OBS, without opening a
window, contacting its WebSocket, or changing recording settings. This reserves
CPU capacity and reduces browser/process churn; it is not a GPU, disk, memory,
or encoder-frame guarantee. OBS rendering/encoding-lag counters during actual
streaming are the acceptance signal for further tuning.

Older worktrees without this change do not cooperate with the lock. The runner
detects outside Python/pytest controllers regardless of whether Claude or Codex
launched them, names their PID and checkout, and waits before starting. If an
old runner appears later, it flags the performance comparison as contaminated;
it never kills somebody else's run. Merge this change into older worktrees to
make admission cooperative. Other ecosystems' runners are not detected.

## Failures and cleanup

No blanket retries. A failure remains visible; diagnose the named test once in
its relevant context before widening scope. The earlier apparent load flake
was traced to testmon reordering shared-page tests; the collection-order guard
remains. Add a retry only for a demonstrated transient external boundary, with
an explicit reason, rather than hiding deterministic failures globally.

The runner contains pytest and all descendants in a Windows job before they
can execute. Normal completion, interruption, and runner death close that job.
It cannot terminate OBS, another session, or an unrelated ffmpeg. Direct pytest
gets admission/affinity but the runner is required for this crash-containment
backstop. There is no machine-wide deletion of browser profiles or orphaned
processes: absence of a live parent does not establish ownership.

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

Two failure shapes that look like coverage and are not, both found in the
2026-09-17 audit:

- **A test that cannot fail.** `test_ranks_api_marelo`'s two celebration tests
  ran against a fixture that grades the FLOOR rank, so every celebration path
  returned before deciding anything. They now seed a graded star first, and
  each was proved by mutation.
- **A skip that can never lift.** Both of those skipped on every machine since
  the day they were written. A whole-suite run now FAILS on a skip whose
  reason is not in `tests/skip_inventory.py`; a narrowed run may skip freely.
  Add a row there only for something this machine genuinely cannot run, with
  what would lift it -- never to silence a guard that should be firing.

## Evidence and test deletion policy

The September 1–2 measurements already answered the 32-worker question after
the responsive sweep was split: at 12 reserved CPUs, 16/24/32 workers took
212/204/206 seconds. Differences below roughly eight seconds were inside the
observed spread. At zero reserve, 32 workers took 228 seconds and p99 scheduler
wake delay reached 52.8 ms. Those measurements were of **one** suite, so they
never established a safe budget for five concurrent suites plus streaming.
The normal default uses 16 to avoid the extra processes inside that speed tie.

Delete a test when its contract has been retired, or when another test proves
the same failure and a mutation demonstrates the duplicate adds no protection.
Do not delete behavior coverage merely because it is slow. Here, five obsolete
tests of the removed global-orphan sweep and old affinity mechanism were
removed with their implementation; resource ownership, crash cleanup and
admission now have real-process checks. The fixture-reach and responsive
tests retain their distinct jobs: populated state versus layout defects.

Reproduce the existing load probe with `tools/measure_run_load.py`; current
runner policy caps requested configurations, so read the admitted budget in
each log rather than treating the requested worker number as the actual one.
Primary references: [xdist worker/scheduling options](https://pytest-xdist.readthedocs.io/en/stable/distribution.html)
and [psutil affinity and process identity](https://psutil.readthedocs.io/stable/index.html).


The shared runner pins its own imports and child `PYTHONPATH` to the selected
checkout's `src` before importing project modules. This prevents a reused virtual
environment's editable install from silently testing or serving another worktree.
Direct one-off probes must set the same absolute source path and report module
`__file__` when comparing candidates. A passing wrong-checkout run is not evidence.
