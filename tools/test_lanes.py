"""Which test modules start a browser, and how the full run splits into jobs.

A module is a BROWSER module when running it would start Chromium or the UI
fixture server:

  - it imports `uilab` or `playwright` anywhere in the file, or names
    `serve_ui` / `serve_ui_live` (the UI fixture server's two doors);
  - or it imports a helper from `tests/` or `tools/` that imports either
    package, or those two names, AT MODULE LEVEL. A helper that only reaches
    for uilab inside one function (`tools/export_overlay.py`) does not drag
    every test that borrows an unrelated function from it.

Classification reads source with `ast`, never an import, and is automatic, so
a new browser test is known as one without anyone remembering a marker. Three
things use it: the blast radius's fallback when a global input changes (every
test that starts no browser; GitHub covers the rest), the local refusal to run
browser tests without uilab, and the tripwire. What static reading cannot
see -- `importlib` tricks, a path handed to `spec_from_file_location` -- the
tripwire catches at run time (tests/conftest.py): a module classified as
starting no browser that launches one fails that test, on every machine.
"""
from __future__ import annotations

import ast
import functools
import heapq
import json
import os
import warnings
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER_DIRS = (ROOT / "tests", ROOT / "tools")
BROWSER_PACKAGES = ("uilab", "playwright")
SERVER_NAMES = ("serve_ui", "serve_ui_live")

# `spread` says "these cases may leave their file". For most of them that is
# free -- test_api.py is spread because it is hundreds of fast in-process
# cases. For a VIEWPORT SWEEP it is not: every case boots its own uvicorn
# fixture AND its own Chromium, so one group per case let ~20 browsers start
# at once and the workers starved each other. Measured 2026-09-20 on the same
# tree: 8 workers went 21, 10 and 19 failed across three full runs -- always
# the sweep, always a different overlapping subset of widths, always the Rank
# board still reading "Loading the leaderboard..." -- while 4 workers passed
# 11016 twice. Cold `/api/leaderboard` is 1431 ms and warm 115 ms, so nothing
# there is slow; the machine was starved.
#
# So the CONCURRENCY is bounded where it is actually expensive. Same group ->
# same worker -> sequential under `--dist loadgroup`, so these files share
# ONE pool of BROWSER_SWEEP_GROUPS groups and never put more than that many
# sweep browsers up at once. The same groups are the units a GitHub job takes.
#
# Keyed on a stable hash of the nodeid, NEVER on collection index: testmon
# selects subsets and reruns reorder, and an index would then move a case
# between groups from run to run, which is a flake source rather than a fix.
BROWSER_SWEEP_GROUPS = 4
BROWSER_SWEEPS = (
    "tests/test_responsive.py",
    "tests/test_responsive_bowser.py",
    "tests/test_responsive_subsections.py",
)
DURATIONS_PATH = ROOT / "tests" / "test_durations.json"


def browser_sweep_group(nodeid: str) -> str:
    """The bounded group a viewport case belongs to: a pure function of the
    nodeid, so testmon's subsets and reruns never move a case between groups."""
    return f"browser_sweep_{zlib.crc32(nodeid.encode()) % BROWSER_SWEEP_GROUPS}"


def _package_of(module: str) -> str:
    return module.split(".")[0]


def _local_helper(module: str) -> Path | None:
    """`ui_fixture`, `tools.ui_fixture` or `tests.source_scan` -> the file."""
    parts = module.split(".")
    if len(parts) == 2 and parts[0] in ("tools", "tests"):
        candidate = ROOT / parts[0] / f"{parts[1]}.py"
        return candidate if candidate.is_file() else None
    if len(parts) != 1:
        return None
    for directory in HELPER_DIRS:
        candidate = directory / f"{module}.py"
        if candidate.is_file():
            return candidate
    return None


def _parse(source: str, filename: str) -> ast.Module:
    # Reading, not compiling for use: an invalid escape in some test's JS
    # string is that file's own warning to give when pytest imports it.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return ast.parse(source, filename=filename)


def _module_level(tree: ast.Module):
    """Statements that run on import: descend into if/try/with, never into a
    function body (a lazy import inside a helper runs only if called)."""
    pending = list(tree.body)
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        yield node
        pending.extend(ast.iter_child_nodes(node))


def _import_reason(node: ast.AST, *, helper_depth: int) -> str | None:
    if isinstance(node, ast.Import):
        for alias in node.names:
            if _package_of(alias.name) in BROWSER_PACKAGES:
                return f"imports {alias.name}"
            helper = _local_helper(alias.name)
            if helper is not None and (why := _helper_reason(helper, helper_depth)):
                return f"imports {alias.name}, which {why}"
    elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
        if _package_of(node.module) in BROWSER_PACKAGES:
            return f"imports from {node.module}"
        named = [alias.name for alias in node.names if alias.name in SERVER_NAMES]
        if named:
            return f"imports {named[0]} from {node.module}"
        helper = _local_helper(node.module)
        if helper is not None and (why := _helper_reason(helper, helper_depth)):
            return f"imports from {node.module}, which {why}"
    return None


