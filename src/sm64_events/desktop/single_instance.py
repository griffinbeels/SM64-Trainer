# src/sm64_events/desktop/single_instance.py
"""Detect a running instance and offer a takeover. Detection rides the
server's /health (via core.relaunch.server_alive); takeover asks it to shut
down gracefully (POST /api/admin/shutdown) and, if that doesn't free the
port, force-closes whatever owns :8064. The native dialog lives in app.py
(verified live, not here)."""
import ctypes
import os
import signal
import socket
import time
import urllib.request
from collections.abc import Callable
from ctypes import wintypes

from sm64_events.core.paths import pidfile_path
from sm64_events.core.relaunch import HOST, PORT, port_in_use, server_alive


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


_TCP_TABLE_OWNER_PID_ALL = 5
_AF_INET = 2


class _MIB_TCPROW_OWNER_PID(ctypes.Structure):
    _fields_ = [("dwState", wintypes.DWORD),
                ("dwLocalAddr", wintypes.DWORD),
                ("dwLocalPort", wintypes.DWORD),
                ("dwRemoteAddr", wintypes.DWORD),
                ("dwRemotePort", wintypes.DWORD),
                ("dwOwningPid", wintypes.DWORD)]


def _pid_on_port(port: int = PORT) -> int | None:
    """PID owning a local TCP socket on `port`, via GetExtendedTcpTable. Lets
    takeover close ANY holder of :8064 — an old server without
    /api/admin/shutdown, a wedged one that never wrote a pidfile, a foreign
    squatter. None if not found or the lookup fails. (argtypes are set so the
    table pointer isn't truncated to 32-bit on a 64-bit build.)"""
    try:
        fn = ctypes.windll.iphlpapi.GetExtendedTcpTable
        fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD),
                       wintypes.BOOL, wintypes.ULONG, ctypes.c_int,
                       wintypes.ULONG]
        fn.restype = wintypes.DWORD
        size = wintypes.DWORD(0)
        fn(None, ctypes.byref(size), False, _AF_INET,
           _TCP_TABLE_OWNER_PID_ALL, 0)
        buf = (ctypes.c_byte * size.value)()
        if fn(buf, ctypes.byref(size), False, _AF_INET,
              _TCP_TABLE_OWNER_PID_ALL, 0) != 0:
            return None
        count = ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0]
        rows = ctypes.cast(
            ctypes.byref(buf, ctypes.sizeof(wintypes.DWORD)),
            ctypes.POINTER(_MIB_TCPROW_OWNER_PID * count))[0]
        for row in rows:
            if socket.ntohs(row.dwLocalPort & 0xFFFF) == port:
                return int(row.dwOwningPid)
    except Exception:
        return None
    return None


def _force_kill_port_owner() -> None:
    """Force-close whatever holds :8064 — the user explicitly chose 'take
    over', so closing the port owner is intended even when it can't be shut
    down gracefully (old version, no pidfile). Falls back to the pidfile if
    the port-owner lookup fails."""
    pid = _pid_on_port(PORT)
    if pid is None:
        _force_kill_pidfile()
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except Exception:
        _force_kill_pidfile()


def take_over(*, shutdown: Callable[[], None] = _post_shutdown,
              port_free: Callable[[], bool] | None = None,
              force_kill: Callable[[], None] = _force_kill_port_owner,
              timeout_s: float = 20.0, poll_s: float = 0.25) -> bool:
    """Free the port for a fresh start: graceful shutdown first, force-kill on
    timeout. Returns True once the port is free. 'Free' = actually bindable
    (port_in_use), not just /health-down — the old listener + replay teardown
    outlive /health, so a health-only wait would force-kill mid-teardown. The
    timeout exceeds the bounded replay teardown (~15 s)."""
    if port_free is None:
        port_free = lambda: not port_in_use()
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
