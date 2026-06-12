"""The proactor ConnectionResetError noise filter (server/app.py).

Browsers abort in-flight Range requests on every <video> seek; on Windows'
proactor loop asyncio's own connection_lost callback then raises
ConnectionResetError (WinError 10054). The handler must swallow exactly
that and delegate everything else to the default handler."""
from sm64_events.server.app import _quiet_connection_resets


class FakeLoop:
    def __init__(self):
        self.delegated = []

    def default_exception_handler(self, context):
        self.delegated.append(context)


def test_connection_reset_quieted_everything_else_delegates():
    loop = FakeLoop()
    _quiet_connection_resets(
        loop, {"exception": ConnectionResetError(10054, "reset"),
               "message": "connection lost"})
    assert loop.delegated == []                  # the one quieted case

    real = {"exception": RuntimeError("real bug"), "message": "x"}
    _quiet_connection_resets(loop, real)
    assert loop.delegated == [real]              # real errors pass through

    no_exc = {"message": "callback context without exception"}
    _quiet_connection_resets(loop, no_exc)
    assert loop.delegated == [real, no_exc]


def test_replay_stop_bounded_abandons_a_wedged_teardown(monkeypatch):
    """Shutdown liveness (live incident 2026-06-12: CTRL+C hung forever):
    a wedged replay teardown must not block the event loop past the
    deadline — the worker is a daemon thread, so abandoning it cannot
    block interpreter exit either."""
    import asyncio
    import time

    from sm64_events.server import app as app_mod

    monkeypatch.setattr(app_mod, "_REPLAY_STOP_DEADLINE_S", 0.2)

    class Wedged:
        def lifecycle_stop(self):
            time.sleep(3.0)          # daemon thread sleeps on, harmlessly

    t0 = time.monotonic()
    asyncio.run(app_mod._stop_replay_bounded(Wedged()))
    assert time.monotonic() - t0 < 1.5   # gave up at the deadline


def test_replay_stop_bounded_fast_path():
    import asyncio

    from sm64_events.server import app as app_mod

    calls = []

    class Quick:
        def lifecycle_stop(self):
            calls.append(1)

    asyncio.run(app_mod._stop_replay_bounded(Quick()))
    assert calls == [1]


def test_ui_responses_force_revalidation():
    """Stale-module incident 2026-06-12: with no Cache-Control, the browser
    served a cached store.js (no togglePause) next to a fresh header.js —
    a dead pause button. / and /ui/* must always say no-cache; /api stays
    untouched."""
    import asyncio

    from fastapi.testclient import TestClient

    from sm64_events.server.app import create_app
    from sm64_events.server.broadcaster import Broadcaster

    class NoMemory:
        attached = False
        def attach(self):
            return False
        def detach(self):
            pass

    class PollerStub:
        memory = NoMemory()
        latest = None
        paused = False
        async def run(self):
            await asyncio.sleep(3600)
        def set_paused(self, p):
            self.paused = p

    with TestClient(create_app(PollerStub(), Broadcaster())) as c:
        assert c.get("/").headers["cache-control"] == "no-cache"
        r = c.get("/ui/store.js")
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-cache"
        assert "cache-control" not in c.get("/api/pause").headers
