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


def test_a_connection_accepted_while_the_server_closes_is_quieted():
    """SM64-TRAINER-D: the proactor accept loop hands a connection to a server
    uvicorn already closed, and asyncio's `Server._attach` asserts. Reproduced
    on a real closed server; an AssertionError anywhere else still reports."""
    import asyncio

    async def closed_attach():
        server = await asyncio.get_running_loop().create_server(asyncio.Protocol, "127.0.0.1", 0)
        server.close()
        await server.wait_closed()
        try:
            server._attach(object())
        except AssertionError as error:
            return error
        raise AssertionError("asyncio no longer asserts on a closed server; drop this filter")

    race = {"exception": asyncio.run(closed_attach()),
            "message": "Exception in callback BaseProactorEventLoop._start_serving.<locals>.loop()"}
    loop = FakeLoop()
    _quiet_connection_resets(loop, race)
    assert loop.delegated == []

    try:
        raise AssertionError("a real assertion")
    except AssertionError as error:
        elsewhere = {"exception": error, "message": "x"}
    _quiet_connection_resets(loop, elsewhere)
    assert loop.delegated == [elsewhere]


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
        pr = c.get("/api/pause")
        assert "cache-control" not in pr.headers
        assert pr.json() == {"paused": False, "reason": None}


def test_pause_state_reasons_and_precedence():
    """PauseReason contract: manual (button) outranks afk (idle gate);
    afk shows whenever the recorder is idle and nothing is manual; the
    poller never pauses for afk (it hosts the activity tap that detects
    the player's return)."""
    from types import SimpleNamespace

    from sm64_events.server.app import pause_state

    def replay_stub(idle):
        rec = SimpleNamespace(status=lambda: {"idle": idle})
        return SimpleNamespace(recorder=rec)

    poller = SimpleNamespace(paused=False)
    assert pause_state(poller, None) == {"paused": False, "reason": None}
    assert pause_state(poller, replay_stub(False)) == \
        {"paused": False, "reason": None}
    assert pause_state(poller, replay_stub(True)) == \
        {"paused": True, "reason": "afk"}

    poller.paused = True
    assert pause_state(poller, replay_stub(True)) == \
        {"paused": True, "reason": "manual"}     # manual outranks afk
    assert pause_state(poller, None) == {"paused": True, "reason": "manual"}
