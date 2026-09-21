# Agent-maintainability — the intention behind the lint rules

This project is written entirely by agents, and every round builds on the last
round's code. So the question these rules answer is narrow and specific:

> **Does this change make the NEXT agent more likely to fail?**

Not "is this idiomatic", not "is this pretty", not "is this simple". Those are
style questions and they belong to review, not to a blocking gate. Every rule in
`pyproject.toml` (`[tool.ruff.lint]`) and `eslint.config.mjs` is there because it
has a failure story. A rule with no failure story is how a gate turns into a
style preset nobody reads, and the first thing to do with one is delete it.

`python tools/verify_lint.py` enforces them: whole changed files against a
reviewed baseline of older findings, in the `quick` verification lane
([local verification](local-verification.md)). Until 2026-09-21 a fail-open
commit hook (`.claude/hooks/lint-gate.py`, with `tools/lint_changed.py`) ran the
same rules on added lines only; it duplicated that check and was removed. Its
design notes are in git history.

---

## Why these rules and not the obvious ones

Measured on this repo, 2026-08-28, before any of this existed.

**Cyclomatic complexity is not the lever, despite being the famous one.**
Across 154 source files, file size predicts where fixes land at r = +0.93.
Controlling for size, peak cyclomatic complexity adds **+0.16**, cognitive
complexity +0.09, and nesting depth **−0.11**. The two files ranked 4th and 5th
by fix-commit count score 17 and **5** on peak complexity — a complexity gate
would have called both pristine. Complexity rules are in the set, but as a
ceiling on new monsters, not as the point.

