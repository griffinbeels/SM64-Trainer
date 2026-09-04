"""Session-wide test guards."""
import asyncio
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


# Where each item sat in the raw collection, stamped before any plugin
# touches the list, so the order can be put back afterwards (below).
RAW_INDEX = pytest.StashKey[int]()


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_collection_modifyitems(items):
    """Every test carries a WORKER GROUP for pytest-xdist's `loadgroup`
    scheduler: its own file by default, so a module's one-server-one-browser
    fixture is built once and its tests keep their order -- exactly what
    `--dist loadfile` gives, which is why the full suite passed under 16
    workers on the first try (2026-09-01). A test marked `spread` is its own
    group instead, so its cases leave the file and land on whichever worker
    is free: that is how a sweep parametrised per viewport stops being one
    three-minute unit. Group names must carry no `@` or `]` -- xdist appends
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
    live session's order against that recipe."""
    for index, item in enumerate(items):
        item.stash[RAW_INDEX] = index
        if item.get_closest_marker("spread"):
            group = re.sub(r"[^A-Za-z0-9_./:-]", "_", item.nodeid)
        else:
            group = item.nodeid.split("::")[0]
        item.add_marker(pytest.mark.xdist_group(group))
    yield
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
