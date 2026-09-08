"""Offline lint gate: 0 passes, 1 findings, 2 unavailable. See docs/local-verification.md."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from sm64_events.core.childproc import quiet_spawn_kwargs  # noqa: E402

RUFF = "0.16.6"
ESLINT = "9.39.5"
BASELINE = ROOT / "tools/verification/lint-baseline.json"
SUFFIXES = {".py", ".js", ".mjs"}


class Unavailable(RuntimeError):
    """Required evidence could not be obtained."""


def run(argv: list[str], root: Path = ROOT) -> subprocess.CompletedProcess:
    executable = shutil.which(argv[0])
    if not executable:
        raise Unavailable(f"missing executable: {argv[0]}")
    try:
        return subprocess.run([executable, *argv[1:]], cwd=root, check=False,
                              capture_output=True, text=True, encoding="utf-8",
                              timeout=60, **quiet_spawn_kwargs())
    except (OSError, subprocess.SubprocessError) as exc:
        raise Unavailable(str(exc)) from exc


def git(*args: str, root: Path = ROOT) -> str:
    result = run(["git", *args], root)
    if result.returncode:
        raise Unavailable(result.stderr.strip())
    return result.stdout


def selected_files(explicit: list[str] | None, root: Path = ROOT,
                   all_files: bool = False) -> list[str]:
    supplied = explicit is not None
    if explicit is None:
        if all_files:
            names = git("ls-files", "-z", root=root)
        else:
            base = git("merge-base", "HEAD", "main", root=root).strip()
            names = git("diff", "--name-only", "-z", "--diff-filter=ACMR", base,
                        "--", root=root)
        names += "\0" + git("ls-files", "--others", "--exclude-standard", "-z", root=root)
        explicit = [name for name in names.split("\0") if name]
        configuration = {"pyproject.toml", "eslint.config.mjs", "tools/verify_lint.py",
                         "tools/verification/lint-baseline.json",
                         "tools/verification/package.json", "tools/verification/package-lock.json"}
        if not all_files and configuration.intersection(explicit):
            return selected_files(None, root, all_files=True)
    selected = set()
    for name in explicit:
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()):
            raise Unavailable(f"path escapes checkout: {name}")
        if not path.is_file():
            if not supplied:
                continue  # Automatic inventory includes tracked worktree deletions.
            raise Unavailable(f"selected file missing: {name}")
        if path.suffix in SUFFIXES:
            selected.add(path.relative_to(root.resolve()).as_posix())
    return sorted(selected)


def parse_result(result: subprocess.CompletedProcess) -> list[dict]:
    if result.returncode not in (0, 1):
        raise Unavailable(result.stderr.strip() or result.stdout.strip() or "linter crashed")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise Unavailable("linter returned malformed JSON") from exc
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise Unavailable("linter returned unexpected JSON shape")
    if result.returncode == 1 and not value:
        raise Unavailable("linter failed without diagnostics")
    return value


def lint(files: list[str], fix: bool = False, root: Path = ROOT) -> list[dict]:
    findings = []
    python = [name for name in files if name.endswith(".py")]
    javascript = [name for name in files if not name.endswith(".py")]
    if python:
        command = ["uvx", "--offline", f"ruff=={RUFF}", "check", "--no-cache",
                   "--config", str(root / "pyproject.toml"), "--output-format", "json"]
        # Only import removal is approved here; Ruff's other safe fixes can
        # alter expressions. Never use --unsafe-fixes or whole-tree fixes.
        if fix:
            fixed = run([*command, "--fix", "--fixable", "F401", *python], root)
            parse_result(fixed)
        for row in parse_result(run([*command, *python], root)):
            findings.append({"path": Path(row["filename"]).resolve().relative_to(root.resolve()).as_posix(),
                             "line": row["location"]["row"], "rule": row["code"],
                             "message": row["message"]})
    if javascript:
        package = root / "tools/verification/node_modules/eslint"
        try:
            version = json.loads((package / "package.json").read_text(encoding="utf-8"))["version"]
        except (OSError, ValueError, KeyError) as exc:
            raise Unavailable("ESLint missing; run npm ci --prefix tools/verification") from exc
        if version != ESLINT:
            raise Unavailable(f"ESLint version {version}; required {ESLINT}")
        command = ["node", str(package / "bin/eslint.js"), "--no-config-lookup", "-c",
                   str(root / "eslint.config.mjs"), "--no-warn-ignored", "--format", "json"]
        # No ESLint fixes are approved initially: existing rules include
        # suggestion fixes requiring semantic review.
        result = run([*command, *javascript], root)
        rows = parse_result(result)
        if result.returncode == 1 and not any(row.get("messages") for row in rows):
            raise Unavailable("ESLint failed without diagnostics")
        for row in rows:
            for message in row["messages"]:
                findings.append({"path": Path(row["filePath"]).resolve().relative_to(root.resolve()).as_posix(),
                                 "line": message.get("line", 1), "rule": message.get("ruleId") or "parse-error",
                                 "message": message["message"]})
    return findings


def signature(finding: dict, root: Path = ROOT) -> str:
    lines = (root / finding["path"]).read_text(encoding="utf-8").splitlines()
    line = finding["line"] - 1
    # Include neighboring source so an unrelated identical diagnostic cannot
    # consume this allowance merely by using the same identifier.
    context = "\n".join(lines[max(0, line - 1):line + 2])
    identity = [finding["path"], finding["rule"], finding["message"], context]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def new_findings(findings: list[dict], baseline: dict, root: Path = ROOT) -> list[dict]:
    remaining = Counter(baseline["allowances"])
    result = []
    for finding in findings:
        key = signature(finding, root)
        if remaining[key]:
            remaining[key] -= 1
        else:
            result.append(finding)
    return result


def probe() -> None:
    """Real offline availability/version evidence, including subordinate tools."""
    commands = [(["uvx", "--offline", f"ruff=={RUFF}", "--version"], f"ruff {RUFF}"),
                (["node", str(ROOT / "tools/verification/node_modules/eslint/bin/eslint.js"),
                  "--version"], f"v{ESLINT}")]
    for command, expected in commands:
        result = run(command)
        if result.returncode or result.stdout.strip() != expected:
            raise Unavailable(result.stderr.strip() or f"expected {expected}; got {result.stdout.strip()}")
        print(result.stdout.strip())
    node = run(["node", "--version"])
    if node.returncode or not node.stdout.strip():
        raise Unavailable("cannot identify Node runtime")
    print(f"node {node.stdout.strip()}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", nargs="+", help="explicit task-owned files")
    parser.add_argument("--fix", action="store_true")
    parser.add_argument("--all", action="store_true", help="check every tracked source against baseline")
    parser.add_argument("--probe", action="store_true", help="identify required offline tools without linting")
    args = parser.parse_args(argv)
    if args.fix and (not args.files or args.all):
        parser.error("--fix requires explicit --files and cannot use --all")
    try:
        if args.probe:
            probe()
            return 0
        files = selected_files(args.files, all_files=args.all)
        if not files:
            print("lint: not applicable (no selected Python/JavaScript files)")
            return 0
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        if baseline["versions"] != {"ruff": RUFF, "eslint": ESLINT}:
            raise Unavailable("lint baseline tool versions do not match")
        findings = lint(files, args.fix)
        fresh = new_findings(findings, baseline)
        for finding in fresh:
            print(f"{finding['path']}:{finding['line']}: [{finding['rule']}] {finding['message']}")
        print(f"lint: {len(files)} files, {len(fresh)} new findings, "
              f"{len(findings) - len(fresh)} explicit legacy findings")
        return 1 if fresh else 0
    except (Unavailable, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"lint: unavailable: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
