"""Raw NUT packets preserve pixels, timing and audio without AVFrame copies."""
import gc
import io
from types import SimpleNamespace

import av
import numpy as np
import pytest

from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.ffmpeg_sink import FfmpegAvSink
from sm64_events.replay.media import MEDIA_TIME_BASE, MediaRun


def record_nut(tmp_path, width, height, eager):
    output = io.BytesIO()
    sink = FfmpegAvSink(ReplayConfig(scratch_dir=tmp_path), lambda seg: None,
                        ffmpeg="unused")
    sink._proc = SimpleNamespace(stdin=output)
    sink._media_run = MediaRun("test-run", 1000.0)
    sink._run_epoch = 1000.0
    # submit retains the existing even crop and contiguous row contract.
    sink._open_mux(width & ~1, height & ~1)
    assigned = []
    try:
        for index, offset in enumerate([0, 0, .00001, .03, .5, .2, 2.1]):
            padded = np.random.default_rng(index).integers(
                0, 256, (height, width + 3, 4), dtype=np.uint8)
            sink.submit(padded[:, :width], (index, 1000.0 + offset))
            pixels, tag = sink._queue.popleft()
            if eager:
                # The previous route: padded AVFrame followed by rawvideo encode.
                frame = av.VideoFrame.from_ndarray(pixels, format="bgra")
                frame.time_base = MEDIA_TIME_BASE
                pts = max(sink._media_run.ticks_at(tag[1]),
                          sink._last_video_pts + 1 if sink._last_video_pts is not None else 0)
                frame.pts = pts
                for packet in sink._mux_stream.encode(frame):
                    sink._mux.mux(packet)
                sink._last_video_pts = pts
                del frame
            else:
                pts = sink._mux_picture(pixels, tag[1])
            assigned.append(pts)
            # The NUT interleaver may hold video waiting for the other stream.
            # Drop every caller reference before that flush; retained data must
            # still decode after GC and subsequent producer allocations.
            del pixels, padded
            gc.collect()
            pcm = np.full((480, 2), index * 1111, dtype=np.int16)
            sink._mux_audio_chunk(pcm.tobytes(), 1_000_000_000 + index * 10000)
    finally:
        sink._close_mux()
    return output.getvalue(), assigned


def decoded(payload, kind):
    with av.open(io.BytesIO(payload), format="nut") as container:
        stream = getattr(container.streams, kind)[0]
        return [(frame.pts, frame.time_base,
                 frame.to_ndarray(format="bgra").tobytes() if kind == "video"
                 else frame.to_ndarray().tobytes()) for frame in container.decode(stream)]


@pytest.mark.parametrize("width,height", [(1190, 10), (1192, 16), (640, 17), (1601, 17)])
def test_direct_packets_match_eager_pixels_pts_and_audio(tmp_path, width, height):
    previous, old_pts = record_nut(tmp_path / "old", width, height, True)
    current, new_pts = record_nut(tmp_path / "new", width, height, False)
    assert new_pts == old_pts == [0, 1, 2, 2700, 45000, 45001, 189000]
    video = decoded(current, "video")
    assert len(video) == 7
    assert video == decoded(previous, "video")
    audio = decoded(current, "audio")
    assert len(audio) == 7
    assert audio == decoded(previous, "audio")
