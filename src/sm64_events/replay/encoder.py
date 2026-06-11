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
Segments are internally contiguous; an index jump forces rotation, so a
capture gap becomes a segment boundary (a coverage hole in the ring), never
a mis-stamped duration.

PyAV 17 API notes (verified against av 17.1.0):
- stream.options = {...} assignment works after add_stream(); no need to pass
  via add_stream() keyword.
- pix_fmt is set directly on the stream object; gop_size and time_base go
  through stream.codec_context.<attr>.
- av.VideoFrame.from_ndarray(arr, format='bgra').reformat(format='yuv420p')
  works without extra width/height args.
- pick_video_codec(): CodecContext.create('h264_nvenc', 'w') + .open() is the
  right probe path; if avcodec_open2 fails we fall back to libx264. NOTE:
  error 22 does NOT necessarily mean nvenc is unavailable — see the probe-size
  caveat in pick_video_codec()'s docstring.
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
    gate per research) — probe with one real frame, not just codec presence.

    Probe at 640x480 (the actual PJ64 window size), NOT a tiny frame: NVENC
    rejects dimensions below its minimum encode size with error 22 at
    avcodec_open2, so a 64x64 probe false-negatives to libx264 on machines
    where NVENC works fine (live-verified on this machine: 64x64 FAIL,
    256x256 OK, 640x480 OK)."""
    try:
        ctx = av.CodecContext.create("h264_nvenc", "w")
        ctx.width, ctx.height = 640, 480
        ctx.pix_fmt = "yuv420p"
        ctx.time_base = Fraction(1, 30)
        ctx.open()
        f = av.VideoFrame(640, 480, "yuv420p")
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
        self._seg_next_index: int | None = None  # enforces per-segment contiguity
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
        # yuv420p requires even dimensions; a window dragged to an odd pixel
        # size would otherwise raise at encode and wedge the segment state.
        h, w = bgra.shape[:2]
        if (h & 1) or (w & 1):
            bgra = bgra[:h & ~1, :w & ~1]
            h, w = bgra.shape[:2]
        if self._container is not None and (
                (w, h) != self._dims
                or self._seg_frames >= self._frames_per_seg
                or frame_index != self._seg_next_index):
            self._close_video_segment()
        if self._container is None:
            self._open_video_segment(frame_index, w, h)
        try:
            vf = av.VideoFrame.from_ndarray(bgra, format="bgra")
            vf = vf.reformat(format="yuv420p")
            vf.pts = frame_index - self._seg_first_index
            for pkt in self._stream.encode(vf):
                self._container.mux(pkt)
            self._seg_frames += 1
            self._seg_next_index = frame_index + 1
        except Exception:
            log.exception("encode failed on frame %d — dropping frame, resetting segment", frame_index)
            try:
                self._container.close()
            except Exception:
                pass
            try:
                self._path.unlink(missing_ok=True)
            except Exception:
                pass
            self._container = self._stream = None

    def _open_video_segment(self, first_index: int, w: int, h: int) -> None:
        self._seg_n += 1
        path = self._dir / f"video_{self._seg_n:06d}.ts"
        self._container = av.open(str(path), "w", format="mpegts")
        self._stream = self._container.add_stream(self._codec, rate=self._cfg.fps)
        self._stream.width, self._stream.height = w, h
        self._stream.pix_fmt = "yuv420p"
        self._stream.codec_context.time_base = Fraction(1, self._cfg.fps)
        self._stream.codec_context.gop_size = self._frames_per_seg
        if self._codec == "h264_nvenc":
            # NVENC defaults to B-frames, which shift the stream's start_time
            # to +3 frames (0.1 s) and break the extractor's frame0=pts0
            # contract; they buy nothing at 480p with a 2 s closed GOP.
            self._stream.options = {"bf": "0"}
        if self._codec == "libx264":
            self._stream.options = {"preset": "ultrafast", "tune": "zerolatency"}
        self._seg_first_index = first_index
        self._seg_next_index = first_index + 1
        self._seg_frames = 0
        self._dims = (w, h)
        self._path = path

    def _close_video_segment(self) -> None:
        if self._container is None:
            return
        container, stream = self._container, self._stream
        path, first_index, seg_frames = self._path, self._seg_first_index, self._seg_frames
        self._container = self._stream = None
        try:
            for pkt in stream.encode(None):
                container.mux(pkt)
            container.close()
        except Exception:
            log.exception("flush failed closing segment %s — segment may be incomplete", path)
            try:
                container.close()
            except Exception:
                pass
            return
        fps = self._cfg.fps
        start = self._clock.anchor_utc + timedelta(seconds=first_index / fps)
        end = start + timedelta(seconds=seg_frames / fps)
        self._on_segment(SegmentInfo(
            path=path, kind="video", utc_start=start, utc_end=end,
            size_bytes=path.stat().st_size))

    # -- audio ---------------------------------------------------------------
    def start_audio(self, t0_utc) -> None:
        self._audio_t0 = t0_utc

    def write_audio(self, pcm_s16: np.ndarray) -> None:
        """pcm_s16: (n, 2) int16 at cfg.audio_rate."""
        if self._audio_t0 is None:
            raise RuntimeError("start_audio() not called")
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
        if self._audio_t0 is not None and self._pcm_buffered:
            self._flush_audio_chunk(self._pcm_buffered)
