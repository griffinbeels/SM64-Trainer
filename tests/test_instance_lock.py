# tests/test_instance_lock.py
"""Tests for the cross-process single-instance lock (msvcrt-based)."""
import msvcrt

import pytest

from sm64_events.storage.instance_lock import acquire_instance_lock


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
