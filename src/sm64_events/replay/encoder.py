"""Encode the capture streams into ring files.

Video: one fresh PyAV MPEG-TS container + encoder per ~2 s segment (PyAV's
built-in segment muxer has a long-standing crash bug — issue #254 — so we
rotate manually). Fresh-per-segment means every segment opens on a keyframe
and is independently decodable; encoder init every 2 s is negligible at
480p. GOP = segment length, closed.

Audio: raw PCM s16le interleaved sidecar chunks (.pcm), NOT per-segment AAC —
fresh AAC encoders add ~21 ms priming silence per segment which would tick
audibly every 2 s in extracted clips. PCM is gapless, sample-exact to slice,
and AAC is encoded once at clip time. Cost: ~0.7 GB/h, comparable to video.

Frame indexes are wall-clock-locked (index = round(seconds_since_anchor *
fps), assigned by the recorder), so utc_start of any segment is
anchor + first_index/fps exactly — no per-frame timestamp bookkeeping.

PyAV 17 API notes (verified against av 17.1.0):
- stream.options = {...} assignment works after add_stream(); no need to pass
  via add_stream() keyword.
- pix_fmt is set directly on the stream object; gop_size and time_base go
  through stream.codec_context.<attr>.
- av.VideoFrame.from_ndarray(arr, format='bgra').reformat(format='yuv420p')
  works without extra width/height args.
- pick_video_codec(): CodecContext.create('h264_nvenc', 'w') + .open() is the
  right probe path; avcodec_open2 error 22 means nvenc is unavailable (driver
  gate), so we fall back to libx264 silently.
"""
import logging
from datetime import timedelta
from fractions import Fraction
from pathlib import Path

import av
import numpy as np

from sm64_events.replay.clock import CaptureClock
from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.ring import SegmentInfo

log = logging.getLogger("sm64.replay")


def pick_video_codec() -> str:
    """NVENC if the bundled ffmpeg + driver can actually encode (driver >= 570
    gate per research) — probe with one real frame, not just codec presence."""
    try:
        ctx = av.CodecContext.create("h264_nvenc", "w")
        ctx.width, ctx.height = 64, 64
        ctx.pix_fmt = "yuv420p"
        ctx.time_base = Fraction(1, 30)
        ctx.open()
        f = av.VideoFrame(64, 64, "yuv420p")
        f.pts = 0
        ctx.encode(f)
        return "h264_nvenc"
    except Exception:
        log.info("h264_nvenc unavailable - falling back to libx264")
        return "libx264"


class SegmentWriter:
    def __init__(self, cfg: ReplayConfig, clock: CaptureClock, out_dir: Path,
                 codec: str, on_segment) -> None:
        self._cfg = cfg
        self._clock = clock
        self._dir = out_dir
        self._codec = codec
        self._on_segment = on_segment
        self._frames_per_seg = int(cfg.fps * cfg.segment_s)
        # video state
        self._container = None
        self._stream = None
        self._seg_first_index: int | None = None
        self._seg_frames = 0
        self._seg_n = 0
        self._dims: tuple[int, int] | None = None  # (w, h)
        self._path: Path | None = None
        # audio state
        self._audio_t0 = None
        self._chunk_samples = int(cfg.audio_rate * cfg.segment_s)
        self._pcm_buf: list[np.ndarray] = []
        self._pcm_buffered = 0
        self._samples_written = 0
        self._chunk_n = 0
        out_dir.mkdir(parents=True, exist_ok=True)

    # -- video ---------------------------------------------------------------
    def write_video(self, bgra: np.ndarray, frame_index: int) -> None:
        h, w = bgra.shape[:2]
        if self._container is not None and (
                (w, h) != self._dims or self._seg_frames >= self._frames_per_seg):
            self._close_video_segment()
        if self._container is None:
            self._open_video_segment(frame_index, w, h)
        vf = av.VideoFrame.from_ndarray(bgra, format="bgra")
        vf = vf.reformat(format="yuv420p")
        vf.pts = frame_index - self._seg_first_index
        for pkt in self._stream.encode(vf):
            self._container.mux(pkt)
        self._seg_frames += 1

    def _open_video_segment(self, first_index: int, w: int, h: int) -> None:
        self._seg_n += 1
        path = self._dir / f"video_{self._seg_n:06d}.ts"
        self._container = av.open(str(path), "w", format="mpegts")
        self._stream = self._container.add_stream(self._codec, rate=self._cfg.fps)
        self._stream.width, self._stream.height = w, h
        self._stream.pix_fmt = "yuv420p"
        self._stream.codec_context.time_base = Fraction(1, self._cfg.fps)
        self._stream.codec_context.gop_size = self._frames_per_seg
        if self._codec == "libx264":
            self._stream.options = {"preset": "ultrafast", "tune": "zerolatency"}
        self._seg_first_index = first_index
        self._seg_frames = 0
        self._dims = (w, h)
        self._path = path

    def _close_video_segment(self) -> None:
        if self._container is None:
            return
        for pkt in self._stream.encode(None):
            self._container.mux(pkt)
        self._container.close()
        fps = self._cfg.fps
        start = self._clock.anchor_utc + timedelta(
            seconds=self._seg_first_index / fps)
        end = start + timedelta(seconds=self._seg_frames / fps)
        self._on_segment(SegmentInfo(
            path=self._path, kind="video", utc_start=start, utc_end=end,
            size_bytes=self._path.stat().st_size))
        self._container = self._stream = None

    # -- audio ---------------------------------------------------------------
    def start_audio(self, t0_utc) -> None:
        self._audio_t0 = t0_utc

    def write_audio(self, pcm_s16: np.ndarray) -> None:
        """pcm_s16: (n, 2) int16 at cfg.audio_rate."""
        self._pcm_buf.append(pcm_s16)
        self._pcm_buffered += len(pcm_s16)
        while self._pcm_buffered >= self._chunk_samples:
            self._flush_audio_chunk(self._chunk_samples)

    def _flush_audio_chunk(self, n_samples: int) -> None:
        buf = np.concatenate(self._pcm_buf)
        chunk, rest = buf[:n_samples], buf[n_samples:]
        self._pcm_buf = [rest] if len(rest) else []
        self._pcm_buffered = len(rest)
        self._chunk_n += 1
        path = self._dir / f"audio_{self._chunk_n:06d}.pcm"
        path.write_bytes(chunk.tobytes())
        rate = self._cfg.audio_rate
        start = self._audio_t0 + timedelta(seconds=self._samples_written / rate)
        end = start + timedelta(seconds=len(chunk) / rate)
        self._samples_written += len(chunk)
        self._on_segment(SegmentInfo(
            path=path, kind="audio", utc_start=start, utc_end=end,
            size_bytes=len(chunk) * 4))  # n*2ch*2bytes

    # -- lifecycle -----------------------------------------------------------
    def close(self) -> None:
        self._close_video_segment()
        if self._pcm_buffered:
            self._flush_audio_chunk(self._pcm_buffered)
