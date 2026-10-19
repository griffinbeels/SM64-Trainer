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


@pytest.mark.parametrize("vendor, expected", [(0x10DE, 0), (0x1002, 1), (0x8086, 1)])
def test_live_session_uses_the_actual_rendering_adapter(monkeypatch, vendor, expected):
    from sm64_events.replay import gpuadapter
    from sm64_events.replay.gpuencoder import Result
    from test_gpu_codec_choice import Replies, session_for

    facts = gpuadapter.AdapterFacts("actual renderer", vendor, False, None)
    monkeypatch.setattr(gpuadapter, "adapter_facts", lambda luid: facts)
    monkeypatch.setattr(config, "MF_QUALITY", 90, raising=False)
    controller = Replies(Result.OK)
    session = session_for(controller)
    header = session.channel.header
    resolved = session.settings.encoder(header, session.owner.cfg,
                                        nominal_rate=session.owner.nominal_rate)
    assert resolved["backend"] == expected
    assert resolved["backend_quality"] == (90 if expected else 0)
    # Routing and backend-specific driver requirements are separate decisions.
    if expected:
        assert gpuadapter.unsupported_reason(facts) is None


def test_native_prechannel_refusal_is_precise_and_does_not_hide_cleanup():
    from types import SimpleNamespace as NS
    from sm64_events.replay.gpucapture import GpuBackendUnsupported
    from test_gpu_codec_choice import Replies, session_for

    session = session_for(Replies())
    session.demand.snapshot = NS(lifecycle=False, state="fault", backend_reason=10031,
                                 cleanup_error="", reason="native unavailable")
    with pytest.raises(GpuBackendUnsupported, match="GL_EXT_win32_keyed_mutex"):
        session._continue()
    session.demand.snapshot.cleanup_error = "retained native resources"
    with pytest.raises(RuntimeError, match="native unavailable") as failure:
        session._continue()
    assert not isinstance(failure.value, GpuBackendUnsupported)
