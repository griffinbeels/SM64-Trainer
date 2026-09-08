# Local verification

`python tools/verify.py quick` invokes the shared harness verifier using this
checkout's `.verification.toml`. `full` includes quick and the existing
resource-aware integration test runner. `fix --files <owned paths>` permits
only explicit file ownership. The installed harness is resolved through
`~/.claude/harness`; `HARNESS_ROOT` can select a harness worktree for development.

## Setup once per checkout

Install the pinned tools explicitly; checking never downloads dependencies:

```
uvx ruff==0.16.6 --version
npm ci --prefix tools/verification --ignore-scripts
uv sync --frozen
```

Ruff uses uv's machine cache offline; ESLint and its transitive dependencies use
the committed npm lockfile and checkout-local node_modules. The ESLint version
was selected from the existing cache for the pilot. It is deprecated upstream;
an upgrade requires a deliberate compatibility review rather than a floating
version inside the draft loop. Browser binaries and the shared UI harness remain
separate requirements documented in [testing](testing.md).

## Standalone lint contract

`python tools/verify_lint.py` checks whole changed Python/JavaScript files against
the merge base with local main, including staged, unstaged and untracked files.
`--all` checks every tracked source plus untracked source. `--files` selects an
explicit scope. No selected source is reported as not applicable.
Changes to lint configuration, tool locks or the checker broaden the default
scope to all source so unchanged files cannot escape a changed rule set.

Exit 0 means no new findings; 1 means actionable findings; 2 means required
evidence is unavailable (including missing tools, timeout, malformed output or
invalid scope). Each finding includes the file, line, rule and diagnostic.
This standalone gate does not import the legacy fail-open commit hook.

The initial baseline records pre-existing findings from the named source commit,
with exact tool versions, diagnostics, and counted hashes of path, rule, message
and neighboring source. Whole selected files are checked, including unchanged
lines. New files and changed diagnostic contexts receive no old allowance.
Moving identical code within the same file can retain its allowance; this is a
debt baseline, not a semantic proof. Changes near old debt may require addressing
that debt. Baseline refresh is never an automatic fix and must be reviewed.

The onboarding merge `f0887142` was reviewed separately during adoption: seven
existing findings were added after proving their files match that main commit.
They are `capturelayer.status` (C901:21, PLR0912:21, PLR0915:60), application
startup in `main.py` (PLR0915:163), and `ui_fixture.serve_ui_live` (C901:27,
PLR0912:22, PLR0915:90). These numbers are diagnostic complexity/branch/statement
counts, respectively. The baseline retains its original entries and records the
full source commit, reason and seven diagnostics in `adoptions`; none of the
new verification implementation's findings were adopted.

`--fix --files ...` only permits Ruff's F401 unused-import removal. Review imports
used exclusively for registration side effects before requesting that fix; mark
intentional imports explicitly. No ESLint automatic fixes are enabled in this
pilot. Files outside the checkout and implicit whole-tree fixes are refused.

The legacy `tools/lint_changed.py` and commit hook retain their staged-content
semantics for compatibility. The standalone verifier is the required workflow
boundary; their fail-open results cannot supply its successful evidence.

The draft lane adds no test-writing requirement. It enforces the existing
curated lint rules plus a narrow strict type pilot: Pyright 1.1.405 checks
`core/timefmt.py`, and TypeScript 5.9.3 checks `ui/timecurve.js` through JSDoc.
These scopes are declared in `pyrightconfig.json` and
`jsconfig.verification.json`; this does not establish typing of their callers
or the rest of the application. The Python checker uses the project's .venv,
avoiding accidental analysis against the machine's different Python version.
Type tools install through the same npm lockfile; `python tools/verify_types.py`
runs the pilot directly. Additional ecosystem adapters are separate adoption
work. Full verification retains the existing integration
suite. No live recorder or server is restarted by these commands.
The full entry point refuses `UILAB_SKIP=1`, missing uilab, and missing
Playwright Chromium before invoking that runner, which otherwise allows some
rendered tests to skip. Other legitimate platform-specific skips remain visible
in the test report; this pilot does not claim that every skip is a failure.
The full wrapper removes inherited `PYTEST_ADDOPTS`, `PYTEST_PLUGINS`, and
`PYTEST_DISABLE_PLUGIN_AUTOLOAD` before both dependency probing and execution,
so shell settings cannot silently select a partial suite or disable its plugins.
CPU/resource configuration remains controlled by the existing runner.
The full integration check has a 3600-second budget including shared admission:
a measured preceding task held the allocation for about 17 minutes during this
pilot. This avoids cancelling a newly admitted suite near its completion merely
because it spent its budget queued. Quick-check timeouts remain unchanged, and
the longer budget neither bypasses admission nor skips required tests.
The integration command caps workers at four within the shared resource budget.
During onboarding integration, eight workers produced seven leaderboard-loading
timeouts; the same failing viewport passed unchanged in isolation. The full
wrapper accepts only a worker ceiling and its identity probe, never pytest
selection arguments. Every test and responsive viewport still runs.
