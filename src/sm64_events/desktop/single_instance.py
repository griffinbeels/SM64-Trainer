# src/sm64_events/desktop/single_instance.py
"""Detect a running instance and offer a takeover. Detection rides the
server's /health (core.relaunch.server_alive); takeover first asks it to shut
down gracefully (POST /api/admin/shutdown) and, if that isn't ACCEPTED or
doesn't free the port, force-closes whatever is LISTENING on :8064. Fast by
design: a non-cooperating server (old version -> 404) skips straight to
force-close instead of waiting out the graceful timeout. The native dialog
lives in app.py (verified live, not here)."""
import ctypes
import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from ctypes import wintypes

from sm64_events.core import build_channel
from sm64_events.core.paths import pidfile_path
from sm64_events.core.relaunch import (HOST, PORT, listener_pid, port_in_use,
                                       server_alive)

log = logging.getLogger("sm64.desktop")


def instance_running(probe: Callable[[], bool] = server_alive) -> bool:
    return probe()


def _matching_beta_server() -> bool:
    """A foreign listener is never a beta shutdown/takeover target."""
    try:
        with urllib.request.urlopen(f"http://{HOST}:{PORT}/health", timeout=2.0) as response:
            value = json.loads(response.read(4096))
        return isinstance(value, dict) and value.get("channel") == "beta"
    except (OSError, ValueError):
        return False


def _post_shutdown(timeout: float = 2.0) -> bool:
    """POST the graceful shutdown; return True iff the server ACCEPTED it
    (2xx). An old server without the route 404s -> False, so takeover skips
    straight to force-close instead of waiting pointlessly."""
    beta = build_channel.current_build().channel == "beta"
    if beta and not _matching_beta_server():
        return False
    headers = {}
    try:
        with urllib.request.urlopen(f"http://{HOST}:{PORT}/api/local-access", timeout=timeout) as response:
            value = json.loads(response.read(4096))
        token = value.get("request_token") if isinstance(value, dict) else None
        if not isinstance(token, str) or not token or len(token) > 256:
            return False
        headers["X-SM64-Request-Token"] = token
    except urllib.error.HTTPError as failure:
        if beta or failure.code != 404:
            return False
        # Older stable builds predate the local mutation token.
    except (OSError, ValueError):
        return False
    req = urllib.request.Request(
        f"http://{HOST}:{PORT}/api/admin/shutdown", method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 300
    except Exception:
        return False  # 404 (old version) / drop as it tears down


def _terminate(pid: int) -> bool:
    """TerminateProcess via the minimal PROCESS_TERMINATE right. (os.kill on
    Windows opens with PROCESS_ALL_ACCESS, which a same-user-but-different-
    integrity target can refuse.) argtypes/restype are set so the 64-bit
    HANDLE isn't truncated. Returns whether the kill was issued."""
    PROCESS_TERMINATE = 0x0001
    k = ctypes.windll.kernel32
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    h = k.OpenProcess(PROCESS_TERMINATE, False, int(pid))
    if not h:
        return False
    try:
        return bool(k.TerminateProcess(h, 1))
    finally:
        k.CloseHandle(h)


def _force_kill_pidfile() -> None:
    if build_channel.current_build().channel == "beta":
        return
    try:
        pid = int(pidfile_path().read_text().strip())
    except Exception:
        return
    _terminate(pid)


def _force_kill_port_owner() -> None:
    """Force-close whatever is LISTENING on :8064 — the user chose 'take
    over', so closing the port owner is intended even when it can't be shut
    down gracefully (old version, no pidfile). Falls back to the pidfile."""
    if build_channel.current_build().channel == "beta":
        return  # Beta never terminates a listener or stale pidfile owner.
    pid = listener_pid(PORT)
    if pid is None:
        log.warning("takeover: nothing listening on :%d; trying pidfile", PORT)
        _force_kill_pidfile()
        return
    log.info("takeover: force-closing PID %d listening on :%d", pid, PORT)
    if not _terminate(pid):
        log.warning("takeover: TerminateProcess(%d) failed; trying pidfile",
                    pid)
        _force_kill_pidfile()


def take_over(*, shutdown: Callable[[], bool] = _post_shutdown,
              port_free: Callable[[], bool] | None = None,
              force_kill: Callable[[], None] = _force_kill_port_owner,
              graceful_wait_s: float = 12.0, force_wait_s: float = 8.0,
              poll_s: float = 0.2,
              identity_check: Callable[[], bool] | None = None) -> bool:
    """Free :8064 for a fresh start. If the running server ACCEPTS a graceful
    shutdown, wait briefly for it to release the port; otherwise (or if it
    doesn't free in time) force-close the LISTENER and wait a short bit, with
    one retry. Returns True once nothing is listening on :8064.

    'Free' = no LISTENER (relaunch.port_in_use), NOT a test-bind: a test-bind
    is fooled by TIME_WAIT for tens of seconds after a server dies, which hung
    this ~40 s and fired a false 'couldn't close it'. A 404'd graceful skips
    its wait entirely, so an old server is closed in ~1 s, not ~40 s."""
    if port_free is None:
        port_free = lambda: not port_in_use(port=PORT)
    if build_channel.current_build().channel == "beta":
        if not (identity_check or _matching_beta_server)():
            return False
        return bool(shutdown() and _wait(port_free, graceful_wait_s, poll_s))
    if shutdown() and _wait(port_free, graceful_wait_s, poll_s):
        return True
    force_kill()
    if _wait(port_free, force_wait_s, poll_s):
        return True
    force_kill()  # TerminateProcess is async; one retry covers a slow release
    return _wait(port_free, force_wait_s, poll_s)


def _wait(pred: Callable[[], bool], timeout_s: float, poll_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(poll_s)
    return pred()
