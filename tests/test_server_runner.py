# tests/test_server_runner.py
"""ServerRunner runs the app under uvicorn in a daemon thread and stops it
deterministically (the GUI owns shutdown, not CTRL+C)."""
import socket

from fastapi import FastAPI

from sm64_events.desktop.server_runner import ServerRunner


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_runner_wires_request_shutdown_on_app_state():
    app = FastAPI()
    ServerRunner(app, port=_free_port())
    assert callable(app.state.request_shutdown)


def test_runner_starts_serves_and_stops():
    app = FastAPI()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    runner = ServerRunner(app, port=_free_port())
    runner.start()
    try:
        assert runner.wait_until_ready(timeout_s=10) is True
    finally:
        runner.stop()
    assert runner._thread is not None and not runner._thread.is_alive()
