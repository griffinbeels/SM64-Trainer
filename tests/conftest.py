"""Session-wide test guards."""
import pytest

from sm64_events.core import perfmon, recorder_lock


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
    from sm64_events.core import paths
    monkeypatch.setattr(paths, "rank_standards_path",
                        lambda: tmp_path / "rank_standards.json")
    monkeypatch.setattr(paths, "bundled_rank_standards", lambda: None)
