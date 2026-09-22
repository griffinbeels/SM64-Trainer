"""Session-wide test guards."""
import asyncio
import json
import os
import re
import sys
from pathlib import Path

import pytest
from _pytest.fixtures import reorder_items

# tests/ has no __init__.py, so pytest puts THIS directory on sys.path and the
# shared helper imports bare: `from source_scan import strip_comments`. The
# repo root goes on too (appended, so it can never shadow an installed package)
# because `from tests.source_scan import ...` is the form people reach for
# first, and getting it wrong is not a normal test failure — an ImportError at
# collection time aborts the ENTIRE suite before anything runs (2026-07-25,
# test_ui_empty_states.py did exactly that on main). Both forms now resolve.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.append(_REPO_ROOT)

from sm64_events.core import perfmon, recorder_lock
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.storage.db import Database
from sm64_events.tracking.service import TrackerService
from tools.test_lanes import (BROWSER_SWEEP_GROUPS, BROWSER_SWEEPS,  # noqa: F401 (tests read these here)
                              LANE_ENV, browser_sweep_group, file_totals,
                              is_test_module, lane_of, load_durations, parse_shard,
                              plan_shards, refusal, shard_unit)


def pytest_addoption(parser):
    group = parser.getgroup("sm64 selection", "what this run covers (tools/run_tests.py)")
    group.addoption("--select-from", default=None, metavar="FILE",
                    help="JSON {'files': {test file: null | [nodeids]}} -- the merge check's "
                         "blast radius (tools/blast_radius.py); nothing else is collected")
    group.addoption("--shard", default=None, metavar="K/N",
                    help="run only job K of N of the whole suite, balanced by "
                         "tests/test_durations.json")
    group.addoption("--lane", default=None, choices=("browser", "nonbrowser"),
                    help="only the modules tools/test_lanes.py puts in this lane: the full "
                         "run gives browser tests and the rest their own jobs and worker counts")


SELECTION = pytest.StashKey[dict]()


def _selection(config) -> dict | None:
    if SELECTION not in config.stash:
        path = config.getoption("select_from", None)
        config.stash[SELECTION] = (json.loads(Path(path).read_text(encoding="utf-8"))["files"]
                                   if path else None)
    return config.stash[SELECTION]


def pytest_ignore_collect(collection_path, config):
    """A blast-radius run never imports a test module outside the radius. Paths
    named on the command line are never ignored."""
    selection = _selection(config)
    if selection is not None and is_test_module(collection_path):
        relative = collection_path.relative_to(config.rootpath).as_posix()
        if relative not in selection:
            return True
    lane = config.getoption("lane", None)
    if lane and is_test_module(collection_path) and lane_of(collection_path) != lane:
        return True
    return None


def _arm_the_browser_tripwire() -> None:
    """Only a module tools/test_lanes.py puts in the browser set may start a
    browser or the UI fixture server. The classifier reads source, so a launch
    it cannot see -- an importlib trick, a tool loaded by path -- would put
    Chromium where the blast radius and the fallback set promise there is none.
    The launch fails that test instead, naming the rule. The per-test lane rides
    in an environment variable (set around each test below) so subprocesses
    and the fixture server's own check in `tools/ui_fixture.py` see it too."""
    try:
        from playwright.sync_api import BrowserType
    except ImportError:
        return
    for name in ("launch", "launch_persistent_context", "connect", "connect_over_cdp"):
        original = getattr(BrowserType, name)

        def guarded(self, *args, _original=original, **kwargs):
            if os.environ.get(LANE_ENV) == "nonbrowser":
                raise RuntimeError(refusal("launch a browser"))
            return _original(self, *args, **kwargs)
        setattr(BrowserType, name, guarded)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    previous = os.environ.get(LANE_ENV)
    os.environ[LANE_ENV] = "browser" if lane_of(Path(str(item.path))) == "browser" else "nonbrowser"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(LANE_ENV, None)
        else:
            os.environ[LANE_ENV] = previous


# Pages a test opened, so a failure can be photographed while the page is
# still up. Only on the full run (SM64_REPORT_DIR names where its artifacts
# go); a local run keeps nothing.
_OPEN_PAGES: list = []


