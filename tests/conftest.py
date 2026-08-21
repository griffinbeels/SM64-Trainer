"""Session-wide test guards."""
import asyncio
import sys
from pathlib import Path

import pytest

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
