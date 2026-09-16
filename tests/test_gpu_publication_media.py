"""Actual compressed output, VFR pictures and audio survive an asynchronous disk stall."""

import threading
from types import SimpleNamespace as NS
import pytest

from sm64_events.replay.gpupublication import ArchiveOutput
from sm64_events.replay.media import MediaRun
from sm64_events.replay.packetmux import H264Format, PacketFragmentMux
from test_packetmux import (Output, RATE, fixture_packets, supply, verify_video,
                            verify_audio)


def test_real_mux_publishes_exact_media_after_blocked_archive(tmp_path):
    packets = fixture_packets()
    run = MediaRun("async-publication", 1000.0)
    actual = Output(tmp_path, run)
    entered, release = threading.Event(), threading.Event()

    def feed(data):
        entered.set()
        assert release.wait(5), "release test-owned archive"
        actual.write(data)

    def finish(error):
        assert error is None
        actual.finish()

    output = ArchiveOutput(NS(feed=feed, finish=finish), max_bytes=4 << 20,
                           max_blocks=1024, max_age=10)
    mux = PacketFragmentMux(output, H264Format(320, 240, 30), run, audio_rate=RATE,
                            audio_bitrate=160000, packet_limit=1 << 20, pcm_limit=384000)
    end_sample = round((packets[-1].pts + packets[-1].duration) / 90000 * RATE)
    events = [(p.pts / 90000, "video", p) for p in packets]
    events += [(first / RATE, "audio", (first, min(960, end_sample - first)))
               for first in range(-2400, end_sample, 960)]
    events.sort(key=lambda event: event[0])
    try:
        supply(mux, events, run, 0)
        mux.close()
        assert entered.wait(2)
        # No archive operation can complete yet. The capture owner admitted the
        # entire small witness without waiting, and no unpublished bytes are visible.
        assert actual.archive.coverage() is None
        assert output.status()["pending_bytes"] > 0
    finally:
        release.set()
        output.finish(timeout=3)
    verify_video(actual, packets, packets, None)
    verify_audio(tmp_path, run, end_sample, mux, actual, None)


def test_real_mux_final_output_refusal_keeps_cause_and_proves_writer_exit():
    from sm64_events.replay.gpucapture_session import CaptureSession
    from sm64_events.replay.gpupublication import PublicationWriteError, PublicationError
    from sm64_events.replay.gpusettings import GpuSettings

    class RefusedTrailer(ArchiveOutput):
        refuse = False

        def write(self, data):
            if self.refuse:
                raise PublicationWriteError("final output unavailable")
            return super().write(data)

    output = RefusedTrailer(NS(feed=lambda data: None, finish=lambda error: None),
                            max_bytes=4 << 20, max_age=10)
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
        assert session._output_joined and mux.closed
        assert isinstance(mux._cleanup_cause, PublicationWriteError)
    finally:
        mux.abort()
        output.finish(timeout=2)
