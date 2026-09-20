# tests/test_server_runner.py
"""ServerRunner runs the app under uvicorn in a daemon thread and stops it
deterministically (the GUI owns shutdown, not CTRL+C)."""
import socket

from fastapi import FastAPI

from sm64_events.desktop.server_runner import ServerRunner


def _free_port() -> int:
    """Sampled, not held -- ServerRunner takes a port number, which is the
    production contract (the GUI passes a configured port).

    `tools/ui_fixture.py` had the same shape and lost the race on a 16-worker
    run, so anything starting from this number retries rather than reporting a
    collision as a failure: see `_started_runner` below and
    tests/test_ui_fixture_port.py for the fixture's socket-holding fix.
    """
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _started_runner(app, attempts=4):
    """A runner that is actually serving, retrying a port another worker took."""
    for attempt in range(attempts):
        runner = ServerRunner(app, port=_free_port())
        runner.start()
        if runner.wait_until_ready(timeout_s=10):
            return runner
        runner.stop()
        if attempt == attempts - 1:
            raise AssertionError("ServerRunner never became ready")
    raise AssertionError("unreachable")


def test_runner_wires_request_shutdown_on_app_state():
    app = FastAPI()
    ServerRunner(app, port=_free_port())
    assert callable(app.state.request_shutdown)


def test_runner_starts_serves_and_stops():
    app = FastAPI()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    runner = _started_runner(app)
    try:
        assert runner.wait_until_ready(timeout_s=10) is True
    finally:
        runner.stop()
    assert runner._thread is not None and not runner._thread.is_alive()
