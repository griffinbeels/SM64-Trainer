"""Session-wide test guards."""
import pytest

from sm64_events.core import perfmon


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
