import numpy as np

from sm64_events.replay.audio import f32_to_s16


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
