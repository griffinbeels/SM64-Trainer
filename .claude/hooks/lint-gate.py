#!/usr/bin/env python3
"""PreToolUse(Bash|PowerShell) guard — the AGENT-MAINTAINABILITY GATE.

WHY THIS EXISTS (the evidence, so a future editor doesn't relax it by feel):
This project is written entirely by agents, and each round builds on the last
round's code. A 2026 controlled study (CodeThread, four frontier agents) found
agents resolve tasks 13.1% less often when building on agent-authored code than
on human-authored code — and that classical complexity metrics did NOT explain
the gap. The one code-level predictor was drift in input validation and error
handling. A measurement of this repo on 2026-08-28 agreed: cyclomatic
complexity added +0.16 to file size as a predictor of where fixes land (size
alone: +0.93), while 135 places in src/ lose or swallow an error outright.

So the rule sets are curated for one thing — what makes the NEXT agent fail —
not for style. What each rule is doing there: docs/agent-maintainability.md.

WHY DIFF-SCOPED, and why that is the whole design:
There are ~312 Python and ~93 JS findings in the tree today. A gate that
reports all of them on every commit is noise on round one and ignored by round
two, which is the documented way lint gates die. This one only ever blocks on
lines the commit ITSELF adds, so the backlog costs nothing and new work cannot
add to it. There is no baseline file to drift, and no bankruptcy to declare.

WHICH CONTENT GETS LINTED, and why it is not simply "the index":
Line numbers only mean something against the revision they were diffed from, so
each path is read from wherever the commit will actually take it. Something
already staged is read with `git show :<path>`; something a `git add` CHAINED
IN THE SAME COMMAND will stage is read from the worktree, because PreToolUse
fires before that add runs. Missing the second case made this gate allow a file
tripping three rules for its whole first hour (2026-08-28) — and `&&`-chaining
add with commit is the form git-staging-guard.py actively steers toward, so it
is the normal case here rather than an edge one. Either way the content lands in
a mirror tree with its relative path intact, so ruff's per-file-ignores
(`tests/**`) and eslint's `ignores` still mean what they say.

Reading the whole worktree unconditionally would be wrong in the other
direction: this checkout is shared by concurrent sessions, and a neighbour's
unstaged edit is not this commit's problem.

CONTRACT
- stdin: the PreToolUse JSON payload ({"tool_input": {"command": "..."}, ...}).
- exit 0  -> allow. The default for everything that is not a `git commit`, and
             for every failure mode below.
- exit 2  -> block; stderr is shown to the model so it can fix and retry.
- ESCAPE: put `lint-gate-ok` anywhere in the command to bypass deliberately.

FAILS OPEN on every unexpected condition — no ruff, no node, a linter that
crashes or times out, an unparseable payload, a non-repo cwd. A guard that can
brick `git commit` in a shared checkout is worse than the mistake it prevents.
That is also why the JS half is skipped rather than blocking when eslint has to
be fetched: the first `npx` resolve can take tens of seconds, and a commit that
appears to hang is how a guard gets deleted.

FALSIFIABILITY: if this blocks something legitimate, the rule id and the line
are printed — fix the rule's entry in pyproject.toml / eslint.config.mjs, or
add a `# noqa: <code>` with a reason. If it MISSES a class of defect that then
cost a round, that class belongs in docs/agent-maintainability.md's re-evaluation
list. Pinned by tests/test_lint_gate_hook.py — including the two mutations that
proved those pins have teeth.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ESCAPE = "lint-gate-ok"
PY_SUFFIXES = {".py"}
JS_SUFFIXES = {".js", ".mjs"}
# The first `npx eslint` on a cold cache downloads ~50MB. Blocking a commit for
# that reads as a hang, so the JS half is best-effort with a hard ceiling.
ESLINT_TIMEOUT = 45
RUFF_TIMEOUT = 60


def _exe(name: str) -> str | None:
    """Resolve a launcher to its full path.

    Windows CreateProcess appends `.exe` for a bare name but NOT `.cmd`, and
    Python 3.12 will not run a `.cmd` without `shell=True`. So `uvx` worked from
    a bare name while `npx` silently raised — the JavaScript half of this gate
    was dead on arrival until this was measured (2026-08-28). Resolving both
    through `which` removes the asymmetry instead of special-casing one.
    """
    return shutil.which(name)


def _run(args, cwd=None, timeout=30):
    """Run a command, returning (returncode, stdout) or None if it cannot run."""
    resolved = _exe(args[0])
    if resolved is None:
        return None
    try:
        proc = subprocess.run(  # noqa: PLW1510 — the caller reads returncode itself; a
            # linter exiting non-zero because it FOUND something is the normal case here.
            [resolved, *args[1:]], cwd=cwd, capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.returncode, proc.stdout


def wants_commit(command: str) -> bool:
    """True for a real `git commit`, ignoring the word inside quotes/heredocs."""
    bare = re.sub(r"<<-?\s*'?(\w+)'?.*?^\1$", "", command, flags=re.S | re.M)
    bare = re.sub(r"\"[^\"]*\"|'[^']*'", "", bare)
    return bool(re.search(r"\bgit\s+commit\b", bare))


def _parse_diff(text: str) -> dict[str, set[int]]:
    """Pull the added-line numbers out of a `--unified=0` diff.

    `--unified=0` so a hunk header names exactly the added run, with no context
    lines to widen it. A pure deletion has a zero-length new side and drops out
    — you cannot lint a line that no longer exists.
    """
    per_file: dict[str, set[int]] = {}
    current = None
    for line in text.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:].strip()
            per_file.setdefault(current, set())
        elif line.startswith("@@") and current is not None:
            match = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if not match:
                continue
            start = int(match.group(1))
            count = 1 if match.group(2) is None else int(match.group(2))
            per_file[current].update(range(start, start + count))
    return {path: lines for path, lines in per_file.items() if lines}


def pending_adds(command: str) -> tuple[list[str], bool]:
    """Paths a `git add` in THIS command will stage, and whether it stages all.

    THE HOLE THIS CLOSES, measured 2026-08-28: PreToolUse fires BEFORE the
    command runs, so for `git add x.py && git commit -m msg` — the form
    git-staging-guard.py actively pushes you toward, because `;` does not
    propagate exit status — the index at hook time does not yet contain the
    change being committed. The gate read an empty index and allowed a file
    that trips three rules. It looked like it was working for its whole first
    hour, which is the failure mode a fail-open guard is most prone to.
    """
    bare = re.sub(r"<<-?\s*'?(\w+)'?.*?^\1$", "", command, flags=re.S | re.M)
    bare = re.sub(r"\"[^\"]*\"|'[^']*'", "", bare)
    if re.search(r"\bgit\s+commit\b[^\n|&;]*?(\s-(?!-)[A-Za-z]*a[A-Za-z]*\b|\s--all\b)", bare):
        return [], True  # `commit -a` stages every tracked modification
    paths: list[str] = []
    for segment in re.finditer(r"\bgit\s+add\b([^\n|&;]*)", bare):
        for token in segment.group(1).split():
            if token in {"-A", "--all", "-a", "."} or token.startswith("-"):
                if token in {"-A", "--all", "-a", "."}:
                    return [], True
                continue
            paths.append(token.replace("\\", "/"))
    return paths, False


def added_lines(repo: Path, command: str = "") -> dict[str, tuple[set[int], str]]:
    """Map each path this commit will change to (added line numbers, source).

    `source` is where the content to lint lives — "index" for something already
    staged, "worktree" for something a chained `git add` is about to stage. The
    two cannot be merged: line numbers only mean something against the revision
    they were diffed from.
    """
    out: dict[str, tuple[set[int], str]] = {}
    staged = _run(["git", "diff", "--cached", "--unified=0", "--no-color",
                   "--diff-filter=ACM"], cwd=repo, timeout=30)
    if staged is not None and staged[0] == 0:
        for path, lines in _parse_diff(staged[1]).items():
            out[path] = (lines, "index")

    paths, stages_everything = pending_adds(command)
    if not paths and not stages_everything:
        return out

    args = ["git", "diff", "HEAD", "--unified=0", "--no-color", "--diff-filter=ACM"]
    if not stages_everything:
        args += ["--", *paths]
    pending = _run(args, cwd=repo, timeout=30)
    if pending is None or pending[0] != 0:
        return out
    for path, lines in _parse_diff(pending[1]).items():
        # HEAD..worktree supersedes HEAD..index for the same file: it is the
        # content the commit will actually carry.
        out[path] = (lines, "worktree")

    if not stages_everything:
        # An untracked file has no HEAD..worktree diff, so `git diff` says
        # nothing about it — ask git which named paths it would newly add.
        listing = _run(["git", "ls-files", "--others", "--exclude-standard",
                        "--", *paths], cwd=repo, timeout=30)
        if listing is not None and listing[0] == 0:
            for rel in listing[1].splitlines():
                rel = rel.strip().replace("\\", "/")
                if not rel:
                    continue
                body = (repo / rel).read_text(encoding="utf-8", errors="replace") \
                    if (repo / rel).is_file() else ""
                out[rel] = (set(range(1, body.count("\n") + 2)), "worktree")
    return out


def materialize(repo: Path, entries, dest: Path) -> list[str]:
    """Copy the content that will be COMMITTED into `dest`, path layout intact.

    `entries` is (rel, source) — "index" reads the staged blob, "worktree" the
    file on disk, matching whichever revision that path's line numbers came
    from. Preserving the relative layout is what keeps ruff's per-file-ignores
    (`tests/**`) and eslint's `ignores` meaning the same thing in the mirror.
    """
    written = []
    for rel, source in entries:
        if source == "worktree":
            origin = repo / rel
            if not origin.is_file():
                continue
            body = origin.read_text(encoding="utf-8", errors="replace")
        else:
            result = _run(["git", "show", f":{rel}"], cwd=repo, timeout=20)
            if result is None or result[0] != 0:
                continue  # deleted, unmerged, or binary — nothing to lint
            body = result[1]
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8", errors="replace")
        written.append(rel)
    return written


def ruff_findings(repo: Path, mirror: Path, rels) -> list[tuple]:
    if not rels:
        return []
    config = repo / "pyproject.toml"
    if not config.exists():
        return []
    result = _run(
        ["uvx", "ruff", "check", "--config", str(config),
         "--output-format", "json", "--no-cache", "--force-exclude", *rels],
        cwd=mirror, timeout=RUFF_TIMEOUT)
    if result is None:
        return []
    try:
        items = json.loads(result[1] or "[]")
    except json.JSONDecodeError:
        return []
    out = []
    for item in items:
        rel = str(Path(item["filename"]).relative_to(mirror)).replace("\\", "/") \
            if Path(item["filename"]).is_absolute() else item["filename"]
        line = (item.get("location") or {}).get("row")
        if line:
            out.append((rel.replace("\\", "/"), line, item.get("code") or "?",
                        item.get("message", "")))
    return out


def eslint_findings(repo: Path, mirror: Path, rels) -> list[tuple]:
    if not rels:
        return []
    config = repo / "eslint.config.mjs"
    if not config.exists():
        return []
    result = _run(
        ["npx", "--yes", "--offline", "eslint@9", "--no-config-lookup",
         "-c", str(config), "--format", "json", *rels],
        cwd=mirror, timeout=ESLINT_TIMEOUT)
    if result is None:
        # Cold npx cache (or no network): skip the JS half rather than stall a
        # commit. `tools/lint_changed.py --warm` primes it deliberately.
        return []
    try:
        files = json.loads(result[1] or "[]")
    except json.JSONDecodeError:
        return []
    out = []
    for entry in files:
        path = Path(entry.get("filePath", ""))
        try:
            rel = str(path.relative_to(mirror))
        except ValueError:
            rel = entry.get("filePath", "")
        for message in entry.get("messages", []):
            line = message.get("line")
            if line:
                out.append((rel.replace("\\", "/"), line,
                            message.get("ruleId") or "parse-error",
                            message.get("message", "")))
    return out


def gate(repo: Path, command: str = "") -> list[tuple]:
    """Return only the findings that sit on lines this commit adds.

    `command` matters: a `git add` chained ahead of the commit has not run yet
    when this fires, so the paths it names are read from the worktree instead of
    the index. See `pending_adds`.
    """
    touched = added_lines(repo, command)
    if not touched:
        return []
    py = [(p, src) for p, (_, src) in touched.items() if Path(p).suffix in PY_SUFFIXES]
    js = [(p, src) for p, (_, src) in touched.items() if Path(p).suffix in JS_SUFFIXES]
    if not py and not js:
        return []
    with tempfile.TemporaryDirectory(prefix="lint-gate-") as tmp:
        mirror = Path(tmp)
        py_rels = materialize(repo, py, mirror)
        js_rels = materialize(repo, js, mirror)
        found = (ruff_findings(repo, mirror, py_rels)
                 + eslint_findings(repo, mirror, js_rels))
    return sorted(f for f in found if f[1] in touched.get(f[0], (set(), ""))[0])


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001 — the fail-open contract in this docstring.
        return 0       # A guard that dies on a payload shape brings `git commit` with it.

    command = ((payload.get("tool_input") or {}).get("command") or "")
    if ESCAPE in command or not wants_commit(command):
        return 0

    repo_dir = payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or "."
    result = _run(["git", "rev-parse", "--show-toplevel"], cwd=repo_dir, timeout=15)
    if result is None or result[0] != 0:
        return 0
    repo = Path(result[1].strip())

    try:
        findings = gate(repo, command)
    except Exception:  # noqa: BLE001 — same fail-open contract: a linter crash,
        return 0       # a weird diff, an unreadable blob must never block a commit.
    if not findings:
        return 0

    lines = [
        "BLOCKED: the agent-maintainability gate found issues on lines this "
        "commit ADDS.\n",
        "  These are not style notes. Every rule here is one that makes the "
        "next agent fail on this code — see docs/agent-maintainability.md.\n\n",
    ]
    for path, line, code, message in findings[:25]:
        lines.append(f"  {path}:{line}  [{code}] {message}\n")
    if len(findings) > 25:
        lines.append(f"  … and {len(findings) - 25} more\n")
    lines.append(
        "\n  Fix them, restage, and commit again.\n"
        "  Reproduce locally:  uv run python tools/lint_changed.py\n"
        "  If a rule is genuinely wrong for this code, change its entry in "
        "pyproject.toml / eslint.config.mjs and say why in the commit — not a "
        "blanket noqa.\n"
        f"  To bypass this once, include `{ESCAPE}` in the command.\n")
    sys.stderr.write("".join(lines))
    return 2


if __name__ == "__main__":
    sys.exit(main())
