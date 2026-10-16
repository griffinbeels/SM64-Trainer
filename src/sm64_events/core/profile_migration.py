"""Explicit copy of a closed legacy beta profile into an empty normal profile.

The original remains a backup. Existing normal data is never merged or replaced;
the new OAuth client requires sign-in and has its own Credential Manager entry.
Stdlib only, so the same bootstrap can offer this before the app starts.
"""
from contextlib import closing, contextmanager
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time

from sm64_events.core.build_channel import BETA_APP_DIR, STABLE_APP_DIR


def _profiles(data_home):
    if data_home is None:
        data_home = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    base = Path(data_home)
    if not base.is_absolute():
        raise ValueError("Profile migration needs an absolute data directory.")
    base = base.resolve()
    return base, base / BETA_APP_DIR, base / STABLE_APP_DIR


def _plain(path):
    try:
        attributes = path.lstat()
    except FileNotFoundError:
        return
    if path.is_symlink() or getattr(attributes, "st_file_attributes", 0) & 0x400:
        raise ValueError("Profile migration cannot follow linked files or directories.")


def _empty(path):
    _plain(path)
    return not path.exists() or path.is_dir() and not any(path.iterdir())


def beta_profile_migration_available(data_home=None):
    """Cheap eligibility only; confirmation and a fresh safety check precede IO."""
    _, source, target = _profiles(data_home)
    _plain(source)
    return source.is_dir() and any(source.iterdir()) and _empty(target)


@contextmanager
def _idle_profile(source):
    # Hold the same kernel-released region as the old tracker for the entire
    # copy. This also fences a concurrent legacy app starting during migration.
    from sm64_events.storage.instance_lock import acquire_instance_lock, release_instance_lock
    path = source / "data" / "tracker.lock"
    _plain(path)
    handle = acquire_instance_lock(path)
    if handle is None:
        raise ValueError("Close SM64 Trainer Beta before copying its practice data.")
    try:
        yield
    finally:
        release_instance_lock(handle)


def _ignored(directory, names):
    for name in names:
        _plain(Path(directory) / name)
    return {name for name in names if name in {"server.pid", "replay_buffer"} or
            name.endswith((".lock", ".db-wal", ".db-shm", ".sqlite3-wal", ".sqlite3-shm"))}


def _copy_sqlite(source, target):
    deadline = time.monotonic() + 30
    def progress(_status, _remaining, _total):
        if time.monotonic() > deadline:
            raise ValueError("Database copy timed out; the original profile was preserved.")
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as original:
        with closing(sqlite3.connect(target)) as copied:
            original.backup(copied, pages=256, progress=progress, sleep=0.1)


def _copy_file(source, target):
    source, target = Path(source), Path(target)
    _plain(source)
    if source.suffix in {".db", ".sqlite3"}:
        _copy_sqlite(source, target)  # Include committed WAL rows in one snapshot.
    else:
        shutil.copy2(source, target)
    return str(target)


def migrate_private_beta_profile(data_home=None, *, confirmed=False):
    """Copy only after a human confirmation; atomically publish a complete copy.

    Returns state/source/target. Failures leave both original practice data and
    any newly occupied normal profile alone. Staging is always below data_home.
    """
    base, source, target = _profiles(data_home)
    result = {"state": "not_requested", "source": str(source), "target": str(target)}
    if confirmed is not True:
        return result
    if not beta_profile_migration_available(base):
        raise ValueError("An empty normal profile and existing beta profile are required.")
    with _idle_profile(source):
        staging = Path(tempfile.mkdtemp(prefix=".sm64-profile-copy-", dir=base))
        removed_empty = False
        try:
            shutil.copytree(source, staging, dirs_exist_ok=True, ignore=_ignored, copy_function=_copy_file)
            if not _empty(target):
                raise ValueError("The normal profile now contains data; migration was canceled.")
            if target.exists():
                target.rmdir()  # Only an empty directory, checked again by the OS.
                removed_empty = True
            os.rename(staging, target)
            result["state"] = "copied"
            return result
        finally:
            if staging.exists():
                _plain(staging)
                if staging.parent != base or not staging.name.startswith(".sm64-profile-copy-"):
                    raise ValueError("Unsafe profile migration staging directory.")
                shutil.rmtree(staging)
            if removed_empty and not target.exists():
                target.mkdir()
