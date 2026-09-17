"""One cut lock follows its holders/waiters, rather than all past attempts."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from test_replay_service import attempt, make_service


def test_attempt_churn_and_exceptions_do_not_retain_locks(tmp_path):
    service = make_service(tmp_path, [])
    for identity in range(100000):
        with service._cut_lock(identity):
            pass
    assert service._cut_locks == {}
    with pytest.raises(RuntimeError, match="cut failed"):
        with service._cut_lock(1):
            raise RuntimeError("cut failed")
    assert service._cut_locks == {}


def wait_for_users(service, count):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        with service._cut_locks_guard:
            if service._cut_locks.get(42, (None, 0))[1] == count:
                return
        threading.Event().wait(0.001)
    pytest.fail(f"replay lock never registered {count} callers")


def test_view_waiters_share_one_lock_through_holder_retirement(tmp_path, monkeypatch):
    service = make_service(tmp_path, [attempt()])
    first_entered, second_entered = threading.Event(), threading.Event()
    first_release, second_release = threading.Event(), threading.Event()
    calls = []
    state_lock = threading.Lock()
    active = 0

    def view(identity, leases=None):
        nonlocal active
        with state_lock:
            active += 1
            assert active == 1
            calls.append(identity)
            ordinal = len(calls)
        try:
            if ordinal == 1:
                first_entered.set()
                assert first_release.wait(3)
            elif ordinal == 2:
                second_entered.set()
                assert second_release.wait(3)
            return ordinal
        finally:
            with state_lock:
                active -= 1

    monkeypatch.setattr(service, "_view", view)
    with ThreadPoolExecutor(max_workers=3) as pool:
        try:
            first = pool.submit(service.view, 42)
            assert first_entered.wait(3)
            second = pool.submit(service.view, 42)
            wait_for_users(service, 2)
            first_release.set()
            assert first.result(timeout=3) == 1
            assert second_entered.wait(3)
            third = pool.submit(service.view, 42)
            wait_for_users(service, 2)
            second_release.set()
            assert second.result(timeout=3) == 2
            assert third.result(timeout=3) == 3
        finally:
            first_release.set()
            second_release.set()
    assert service._cut_locks == {}
