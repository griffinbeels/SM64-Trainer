# src/sm64_events/storage/instance_lock.py
"""Cross-process single-instance guard for the tracker database.

Two servers polling the same emulator and journaling into the same SQLite
file double-record every game event (live incident, 2026-06-11). The lock
is a Windows file-region lock (msvcrt): held for the process lifetime,
released by the OS on ANY exit — crash included — so there is no stale-
lockfile problem. Windows-only by design (the whole project reads PJ64)."""
import msvcrt
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
