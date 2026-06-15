# src/sm64_events/core/relaunch.py
"""Full-process relaunch primitives for the one-click "Restart server".

An in-process restart can't reload edited backend modules (CPython caches
imports), so restart RELAUNCHES this exact process. server_alive /
wait_port_free let the fresh process wait for the old one to release :8064
before it binds; spawn_replacement re-launches sys.orig_argv tagged with
SM64_RESTART=1 so the fresh process knows to wait and skip the takeover
dialog."""
import ctypes
import os
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
from ctypes import wintypes

HOST = "127.0.0.1"
PORT = 8064

_TCP_TABLE_OWNER_PID_ALL = 5
_AF_INET = 2
_MIB_TCP_STATE_LISTEN = 2


class _MIB_TCPROW_OWNER_PID(ctypes.Structure):
    _fields_ = [("dwState", wintypes.DWORD),
                ("dwLocalAddr", wintypes.DWORD),
                ("dwLocalPort", wintypes.DWORD),
                ("dwRemoteAddr", wintypes.DWORD),
                ("dwRemotePort", wintypes.DWORD),
                ("dwOwningPid", wintypes.DWORD)]


def server_alive(timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(
                f"http://{HOST}:{PORT}/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def listener_pid(port: int = PORT) -> int | None:
    """PID of the process LISTENING on local TCP `port` (the server), via
    GetExtendedTcpTable, or None if nothing is listening.

    ONLY the LISTEN state counts. A test-bind (the previous approach) is
    confounded by lingering TIME_WAIT/ESTABLISHED sockets — on Windows it
    keeps reporting "busy" for tens of seconds AFTER a server has died, which
    hung takeover ~40 s and fired a false "couldn't close it" error. Those
    sockets never block a fresh listener bind; only an actual LISTENER does.
    argtypes are set so the table pointer isn't truncated on a 64-bit build."""
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
            if (row.dwState == _MIB_TCP_STATE_LISTEN
                    and socket.ntohs(row.dwLocalPort & 0xFFFF) == port):
                return int(row.dwOwningPid)
    except Exception:
        return None
    return None


def port_in_use(host: str = HOST, port: int = PORT) -> bool:
    """True iff a process is actively LISTENING on `port` (a live server).
    Listener-based, NOT a test-bind — see listener_pid for why a test-bind
    falsely reports busy (TIME_WAIT) and hung takeover. A fresh listener can
    always bind once the old LISTENER is gone, regardless of TIME_WAIT."""
    return listener_pid(port) is not None


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
