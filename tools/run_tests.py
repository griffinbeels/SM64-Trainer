"""Focused checks, the merge check and the browser lane within a shared test budget.

    uv run python tools/run_tests.py tests/test_run_tests.py  # focused, serial, never queues
    uv run python tools/run_tests.py --changed                # Python coverage selection
    uv run python tools/run_tests.py                          # the merge check
    uv run python tools/run_tests.py --browser                # the browser lane, locally

The merge check is every test that starts no UI fixture server and no browser
(tools/test_lanes.py decides); the browser lane runs on GitHub on every push
to main (tools/browser_ci.py reads it). Resource policy lives in
test_resources.py; commands and limits live in docs/testing.md. Explicit
pytest arguments never earn a full-run stamp. Unknown non-Python changes force
--changed to run the whole merge check: testmon cannot track JS, seed data or
Python in child processes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# A shared venv may have another worktree installed editable. Pin BOTH this
# runner and its pytest/browser children to the checkout being measured.
_SOURCE = str(ROOT / "src")
sys.path.insert(0, _SOURCE)
os.environ["PYTHONPATH"] = os.pathsep.join(
    [_SOURCE, *[p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep)
                if p and p != _SOURCE]])

from find_uilab import find_uilab  # noqa: E402
from test_lanes import parse_shard, rerun_args  # noqa: E402
from test_resources import TestResources  # noqa: E402
from test_job import TestJob  # noqa: E402
from sm64_events.core.childproc import quiet_spawn_kwargs  # noqa: E402

STATE_PATH = ROOT / ".run_tests.json"
# pytest exit codes that mean the run COMPLETED and the coverage map is whole:
# 0 all passed, 1 some failed. 2 is an interruption, 3/4 are pytest's own
# errors, 5 collected nothing -- none of those leave a map worth stamping.
COMPLETED_EXIT_CODES = (0, 1)
# The door's own state never enters its fingerprint. testmon keeps its map
# in SQLite, whose `-wal`/`-shm` side files appear and vanish between runs;
# with them counted, every `--changed` read as "non-Python changed" and ran
# everything, forever -- the safe failure, and a useless door (2026-09-01).
OWN_STATE_PREFIXES = (".testmondata", ".run_tests.json")


def base_args(workers: int) -> list[str]:
    """The flags every run through this door shares. One place, so the merge
    check and the inner loop can never drift onto different schedulers -- the
    nodeids testmon keys its map by carry the worker group as a suffix under
    loadgroup, so a run without it would look like 8,763 brand-new tests."""
    return ["-n", str(workers), "--dist", "loadgroup", "-q"]


NO_RERUNS = ["--reruns", "0"]


def pytest_args(mode: str, workers: int, extra: list[str]) -> list[str]:
    if mode == "merge":
        return [*base_args(workers), *NO_RERUNS, "--testmon-noselect", "--lane", "merge", *extra]
    if mode == "select":
        return [*base_args(workers), *NO_RERUNS, "--testmon", "--lane", "merge", *extra]
    if mode == "focused":
        return [*base_args(workers), *NO_RERUNS, "--no-testmon", *extra]
    if mode == "browser":
        # The one lane with a retry, and only for setup errors (test_lanes.py).
        return [*base_args(workers), *rerun_args(), "--no-testmon", "--lane", "browser", *extra]
    raise ValueError(mode)


ADMITTED_MODES = ("merge", "browser")


def is_python(path: str) -> bool:
    return path.endswith(".py")


def git_known_files() -> list[str]:
    """Every path git tracks plus every untracked path it does not ignore --
    the set a commit could carry, which is the set a merge gate answers for."""
    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True,
                             capture_output=True, **quiet_spawn_kwargs()).stdout
    untracked = subprocess.run(["git", "ls-files", "-z", "--others", "--exclude-standard"],
                               cwd=ROOT, check=True, capture_output=True, **quiet_spawn_kwargs()).stdout
    paths = (tracked + untracked).decode("utf-8").split("\0")
    return sorted(path for path in paths if path)


def fingerprint(paths: list[str], read=lambda path: (ROOT / path).read_bytes()) -> dict[str, str]:
    """path -> sha1 of contents, for every non-Python file that still exists.
    Content, not mtime: a checkout or a merge touches mtimes without changing
    a byte, and a spurious full run is safe but costs four minutes."""
    prints = {}
    for path in paths:
        if is_python(path) or path.startswith(OWN_STATE_PREFIXES):
            continue
        try:
            data = read(path)
        except (FileNotFoundError, IsADirectoryError, PermissionError):
            continue
        prints[path] = hashlib.sha1(data).hexdigest()
    return prints


def decide(saved: dict[str, str] | None, current: dict[str, str]) -> tuple[str, str]:
    """('merge' | 'select', why). Pure, so the rule is testable without git."""
    if saved is None:
        return "merge", "no record of a merge check in this checkout yet"
    changed = sorted(path for path in set(saved) | set(current)
                     if saved.get(path) != current.get(path))
    if changed:
        shown = ", ".join(changed[:5]) + (f" (+{len(changed) - 5} more)" if len(changed) > 5 else "")
        return "merge", (f"{len(changed)} non-Python file(s) changed since the last merge check, "
                         f"and nothing can see what they affect: {shown}")
    return "select", "only Python changed since the last merge check; testmon picks the tests"


def records_full_run(mode: str, extra: list[str], exit_code: int) -> bool:
    """Only a merge check over its WHOLE lane that completed earns the stamp:
    a `-k` or a path narrows the run, and stamping it would let `--changed`
    select against a map that never covered the rest."""
    return mode == "merge" and not extra and exit_code in COMPLETED_EXIT_CODES


def load_state() -> dict | None:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_state(prints: dict[str, str], workers: int, exit_code: int) -> None:
    STATE_PATH.write_text(json.dumps({
        "recorded_utc": datetime.now(timezone.utc).isoformat(),
        "workers": workers,
        "pytest_exit": exit_code,
        "fingerprint": prints,
    }, indent=0), encoding="utf-8")


def say_if_the_rendered_gates_cannot_run() -> None:
    """Announce a missing uilab, because a run without it looks GREEN.

    Every layout, contact-sheet and rendered-behaviour gate skips when uilab
    is unreachable, and a skip is not a failure: the run reports `8413 passed,
    75 skipped` in 68 seconds and exits 0. Measured 2026-09-03, building a
    baseline to compare a branch against -- a `git worktree` outside
    `.claude/worktrees/` resolves the sibling checkout relative to ITSELF, so
    the 62 browser tests silently left the run and the comparison was about to
    be believed. `find_uilab`'s own docstring covers the other way this
    vanishes (a `uv sync` pruning an editable install).

    Prints and returns; it never blocks. The point is that a run missing its
    rendered half cannot look identical to one that has it."""
    if find_uilab() is not None:
        print("run_tests: !! uilab NOT FOUND -- every rendered gate will SKIP, "
              "and the run will still exit 0. Set UILAB_PATH to your uilab "
              "checkout before trusting this result.", flush=True)


TIMED_OUT = 124
PYTEST = [sys.executable, "-m", "pytest"]


def run(mode: str, extra: list[str], resources: TestResources,
        limit_seconds: float | None = None) -> int:
    """`limit_seconds` starts at admission, so a run queued behind two merge
    checks is not cancelled for the time it spent waiting its turn."""
    command = [*PYTEST, *pytest_args(mode, resources.workers, extra)]
    with TestJob(command, ROOT) as child:
        def relay():
            for line in child.stdout:
                print(line, end="", flush=True)
        reader = threading.Thread(target=relay, daemon=True)
        reader.start()
        try:
            exit_code = child.wait(timeout=limit_seconds)
        except subprocess.TimeoutExpired:
            print(f"run_tests: stopped after {limit_seconds / 60:.0f} minutes of testing "
                  "(queue time not counted); closing the test job", flush=True)
            exit_code = TIMED_OUT
    # Close the job before waiting for EOF: a leaked descendant may still
    # hold the output pipe even though pytest itself already exited.
    reader.join(timeout=5)
    if reader.is_alive():
        raise RuntimeError("pytest output did not close after its process tree ended")
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--changed", action="store_true",
                        help="run only tests the last merge check's map says your Python "
                             "change touched; runs the whole merge check if a non-Python file changed")
    parser.add_argument("--browser", action="store_true",
                        help="run the browser lane here (it runs on GitHub on every push to main); "
                             "to run a few browser files, name them instead")
    parser.add_argument("--shard", metavar="K/N",
                        help="with --browser: only job K of N (how the GitHub run splits the lane)")
    parser.add_argument("--junitxml", metavar="PATH", help="also write a JUnit XML report")
    parser.add_argument("--limit-minutes", type=float, default=None,
                        help="stop the run this long after admission; queue time does not count")
    parser.add_argument("--workers", type=int, default=None,
                        help="worker ceiling (default: serial for focused checks, else half the "
                             "machine budget: 8, or 4 with OBS open)")
    parser.add_argument("--reserve", type=int, default=None,
                        help="additional CPU reserve; cannot weaken the automatic desktop/OBS budget")
    parser.add_argument("--dry-run", action="store_true", help="print selection without starting tests")
    args, extra = parser.parse_known_args(argv)
    if (args.workers is not None and args.workers < 0) or (args.reserve is not None and args.reserve < 0):
        parser.error("workers and reserve must be nonnegative")
    if args.changed and extra:
        parser.error("choose --changed OR explicit pytest targets/options; do not combine them")
    if args.browser and (args.changed or extra):
        parser.error("--browser runs the whole browser lane; to run a few browser tests, name their files")
    if args.shard:
        if not args.browser:
            parser.error("--shard splits the browser lane; add --browser")
        try:
            parse_shard(args.shard)
        except ValueError as error:
            parser.error(str(error))
    if any(option in {"-n", "--numprocesses", "--tx"} or
           option.startswith(("-n", "--numprocesses=", "--tx=")) for option in extra):
        parser.error("use --workers for local parallelism; pytest worker/transport overrides bypass the budget")
    mode, why, current = selection(args.changed, args.browser, extra)
    if args.dry_run:
        print(f"run_tests: {mode} -- {why}")
        return 0
    if mode == "browser" and (missing := find_uilab()) is not None:
        print(f"run_tests: the browser lane needs uilab. {missing}", flush=True)
        return 2
    options = [*(["--shard", args.shard] if args.shard else []),
               *(["--junitxml", args.junitxml] if args.junitxml else [])]
    workers = args.workers if args.workers is not None else (0 if mode == "focused" else None)
    admit = mode in ADMITTED_MODES
    try:
        with TestResources(workers, args.reserve, admit=admit) as resources:
            if mode == "merge":
                # Re-read after queueing: the tree it stamps is the tree it tests.
                current = fingerprint(git_known_files())
            print(f"run_tests: {mode} -- {why}", flush=True)
            if mode == "focused":
                say_if_the_rendered_gates_cannot_run()
            limit = args.limit_minutes * 60 if args.limit_minutes else None
            exit_code = run(mode, [*options, *extra], resources, limit)
            if records_full_run(mode, extra, exit_code):
                save_state(current, resources.workers, exit_code)
            return exit_code
    except KeyboardInterrupt:
        return 130


def selection(changed: bool, browser: bool, extra: list[str]) -> tuple[str, str, dict[str, str]]:
    if extra:
        return "focused", "explicit pytest scope; no queue, coverage map or full-run stamp", {}
    if browser:
        return "browser", "the browser lane: every test that starts a UI fixture server or a browser", {}
    current = fingerprint(git_known_files())
    if changed:
        state = load_state()
        mode, why = decide(state.get("fingerprint") if state else None, current)
        return mode, why, current
    return "merge", "the merge check: every test that starts no browser; coverage map refreshed", current


if __name__ == "__main__":
    sys.exit(main())
