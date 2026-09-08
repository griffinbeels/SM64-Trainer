# SM64 Trainer — development guide

Codex and Claude Code share this project map. The human runs the emulator and
verifies live behavior. Read the relevant tests before editing: they are the
executable spec. Use [the glossary](docs/glossary.md) for project names.

## What this is

A Python server reads Super Mario 64 Usamune practice-ROM memory from Project64
1.6 on Windows through `ReadProcessMemory`, detects events, records attempts,
and broadcasts JSON over WebSocket. Stack: Python 3.12+ via uv, FastAPI,
uvicorn, pymem, pytest. Never write to emulator memory. Layout support and its
live evidence belong to the version-sync rules, not assumptions about a ROM.

## Start here

**Read [the canonical rule index](docs/rule-index.md) before editing.** Open
every rule matching the touched paths, including overlapping zones and chain
rules. Both readers use this index; Claude Code also loads scoped rules
automatically. Regenerate it with `python tools/build_rule_index.py` after
changing rule metadata. Keep feature status in the feature's current handoff,
not in this map.

| Need | Command / reference |
| --- | --- |
| Dependencies | `uv sync` |
| Focused check while editing | `uv run python tools/run_tests.py tests/test_<module>.py` |
| Python coverage selection | `uv run python tools/run_tests.py --changed` |
| Full integration gate | `uv run python tools/run_tests.py` |
| Test scope, shared machine budget, evidence reuse | [docs/testing.md](docs/testing.md) |
| Run the app when authorized | `uv run python -m sm64_events.main` from repo root; data is cwd-relative |
| Read back live play and the rendered UI | `uv run python tools/what_happened.py`; `--list` names all journals |
| New memory address live gate | `uv run python tools/verify_addresses.py` with PJ64 + ROM |
| Per-ROM verification | `uv run python tools/sync_version.py --version us`; [runbook](docs/version-sync.md) |
| Import correctness | `uv run python tools/scorecard_parity.py`; successive runners must produce `+0.00` on every corresponding Scorecard tile |
| Inspect one UI surface at supported widths | `uv run python tools/contact_sheet.py <selector>`; read UI rules for state-specific fixtures |
| Maintainability gate | `uv run python tools/lint_changed.py`; [gate rationale](docs/agent-maintainability.md) |
| API consumer | [docs/api.md](docs/api.md): `GET /state`, `GET /health`, `/ws/events` |
| Cross-cutting domain evidence | [docs/architecture.md](docs/architecture.md) |
| Older probe commands, incidents and rejected approaches | [archived guide](docs/history/agent-guide-2026-09-05.md), historical evidence only |
| Claude Design component publishing | `.design-sync/components.mjs` is the registry; [workflow](.design-sync/NOTES.md) |

## Protect live play

- Never restart the live server. Griffin restarts it when ready; it may be
  serving another branch's work. Report when changes need a restart and leave
  it to him. This does not prevent starting/closing isolated UI test fixtures.
- When asking Griffin to run or restart the server, include a clickable absolute
  link to `run-test-server.bat` in the checkout containing the changes: that
  specific worktree's launcher for worktree changes, or the primary checkout's
  launcher for changes on main. Verify the linked file exists.
- Do not start `python -m sm64_events.main` for UI checks while the user may
  be playing. One recorder operates machine-wide (`core/recorder_lock.py`);
  one server owns each database (`storage/instance_lock.py`), with second
  instances broadcast-only. Use the UI fixture for checks.
- Discover the active listener and confirm `GET /health`; do not infer it
  from a guessed port. `core/paths.py::server_port()` owns `SM64_PORT`, with
  8064 frozen and 8065 source defaults; `run-test-server.bat` uses 8066.
- Start live diagnosis with `tools/what_happened.py`. The freshest journal
  identifies current recording; a reported older row may belong to a different
  checkout or installed app. Search that row by value across the listed journals.
- Journal events describe the game; `data/ui_log.jsonl` describes the rendered
  screen. Inspect both for visual reports. Investigate missing UI telemetry,
  including stale client code; never write derived UI state back as game events.
- Clean up only processes this task owns. End every server or harness started
  for this task in the same session, and verify its listener/children exited.