def _keep_failure_screenshots() -> None:
    try:
        from playwright.sync_api import Browser, BrowserContext
    except ImportError:
        return
    for owner in (Browser, BrowserContext):
        def new_page(self, *args, _original=owner.new_page, **kwargs):
            page = _original(self, *args, **kwargs)
            _OPEN_PAGES[:] = [kept for kept in _OPEN_PAGES if not kept.is_closed()][-5:]
            _OPEN_PAGES.append(page)
            return page
        owner.new_page = new_page


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    report = (yield).get_result()
    if not (report.failed and _OPEN_PAGES and os.environ.get("SM64_REPORT_DIR")):
        return
    folder = Path(os.environ["SM64_REPORT_DIR"]) / "screenshots"
    stem = re.sub(r"[^A-Za-z0-9_.-]", "_", item.nodeid)[-150:]
    for index, page in enumerate(_OPEN_PAGES):
        try:
            if not page.is_closed():
                folder.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(folder / f"{stem}-{report.when}-{index}.png"), timeout=5000)
        except Exception as error:  # noqa: BLE001 -- a crashed browser gives no picture, never a second failure
            report.sections.append(("screenshot", f"page {index} not captured: {error}"))


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    """Direct pytest shares the runner's admission and CPU budget too.

    Configure precedes xdist worker creation. Workers and runner-owned pytest
    inherit the live ancestor's budget and must never acquire it a second time.
    """
    _arm_the_browser_tripwire()
    if os.environ.get("SM64_REPORT_DIR"):
        _keep_failure_screenshots()
    # Browser waits get a bound that scales with the machine this run ACTUALLY
    # gets. uilab's 10s default suits one browser on an idle box; this suite
    # runs several servers, browsers and node drivers at once, and when OBS is
    # open the runner deliberately caps itself to a quarter of the CPUs so his
    # capture never stutters. A page that paints in under a second alone took
    # more than 20s under that cap (2026-09-19), which failed a merge.
    #
    # A bound is not a timing assertion: a page that never renders still
    # fails, and a test that means "within 200 ms" still passes its own
    # timeout_ms. What it must not do is report a busy machine as a defect.
    from tools.test_resources import obs_is_open
    # A GitHub runner is the slow machine too: four CPUs shared by a page, its
    # server and Chromium's processes (a 30 s wait timed out there, 2026-09-21).
    from tools.test_resources import dedicated_machine
    os.environ.setdefault("UILAB_WAIT_MS", "60000" if obs_is_open() or dedicated_machine() else "30000")
    from tools.test_resources import TestResources, WORKERS_ENV, effective_workers, inherited_owner

    if hasattr(config, "workerinput"):
        return
    try:
        requested = effective_workers(getattr(config.option, "tx", []) or [])
    except ValueError as error:
        raise pytest.UsageError(str(error)) from error
    if inherited_owner():
        workers = min(requested, int(os.environ[WORKERS_ENV]))
    else:
        # A whole-lane run takes a slot; a narrowed one never queues.
        resources = TestResources(requested, admit=_whole_suite(config))
        resources.__enter__()
        config.add_cleanup(lambda: resources.__exit__(None, None, None))
        workers = resources.workers
    if requested:
        config.option.numprocesses = workers
        config.option.tx = ["popen"] * workers
        if workers == 0:
            config.option.dist = "no"


# Where each item sat in the raw collection, stamped before any plugin
# touches the list, so the order can be put back afterwards (below).
RAW_INDEX = pytest.StashKey[int]()

# Files that touch ONE REAL file on disk and so may never run beside each
# other, whatever worker is free. A file's own name is its group otherwise.
#
# `test_ui_sync_page.py` is the one test in this project that writes a real
# PUT into `data/version_sync/jp.json` (it backs the file up and restores it,
# by design -- the dashboard has to be driven against the real store), and
# `test_layout_matches_report.py` READS that same path to catch layout drift,
# skipping when it is absent. Under 24 workers those overlapped: the reader
# found the writer's throwaway report mid-run and went red on a `failed`
# verdict for a gate the layout ships, then the file vanished and the failure
# could not be reproduced alone (2026-09-05). One group, no overlap.
def _own_group(nodeid: str) -> str:
    """A worker group for one test. xdist appends `@<group>` to the nodeid,
    and JUnit splits that on `::`, so a group holding `::` read back from a
    full run's report as a different test; `@`, `[`, `]` break xdist itself."""
    return re.sub(r"[^A-Za-z0-9_./-]", "_", nodeid)


