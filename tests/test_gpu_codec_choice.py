"""Which codec the recorder writes, and how the adapter gets to answer.

AV1 is worth 32% of H.264's bytes at equal quality and encodes faster, so the
recorder asks for it first and Save publishes already-small bytes. Only
RTX 40-series and newer have an AV1 encoder, and the answer comes from the
adapter itself rather than from a model name: an adapter without one refuses
BY CODEC, which is typed apart from every real fault.
"""

from functools import partial
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from sm64_events.replay import gpuadapter, gpucapture_session, gpusettings
from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.encoderstage import staged_encoder_dll
from sm64_events.replay.gpuencoder import Result
from sm64_events.replay.gpuencoder_abi import CODEC_AV1, CODEC_H264
from sm64_events.replay.gpucapture_session import CaptureSession
from sm64_events.replay.gpusettings import (GpuSettings, note_codec_unavailable,
                                            recording_codec)

ONE = (0x11, 0x22)
OTHER = (0x33, 0x44)


@pytest.fixture(autouse=True)
def fresh_memo():
    gpusettings.forget_adapter_codecs()
    yield
    gpusettings.forget_adapter_codecs()


@pytest.fixture
def encoder_asset(tmp_path, monkeypatch):
    """Fake controllers negotiate without a locally built encoder or real GPU.

    Keep the actual private-copy staging contract with temporary shipped bytes
    and scratch root. Adapter discovery is unknown; no helper loads these bytes.
    """
    shipped = tmp_path / "shipped" / "SM64GpuEncoderV1.dll"
    shipped.parent.mkdir()
    shipped.write_bytes(b"inert encoder bytes for a fake controller")
    monkeypatch.setattr(gpucapture_session, "bundled_encoder_dll", lambda: shipped)
    monkeypatch.setattr(gpucapture_session, "staged_encoder_dll",
                        partial(staged_encoder_dll, root=tmp_path / "scratch"))
    monkeypatch.setattr(gpuadapter, "adapter_facts", lambda _luid: None)
    gpusettings.forget_adapter_codecs()
    yield shipped
    gpusettings.forget_adapter_codecs()


def test_an_adapter_is_asked_for_av1_until_it_says_it_has_none():
    assert recording_codec(ONE) == CODEC_AV1
    assert note_codec_unavailable(ONE, CODEC_AV1) is True
    assert recording_codec(ONE) == CODEC_H264
    # Remembered per adapter: a second GPU answers for itself.
    assert recording_codec(OTHER) == CODEC_AV1


def test_h264_has_nowhere_to_fall_back_to():
    """The fallback must terminate. H.264 is the floor -- an adapter that
    refuses it by codec has no NVENC we can use, and reporting that is the
    session's job, not something to retry around forever."""
    assert note_codec_unavailable(ONE, CODEC_H264) is False
    assert recording_codec(ONE) == CODEC_AV1


class Replies:
    """A controller that answers each Open with the next queued result."""

    def __init__(self, *results):
        self.results = list(results)
        self.requested = []
        self.commands = []
        self.next_id = 0

    def enqueue(self, command):
        self.commands.append(command)
        self.requested.append(command["options"]["codec"])
        self.next_id += 1
        return self.next_id

    def take_result(self):
        result = self.results.pop(0)
        return NS(request_id=self.next_id,
                  metadata={"result": int(result), "error": None if result == Result.OK
                            else f"open refused: {result.name}",
                            "worker_disposal_required": False})

    def status(self):
        return {"fault": None}


def session_for(controller):
    header = NS(even_width=320, even_height=240, luid_high=ONE[0],
                luid_low=ONE[1], format=1)
    owner = NS(settings=GpuSettings(), cfg=ReplayConfig(), nominal_rate=30,
               want_capture=lambda: True, begin_audio=lambda: "handoff")
    session = CaptureSession(owner, NS(snapshot=NS(lifecycle=None, state="running")),
                             controller_factory=lambda **_kwargs: controller)
    session.channel = NS(header=header, texture_names=("a", "b"),
                         encoder_ready=lambda: None)
    session._prepare_sink = lambda: True
    return session


