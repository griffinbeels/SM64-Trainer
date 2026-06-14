# src/sm64_events/desktop/single_instance.py
"""Detect a running instance and offer a takeover. Detection rides the
server's /health (via core.relaunch.server_alive); takeover asks it to shut
down gracefully (POST /api/admin/shutdown) and force-kills by pidfile only on
timeout. The native dialog lives in app.py (verified live, not here)."""
import os
import signal
import time
import urllib.request
from collections.abc import Callable

from sm64_events.core.paths import pidfile_path
from sm64_events.core.relaunch import HOST, PORT, server_alive


def instance_running(probe: Callable[[], bool] = server_alive) -> bool:
    return probe()


def _post_shutdown(timeout: float = 2.0) -> None:
    req = urllib.request.Request(
        f"http://{HOST}:{PORT}/api/admin/shutdown", method="POST")
    try:
        urllib.request.urlopen(req, timeout=timeout).close()
    except Exception:
        pass  # the connection may drop as the server tears down — expected


def _force_kill_pidfile() -> None:
    try:
        pid = int(pidfile_path().read_text().strip())
    except Exception:
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except Exception:
        pass


def take_over(*, shutdown: Callable[[], None] = _post_shutdown,
              port_free: Callable[[], bool] | None = None,
              force_kill: Callable[[], None] = _force_kill_pidfile,
              timeout_s: float = 8.0, poll_s: float = 0.25) -> bool:
    """Free the port for a fresh start: graceful shutdown first, force-kill on
    timeout. Returns True once the port is free."""
    if port_free is None:
        port_free = lambda: not server_alive()
    shutdown()
    if _wait(port_free, timeout_s, poll_s):
        return True
    force_kill()
    return _wait(port_free, timeout_s, poll_s)


def _wait(pred: Callable[[], bool], timeout_s: float, poll_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(poll_s)
    return pred()