SHARED_GROUPS = {
    "tests/test_ui_sync_page.py": "version_sync_report",
    "tests/test_layout_matches_report.py": "version_sync_report",
}

# The viewport sweeps' bounded pool (BROWSER_SWEEPS, BROWSER_SWEEP_GROUPS,
# browser_sweep_group) lives in tools/test_lanes.py with its measurement,
# because the GitHub browser run splits its jobs along the same groups.
# Imported above; tests/test_worker_groups.py reads it from here.
SHARD_UNIT = pytest.StashKey[str]()


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_collection_modifyitems(config, items):
    """Every test carries a WORKER GROUP for pytest-xdist's `loadgroup`
    scheduler: its own file by default, so a module's one-server-one-browser
    fixture is built once and its tests keep their order -- exactly what
    `--dist loadfile` gives, which is why the full suite passed under 16
    workers on the first try (2026-09-01). A test marked `spread` is its own
    group instead, so its cases leave the file and land on whichever worker
    is free: that is how a sweep parametrised per viewport stops being one
    three-minute unit. A file in `BROWSER_SWEEPS` is spread into a BOUNDED
    pool instead -- same freedom to leave the file, a ceiling on how many of
    its browsers stand up together; the reason is on that constant.
    Group names must carry no `@` or `]` -- xdist appends
    `@<group>` to the nodeid and splits it back on those two characters.
    `tryfirst` because xdist's worker reads the marks in ITS hook of the same
    name, and this conftest registers before that worker plugin does -- so
    without it the marks arrive one hook too late and every file spreads.

    A HOOKWRAPPER since 2026-09-02, because the group alone did not keep a
    file's order: pytest-testmon's `--testmon-noselect` -- the flag the
    merge gate runs under to refresh its map -- is documented as "reorder
    and prioritize the tests most likely to fail first", and does it in a
    `trylast` impl of this same hook once a map exists. Measured on the
    fixture-reach file: one worker without the flag ran it in file order,
    built its module-scoped page twice and passed 81/81 in 41 s; one worker
    WITH the flag ran a scrambled order, built the page 38 times, and went
    3 failed + 7 reruns in 161 s with no other load on the machine -- tests
    that assume the Practice tab ran on a page a story test had just left
    on Segments. The map only exists after a first full run, which is why
    the first gate in a fresh worktree was green and every one after it
    red on a different set (2026-09-01/02). The post-yield half runs after
    EVERY non-wrapper impl, testmon's included: it puts the items back in
    raw collection order and re-applies pytest's own parametrised-fixture
    grouping (`reorder_items`, the thing that keeps both viewports of a
    module-scoped page together), so what a plugin does to the order can
    never reach the workers. `tests/test_worker_groups.py` compares the
    live session's order against that recipe.

    `--shard K/N` keeps one GitHub job's share of the whole suite. The unit
    is read BEFORE the yield: xdist's worker appends `@<group>` to the nodeid
    in its own impl, and the unit is a function of the plain id."""
    selection = _selection(config)
    if selection is not None:
        outside = [item for item in items
                   if (chosen := selection.get(item.nodeid.split("::")[0])) is not None
                   and item.nodeid not in chosen]
        if outside:
            config.hook.pytest_deselected(items=outside)
            dropped = set(map(id, outside))
            items[:] = [item for item in items if id(item) not in dropped]
    sharded = bool(config.getoption("shard"))
    durations = load_durations()
    totals = file_totals(durations)
    for index, item in enumerate(items):
        item.stash[RAW_INDEX] = index
        item.stash[SHARD_UNIT] = shard_unit(item.nodeid, totals)
        if sharded and item.stash[SHARD_UNIT] == item.nodeid:
            # A split file's test (a sweep case among them): either of the
            # job's workers may take it; the job's two workers are the bound.
            group = _own_group(item.nodeid)
        elif item.nodeid.split("::")[0] in BROWSER_SWEEPS:
            group = browser_sweep_group(item.nodeid)
        elif item.get_closest_marker("spread"):
            group = _own_group(item.nodeid)
        else:
            group = SHARED_GROUPS.get(item.nodeid.split("::")[0],
                                      item.nodeid.split("::")[0])
        item.add_marker(pytest.mark.xdist_group(group))
    yield
    if config.getoption("shard"):
        index, count = parse_shard(config.getoption("shard"))
        plan = plan_shards([item.stash[SHARD_UNIT] for item in items], count, durations)
        elsewhere = [item for item in items if plan[item.stash[SHARD_UNIT]] != index]
        if elsewhere:
            config.hook.pytest_deselected(items=elsewhere)
            items[:] = [item for item in items if plan[item.stash[SHARD_UNIT]] == index]
    items.sort(key=lambda item: item.stash.get(RAW_INDEX, len(items)))
    items[:] = reorder_items(items)