def test_an_adapter_without_av1_records_h264_after_one_extra_open(encoder_asset):
    controller = Replies(Result.CODEC, Result.OK)
    session = session_for(controller)
    assert session._open_encoder() is not None
    assert controller.requested == [CODEC_AV1, CODEC_H264]
    assert session.codec == "h264"
    # Asked once, then remembered: the next capture on this adapter goes
    # straight to H.264 instead of paying the refusal again.
    assert recording_codec(ONE) == CODEC_H264


def test_an_adapter_with_av1_records_av1_on_the_first_open(encoder_asset):
    controller = Replies(Result.OK)
    session = session_for(controller)
    assert session._open_encoder() is not None
    assert controller.requested == [CODEC_AV1]
    assert session.codec == "av1"


def test_a_real_open_failure_is_reported_and_never_retried_as_a_codec(encoder_asset):
    """The fallback must not become a way to swallow driver faults. Only a
    by-codec refusal is a fact about the hardware; everything else ends the
    session with its own cause."""
    controller = Replies(Result.ENCODER)
    session = session_for(controller)
    with pytest.raises(RuntimeError, match="ENCODER"):
        session._open_encoder()
    assert controller.requested == [CODEC_AV1]
    assert recording_codec(ONE) == CODEC_AV1


def test_the_session_names_its_codec_to_the_mux(encoder_asset):
    """The format the archive is opened with is the codec that was actually
    negotiated, never a default -- an `av01` track written as `avc1` would
    index and then fail to decode."""
    from sm64_events.replay.packetmux import NativeFormat
    for reply, expected in ((Result.OK, "av1"), (Result.CODEC, "h264")):
        gpusettings.forget_adapter_codecs()
        results = [reply] if reply == Result.OK else [reply, Result.OK]
        session = session_for(Replies(*results))
        formats = []

        def prepare(s=session, seen=formats):
            seen.append(NativeFormat(s.codec, 320, 240, 30))
            return True

        session._prepare_sink = prepare
        session._open_encoder()
        assert [f.codec for f in formats] == [expected]


def test_fake_controller_receives_a_private_staged_encoder_asset(encoder_asset):
    controller = Replies(Result.OK)
    session_for(controller)._open_encoder()
    staged = Path(controller.commands[0]["dll_path"])
    assert staged != encoder_asset and staged.is_relative_to(encoder_asset.parent.parent / "scratch")
    assert staged.name == encoder_asset.name and staged.read_bytes() == encoder_asset.read_bytes()


def test_a_missing_encoder_asset_remains_a_fault_before_any_helper(monkeypatch):
    monkeypatch.setattr(gpuadapter, "adapter_facts", lambda _luid: None)
    monkeypatch.setattr(gpucapture_session, "bundled_encoder_dll", lambda: None)
    controller = Replies(Result.OK)
    with pytest.raises(RuntimeError, match="carries no SM64GpuEncoderV1.dll") as fault:
        session_for(controller)._open_encoder()
    from sm64_events.replay.gpucapture import GpuBackendUnsupported
    assert not isinstance(fault.value, GpuBackendUnsupported)
    assert controller.commands == [] and gpusettings.backend_unsupported(ONE) is None


def test_encoder_staging_failure_remains_a_fault_before_any_helper(encoder_asset, monkeypatch):
    def denied(_shipped):
        raise OSError("encoder staging denied")
    monkeypatch.setattr(gpucapture_session, "staged_encoder_dll", denied)
    controller = Replies(Result.OK)
    with pytest.raises(OSError, match="encoder staging denied"):
        session_for(controller)._open_encoder()
    assert controller.commands == [] and gpusettings.backend_unsupported(ONE) is None
