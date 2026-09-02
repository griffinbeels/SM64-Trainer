#!/usr/bin/env python3
"""Run the agent-maintainability gate by hand — the same code the hook runs.

The gate itself lives in `.claude/hooks/lint-gate.py` and this imports it
rather than restating it, because a second copy of the diff-scoping logic is a
second answer to "did this commit add a finding". What the rules are for and
how to re-evaluate them: `docs/agent-maintainability.md`.

    uv run python tools/lint_changed.py            # what the gate would block
    uv run python tools/lint_changed.py --all      # the whole-tree backlog
    uv run python tools/lint_changed.py --warm     # prime the npx eslint cache

`--all` is the one to read during a re-evaluation pass: it is the standing debt
the diff-scoped gate deliberately does not block on, and a family that keeps
growing there is a rule that is not reaching anybody.

`--warm` matters because the gate SKIPS the JavaScript half rather than stall a
commit while npx fetches eslint. Run it once per machine (and after clearing
the npx cache) or JS findings quietly never appear.
"""
import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GATE = None


def load_gate():
    """Import the hook module — it owns the diff-scoping, this does not."""
    global GATE
    if GATE is None:
        path = REPO / ".claude" / "hooks" / "lint-gate.py"
        spec = importlib.util.spec_from_file_location("lint_gate", path)
        if spec is None or spec.loader is None:
            raise SystemExit(f"cannot load the gate at {path}")
        GATE = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(GATE)
    return GATE


def launcher(name: str) -> str | None:
    """Resolve `npx`/`uvx` the way the gate does — never from a bare name.

    Windows will not start a `.cmd` from a bare name, and this file learned that
    the second time (2026-08-28): the gate was fixed and its companion tool was
    not, so `--all` and `--warm` both died with WinError 2 while the gate itself
    was fine. Borrowing the gate's resolver is the point — a second copy is a
    second chance to be wrong in a different place.
    """
    return load_gate()._exe(name)


def warm() -> int:
    npx = launcher("npx")
    if npx is None:
        print("npx not found — the JavaScript half of the gate cannot run here.")
        return 1
    print("priming the npx cache with eslint@9 (one-time, ~50MB) …")
    proc = subprocess.run([npx, "--yes", "eslint@9", "--version"],
                          capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        print(proc.stderr.strip() or "failed")
        return 1
    print(f"ready: eslint {proc.stdout.strip()}")
    return 0


def whole_tree() -> int:
    """The standing backlog, by rule, for both languages."""
    status = 0
    print("=== Python (ruff, config in pyproject.toml) ===")
    uvx = launcher("uvx")
    if uvx is None:
        print("uvx not found — skipped")
    else:
        proc = subprocess.run([uvx, "ruff", "check", "src", "tests", "tools",
                               "--statistics"], cwd=REPO, capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
        print(proc.stdout.strip() or "clean")

    print("\n=== JavaScript (eslint, config in eslint.config.mjs) ===")
    npx = launcher("npx")
    if npx is None:
        print("npx not found — skipped")
        return status
    proc = subprocess.run(
        [npx, "--yes", "eslint@9", "--no-config-lookup", "-c",
         str(REPO / "eslint.config.mjs"), "--format", "json",
         "src/sm64_events/ui/**/*.js"],
        cwd=REPO, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=300)
    try:
        files = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        print(proc.stderr.strip() or "eslint produced no JSON")
        return status
    counts: dict[str, int] = {}
    for entry in files:
        for message in entry.get("messages", []):
            rule = message.get("ruleId") or "parse-error"
            counts[rule] = counts.get(rule, 0) + 1
    if not counts:
        print("clean")
        return status
    for rule, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"{count:>6}\t{rule}")
    print(f"Found {sum(counts.values())} findings in {len(files)} files.")
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true",
                        help="report the whole tree, not just staged changes")
    parser.add_argument("--warm", action="store_true",
                        help="download eslint into the npx cache, then exit")
    args = parser.parse_args()

    if args.warm:
        return warm()
    if args.all:
        return whole_tree()

    findings = load_gate().gate(REPO)
    if not findings:
        print("clean — nothing the gate would block on the staged lines.")
        return 0
    for path, line, code, message in findings:
        print(f"{path}:{line}  [{code}] {message}")
    print(f"\n{len(findings)} finding(s) on lines the staged change adds.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
