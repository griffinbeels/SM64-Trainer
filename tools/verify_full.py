"""Require rendered-test dependencies before the existing integration runner."""
import os
import hashlib
import importlib.metadata
from pathlib import Path
import runpy
import sys

from verify_lint import git
from find_uilab import find_uilab


def prepare_environment() -> None:
    """The full gate owns selection/plugins; the caller retains resource limits."""
    for name in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTEST_DISABLE_PLUGIN_AUTOLOAD"):
        os.environ.pop(name, None)


def main() -> int:
    prepare_environment()
    component_runner = Path(__file__).resolve().parents[1] / "tests/frontend/node_modules/vitest/vitest.mjs"
    if not component_runner.is_file():
        print("full: unavailable: run npm ci --prefix tests/frontend --ignore-scripts", file=sys.stderr)
        return 2
    if os.environ.get("UILAB_SKIP") == "1":
        print("full: unavailable: UILAB_SKIP=1 disables required rendered checks", file=sys.stderr)
        return 2
    # Git owns checkout identity; native Codex and Claude worktree layouts both
    # resolve to the same primary checkout without hard-coded folder depths.
    common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir").strip())
    sibling = common.parent.parent / "uilab"
    if "UILAB_PATH" not in os.environ and (sibling / "uilab/__init__.py").is_file():
        os.environ["UILAB_PATH"] = str(sibling)
    missing = find_uilab()
    if missing:
        print(f"full: unavailable: {missing}", file=sys.stderr)
        return 2
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = Path(playwright.chromium.executable_path)
            if not browser.is_file():
                print("full: unavailable: install Playwright Chromium", file=sys.stderr)
                return 2
    except ImportError as exc:
        print(f"full: unavailable: {exc}", file=sys.stderr)
        return 2
    if "--probe" in sys.argv[1:]:
        import uilab
        digest = hashlib.sha256()
        for source in sorted(Path(uilab.__file__).parent.rglob("*.py")):
            digest.update(str(source).encode())
            digest.update(source.read_bytes())
        packages = sorted((item.metadata["Name"], item.version)
                          for item in importlib.metadata.distributions())
        print(f"uilab {digest.hexdigest()}\npackages {packages}\n"
              f"chromium {browser} {browser.stat().st_size} {browser.stat().st_mtime_ns}\n"
              f"vitest {hashlib.sha256(component_runner.read_bytes()).hexdigest()}")
        return 0
    sys.argv = [str(Path(__file__).with_name("run_tests.py"))]
    runpy.run_path(sys.argv[0], run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
