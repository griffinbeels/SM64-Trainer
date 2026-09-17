"""A stop result must prove quiet, including full queues and device failures."""
import threading
import time
from types import SimpleNamespace as NS

import pytest

from sm64_events.replay._system_audio import AudioPump
from sm64_events.replay.audio import ProcessAudioSource, SystemAudioSource, DeafStreamWatchdog
from sm64_events.replay.audiostop import stop_process_tap


def test_full_pump_queue_never_blocks_stop_admission_and_retains_pending_consumer():
    entered, release = threading.Event(), threading.Event()
    received = []
    def consume(data):
        entered.set()
        assert release.wait(2)
        received.append(data.tobytes())
    pump = AudioPump(48000, consume)
    try:
        pump.feed(b"\x01\x00\x02\x00", 0)
        assert entered.wait(1)
        for _ in range(pump._q.maxsize):
            pump.feed(b"\x03\x00\x04\x00", 0)
        started = time.monotonic()
        with pytest.raises(RuntimeError, match="consumer still owns PCM"):
            pump.stop(timeout=0.01)
        assert time.monotonic() - started < 0.5
        assert pump._thread.is_alive()
        size = pump._q.qsize()
        pump.feed(b"\x05\x00\x06\x00", 0)
        assert pump._q.qsize() == size
    finally:
        release.set()
        pump.stop(timeout=1)
    assert len(received) == 257 and not pump._thread.is_alive()


@pytest.mark.parametrize("kind", ["process", "system", "terminate"])
def test_device_cleanup_failure_retains_identity_and_blocks_reopen(kind, monkeypatch):
    can_stop = [False]
    calls = []
    def guarded_stop():
        calls.append("stop")
        if not can_stop[0]:
            raise RuntimeError("device still owns capture")
    if kind == "process":
        source = ProcessAudioSource(42)
        device = NS(stop=guarded_stop)
        source._tap = device
        attribute, opener = "_tap", "_open_tap"
    else:
        source = SystemAudioSource(pid=42)
        device = NS(stop_stream=guarded_stop, close=lambda: calls.append("close"))
        source._stream = device if kind == "system" else None
        source._pa = NS(terminate=guarded_stop if kind == "terminate" else lambda: calls.append("terminate"))
        if kind == "terminate":
            device = source._pa
        attribute, opener = ("_stream" if kind == "system" else "_pa"), "_open_stream"
    monkeypatch.setattr(source, opener, lambda: pytest.fail("overlapping device opened"))
    with pytest.raises(RuntimeError, match="device still owns capture"):
        source._reopen()
    assert getattr(source, attribute) is device
    with pytest.raises(RuntimeError, match="device still owns capture"):
        source.stop()
    assert getattr(source, attribute) is device
    can_stop[0] = True
    source.stop()
    assert getattr(source, attribute) is None


def test_watchdog_join_timeout_is_not_success():
    alive = [True]
    calls = []
    watchdog = DeafStreamWatchdog(42, lambda: 0, lambda: None, "fixture")
    watchdog._thread = NS(is_alive=lambda: alive[0], join=lambda timeout: calls.append(timeout))
    with pytest.raises(RuntimeError, match="source may still reopen"):
        watchdog.stop()
    assert watchdog._stop_evt.is_set() and calls == [5]
    alive[0] = False
    watchdog.stop()


def process_tap(*, alive=False, native_stop=lambda: None):
    # Real dependency object, inert native/reader boundaries; never open audio.
    from proctap import ProcessAudioCapture
    from proctap.backends.windows import WindowsBackend

    tap = ProcessAudioCapture.__new__(ProcessAudioCapture)
    tap._backend = WindowsBackend.__new__(WindowsBackend)
    tap._backend._pid = 42
    tap._backend._native = NS(stop=native_stop)
    tap._stop_event = threading.Event()
    tap._thread = NS(is_alive=lambda: alive, join=lambda timeout: None)
    return tap


def test_real_proctap_public_stop_hides_live_reader_but_adapter_retains_it():
    unsafe = process_tap(alive=True)
    unsafe.stop()
    assert unsafe._thread is None  # Calibrates the dependency failure we guard.
    tap = process_tap(alive=True, native_stop=lambda: pytest.fail("reader still live"))
    original = tap._thread
    with pytest.raises(RuntimeError, match="reader did not stop"):
        stop_process_tap(tap)
    assert tap._thread is original and tap._stop_event.is_set()
    assert tap._backend._native is not None


def test_real_proctap_stop_failure_is_visible_and_native_owner_released_only_after_success():
    can_stop = [False]
    released = []

    class Native:
        def stop(self):
            if not can_stop[0]:
                raise RuntimeError("native failed")

        def __del__(self):
            released.append(True)

    tap = process_tap()
    tap._backend._native = Native()
    tap.stop()  # Both installed public layers suppress this native exception.
    assert tap._backend._native is not None and not released
    with pytest.raises(RuntimeError, match="native failed"):
        stop_process_tap(tap)
    assert tap._backend._native is not None and not released
    can_stop[0] = True
    stop_process_tap(tap)
    assert tap._backend._native is None and tap._thread is None and released == [True]
    stop_process_tap(tap)
    assert released == [True]
