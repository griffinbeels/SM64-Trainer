# src/sm64_events/core/relaunch.py
"""Full-process relaunch primitives for the one-click "Restart server".

An in-process restart can't reload edited backend modules (CPython caches
imports), so restart RELAUNCHES this exact process. server_alive /
wait_port_free let the fresh process wait for the old one to release :8064
before it binds; spawn_replacement re-launches sys.orig_argv tagged with
SM64_RESTART=1 so the fresh process knows to wait and skip the takeover
dialog."""
import os
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable

HOST = "127.0.0.1"
PORT = 8064


def server_alive(timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(
                f"http://{HOST}:{PORT}/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def wait_port_free(timeout_s: float = 10.0, poll_s: float = 0.25,
                   alive: Callable[[], bool] = server_alive) -> bool:
    """Block until the server is gone (port free), bounded by timeout_s."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not alive():
            return True
        time.sleep(poll_s)
    return not alive()


def spawn_replacement() -> None:
    """Launch a fresh copy of this exact process, tagged for restart."""
    if getattr(sys, "frozen", False):
        argv = [sys.executable, *sys.orig_argv[1:]]
    else:
        argv = list(sys.orig_argv)
    subprocess.Popen(argv, env={**os.environ, "SM64_RESTART": "1"},
                     close_fds=False)
