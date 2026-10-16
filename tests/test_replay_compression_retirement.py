"""A bounded stop must retain a compressor whose worker has not retired."""
from contextlib import contextmanager
import threading

import pytest

from sm64_events.replay import compress
from test_replay_service import make_service


@contextmanager
def blocked_worker(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    adoptions = []

    def shrink(*_args, **_kwargs):
        entered.set()
        assert release.wait(5), "test did not release its owned compressor worker"
        return {"state": "interrupted"}

    worker = compress.SavedReplayCompressor("unused", tmp_path, shrink_fn=shrink)
    monkeypatch.setattr(worker, "adopt_ready", lambda **kw: adoptions.append(kw) or [])
    # This witness owns no child process; never touch another test's children.
    monkeypatch.setattr(compress, "end_children", lambda: None)
    saved = tmp_path / "attempt_42_test.mp4"
    saved.write_bytes(b"saved")
    worker.start()
    worker.enqueue(saved)
    thread = worker._thread
    join = thread.join
    try:
        assert entered.wait(3), "owned worker never entered the blocked operation"
        adoptions.clear()
        # Model the existing five-second boundary being reached while work is
        # still in flight, without spending five seconds or changing its policy.
        monkeypatch.setattr(thread, "join", lambda timeout=None: join(timeout=0))
        yield worker, thread, release, join, adoptions
    finally:
        worker._stopping.set()
        release.set()
        worker._wake.set()
        join(timeout=3)
        assert not thread.is_alive(), "owned compressor worker escaped cleanup"


def test_unjoined_stop_preserves_owner_and_never_adopts_concurrently(tmp_path, monkeypatch):
    with blocked_worker(tmp_path, monkeypatch) as (worker, thread, release, join, adoptions):
        with pytest.raises(RuntimeError, match="compression cleanup pending"):
            worker.stop()
        assert worker._thread is thread and thread.is_alive()
        assert worker._stopping.is_set()
        assert adoptions == []

        with pytest.raises(RuntimeError, match="compression cleanup pending"):
            worker.start()
        assert worker._thread is thread and worker._stopping.is_set()
        assert adoptions == []

        release.set()
        join(timeout=3)
        assert not thread.is_alive()
        adoptions.clear()
        worker.stop()
        assert worker._thread is None
        assert adoptions == [{"everything_idle": True}]


def test_service_retains_pending_compressor_until_stop_can_finish(tmp_path, monkeypatch):
    service = make_service(tmp_path, [])
    monkeypatch.setattr(service.recorder, "stop", lambda: None, raising=False)
    with blocked_worker(tmp_path, monkeypatch) as (worker, thread, release, join, _):
        service.compressor = worker
        with pytest.raises(RuntimeError, match="compression cleanup pending"):
            service.lifecycle_stop()
        assert service.compressor is worker and thread.is_alive()

        release.set()
        join(timeout=3)
        service.lifecycle_stop()
        assert service.compressor is None
        assert worker._thread is None


def test_start_is_idempotent_and_joined_worker_can_restart(tmp_path, monkeypatch):
    worker = compress.SavedReplayCompressor("unused", tmp_path)
    adoptions = []
    monkeypatch.setattr(worker, "adopt_ready", lambda **kw: adoptions.append(kw) or [])
    monkeypatch.setattr(compress, "end_children", lambda: None)
    worker.start()
    first = worker._thread
    second = None
    try:
        worker.start()
        second = worker._thread
        assert second is first
        worker.stop()
        assert not first.is_alive() and worker._thread is None
        worker.stop()
        worker.start()
        second = worker._thread
        assert second is not first and second.is_alive()
        assert not worker._stopping.is_set()
    finally:
        worker._stopping.set()
        worker._wake.set()
        for thread in {first, second} - {None}:
            thread.join(timeout=3)
            assert not thread.is_alive(), "owned compressor worker escaped cleanup"
