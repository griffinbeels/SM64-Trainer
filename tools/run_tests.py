"""Run the suite the fast way and the honest way -- the one door for both.

    uv run python tools/run_tests.py             # the merge gate: everything, on workers, refreshing the coverage map
    uv run python tools/run_tests.py --changed   # the inner loop: only what the last full run says your Python change touched
    uv run python tools/run_tests.py -x -k rank  # anything else goes straight to pytest

Two instruments, and why each is shaped as it is (all numbers 2026-09-01,
8,763 tests):

* **Workers.** 19m 29s serial; 4m 11s on 16 workers grouped by file, identical
  outcomes. `tests/conftest.py::pytest_collection_modifyitems` gives every
  test a worker group -- its file, or itself when marked `spread` -- and
  `--dist loadgroup` schedules by that. 79% of the suite's time is browser
  tests waiting on Chrome, which is why the workers overlap so well; 16
  rather than 32 because each browser test is several Chrome processes plus
  a server, and the sweep floor made 32 pointless until the sweep was split.

* **Selection** (pytest-testmon). A run records, per test, the source lines it
  executed; the next `--changed` run reruns only tests that executed a block
  that has since changed, plus tests whose own file changed or that failed
  last time. A leaf module's change selected 405 tests, a hub's 600 to 1,072,
  an unchanged tree none in two seconds. It is BLIND to everything that is
  not Python executed in-process: a JS, HTML, CSS, seed-data or docs edit
  selects nothing, and so does Python that only runs inside a spawned
  subprocess. So `--changed` is not the gate. It never replaces the full run;
  the full run is what refreshes the map, and it is the one command that
  says "this merges".

The door closes the blind spot it can: after every full run it records a
content hash of every non-Python file git knows about (tracked or untracked,
not ignored), and `--changed` refuses to select when any of them differs --
it runs everything instead and says which files made it. Without that, a JS
edit reads as green on zero tests, which is the failure `CLAUDE.md` names as
"a guard nobody has seen fail is green forever".

State: `.testmondata` (testmon's map) and `.run_tests.json` (the last full
run's hash), both per checkout, both gitignored; a fresh worktree's first
run is a full one because it has neither.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / ".run_tests.json"
DEFAULT_WORKERS = 16
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
    return ["-n", str(workers), "--dist", "loadgroup", "-q"]


def pytest_args(mode: str, workers: int, extra: list[str]) -> list[str]:
    if mode == "full":
        return [*base_args(workers), "--testmon-noselect", *extra]
    if mode == "select":
        return [*base_args(workers), "--testmon", *extra]
    raise ValueError(mode)


def is_python(path: str) -> bool:
    return path.endswith(".py")


def git_known_files() -> list[str]:
    """Every path git tracks plus every untracked path it does not ignore --
    the set a commit could carry, which is the set a merge gate answers for."""
    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True,
                             capture_output=True).stdout
    untracked = subprocess.run(["git", "ls-files", "-z", "--others", "--exclude-standard"],
                               cwd=ROOT, check=True, capture_output=True).stdout
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--changed", action="store_true",
                        help="run only tests the last full run's map says your Python "
                             "change touched; runs everything if a non-Python file changed")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                        help=f"pytest-xdist workers (default {DEFAULT_WORKERS})")
    args, extra = parser.parse_known_args(argv)

    current = fingerprint(git_known_files())
    if args.changed:
        state = load_state()
        mode, why = decide(state.get("fingerprint") if state else None, current)
    else:
        mode, why = "full", "the merge gate: every test, and the coverage map refreshed"
    print(f"run_tests: {mode} -- {why}", flush=True)

    command = [sys.executable, "-m", "pytest", *pytest_args(mode, args.workers, extra)]
    exit_code = subprocess.run(command, cwd=ROOT).returncode
    if records_full_run(mode, extra, exit_code):
        save_state(current, args.workers, exit_code)
        print(f"run_tests: recorded a full run against {len(current)} non-Python files "
              f"(pytest exit {exit_code})", flush=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