- Session-start process inspection is report-only (`tools/dev_cleanup.py`).
  A PID without a listener may be a live server's launcher or a recorder;
  socket absence and command-line matching never authorize termination.

## Domain contracts

1. A new absolute address gets one row in `memory/layout.py` with US evidence,
   JP `None` until verified, and its `address.<field>` gate in
   `sync/address_gates.py`. Version-independent offsets belong in
   `memory/addresses.py`. Mark new reads `VERIFY` until the human live gate passes;
   retain the poller's implausible-read refusal.
2. Star grabs fire on re-collection through action edges, never save-flag diffs.
   IGT comes through `detectors/igt_clock.py` from Usamune expansion RAM
   (result, counter, reconstruction), never the HUD timer, object-pool address,
   or a `global_timer` delta. Preserve measured calibration constants and evidence.
3. Detectors receive consecutive `(prev, curr)` pairs, keep bounded state, and
   self-heal when `global_timer` jumps backward. Store UTC timestamps; game
   frames at 30 fps are the primary clock.
4. Browser and desktop share all user-facing behavior through `ui/` and server;
   `desktop/` adds native chrome. Stars and segments share practice workflows;
   ship both or document the reason for an asymmetry. The shared log, analysis,
   drawer and kind-dispatched endpoints are pinned by UI section-parity tests.
5. Seeded route steps stay in completion-event order or runs silently stall.
   Read the tracking-storage rule before changing them.
6. One module owns a shared derivation. Callers pass identity rather than
   independently assembling ingredients; single-source tests forbid competing
   paths. When Python and JS must both compute a value, compare the real
   implementations with cross-language parity tests, not a third restatement.
7. Tests store the reference configuration they probe. Do not pin the contents
   of a shipped tuning default or preference; check its coherence and valid range.

## Work ownership and verification

Use one isolated worktree per writing task; keep the primary checkout on main.
Coordinate these shared contracts before parallel edits: `core/events.py`,
`core/snapshot.py`, `memory/addresses.py`, `memory/layout.py`,
`tracking/projection.py`, `main.py`. Keep `storage/`, `stats/`, and `tracking/`
together when changing their shared Attempt contract. Land prerequisite contract
changes before dependent work. Integrate with `--no-ff`.

Choose the smallest meaningful check while editing. The full runner is the
integration gate and owns the shared resource budget and child cleanup.
After appropriate checks pass, repeat or broaden only for changed inputs,
failures, or unresolved concerns; the full suite already includes responsive
checks. Preserve actual native exit codes; in PowerShell never pipe test output
into `Select-Object` or use `2>&1` on native commands.

For UI changes, render and inspect the named surface with representative data
at its supported widths (850px minimum, any height). Confirm the fixture reaches
the changed state, count repeated elements, and use realistically long values.
Read UI rules for `@container` layout gates, fixture-reach and surface contracts.
Behavior judged by feel gets a tuning inspector and human verification.
Prove new source-scan or layout guards by restoring the violation, seeing the
failure, then reverting it. Tests alone do not establish visual correctness.

## Definition of done for integration

- Full `tools/run_tests.py` gate passes for the integrated tree. An identical
  tree reuses that evidence; record revision, command, result and skips.
- Relevant behavior tests and, for visible changes, rendered evidence cover
  the change. New memory reads have human live verification.
- Update the glossary for changed domain nouns, chain rules for moved value
  hops, the rule index for changed routes, architecture for cross-cutting facts,
  API docs for endpoints, and README for consumer-facing changes.
- Keep one authoritative home for each fact and link it elsewhere. Tracked
  references must resolve in a fresh clone; private working notes are not public
  evidence. Preserve incident detail on demand rather than appending it here.
- Use the shared harness `create-artifacts` skill for learning capture; this
  project has no separate harvesting workflow. Project-specific facts belong
  in the applicable rule, test, module docstring, or architecture document.
- Review the full diff, commit with the reason for the change, and check recent
  user messages for outstanding requests before reporting completion.

Project hooks live in `.claude/hooks/`. Edit `.claude/settings.json`, then run
`python ~/.claude/harness/install.py --repo .` to regenerate `.codex/hooks.json`;
never maintain reader-specific script copies. Parity tests check the configured
scripts and generated file; live hook execution is a separate harness check.