**The error-handling family is the lever.** A 2026 controlled study
([CodeThread](https://arxiv.org/abs/2606.21804), four frontier agents across
four benchmarks) found agents resolve tasks up to 13.1% less often when building
on agent-authored code — and that cyclomatic complexity, cognitive complexity,
Halstead volume and logical LOC all showed *near-identical distributions*
between the cases where agents succeeded and failed. The one code-level
predictor was drift in **input validation and error handling**. This repo has
135 places in `src/` that lose or swallow an error outright.

**"Write less code" beats "branch less".** The
[complexity-feedback study](https://arxiv.org/abs/2505.23953) that popularised
the idea does work — pass@1 rose 35.71% against a 12.5% baseline — but when they
ranked all 53 metrics by contribution, the ones carrying the signal were
Halstead length, vocabulary, effort and lines of code. Cyclomatic complexity did
not make the list.

**Slop is not the same as complexity.** A
[500k-sample comparison](https://arxiv.org/abs/2508.21634) found AI code is
already *less* structurally complex than human code (Java: 1.83–2.35 against
3.48), with 6.75 fewer lines and 63.74 fewer tokens per function. The AI failure
modes were unused constructs, hardcoded debugging, and vulnerabilities.

**The most-cited alternative is only weakly validated.** Cognitive complexity is
the one understandability metric with real human data behind it
([427 snippets, ~24,000 evaluations](https://arxiv.org/abs/2007.12520),
r = 0.54 with comprehension time) — but its correlation with comprehension
*correctness* was −0.13. Worth knowing before treating any number as truth.

### The families, and what each is doing here

| Family | Rules | The failure it prevents |
|---|---|---|
| Silent failure | `E722`, `BLE`, `S110`, `S112`, `TRY400`, `TRY401`, `LOG`, `PLW1510`; JS `no-empty`, `no-fallthrough`, `array-callback-return`, `no-promise-executor-return` | Something failed and nothing said so. The next agent reads green and concludes the code works. Direct evidence above. |
| Lost causality | `B904` (65 sites) | Re-raising without `from` discards the original traceback — the answer to "why" is destroyed at exactly the moment it becomes needed. |
| The value is not what the code says | `F`, `PLE`, `B`, `E711`–`E721`, `DTZ`, `ASYNC`; JS `eqeqeq`, `no-template-curly-in-string`, `no-self-compare`, `no-constant-binary-expression`, `no-unsafe-optional-chaining` | Silent truncation (`zip` without `strict`), naive datetimes against domain rule 7, `'${x}'` rendered verbatim in a UI written in template literals. |
| Dead weight | `F401`, `F841`, `RUF059`; JS `no-unused-vars` | The top AI failure mode in the 500k study. Every unread name is something the next agent has to decide about. |
| Size and shape | `C901` (15), `PLR0915` (50 statements), `PLR0912` (18); JS `complexity` 15, `max-lines-per-function` 100, `max-depth` 4 | A ceiling on new monsters, not a verdict on old ones. |

**Calibration.** The median function here is 10 lines and the 95th percentile is
53. `max-statements = 50` is roughly a 75-line function: it flags 16 of 1,592
source functions, 5 in `tools/`, and **0** in `tests/`. That last number is why
test files needed no length exemption.

### Test files

Test *style* is deliberately unpoliced (his call, 2026-08-28) — `S101`,
magic values, private access and fixture shadowing are all off. Everything in
the silent-failure family still applies, because a test that swallows an error
is a test that passes while proving nothing, and that is agent-maintainability
in its purest form. `test_test_files_keep_the_silent_failure_rules` pins both
halves of that split.

### What was deliberately left out, so nobody re-adds it

- **`--select ALL`** — 9,310 findings, nearly all `assert` in tests.
- **`eqeqeq` at its default** — 405 findings, 334 of them the deliberate
  `== null` idiom for "null or undefined", the rest inside vendored minified
  Preact. Both had to be exempted before the rule meant anything. This is the
  clearest example of the general law: **the first run of any linter lies, and
  the curation is the deliverable.**
- **Dead-code scanning (`vulture`)** — 201 findings at 60% confidence, only 4
  survive at 80%. The address registry and behaviour tables look exactly like
  dead code to it, so the whitelist would cost more than the findings are worth.
- **Auto-formatting (`ruff format`)** — rewrites the whole tree once, and this
  repo has a documented line-ending hazard. If ever, its own commit, deliberately.
- **`E501` line length, `Q000` quotes, `D` docstrings, `ANN` annotations,
  `TRY003`, `EM101/102`, `PLC0415`, `T201`** — style, all of it.

---

## Re-evaluating this — the standing job

The point of writing the intention down is that the field moves. **This is not a
"set it and forget it" artifact.** Run a pass when any of these happens:

- a round loses time to a defect class this gate did not catch
- a new model generation lands and its failure modes are visibly different
- new research on agent-maintainability or agent code quality appears
- the `--all` backlog for one family is *growing*, which means that rule is
  reaching nobody

### The pass

1. **Read the backlog by family.** `uv run python tools/verify_lint.py --all`.
   A family growing while the gate is on means either the rule never fires at
   commit time or it is routinely bypassed — find out which.
2. **Ask what the last few rounds actually cost.** Name the defects that reached
   the human. For each, ask whether any *mechanical* check could have caught it.
   Most will be answerable only by a guard test, not a linter — that is the
   expected answer and it is not a failure of this document (see the limit
   below).
3. **Re-read the primary sources, and look for newer ones.** The evidence above
   is dated; re-derive rather than trusting the summary. Read the paper itself,
   never a model's summary of it — every number quoted above came from the full
   text, and the one that mattered most (that complexity metrics do NOT explain
   the agent-maintainability gap) is the kind of finding a summary volunteers
   last.
4. **Change rules only with a failure story.** Adding a rule means naming the
   defect it would have caught. Removing one means saying why its story stopped
   being true.
5. **Re-measure the calibration** if the codebase has grown a lot. The
   percentile numbers above are what make the thresholds defensible; stale ones
   make them arbitrary.
6. **Prove any new rule by mutation** — write the violation, watch the gate go
   red, revert. A guard nobody has seen fail is green forever.

### The honest limit

Every bug class that has actually cost this project a round is invisible to
every linter measured: three surfaces each deriving a star icon their own way,
an endpoint the UI never calls, a fixture in the wrong state reporting a clean
page, the rank ladder drifting between its Python and JavaScript copies. Those
are covered by guard tests — `tests/test_single_source.py`,
`tests/test_cross_language_parity.py`,
`tests/test_fixture_reaches_the_real_page.py` — and those guards are what has
caught real bugs here.

**A linter is a floor under new code. It is not the mechanism that saves the
expensive rounds.** When a re-evaluation pass finds a defect class worth
catching, the right home is usually a guard test naming the ingredients, not a
new lint rule. Adding rules is the easy move and mostly the wrong one.
