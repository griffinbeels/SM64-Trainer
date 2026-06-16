"""Machine-wide 'only one recorder' lock.

Multiple tracker instances (the packaged exe + dev servers in separate
worktrees, each with its OWN data dir) would each spin up a redundant DWM
capture + ffmpeg encode + audio loopback of the SAME PJ64 window — duplicated
GPU/CPU/audio load that lagged the whole machine, and they collided on the
shared replay buffer (PermissionError WinError 32, live exe log 2026-06-15:
two instances unlinking the same video_*.ts). The existing
storage/instance_lock only guards the per-DB journal, and the exe and a dev
server use DIFFERENT db files — so it does NOT coordinate capture across them.

This lock lives at a FIXED path in the OS temp dir, visible to EVERY instance
regardless of data dir, so exactly one recorder captures at a time. It's the
same OS file-region lock (released on ANY exit, crash included), so a dead
owner never blocks takeover. The path is read at call time so tests can
redirect it (conftest) without touching a real running server's lock."""
import logging
import tempfile
from pathlib import Path

from sm64_events.storage.instance_lock import acquire_instance_lock

log = logging.getLogger("sm64.replay")

RECORDER_LOCK_PATH = Path(tempfile.gettempdir()) / "sm64_tracker_recorder.lock"


def acquire_recorder_lock(path: Path | None = None):
    """Try to become THE recorder for this machine. Returns an open file handle
    to KEEP REFERENCED while recording (.close() releases it), or None if
    another live instance already holds it. `path` defaults (at call time) to
    RECORDER_LOCK_PATH so the module global can be patched in tests."""
    return acquire_instance_lock(path or RECORDER_LOCK_PATH)