@pytest.fixture(autouse=True, scope="session")
def _redirect_perf_log(tmp_path_factory):
    """No test may write the PRODUCTION data/perf_log.jsonl. Many tests build
    the app via create_app under TestClient, whose lifespan runs PerfMonitor —
    a test-process sample appended to the real file would corrupt the session
    data tools/analyze_perf_log.py reads. PerfMonitor resolves _DEFAULT_LOG at
    construction (the _USE_DEFAULT sentinel), so redirecting the module global
    for the whole session covers every fixture scope."""
    orig = perfmon._DEFAULT_LOG
    perfmon._DEFAULT_LOG = tmp_path_factory.mktemp("perf") / "perf_log.jsonl"
    yield
    perfmon._DEFAULT_LOG = orig


@pytest.fixture(autouse=True)
def _isolate_recorder_lock(tmp_path, monkeypatch):
    """No test may touch the REAL machine-wide recorder lock — a live server
    (or another test) holds it, which would make recorder tests flaky. Redirect
    it to a per-test temp path (acquire_recorder_lock reads the global at call
    time, so this takes effect even for the import-time-built app)."""
    monkeypatch.setattr(recorder_lock, "RECORDER_LOCK_PATH",
                        tmp_path / "recorder.lock")


@pytest.fixture(autouse=True)
def _isolate_rank_standards(tmp_path, monkeypatch):
    """No test may write the real rank-standards store.

    KNOWN LIMIT, measured 2026-08-21 and stated here because it looked like
    total cover for two years: this rebinds the attribute on the `paths`
    MODULE, so it reaches callers that do `paths.rank_standards_path()` and
    NOT ones holding a `from ... import rank_standards_path` alias taken at
    import time. `tools/ui_fixture.py` held exactly such an alias, so every
    driven test went straight past this fixture and wrote the worktree's own
    `data/rank_standards.json` -- until one of them cleared four strategies
    and the next full suite came back with 6 failures and 4 errors in four
    unrelated files.

    `serve_ui` now builds its store under its own scratch dir and never names
    the shared path, which is enforced by
    `test_fixture_reaches_the_real_page.py::
    test_the_fixture_never_reaches_for_the_shared_ladder_store` rather than
    left to this fixture. Any NEW consumer that imports the name directly is
    outside this patch's reach too -- prefer `paths.rank_standards_path()`.
    """
    from sm64_events.core import paths
    monkeypatch.setattr(paths, "rank_standards_path",
                        lambda: tmp_path / "rank_standards.json")
    monkeypatch.setattr(paths, "bundled_rank_standards", lambda: None)


@pytest.fixture(autouse=True)
def _isolate_ui_log(tmp_path, monkeypatch):
    """No test may append to the checkout's own UI log.

    A browser-driven test serves the REAL page, and the page posts what it
    painted back to the app, which appends to the log at the data root -- from
    source, this checkout's `data/`. Measured 2026-09-01: one full suite run
    left 856 fixture paints in the dev log that `tools/what_happened.py` then
    interleaved with real play. Under 16 parallel workers it is also a race
    between appends and the log's own trim.

    `uilog` holds `data_root` as an import-time alias, so the alias is what is
    rebound (the KNOWN LIMIT above, read the other way round: patching
    `paths.data_root` would never reach it). `tests/test_uilog.py::
    test_no_test_can_reach_the_checkouts_own_ui_log` proves this is active.
    """
    from sm64_events.core import uilog
    monkeypatch.setattr(uilog, "data_root", lambda: tmp_path)


