"""Raw PCM transport retains the old encoder's sample and timestamp contract."""
import gc
import io
from types import SimpleNamespace

import av
import numpy as np
import pytest

from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.ffmpeg_sink import FfmpegAvSink, PICTURE_TIME_BASE
from sm64_events.replay.media import MediaRun


def legacy_pcm(sink, pcm, pts_us):
    """The previous AudioFrame/PCM encode route, retained as the counterfactual."""
    rate = sink._cfg.audio_rate
    relative = pts_us - 1_000_000_000
    if relative < 0:
        skip = min(len(pcm) // 4, (-relative * rate + 999_999) // 1_000_000)
        pcm = pcm[skip * 4:]
        if not pcm:
            return
        relative += round(skip * 1_000_000 / rate)
    block = np.frombuffer(pcm, dtype=np.int16).reshape(1, -1)
    frame = av.AudioFrame.from_ndarray(block, format="s16", layout="stereo")
    frame.sample_rate, frame.time_base, frame.pts = rate, PICTURE_TIME_BASE, relative
    for packet in sink._mux_audio.encode(frame):
        sink._mux.mux(packet)


def record(tmp_path, rate, write):
    output = io.BytesIO()
    sink = FfmpegAvSink(ReplayConfig(scratch_dir=tmp_path, audio_rate=rate), lambda seg: None)
    sink._proc = SimpleNamespace(stdin=output)
    sink._media_run, sink._run_epoch = MediaRun("pcm-test", 1000.0), 1000.0
    sink._open_mux(64, 48)
    stamp = 999_990_000  # pre-origin packets and a packet crossing picture zero
    try:
        sink._mux_picture(np.zeros((48, 64, 4), dtype=np.uint8), 1000.0)
        # Include half-microsecond duration ties at 48/96 kHz, including odd
        # callback sizes that can also result from trimming the first batch.
        counts = [1, 73, 480, 512, 1, 2048, 481, 1, 7, 480, 3, 6, 9, 18, 513]
        for index, count in enumerate(counts):
            samples = np.random.default_rng(index).integers(
                -32768, 32767, (count, 2), dtype=np.int16)
            write(sink, samples.tobytes(), stamp)
            stamp += round(count * 1_000_000 / rate) + [0, 1, 13, 137, 0, 5, 791][index % 7]
            del samples
            gc.collect()  # no caller PCM reference remains while NUT interleaves
        sink._mux_picture(np.ones((48, 64, 4), dtype=np.uint8), 1000.2)
    finally:
        sink._close_mux()
    return output.getvalue()


def decoded_evidence(payload):
    with av.open(io.BytesIO(payload), format="nut") as container:
        packets = [(p.stream.index, p.pts, p.dts, p.duration, p.time_base,
                    p.is_keyframe, bytes(p)) for p in container.demux() if p.size]
    with av.open(io.BytesIO(payload), format="nut") as container:
        audio = [(f.pts, f.time_base, f.sample_rate, f.to_ndarray().tobytes())
                 for f in container.decode(audio=0)]
    return packets, audio


@pytest.mark.parametrize("rate", [44100, 48000, 96000])
def test_direct_pcm_retains_samples_packets_and_clock_through_origin_trim(tmp_path, rate):
    previous = decoded_evidence(record(tmp_path, rate, legacy_pcm))
    current = decoded_evidence(record(tmp_path, rate, FfmpegAvSink._mux_audio_chunk))
    packets, audio = current
    assert len(audio) >= 7
    assert sum(packet[0] == 0 for packet in packets) == 2
    assert current == previous
    assert all(pts >= 0 for pts, *_ in audio)


def test_closed_mux_and_incomplete_stereo_packet_fail_without_silent_audio(tmp_path):
    sink = FfmpegAvSink(ReplayConfig(scratch_dir=tmp_path), lambda seg: None)
    sink._run_epoch = 1000.0
    with pytest.raises(OSError, match="no NUT mux open"):
        sink._mux_audio_chunk(b"\0" * 4, 1_000_000_000)
    with pytest.raises(ValueError, match="complete s16le stereo samples"):
        sink._mux_audio_chunk(b"\0" * 6, 1_000_000_000)
