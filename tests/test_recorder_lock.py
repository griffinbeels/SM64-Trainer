"""Machine-wide recorder lock: exactly one holder at a time, released on close."""
from sm64_events.core.recorder_lock import acquire_recorder_lock


def test_only_one_holder_at_a_time(tmp_path):
    p = tmp_path / "rec.lock"
    first = acquire_recorder_lock(p)
    assert first is not None
    # a second acquisition while the first is held is refused (per-handle lock,
    # so even same-process — exactly how a second instance would see it)
    assert acquire_recorder_lock(p) is None
    first.close()                                  # release
    second = acquire_recorder_lock(p)              # now free
    assert second is not None
    second.close()