@pytest.fixture
def service(tmp_path):
    """A fully-started TrackerService over a fresh throwaway db -- same
    construction as tests/test_tracker_service.py::make, shared here for
    tests that only exercise the command surface (KV round-trips,
    broadcasts) and don't need session/journal internals of their own."""
    db = Database(tmp_path / "t.db")
    svc = TrackerService(db, Broadcaster())
    asyncio.run(svc.start())
    return svc


@pytest.fixture(scope="session")
def modern_gl():
    """The native GL witnesses need an OpenGL 3.3+ driver: a GitHub runner has
    none, only Windows' GDI OpenGL 1.1, and every witness would fail inside its
    host on the loader assertion. Skips with the reason `tests/gl_probe.py`
    measured, which tests/skip_inventory.py lists."""
    from gl_probe import missing_modern_gl
    if (why := missing_modern_gl()) is not None:
        pytest.skip(why)


@pytest.fixture(scope="session")
def runtime_supervisor_exe(tmp_path_factory):
    """`runtime_supervisor_host.c` over the four real runtime objects.

    tests/test_runtime_supervisor.py (the watchdog against a blocked delivery
    facade) and tests/test_gpudemand_native.py (a Python renew/revoke thread
    against the same control IPC) drive the SAME host binary. Each used to
    build it itself, so one run paid two identical MSVC passes for one
    artifact. Built here once and shared by both.
    """
    import importlib.util

    work = tmp_path_factory.mktemp("runtime_supervisor")
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "runtime_supervisor_build", root / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vcvars = build.find_vcvars32()
    assert vcvars, "the x86 MSVC toolchain (vcvars32.bat) is required"
    native = root / "plugin/gfxwrap"
    names = ["gpu_request", "runtime_control", "runtime_delivery",
             "runtime_supervisor_fake"]
    flags = [flag for flag in build.COMMON_FLAGS if not flag.startswith("/std:")]
    build._cl(vcvars, flags + ["/std:c++17", "/EHsc", "/c", f"/I{native}",
        *(str(native / f"{name}.cpp") for name in names), f"/Fo{work}\\"], work)
    target = work / "runtime_supervisor.exe"
    build._cl(vcvars, build.COMMON_FLAGS + [f"/I{native}",
        str(native / "runtime_supervisor_host.c"),
        *(str(work / f"{name}.obj") for name in names),
        f"/Fo{work}\\", f"/Fe:{target}", "/link", *build.LIBS], work)
    return target


# --- the skip inventory -----------------------------------------------------
# A skip is invisible: the gate prints one number and 67 of them can mean
# "three vendors' hardware is absent" or "the rendered gate switched itself off
# and 321 browser tests did nothing". Both read the same, and on 2026-09-17 the
# second was true in every .codex worktree. So a whole-suite run fails on a
# skip whose reason is not documented in tests/skip_inventory.py.
#
# WHOLE-SUITE ONLY: `pytest tests/test_x.py` or a `-k` run may skip freely --
# only the run that claims to have covered everything has to account for what
# it did not run. Reports arrive here on the xdist CONTROLLER, so one list
# holds every worker's skips.
_SKIPS: list[tuple[str, str]] = []
# The full run retries a SETUP failure once (tools/test_lanes.py). A test
# that needed it is FLAKY, not green: said out loud, and handed to the job
# summary, so a machine problem that recurs cannot hide behind the retry.
_RERUNS: dict[str, str] = {}
_FAILED: set[str] = set()


def _skip_reason(report) -> str:
    longrepr = getattr(report, "longrepr", None)
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        return str(longrepr[2]).removeprefix("Skipped: ")
    return str(longrepr or "")


