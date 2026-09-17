"""A real mux whose final output is refused still proves the writer exited."""

import io
from types import SimpleNamespace as NS

import pytest

from sm64_events.replay.media import MediaRun
from sm64_events.replay.packetmux import H264Format, PacketFragmentMux
from test_packetmux import RATE, fixture_packets


def test_real_mux_final_output_refusal_keeps_cause_and_proves_writer_exit():
    from sm64_events.replay.gpucapture_session import CaptureSession
    from sm64_events.replay.gpupublication import PublicationWriteError, PublicationError
    from sm64_events.replay.gpusettings import GpuSettings

    class RefusingOutput(io.RawIOBase):
        """The session's output contract: libav writes, the owner joins it."""
        refuse = False
        finished = False

        def writable(self):
            return True

        def write(self, data):
            if self.refuse:
                raise PublicationWriteError("final output unavailable")
            return len(data)

        def finish(self, error=None, *, timeout):
            self.finished = True

        def status(self):
            return {}

    output = RefusingOutput()
    run = MediaRun("trailer-refusal", 1000.0)
    mux = PacketFragmentMux(output, H264Format(320, 240, 30), run, audio_rate=RATE,
                            audio_bitrate=160000, packet_limit=1 << 20, pcm_limit=384000)
    session = CaptureSession(NS(settings=GpuSettings(), end_audio=lambda h: None),
                             NS(request_stop=lambda reason: None))
    session.output, session.mux = output, mux
    session._finish_media = mux.close
    try:
        for packet in fixture_packets():
            mux.write_video(packet)
        output.refuse = True  # exercise actual PyAV final-fragment/trailer callback
        with pytest.raises(PublicationError, match="final output unavailable"):
            session.close()
        assert session._output_joined and mux.closed and output.finished
        assert isinstance(mux._cleanup_cause, PublicationWriteError)
    finally:
        mux.abort()
