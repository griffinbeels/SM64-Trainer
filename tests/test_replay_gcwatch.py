"""gen-2 GC policy: the manual collector that stops the disabled-gen-2 leak.

These tests do NOT call arm() — it mutates GLOBAL gc state (freeze / threshold)
and would bleed into every other test. The collector and its decision function
are exercised directly with an injected collect_fn.
"""
import threading

from sm64_events.replay._gcwatch import _Gen2Collector, should_collect


def test_should_collect_when_idle():
    assert should_collect(idle=True, secs_since_collect=0.0, force_after_s=300)


def test_should_collect_forces_after_interval_even_when_active():
    assert not should_collect(False, 10.0, 300.0)      # active, recent -> skip
    assert should_collect(False, 300.0, 300.0)         # active, stale -> force


def test_collector_runs_collection_while_idle():
    """Idle is the free moment to collect — the loop must actually fire
    collect_fn (this is the bug that caused the leak: nothing ever did)."""
    fired = threading.Event()
    calls = []

    def fake_collect():
        calls.append(1)
        fired.set()
        return 7  # pretend it reclaimed something

    col = _Gen2Collector(is_idle=lambda: True, poll_s=0.01,
                         force_after_s=999, collect_fn=fake_collect)
    col.start()
    try:
        assert fired.wait(timeout=2.0)                 # collected while idle
    finally:
        col.stop()
    assert len(calls) >= 1


def test_collector_idle_callable_is_honored():
    """When never idle and the force interval is far off, no collection
    happens — the collector must not collect unconditionally."""
    calls = []
    col = _Gen2Collector(is_idle=lambda: False, poll_s=0.01,
                         force_after_s=999, collect_fn=lambda: calls.append(1))
    col.start()
    try:
        threading.Event().wait(0.1)                    # several poll cycles
    finally:
        col.stop()
    assert calls == []                                 # active + not stale -> no collect