def _first_line(report) -> str:
    crash = getattr(getattr(report, "longrepr", None), "reprcrash", None)
    text = crash.message if crash is not None else str(getattr(report, "longrepr", "") or "")
    return text.strip().splitlines()[0] if text.strip() else ""


def pytest_runtest_logreport(report):
    # `wasxfail` also arrives as "skipped"; an xfail is a tracked defect with
    # its own reason on the mark, not an untested path.
    if report.skipped and not hasattr(report, "wasxfail"):
        _SKIPS.append((report.nodeid, _skip_reason(report)))
    outcome = getattr(report, "outcome", None)
    if outcome == "rerun":
        _RERUNS.setdefault(report.nodeid, _first_line(report))
    elif outcome == "failed":
        _FAILED.add(report.nodeid)


def pytest_collectreport(report):
    """A MODULE-level skip never produces a test report, and that is the
    dangerous kind: `pytest.skip(..., allow_module_level=True)` is what every
    browser file does when uilab is absent, so the version of this guard that
    only watched test reports proved nothing about the 321 tests it was
    written for. Measured 2026-09-17: a planted module skip sailed through a
    whole-suite run reporting "0 undocumented"."""
    if report.skipped:
        _SKIPS.append((report.nodeid or "<collection>", _skip_reason(report)))


def _whole_suite(config) -> bool:
    # SM64_SKIP_AUDIT=1 forces the check on a narrowed run. It exists so the
    # guard itself can be tested in seconds instead of a 15-minute suite --
    # and an untested guard is the thing this file is here to prevent.
    if os.environ.get("SM64_SKIP_AUDIT") == "1":
        return True
    return not getattr(config.option, "file_or_dir", None) \
        and not getattr(config.option, "keyword", "") \
        and not getattr(config.option, "select_from", None)


def _report_reruns() -> None:
    if not _RERUNS:
        return
    lines = [f"{'FAILED after rerun' if nodeid in _FAILED else 'FLAKY'} {nodeid}: {cause}"
             for nodeid, cause in sorted(_RERUNS.items())]
    print("\n" + "\n".join(lines))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
            summary.write("".join(f"- {line}\n" for line in lines))
    if os.environ.get("SM64_REPORT_DIR"):
        report = Path(os.environ["SM64_REPORT_DIR"]) / "reruns.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps([
            {"nodeid": nodeid, "cause": cause, "flaky": nodeid not in _FAILED}
            for nodeid, cause in sorted(_RERUNS.items())], indent=1), encoding="utf-8")


def pytest_sessionfinish(session, exitstatus):
    if hasattr(session.config, "workerinput"):
        return
    _report_reruns()
    if not _whole_suite(session.config):
        return
    import skip_inventory
    undocumented = [(nodeid, reason) for nodeid, reason in _SKIPS
                    if skip_inventory.allowed_for(reason) is None]
    # Say the number out loud even when it is fine: "67 skipped" in the gate's
    # summary is the line that hid 321 disabled browser tests, and a silent
    # guard is indistinguishable from a guard that never ran.
    categories = {skip_inventory.allowed_for(reason)[0] for _, reason in _SKIPS
                  if skip_inventory.allowed_for(reason) is not None}
    print(f"\nskip inventory: {len(_SKIPS) - len(undocumented)} documented skips "
          f"in {len(categories)} categories, {len(undocumented)} undocumented")
    if not undocumented:
        return
    session.exitstatus = 1
    if os.environ.get("SM64_REPORT_DIR"):
        report = Path(os.environ["SM64_REPORT_DIR"]) / "undocumented-skips.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps([{"nodeid": n, "reason": r} for n, r in undocumented], indent=1),
                          encoding="utf-8")
    shown = "\n".join(f"  {nodeid}\n    {reason}" for nodeid, reason in undocumented[:20])
    more = f"\n  ... and {len(undocumented) - 20} more" if len(undocumented) > 20 else ""
    print(f"\nUNDOCUMENTED SKIPS ({len(undocumented)}): a whole-suite run must "
          f"account for every test it did not run.\n{shown}{more}\n"
          "Fix the cause, or add a row to tests/skip_inventory.py saying why "
          "this machine cannot run it and what would lift it.")
