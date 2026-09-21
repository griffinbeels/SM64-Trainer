# Local verification

`python tools/verify.py quick` invokes the shared harness verifier using this
checkout's `.verification.toml`. `full` includes quick and the merge check
([testing](testing.md)); the browser run happens on GitHub, not here.
`fix --files <owned paths>` permits
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
version inside the draft loop. Neither lane here needs a browser binary or
the shared UI harness; running browser files locally does ([testing](testing.md)).

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

The replay scrub change received a separate review of the existing `useTracker`
allowance: extracting serial pause polling reduces its measured length from 297
to 290 lines and leaves its other logic unchanged. Only that fingerprint was
replaced; the older, larger allowance was removed. The baseline records this
reviewed decrease and does not permit new findings or a return to 297 lines.

`--fix --files ...` only permits Ruff's F401 unused-import removal. Review imports
used exclusively for registration side effects before requesting that fix; mark
intentional imports explicitly. No ESLint automatic fixes are enabled in this
pilot. Files outside the checkout and implicit whole-tree fixes are refused.

The draft lane adds no test-writing requirement. It enforces the existing
curated lint rules plus a narrow strict type pilot: Pyright 1.1.405 checks
`core/timefmt.py`, and TypeScript 5.9.3 checks `ui/timecurve.js` through JSDoc.
These scopes are declared in `pyrightconfig.json` and
`jsconfig.verification.json`; this does not establish typing of their callers
or the rest of the application. The Python checker uses the project's .venv,
avoiding accidental analysis against the machine's different Python version.
Type tools install through the same npm lockfile; `python tools/verify_types.py`
runs the pilot directly. Additional ecosystem adapters are separate adoption
work. No live recorder or server is restarted by these commands.

The `full` lane's `merge-check` runs `tools/verify_full.py`, which refuses only
what the merge check needs: the Vitest bridge (`npm ci --prefix tests/frontend
--ignore-scripts`) and Node on PATH. It no longer asks for uilab or Chromium,
because the merge check starts neither. It removes inherited `PYTEST_ADDOPTS`,
`PYTEST_PLUGINS` and `PYTEST_DISABLE_PLUGIN_AUTOLOAD` so shell settings cannot
select a partial suite, accepts only a worker ceiling and its identity probe,
and leaves the worker count to the runner's budget (8, or 4 with OBS open).
It stops the run 30 minutes after admission; the lane's 7200-second timeout is
only an outer net, so time queued behind two other merge checks never cancels
a run.
