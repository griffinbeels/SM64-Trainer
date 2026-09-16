"""Failed initialization cannot turn recorder reattachment into GPU churn."""

from dataclasses import replace
from types import SimpleNamespace as NS

from sm64_events.replay.capturecontrol import CaptureStatus, PASSIVE, CAP_GPU
from sm64_events.replay.gpuretry import RetryGate


def producer(**changes):
    value = CaptureStatus(42, 1, PASSIVE, 0, 8, 4, CAP_GPU, True, 123, 0, "fixture")
    return replace(value, **changes)


def test_repeated_failures_back_off_across_reattachment_and_capability_refresh():
    now = [0.0]
    gate = RetryGate(clock=lambda: now[0], cooldown=10)
    key = gate.observe(producer())
    assert gate.blocked(key) is None
    gate.failed(key, "native GPU unavailable")
    for i in range(5):
        now[0] = i * 2
        assert gate.observe(producer()) == key
        assert gate.blocked(key) is not None
    now[0] = 10
    assert gate.blocked(key) is None
    gate.failed(key, "still unavailable")
    now[0] = 29.9
    assert "repeated failures" in gate.blocked(key)
    assert gate.observe(producer()) == key and gate.blocked(key) is not None
    now[0] = 30
    assert gate.blocked(key) is None  # Same producer can recover after the delay.
    gate.failed(key, "third transient failure")
    now[0] = 69.9
    assert gate.blocked(key)
    now[0] = 70
    assert gate.blocked(key) is None
    gate.failed(key, "another transient failure")
    gate.observe(producer(rom_open=False))
    gate.observe(producer())
    assert gate.blocked(key) is None


def test_new_producer_resets_budget_and_old_failure_cannot_poison_it():
    gate = RetryGate()
    old = gate.observe(producer())
    gate.failed(old, "old failure")
    new = gate.observe(producer(producer_created_lo=124))
    gate.failed(old, "late old failure")
    assert new != old and gate.blocked(new) is None
    assert gate.blocked(old) == "producer changed"


def test_native_exhaustion_requires_new_process_not_rom_or_control_generation():
    gate = RetryGate()
    key = gate.observe(producer())
    gate.failed(key, "native_capture_ended:3:9; demand=fault:native_capture_ended:3:9")
    assert "fully close and reopen Project64" in gate.blocked(key)
    gate.observe(producer(rom_open=False))
    key = gate.observe(producer(generation=9))
    assert "fully close and reopen Project64" in gate.blocked(key)
    new = gate.observe(producer(generation=9, producer_created_lo=124))
    assert gate.blocked(new) is None


def test_unknown_native_reason_is_not_assumed_exhausted():
    gate = RetryGate(cooldown=0)
    key = gate.observe(producer())
    gate.failed(key, "native_capture_ended:3:900")
    assert gate.blocked(key) is None


def test_blocked_worker_only_observes_control_and_stays_interruptible():
    reads, waits = [], []
    status = [producer()]

    class Control:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def status(self):
            reads.append(status[0])
            return status[0]

    gate = RetryGate(control_factory=Control)
    key = gate.observe(status[0])
    gate.failed(key, "one")
    gate.failed(key, "two")
    active = [True]

    def wait(period):
        waits.append(period)
        active[0] = False

    owner = NS(want_capture=lambda: active[0], wait=wait, report_wait=lambda r: None)
    assert not gate.wait(owner, key)
    assert len(reads) == 1 and waits == [0.5]
