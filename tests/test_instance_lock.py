# tests/test_instance_lock.py
"""Tests for the cross-process single-instance lock (msvcrt-based)."""
import msvcrt
import threading
import time

import pytest

from sm64_events.storage.instance_lock import (acquire_instance_lock,
                                               release_instance_lock,
                                               wait_lock_free)


def test_first_acquire_returns_handle(tmp_path):
    lock_path = tmp_path / "tracker.lock"
    handle = acquire_instance_lock(lock_path)
    assert handle is not None
    # Clean up: unlock + close
    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    handle.close()


def test_second_acquire_while_first_held_returns_none(tmp_path):
    lock_path = tmp_path / "tracker.lock"
    first = acquire_instance_lock(lock_path)
    assert first is not None

    second = acquire_instance_lock(lock_path)
    assert second is None  # lock is still held by `first`

    # Release the first lock.
    msvcrt.locking(first.fileno(), msvcrt.LK_UNLCK, 1)
    first.close()


def test_acquire_after_release_succeeds(tmp_path):
    lock_path = tmp_path / "tracker.lock"
    first = acquire_instance_lock(lock_path)
    assert first is not None

    # Release by unlocking and closing.
    msvcrt.locking(first.fileno(), msvcrt.LK_UNLCK, 1)
    first.close()

    # Now a fresh acquire must succeed.
    second = acquire_instance_lock(lock_path)
    assert second is not None
    msvcrt.locking(second.fileno(), msvcrt.LK_UNLCK, 1)
    second.close()


def test_acquire_creates_parent_dirs(tmp_path):
    lock_path = tmp_path / "nested" / "dir" / "tracker.lock"
    assert not lock_path.parent.exists()
    handle = acquire_instance_lock(lock_path)
    assert handle is not None
    assert lock_path.parent.exists()
    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    handle.close()


# -- release + wait_lock_free (the restart-handoff wait) ---------------------
# Contract: a restart handoff must be able to wait until the OLD process's
# lock is actually gone — the port frees seconds earlier, and losing this
# race stuck a post-update server in broadcast-only (live 2026-07-23).


def test_release_allows_reacquire(tmp_path):
    lock_path = tmp_path / "tracker.lock"
    first = acquire_instance_lock(lock_path)
    assert first is not None
    release_instance_lock(first)
    second = acquire_instance_lock(lock_path)
    assert second is not None
    release_instance_lock(second)


def test_wait_lock_free_immediate_when_unheld(tmp_path):
    lock_path = tmp_path / "tracker.lock"
    started = time.monotonic()
    assert wait_lock_free(lock_path, timeout_s=5.0) is True
    assert time.monotonic() - started < 1.0


def test_wait_lock_free_times_out_while_held(tmp_path):
    lock_path = tmp_path / "tracker.lock"
    holder = acquire_instance_lock(lock_path)
    assert holder is not None
    try:
        assert wait_lock_free(lock_path, timeout_s=0.3, poll_s=0.05) is False
    finally:
        release_instance_lock(holder)


def test_wait_lock_free_does_not_keep_the_lock(tmp_path):
    lock_path = tmp_path / "tracker.lock"
    assert wait_lock_free(lock_path, timeout_s=1.0) is True
    # The wait is a probe — after it returns the caller must be able to
    # take the lock for real (build() re-acquires it).
    handle = acquire_instance_lock(lock_path)
    assert handle is not None
    release_instance_lock(handle)


def test_wait_lock_free_returns_once_released(tmp_path):
    lock_path = tmp_path / "tracker.lock"
    holder = acquire_instance_lock(lock_path)
    assert holder is not None
    releaser = threading.Timer(0.2, lambda: release_instance_lock(holder))
    releaser.start()
    try:
        assert wait_lock_free(lock_path, timeout_s=5.0, poll_s=0.05) is True
    finally:
        releaser.join()