@functools.lru_cache(maxsize=None)
def _helper_reason(path: Path, depth: int) -> str | None:
    if depth > 4:
        return None  # A cycle or a deep chain; the run-time tripwire still guards it.
    try:
        tree = _parse(path.read_text(encoding="utf-8"), str(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None
    for node in _module_level(tree):
        if why := _import_reason(node, helper_depth=depth + 1):
            return why
    return None


def browser_reason_in_source(source: str, filename: str = "<test module>") -> str | None:
    """Why a test module with this source starts a browser or the UI fixture
    server, or None. The whole file counts: its functions ARE the tests."""
    try:
        tree = _parse(source, filename)
    except SyntaxError:
        return None  # Collection reports it; a lane cannot fix a syntax error.
    for node in ast.walk(tree):
        if why := _import_reason(node, helper_depth=0):
            return why
        if isinstance(node, ast.Attribute) and node.attr in SERVER_NAMES:
            return f"calls {node.attr}"
    return None


@functools.lru_cache(maxsize=None)
def browser_reason(path: Path) -> str | None:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return browser_reason_in_source(source, str(path))


def lane_of(path: Path) -> str:
    return "browser" if browser_reason(Path(path).resolve()) else "nonbrowser"


# The lane of the test running now, set around every test by tests/conftest.py;
# inherited by any subprocess it starts, so the tripwire holds wherever it reaches.
LANE_ENV = "SM64_TEST_LANE"


def refusal(what: str) -> str:
    test = os.environ.get("PYTEST_CURRENT_TEST", "this test").split(" ")[0]
    return (f"refused to {what} for {test}: tools/test_lanes.py classifies its module as "
            "starting no browser, so the blast radius may run it where no browser is "
            "expected. Import uilab, playwright or serve_ui in the test module itself so "
            "it is known as a browser module.")


def refuse_outside_browser_modules(what: str) -> None:
    if os.environ.get(LANE_ENV) == "nonbrowser":
        raise RuntimeError(refusal(what))


def is_test_module(path: Path) -> bool:
    return path.suffix == ".py" and path.name.startswith("test_")


# --- splitting the full run across GitHub jobs -------------------------------

def shard_unit(nodeid: str) -> str:
    """The piece a job takes whole. A file, because its module-scoped server
    and browser are built once per file; a sweep case goes with its bounded
    group instead, because each case builds its own."""
    path = nodeid.split("::")[0]
    return browser_sweep_group(nodeid) if path in BROWSER_SWEEPS else path


def load_durations(path: Path = DURATIONS_PATH) -> dict[str, float]:
    try:
        return {unit: float(seconds) for unit, seconds in
                json.loads(path.read_text(encoding="utf-8"))["units"].items()}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def default_duration(durations: dict[str, float]) -> float:
    """What a unit nobody has timed yet is assumed to cost: the median file.
    A new browser file is usually one server and a few pages."""
    if not durations:
        return 30.0
    ordered = sorted(durations.values())
    return ordered[len(ordered) // 2]


def plan_shards(units: list[str], count: int,
                durations: dict[str, float]) -> dict[str, int]:
    """unit -> shard number (1-based), longest unit first onto the least
    loaded shard. Pure and deterministic: every job and every xdist worker
    computes the same plan from the same collection, so nothing coordinates."""
    if count < 1:
        raise ValueError("shard count must be at least 1")
    fallback = default_duration(durations)
    ordered = sorted(set(units), key=lambda unit: (-durations.get(unit, fallback), unit))
    loads = [(0.0, shard) for shard in range(1, count + 1)]
    heapq.heapify(loads)
    plan = {}
    for unit in ordered:
        load, shard = heapq.heappop(loads)
        plan[unit] = shard
        heapq.heappush(loads, (load + durations.get(unit, fallback), shard))
    return plan


def parse_shard(text: str) -> tuple[int, int]:
    index, _, count = text.partition("/")
    if not (index.isdecimal() and count.isdecimal()) or not 1 <= int(index) <= int(count):
        raise ValueError(f"--shard wants K/N with 1 <= K <= N, got {text!r}")
    return int(index), int(count)


# --- the full run's one retry ------------------------------------------------
# Only failures that say the MACHINE could not stand the page up, never what
# the page showed. pytest-rerunfailures matches each pattern against
# "<ExceptionType>: <message>"; every AssertionError is excluded outright, so a
# wrong value is never retried however its message reads.
SETUP_ERRORS = (
    r"fixture server failed to (start|stop)",           # tools/ui_fixture.py boot
    r"urlopen error timed out|TimeoutError: timed out",  # fixture seeding POSTs
    r"WinError 10055|ERR_NO_BUFFER_SPACE",              # socket buffers exhausted
    r"Target page, context or browser has been closed|Browser has been closed"
    r"|browser has disconnected|Target crashed|Page crashed",
)
NEVER_RERUN = (r"^AssertionError",)


def rerun_args() -> list[str]:
    args = ["--reruns", "1"]
    for pattern in SETUP_ERRORS:
        args += ["--only-rerun", pattern]
    for pattern in NEVER_RERUN:
        args += ["--rerun-except", pattern]
    return args
