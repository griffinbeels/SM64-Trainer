# src/sm64_events/desktop/server_runner.py
"""Run the FastAPI app under uvicorn in a background daemon thread with a
deterministic start/stop. uvicorn skips signal-handler install off the main
thread, which is exactly what we want — the GUI drives shutdown via
``should_exit`` (window close / tray quit / admin endpoint), never CTRL+C.
``timeout_graceful_shutdown=3`` is preserved (the load-bearing CTRL+C fix)."""
import threading
import time
import urllib.request

import uvicorn

from sm64_events.core.paths import server_port


class ServerRunner:
    def __init__(self, app, host: str = "127.0.0.1", port: int | None = None):
        self.host = host
        self.port = server_port() if port is None else port
        self._server = uvicorn.Server(uvicorn.Config(
            app, host=host, port=self.port, log_config=None,
            timeout_graceful_shutdown=3))
        self._thread: threading.Thread | None = None
        # Default: the admin shutdown endpoint stops the server. The desktop
        # composition overrides this with a FULL quit (app.py).
        app.state.request_shutdown = self.request_stop

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._server.run, name="uvicorn", daemon=True)
        self._thread.start()

    def wait_until_ready(self, timeout_s: float = 15.0) -> bool:
        deadline = time.monotonic() + timeout_s
        url = f"http://{self.host}:{self.port}/health"
        while time.monotonic() < deadline:
            # Require OUR uvicorn to have actually started (bound the port).
            # Without this, during a restart handoff a /health 200 from a
            # still-dying OLD instance would falsely report ready and the
            # window would open against a server about to vanish.
            if self._server.started:
                try:
                    with urllib.request.urlopen(url, timeout=1) as r:
                        if r.status == 200:
                            return True
                except Exception:
                    pass
            time.sleep(0.1)
        return False

    def request_stop(self) -> None:
        self._server.should_exit = True

    def stop(self, timeout_s: float = 20.0) -> None:
        self.request_stop()
        if self._thread is not None:
            self._thread.join(timeout_s)
