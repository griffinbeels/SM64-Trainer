"""Focused checks and the full integration gate within a shared test budget.

    uv run python tools/run_tests.py tests/test_run_tests.py  # focused, serial
    uv run python tools/run_tests.py --changed                # Python coverage selection
    uv run python tools/run_tests.py                          # full integration gate

Resource policy lives in test_resources.py; commands, historical measurements
and the selection limits live in docs/testing.md. Explicit pytest arguments
never earn a full-run stamp. Unknown non-Python changes force --changed to run
everything: testmon cannot track JS, seed data or Python in child processes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from find_uilab import find_uilab
from test_resources import TestResources
from test_job import TestJob
from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parent.parent
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
    gate and the inner loop can never drift onto different schedulers -- the
    nodeids testmon keys its map by carry the worker group as a suffix under
    loadgroup, so a run without it would look like 8,763 brand-new tests."""
    return ["-n", str(workers), "--dist", "loadgroup",
            "--reruns", "0", "-q"]


def pytest_args(mode: str, workers: int, extra: list[str]) -> list[str]:
    if mode == "full":
        return [*base_args(workers), "--testmon-noselect", *extra]
    if mode == "select":
        return [*base_args(workers), "--testmon", *extra]
    if mode == "focused":
        return [*base_args(workers), "--no-testmon", *extra]
    raise ValueError(mode)


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
    """('full' | 'select', why). Pure, so the rule is testable without git."""
    if saved is None:
        return "full", "no record of a full run in this checkout yet"
    changed = sorted(path for path in set(saved) | set(current)
                     if saved.get(path) != current.get(path))
    if changed:
        shown = ", ".join(changed[:5]) + (f" (+{len(changed) - 5} more)" if len(changed) > 5 else "")
        return "full", (f"{len(changed)} non-Python file(s) changed since the last full run, "
                        f"and nothing can see what they affect: {shown}")
    return "select", "only Python changed since the last full run; testmon picks the tests"


def records_full_run(mode: str, extra: list[str], exit_code: int) -> bool:
    """Only a full run over EVERYTHING that completed earns the stamp: a `-k`
    or a path narrows the run, and stamping it would let `--changed` select
    against a map that never covered the rest."""
    return mode == "full" and not extra and exit_code in COMPLETED_EXIT_CODES


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


def run(mode: str, extra: list[str], resources: TestResources) -> int:
    command = [sys.executable, "-m", "pytest", *pytest_args(mode, resources.workers, extra)]
    with TestJob(command, ROOT) as child:
        def relay():
            for line in child.stdout:
                print(line, end="", flush=True)
        reader = threading.Thread(target=relay, daemon=True)
        reader.start()
        exit_code = child.wait()
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
                        help="run only tests the last full run's map says your Python "
                             "change touched; runs everything if a non-Python file changed")
    parser.add_argument("--workers", type=int, default=None,
                        help="worker ceiling (default: serial for focused checks, 16 full, 8 with OBS)")
    parser.add_argument("--reserve", type=int, default=None,
                        help="additional CPU reserve; cannot weaken the automatic desktop/OBS budget")
    parser.add_argument("--dry-run", action="store_true", help="print selection without starting tests")
    args, extra = parser.parse_known_args(argv)
    if (args.workers is not None and args.workers < 0) or (args.reserve is not None and args.reserve < 0):
        parser.error("workers and reserve must be nonnegative")
    if args.changed and extra:
        parser.error("choose --changed OR explicit pytest targets/options; do not combine them")
    if any(option in {"-n", "--numprocesses", "--tx"} or
           option.startswith(("-n", "--numprocesses=", "--tx=")) for option in extra):
        parser.error("use --workers for local parallelism; pytest worker/transport overrides bypass the budget")
    if args.dry_run:
        mode, why, _ = selection(args.changed, extra)
        print(f"run_tests: {mode} -- {why}")
        return 0
    workers = args.workers if args.workers is not None else (0 if extra else None)
    try:
        with TestResources(workers, args.reserve) as resources:
            # Read after queueing: a sibling may have completed the map while we waited.
            mode, why, current = selection(args.changed, extra)
            print(f"run_tests: {mode} -- {why}", flush=True)
            say_if_the_rendered_gates_cannot_run()
            exit_code = run(mode, extra, resources)
            if records_full_run(mode, extra, exit_code):
                save_state(current, resources.workers, exit_code)
            return exit_code
    except KeyboardInterrupt:
        return 130


def selection(changed: bool, extra: list[str]) -> tuple[str, str, dict[str, str]]:
    if extra:
        return "focused", "explicit pytest scope; no coverage-map overhead or full-run stamp", {}
    current = fingerprint(git_known_files())
    if changed:
        state = load_state()
        mode, why = decide(state.get("fingerprint") if state else None, current)
        return mode, why, current
    return "full", "the integration gate: every test, coverage map refreshed", current


if __name__ == "__main__":
    sys.exit(main())
