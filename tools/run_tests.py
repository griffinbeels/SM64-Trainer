"""The one door to the suite: focused checks, the merge check, the full run.

    uv run python tools/run_tests.py tests/test_x.py "tests/test_y.py::t"  # focused: exactly these, never queues
    uv run python tools/run_tests.py                 # the merge check: the blast radius of this change
    uv run python tools/run_tests.py --why           # what the merge check would run, and why
    uv run python tools/run_tests.py --all           # the whole suite: what the full run on GitHub does

Griffin, 2026-09-21: "we ALREADY TESTED THE WHOLE SUITE. NO NEED TO RERUN ANY
TESTS OTHER THAN THE BLAST RADIUS FOR OUR CHANGES." The merge check therefore
runs only the tests tools/blast_radius.py selects for the diff since the newest
green full run on main; the full run on GitHub (tools/full_run.py) runs
everything. Resource policy lives in test_resources.py; commands and limits
in docs/testing.md.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
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
from test_lanes import lane_of, parse_shard, rerun_args  # noqa: E402
from test_resources import TestResources  # noqa: E402
from test_job import TestJob  # noqa: E402

TIMED_OUT = 124
PYTEST = [sys.executable, "-m", "pytest"]
NO_RERUNS = ["--reruns", "0"]
ADMITTED_MODES = ("merge", "all")   # a focused run never waits for a slot


def base_args(workers: int) -> list[str]:
    """The flags every run through this door shares, so the merge check, the
    full run and the inner loop can never drift onto different schedulers."""
    return ["-n", str(workers), "--dist", "loadgroup", "-q"]


def pytest_args(mode: str, workers: int, extra: list[str], *, record_coverage: bool = False) -> list[str]:
    if mode == "all":
        # The one mode with a retry, and only for setup errors (test_lanes.py).
        coverage = ["--testmon-noselect"] if record_coverage else ["--no-testmon"]
        return [*base_args(workers), *rerun_args(), *coverage, *extra]
    if mode in ("merge", "focused"):
        return [*base_args(workers), *NO_RERUNS, "--no-testmon", *extra]
    raise ValueError(mode)


def say_if_the_rendered_gates_cannot_run() -> None:
    """Announce a missing uilab, because a run without it looks GREEN: every
    browser module skips itself when uilab is unreachable, and a skip is not
    a failure. `find_uilab`'s docstring covers the ways it goes missing."""
    if find_uilab() is not None:
        print("run_tests: !! uilab NOT FOUND -- every browser test will SKIP, "
              "and the run will still exit 0. Set UILAB_PATH to your uilab "
              "checkout before trusting this result.", flush=True)


def run(mode: str, extra: list[str], resources: TestResources,
        limit_seconds: float | None = None, *, record_coverage: bool = False) -> int:
    """`limit_seconds` starts at admission, so a run queued behind two merge
    checks is not cancelled for the time it spent waiting its turn."""
    command = [*PYTEST, *pytest_args(mode, resources.workers, extra, record_coverage=record_coverage)]
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


