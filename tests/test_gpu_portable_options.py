"""The portable encoder cannot silently reinterpret NVIDIA tuning numbers."""
import ctypes

import pytest

from sm64_events.replay import config
from sm64_events.replay.gpuencoder_abi import BACKEND_WINDOWS, Options
from sm64_events.replay.gpuencoder_options import pod_options
from test_gpuencoder_client import options


def test_old_nvenc_callers_keep_identical_v1_bytes():
    current = options()
    legacy = {key: value for key, value in current.items()
              if key not in {"backend", "backend_quality"}}
    assert bytes(pod_options(current)) == bytes(pod_options(legacy))
    assert ctypes.sizeof(Options) == 128
    assert Options.backend.offset == 112
    assert Options.backend_quality.offset == 116


def test_portable_quality_is_resolved_independently_at_call_time(monkeypatch):
    from sm64_events.replay.gpuencoder_options import from_replay_config

    current = options()
    structural = {key: value for key, value in current.items() if key in {
        "codec", "width", "height", "nominal_fps_num", "nominal_fps_den",
        "b_frames", "gop_frames", "initial_qp_p", "initial_qp_i", "initial_qp_b",
        "idr_interval_ticks", "input_format", "signal_color", "full_range",
        "matrix", "primaries", "transfer", "max_packet_bytes",
    }}
    monkeypatch.setattr(config, "MF_QUALITY", 91, raising=False)
    portable = from_replay_config(**structural, backend=BACKEND_WINDOWS)
    assert portable["backend_quality"] == 91
    assert all(portable[key] == 0 for key in (
        "preset", "tuning", "rate_control", "cq", "max_bitrate", "vbv_buffer_bits",
    ))
    monkeypatch.setattr(config, "VIDEO_CQ", 37)
    assert from_replay_config(**structural, backend=BACKEND_WINDOWS) == portable
    with pytest.raises(ValueError, match="backend"):
        from_replay_config(**structural, backend=999)
