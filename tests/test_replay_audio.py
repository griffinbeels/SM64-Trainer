import sys
import threading
import time
import types

import numpy as np
import pytest

from sm64_events.replay import audio as audio_mod
from sm64_events.replay.audio import (DeafStreamWatchdog, ProcessAudioSource,
                                      f32_to_s16)


class FakeTap:
    """Stand-in for proctap.ProcessAudioCapture."""

    def __init__(self, pid, on_data):
        self.pid, self.on_data = pid, on_data
        self.started = self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


class FailingTap(FakeTap):
    def start(self):
        raise RuntimeError("process loopback unavailable on this Windows build")


@pytest.fixture
def fake_proctap(monkeypatch):
    """Install a fake `proctap` module; yields the list of taps it made."""
    taps = []

    def install(tap_cls=FakeTap):
        module = types.ModuleType("proctap")

        def capture(pid, on_data):
            taps.append(tap_cls(pid, on_data))
            return taps[-1]

        module.ProcessAudioCapture = capture
        monkeypatch.setitem(sys.modules, "proctap", module)
        return taps

    return install


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_f32_to_s16_shape_and_scale():
    pcm = np.array([0.0, 0.5, -0.5, 1.0], dtype=np.float32)  # 2 stereo samples
    out = f32_to_s16(pcm)
    assert out.shape == (2, 2) and out.dtype == np.int16
    assert out[0, 0] == 0 and out[0, 1] == 16383
    assert out[1, 1] == 32767


def test_f32_to_s16_clips_out_of_range():
    pcm = np.array([2.0, -2.0], dtype=np.float32)
    out = f32_to_s16(pcm)
    # clip(-2.0, -1.0, 1.0) = -1.0; -1.0 * 32767 = -32767.0; astype(int16) = -32767
    assert out[0, 0] == 32767 and out[0, 1] == -32767


def test_pick_loopback_device_prefers_app_endpoint():
    from sm64_events.replay.audio import pick_loopback_device
    devs = [{"name": "System (Elgato Wave:XLR) [Loopback]", "index": 1},
            {"name": "Game (Elgato Wave:XLR) [Loopback]", "index": 2}]
    got = pick_loopback_device(devs, "Game (Elgato Wave:XLR)",
                               "System (Elgato Wave:XLR)")
    assert got["index"] == 2
    # no app endpoint known -> default
    got = pick_loopback_device(devs, None, "System (Elgato Wave:XLR)")
    assert got["index"] == 1
    # nothing matches -> None
    assert pick_loopback_device(devs, "Nope", "AlsoNope") is None


def test_audio_pump_forwards_pcm_in_order_off_callback():
    """The pump is now a pure RT-safe handoff: each delivered packet's PCM
    reaches on_pcm in order on the consumer thread, with NO silence injection
    and NO sample-count placement — ffmpeg's wall-clock + aresample own the
    timeline and fill idle gaps, so an idle gap here must NOT manufacture
    silence (that would dump a burst into the pipe and fight aresample)."""
    import time
    import numpy as np
    from sm64_events.replay._system_audio import AudioPump

    out = []
    pump = AudioPump(48000, lambda a: out.append(a.copy()))
    one = np.ones((480, 2), dtype=np.int16)
    two = (np.ones((480, 2), dtype=np.int16) * 7)

    pump.feed(one.tobytes(), 0)
    pump.feed(two.tobytes(), 0)                    # an idle gap would be here
    deadline = time.monotonic() + 5
    while len(out) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    pump.stop()
    assert len(out) == 2                           # exactly the two packets
    assert np.array_equal(out[0], one)
    assert np.array_equal(out[1], two)             # no injected silence between


def test_process_source_taps_the_target_pid_and_delivers_int16(fake_proctap):
    """The primary source captures ONE process, and hands the recorder the
    same int16 (n, 2) shape the device path does — the sink speaks one
    format regardless of which capture path is live."""
    taps = fake_proctap()
    out = []
    src = ProcessAudioSource(pid=4242, rate=48000)
    src.start(lambda pcm: out.append(pcm.copy()))
    try:
        assert len(taps) == 1
        assert taps[0].pid == 4242 and taps[0].started
        # proctap always delivers float32 stereo; num_frames is always -1
        taps[0].on_data(np.array([0.0, 0.5, -0.5, 1.0], np.float32).tobytes(), -1)
        assert wait_for(lambda: out)
    finally:
        src.stop()
    assert out[0].dtype == np.int16 and out[0].shape == (2, 2)
    assert out[0][0, 1] == 16383 and out[0][1, 1] == 32767
    assert taps[0].stopped


def test_process_source_releases_the_pump_when_the_tap_cannot_start(fake_proctap):
    """A machine without process loopback must surface as a raise, so the
    recorder's fallback chain runs — and must not leak the pump thread,
    because the recorder only stop()s sources whose start() succeeded."""
    fake_proctap(FailingTap)
    src = ProcessAudioSource(pid=1, rate=48000)
    before = threading.active_count()
    with pytest.raises(RuntimeError):
        src.start(lambda pcm: None)
    assert wait_for(lambda: threading.active_count() <= before)


def test_watchdog_reopens_a_deaf_stream_once_per_deaf_spell(monkeypatch):
    """Deaf on our side while the app's own meter says it is emitting = the
    stream is broken, so reopen. The grace period after a reopen is what
    stops the next tick firing again before any audio could have arrived."""
    monkeypatch.setattr(audio_mod, "session_peak_for_pid", lambda pid: 0.5)
    reopens = []
    dog = DeafStreamWatchdog(
        pid=7, heard_at=lambda: 0.0, reopen=lambda: reopens.append(1),
        label="test", deaf_after_s=1.0, check_every_s=0.01)
    dog.start()
    try:
        assert wait_for(lambda: reopens)
        time.sleep(0.3)               # many more ticks, all inside the grace
    finally:
        dog.stop()
    assert len(reopens) == 1


def test_watchdog_leaves_a_genuinely_quiet_app_alone(monkeypatch):
    """Silence is not evidence of a fault: a paused game emits nothing and
    its session meter says so. Reopening on that would thrash the stream."""
    monkeypatch.setattr(audio_mod, "session_peak_for_pid", lambda pid: 0.0)
    reopens = []
    dog = DeafStreamWatchdog(
        pid=7, heard_at=lambda: 0.0, reopen=lambda: reopens.append(1),
        label="test", deaf_after_s=0.0, check_every_s=0.01)
    dog.start()
    time.sleep(0.3)
    dog.stop()
    assert reopens == []


def test_audio_pump_tracks_loud_for_deaf_watchdog():
    """The deaf-stream watchdog still needs last_loud_t: a loud packet must
    bump it off its initial 0.0 (a silent packet must not)."""
    import time
    import numpy as np
    from sm64_events.replay._system_audio import AudioPump

    pump = AudioPump(48000, lambda a: None)
    pump.feed(np.zeros((480, 2), dtype=np.int16).tobytes(), 0)
    time.sleep(0.1)
    assert pump.last_loud_t == 0.0                 # silence: no bump
    pump.feed((np.ones((480, 2), dtype=np.int16) * 5000).tobytes(), 0)
    deadline = time.monotonic() + 5
    while pump.last_loud_t == 0.0 and time.monotonic() < deadline:
        time.sleep(0.01)
    pump.stop()
    assert pump.last_loud_t > 0.0                  # loud: bumped