def blast_radius(base: str | None, why: bool) -> tuple[dict[str, list[str] | None], str]:
    """(test file -> None or nodeids, what to print)."""
    import blast_radius as radius_module
    radius = radius_module.select(ROOT, base)
    text = radius_module.why(radius) if why else (
        radius_module.summary(radius) + "  (--why lists the reasons)")
    return radius.selection(), text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--why", "--dry-run", dest="why", action="store_true",
                        help="print the blast radius and the reason for every part of it; run nothing")
    parser.add_argument("--base", help="diff against this commit instead of the newest green full run")
    parser.add_argument("--changed", action="store_true", help=argparse.SUPPRESS)  # the old spelling of the default
    parser.add_argument("--all", action="store_true",
                        help="the whole suite (the full run on GitHub does this on every push to main)")
    parser.add_argument("--shard", metavar="K/N", help="with --all: only job K of N")
    parser.add_argument("--lane", choices=("browser", "nonbrowser"),
                        help="with --all: only the browser modules, or only the rest")
    parser.add_argument("--record-coverage", action="store_true",
                        help="with --all: keep a pytest-testmon map for the blast radius to read")
    parser.add_argument("--junitxml", metavar="PATH", help="also write a JUnit XML report")
    parser.add_argument("--limit-minutes", type=float, default=None,
                        help="stop the run this long after admission; queue time does not count")
    parser.add_argument("--workers", type=int, default=None,
                        help="worker ceiling (default: serial for focused checks, else half the "
                             "machine budget: 8, or 4 with OBS open)")
    parser.add_argument("--reserve", type=int, default=None,
                        help="additional CPU reserve; cannot weaken the automatic desktop/OBS budget")
    args, extra = parser.parse_known_args(argv)
    # A vitest failure prints characters the console's code page lacks; the
    # relay must pass them on as '?' rather than die and hide every failure.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    refuse_contradictions(parser, args, extra)
    mode = "focused" if extra else "all" if args.all else "merge"
    options = [*(["--shard", args.shard] if args.shard else []),
               *(["--lane", args.lane] if args.lane else []),
               *(["--junitxml", args.junitxml] if args.junitxml else [])]
    chosen = None
    if mode == "merge":
        outcome, chosen = prepare_merge_check(args)
        if chosen is None:
            return outcome
        options.append(f"--select-from={chosen}")
    if mode == "all" and args.lane != "nonbrowser" and (missing := find_uilab()) is not None:
        print(f"run_tests: the whole suite needs uilab for its browser tests. {missing}", flush=True)
        return 2
    workers = args.workers if args.workers is not None else (0 if mode == "focused" else None)
    try:
        with TestResources(workers, args.reserve, admit=mode in ADMITTED_MODES) as resources:
            if mode != "all":
                say_if_the_rendered_gates_cannot_run()
            limit = args.limit_minutes * 60 if args.limit_minutes else None
            return run(mode, [*options, *extra], resources, limit, record_coverage=args.record_coverage)
    except KeyboardInterrupt:
        return 130
    finally:
        if chosen is not None:
            chosen.unlink(missing_ok=True)


def refuse_contradictions(parser, args, extra: list[str]) -> None:
    if (args.workers is not None and args.workers < 0) or (args.reserve is not None and args.reserve < 0):
        parser.error("workers and reserve must be nonnegative")
    if (args.shard or args.record_coverage or args.lane) and not args.all:
        parser.error("--shard, --lane and --record-coverage split or map the whole suite; add --all")
    if args.all and (extra or args.why or args.base):
        parser.error("--all runs the whole suite; name tests to run a few, or drop --all for the blast radius")
    if (args.why or args.base) and extra:
        parser.error("named tests run exactly as named; --why and --base describe the blast radius")
    if args.shard:
        try:
            parse_shard(args.shard)
        except ValueError as error:
            parser.error(str(error))
    if any(option in {"-n", "--numprocesses", "--tx"} or
           option.startswith(("-n", "--numprocesses=", "--tx=")) for option in extra):
        parser.error("use --workers for local parallelism; pytest worker/transport overrides bypass the budget")



def prepare_merge_check(args) -> tuple[int, Path | None]:
    """(exit code, None) when there is nothing to run here, else (0, the
    selection file for conftest's --select-from)."""
    selection, text = blast_radius(args.base, args.why)
    print(text, flush=True)
    if args.why:
        return 0, None
    if not selection:
        print("run_tests: nothing in the blast radius; the full run on GitHub covers the rest")
        return 0, None
    browser = [name for name in selection if lane_of(ROOT / name) == "browser"]
    if browser and (missing := find_uilab()) is not None:
        # A narrowed run may skip freely, so without this a radius of
        # browser tests would skip every one of them and read as green.
        print(f"run_tests: the blast radius includes browser tests, which need uilab. {missing}")
        return 2, None
    chosen = Path(tempfile.gettempdir()) / f"sm64-blast-radius-{os.getpid()}.json"
    chosen.write_text(json.dumps({"files": selection}), encoding="utf-8")
    return 0, chosen


if __name__ == "__main__":
    sys.exit(main())
