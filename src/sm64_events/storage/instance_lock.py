# src/sm64_events/storage/instance_lock.py
"""Cross-process single-instance guard for the tracker database.

Two servers polling the same emulator and journaling into the same SQLite
file double-record every game event (live incident, 2026-06-11). The lock
is a Windows file-region lock (msvcrt): held for the process lifetime,
released by the OS on ANY exit — crash included — so there is no stale-
lockfile problem. Windows-only by design (the whole project reads PJ64)."""
import msvcrt
import time
from pathlib import Path


def acquire_instance_lock(path: Path):
    """Try to take the exclusive lock; returns the open file handle to KEEP
    REFERENCED for the process lifetime, or None if another live process
    holds it. Locks are per-handle on Windows, so even a second handle in
    the same process fails — which makes this testable in-process."""
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a")
    try:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        f.close()
        return None
    return f


def release_instance_lock(handle) -> None:
    """Explicitly unlock the region, then close the handle. Process exit
    releases the lock anyway; this exists for in-process probes
    (wait_lock_free) and tests, where the handle outlives no process."""
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        handle.close()


def wait_lock_free(path: Path, timeout_s: float = 30.0,
                   poll_s: float = 0.25) -> bool:
    """Block until the instance lock is acquirable; True once free, False on
    timeout. THE restart-handoff wait, and it must run IN ADDITION to
    relaunch.wait_port_free: uvicorn frees the PORT early in the old
    process's shutdown, but this lock releases only at process EXIT —
    after replay teardown and window destruction, seconds later. A handoff
    that waited only for the port lost that race and the fresh server ran
    broadcast-only forever, /api/session 503ing under a 'live' header
    (post-update incident 2026-07-23; also 2026-06-23). Probing by
    acquire-then-release is safe here: the handoff is 1:1, nothing else
    contends for the lock between the probe and the caller's re-acquire
    in build()."""
    deadline = time.monotonic() + timeout_s
    while True:
        handle = acquire_instance_lock(path)
        if handle is not None:
            release_instance_lock(handle)
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll_s)
