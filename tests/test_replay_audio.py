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


def test_pcm_continuity_fills_idle_gaps_only():
    from sm64_events.replay.audio import PcmContinuity
    g = PcmContinuity(rate=48000, qpc_start_100ns=0)
    # 1 s in, delivered 1 s of samples: no gap
    g.on_delivered(48000)
    assert g.fill_before(10_000_000) == 0
    # 0.9 s late-burst jitter (flush stalls, GIL): below threshold, no fill
    assert g.fill_before(19_000_000) == 0
    # 3 s idle: expected 4 s worth, delivered 1 s -> fill the difference
    fill = g.fill_before(40_000_000)
    assert fill == 48000 * 3
    g.on_delivered(fill)
    assert g.fill_before(40_000_000) == 0


def test_audio_pump_orders_fills_and_data_off_callback():
    """The pump consumes (bytes, qpc) pairs on its own thread: idle gaps
    become silence BEFORE the late data, and every delivered byte reaches
    on_pcm in order."""
    import time
    import numpy as np
    from sm64_events.replay._system_audio import AudioPump

    out = []
    pump = AudioPump(48000, lambda a: out.append(a.copy()), 0)
    one = np.ones((480, 2), dtype=np.int16)  # 10ms of "audio"

    pump.feed(one.tobytes(), 100_000, 0)          # 10ms mark: no gap
    pump.feed(one.tobytes(), 30_000_000, 0)       # 3s mark: ~3s idle gap
    deadline = time.monotonic() + 5
    while len(out) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    pump.stop()
    assert len(out) == 3
    assert np.array_equal(out[0], one)            # first packet, no fill
    assert (out[1] == 0).all() and len(out[1]) > 48000 * 2  # the idle fill
    assert np.array_equal(out[2], one)            # late packet after fill


def test_plan_placement_regimes():
    from sm64_events.replay._system_audio import plan_placement
    MIN = 12000  # 250 ms at 48 k
    # in-sequence (tiny jitter either way): append, no fill, no drop
    assert plan_placement(1000, 1000, 480, MIN) == (0, False)
    assert plan_placement(900, 1000, 480, MIN) == (0, False)
    assert plan_placement(1300, 1000, 480, MIN) == (0, False)
    # engine nap: exact silence fill
    assert plan_placement(50_000, 1000, 480, MIN) == (49_000, False)
    # stale packet fully behind the timeline: dropped
    assert plan_placement(0, 1000, 480, MIN) == (0, True)
