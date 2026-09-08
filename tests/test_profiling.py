"""An instrument must detect a known stall and refuse contaminated sessions."""
import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sm64_events.core import profiling
from sm64_events.server.profile_api import create_profile_router


def test_known_durations_histogram_and_session_expiry():
    clock = [10.]
    p = profiling.Profiler(lambda: clock[0])
    session = p.start(2)["session_id"]
    for ms in [1.] * 98 + [90., 120.]:
        p.record("readback", ms, session_id=session)
    stage = p.snapshot()["stages"]["readback"]
    assert stage["count"] == sum(stage["bucket_counts"]) == 100
    assert stage["p95_ms"] == 1
    assert stage["p99_ms"] == 100
    assert stage["max_ms"] == 120
    assert stage["total_ms"] == 308
    clock[0] = 13
    assert not p.active()
    assert p.snapshot()["elapsed_s"] == 2
    newer = p.start(2)["session_id"]
    assert newer != session
    p.record("late", 5, session_id=session)
    assert p.snapshot()["stages"] == {}
    with pytest.raises(ValueError):
        p.stop(session)
    assert p.active()


def test_disabled_wrapper_never_reads_clock_and_preserves_errors(monkeypatch):
    def forbidden():
        pytest.fail("disabled profiling read its clock")
    p = profiling.Profiler(forbidden)
    monkeypatch.setattr(profiling, "profile", p)

    @profiling.measured("work")
    def work(x):
        if x == 3:
            raise LookupError("original")
        return x + 1

    assert work(2) == 3
    with pytest.raises(LookupError, match="original"):
        work(3)
    assert work.__name__ == "work"


def test_real_wrapper_detects_injected_delay_and_async_exception(monkeypatch):
    clock = [1.]
    p = profiling.Profiler(lambda: clock[0])
    monkeypatch.setattr(profiling, "profile", p)

    @profiling.measured("sample", interval=True)
    def sample(delay):
        clock[0] += delay

    @profiling.measured("async")
    async def failed():
        clock[0] += .25
        raise RuntimeError("keep exception")

    p.start()
    sample(.001)
    clock[0] += .1
    sample(.080)
    with pytest.raises(RuntimeError, match="keep exception"):
        asyncio.run(failed())
    rows = p.snapshot()["stages"]
    assert rows["sample"]["max_ms"] == pytest.approx(80)
    assert rows["sample.interval"]["max_ms"] == pytest.approx(101)
    assert rows["async"]["errors"] == 1
    assert rows["async"]["max_ms"] == pytest.approx(250)


def test_thread_safe_bounded_counts():
    p = profiling.Profiler()
    session = p.start()["session_id"]
    def submit(_):
        for _ in range(1000):
            p.record("parallel", 2, session_id=session)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(submit, range(4)))
    assert p.snapshot()["stages"]["parallel"]["count"] == 4000
    for i in range(200):
        p.record(str(i), 1, session_id=session)
    snap = p.snapshot()
    assert len(snap["stages"]) == profiling.MAX_STAGES
    assert snap["counters"]["stage_limit_rejections"] > 0


def test_profile_api_ownership_expiry_and_bad_requests():
    clock = [1.]
    p = profiling.Profiler(lambda: clock[0])
    app = FastAPI()
    app.include_router(create_profile_router(p))
    with TestClient(app) as client:
        assert client.get("/api/diagnostics/profile").json()["enabled"] is False
        for duration in [0, 301, -1]:
            assert client.post("/api/diagnostics/profile/start", json={"duration_s": duration}).status_code == 422
        result = client.post("/api/diagnostics/profile/start", json={"duration_s": 1})
        assert result.status_code == 201
        session = result.json()["session_id"]
        assert client.post("/api/diagnostics/profile/start", json={}).status_code == 409
        assert client.post("/api/diagnostics/profile/stop", json={"session_id": "a" * 32}).status_code == 409
        assert p.active()
        clock[0] = 3
        assert client.get("/api/diagnostics/profile").json()["enabled"] is False
        assert client.post("/api/diagnostics/profile/stop", json={"session_id": session}).status_code == 200
