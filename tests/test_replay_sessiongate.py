"""Session changes drain work without serializing ordinary parallel reads."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from sm64_events.replay.sessiongate import SessionGate


def test_change_waits_for_existing_work_then_rejects_work_after_close():
    gate = SessionGate()
    entered, release, changed = Event(), Event(), Event()
    def read():
        with gate.use():
            entered.set()
            assert release.wait(3)
    def close():
        with gate.change(close=True):
            changed.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        reader = pool.submit(read)
        assert entered.wait(3)
        closer = pool.submit(close)
        assert not changed.wait(.05)
        release.set()
        reader.result(timeout=3)
        closer.result(timeout=3)
    with pytest.raises(RuntimeError, match="closed"):
        with gate.use():
            pytest.fail("closed session accepted work")
    with gate.change():
        pass  # A fresh server/session lifetime can reopen the service.
    with gate.use():
        pass


def test_failed_operation_releases_the_session():
    gate = SessionGate()
    with pytest.raises(ValueError):
        with gate.use():
            raise ValueError("read failed")
    with gate.change():
        pass
