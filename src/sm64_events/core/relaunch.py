# src/sm64_events/core/relaunch.py
"""Full-process relaunch primitives for the one-click "Restart server".

An in-process restart can't reload edited backend modules (CPython caches
imports), so restart RELAUNCHES this exact process. server_alive /
wait_port_free let the fresh process wait for the old one to release :8064
before it binds; spawn_replacement re-launches sys.orig_argv tagged with
SM64_RESTART=1 so the fresh process knows to wait and skip the takeover
dialog."""
import os
import socket
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


def port_in_use(host: str = HOST, port: int = PORT) -> bool:
    """True if the port can't be bound right now (a server holds it).

    This is the RIGHT signal for a restart handoff — more reliable than a
    /health probe. The old uvicorn stops answering /health the moment
    should_exit is set, but its listening socket (and the lengthy replay
    teardown) outlive that, so a health-only wait lets the replacement
    bind-race the dying process and land an unreachable server."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def wait_port_free(timeout_s: float = 30.0, poll_s: float = 0.25,
                   occupied: Callable[[], bool] = port_in_use) -> bool:
    """Block until the port is actually free to BIND, bounded by timeout_s.
    The default timeout comfortably exceeds the old process's bounded replay
    teardown (~15 s) so the replacement never binds while the old still holds
    the socket. Returns True once free, False on timeout."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not occupied():
            return True
        time.sleep(poll_s)
    return not occupied()


def spawn_replacement() -> None:
    """Launch a fresh copy of this exact process, tagged for restart."""
    if getattr(sys, "frozen", False):
        argv = [sys.executable, *sys.orig_argv[1:]]
    else:
        argv = list(sys.orig_argv)
    env = {**os.environ, "SM64_RESTART": "1"}
    # PyInstaller onefile self-relaunch: the running (2nd-stage) process has
    # _MEIPASS2 / _PYI_* set, pointing at THIS exe's extraction temp dir.
    # Inheriting them makes the relaunched exe skip its OWN extraction and try
    # to load from our dir — which the bootloader deletes as we exit, so the
    # child crashes importing a bundled module (e.g. _cffi_backend, via
    # pywebview -> pythonnet). Scrub them so the child bootstraps cleanly.
    for key in [k for k in env if k.startswith(("_MEI", "_PYI"))]:
        del env[key]
    subprocess.Popen(argv, env=env, close_fds=False)
