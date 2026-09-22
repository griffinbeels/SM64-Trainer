"""The merge check as the harness's `full` lane: this checkout's blast radius.

The merge check runs only the tests tools/blast_radius.py selects for the diff
since the newest green full run on main; the full run on GitHub covers the
rest. It refuses a missing Vitest bridge up front, because the bridge would
otherwise fail every component test with the same setup message, which reads
as a code regression. A radius holding browser tests without uilab is refused
by tools/run_tests.py itself.
"""
import os
import argparse
import hashlib
import importlib.metadata
from pathlib import Path
import runpy
import shutil
import sys

# A blast radius is minutes at most, and a global change's fallback (every
# test that starts no browser) measured 7 minutes; a run still going after this
# many minutes of TESTING (queue time excluded) is hung, not slow.
LIMIT_MINUTES = 30


def prepare_environment() -> None:
    """The full gate owns selection/plugins; the caller retains resource limits."""
    for name in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTEST_DISABLE_PLUGIN_AUTOLOAD"):
        os.environ.pop(name, None)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe', action='store_true')
    parser.add_argument('--workers', type=int, help='Ceiling within the shared test budget; scope stays full')
    args = parser.parse_args()
    if args.workers is not None and args.workers < 0:
        parser.error('workers must be nonnegative')
    prepare_environment()
    component_runner = Path(__file__).resolve().parents[1] / "tests/frontend/node_modules/vitest/vitest.mjs"
    if not component_runner.is_file():
        print("full: unavailable: run npm ci --prefix tests/frontend --ignore-scripts", file=sys.stderr)
        return 2
    if shutil.which("node") is None:
        print("full: unavailable: Node 24.13+ is not on PATH", file=sys.stderr)
        return 2
    if args.probe:
        packages = sorted((item.metadata["Name"], item.version)
                          for item in importlib.metadata.distributions())
        print(f"packages {packages}\n"
              f"vitest {hashlib.sha256(component_runner.read_bytes()).hexdigest()}")
        return 0
    sys.argv = [str(Path(__file__).with_name("run_tests.py")), "--limit-minutes", str(LIMIT_MINUTES)]
    if args.workers is not None:
        sys.argv.extend(['--workers', str(args.workers)])
    runpy.run_path(sys.argv[0], run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
