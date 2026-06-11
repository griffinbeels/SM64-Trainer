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
