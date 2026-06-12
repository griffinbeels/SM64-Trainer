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
        self._enc = None                 # persistent encoder (one per run)
        self._enc_dims: tuple[int, int] | None = None
        self._run_first_index: int | None = None
        self._seg_base_pts = 0
        self._seg_pkts = 0
        self._seg_first_index: int | None = None
        self._seg_next_index: int | None = None
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
    # PERSISTENT ENCODER, ROTATING CONTAINERS. The encoder (NVENC session)
    # opens ONCE per recording run: PyAV holds the GIL through avcodec_open2,
    # and an NVENC session open measures ~110 ms — opening one per 2 s
    # segment froze EVERY thread in the process each rotation (grab loop
    # missed ~6 slots = visible skip; the PortAudio callback starved = audio
    # crackle; live: "grab stall 92-177 ms" at exactly the rotation cadence,
    # reproduced standalone by a heartbeat-gap probe). Containers are just
    # files + muxer state and rotate in ~1 ms. Packets are encoded with
    # RUN-GLOBAL pts and rebased per segment on mux, preserving the
    # extractor contract (frame0 = pts0 per segment file); each segment's
    # first frame is a forced IDR so segments stay independently decodable.

    def _ensure_encoder(self, w: int, h: int) -> None:
        if self._enc is not None and self._enc_dims == (w, h):
            return
        if self._enc is not None:
            log.info("dimension change %s -> %s: recreating encoder "
                     "(expected one-off ~110 ms stall)", self._enc_dims, (w, h))
        ctx = av.CodecContext.create(self._codec, "w")
        ctx.width, ctx.height = w, h
        ctx.pix_fmt = "yuv420p"
        ctx.time_base = Fraction(1, self._cfg.fps)
        ctx.framerate = Fraction(self._cfg.fps, 1)
        ctx.gop_size = self._frames_per_seg
        if self._codec == "h264_nvenc":
            # bf=0: B-frames shift start_time +3 frames and break the
            # frame0=pts0 contract. p1+ull: encode must beat 16.7 ms/frame.
            # forced-idr: pict_type=I at segment starts must be a real IDR
            # or rotated segments would not be independently decodable.
            ctx.options = {"bf": "0", "preset": "p1", "tune": "ull",
                           "forced-idr": "1"}
        if self._codec == "libx264":
            ctx.options = {"preset": "ultrafast", "tune": "zerolatency"}
        ctx.open()
        self._enc = ctx
        self._enc_dims = (w, h)

    def write_video(self, bgra: np.ndarray, frame_index: int) -> None:
        # yuv420p requires even dimensions; a window dragged to an odd pixel
        # size would otherwise raise at encode and wedge the segment state.
        h, w = bgra.shape[:2]
        if (h & 1) or (w & 1):
            bgra = bgra[:h & ~1, :w & ~1]
            h, w = bgra.shape[:2]
        if self._container is not None and frame_index < self._seg_next_index:
            return  # backwards/duplicate index — drop silently to preserve monotonic utc_end
        if self._container is not None and (
                (w, h) != self._dims or frame_index != self._seg_next_index):
            # Dim change / index gap: the encoder must drain (it may hold a
            # couple of delayed frames) and restart — segment-length rotation
            # is NOT handled here; it is packet-driven in _mux_routed, because
            # NVENC emits frame N's packet ~2 frames late even at p1/ull and
            # frame-driven rotation dropped the tail of every segment.
            self._drain_encoder()
            self._close_video_segment()
        try:
            self._ensure_encoder(w, h)
            if self._container is None:
                self._open_video_segment(frame_index, w, h)
            vf = av.VideoFrame.from_ndarray(bgra, format="bgra")
            vf = vf.reformat(format="yuv420p")
            if self._run_first_index is None:
                self._run_first_index = frame_index
            vf.pts = frame_index - self._run_first_index   # run-global pts
            vf.time_base = Fraction(1, self._cfg.fps)
            if (vf.pts - self._seg_base_pts) % self._frames_per_seg == 0:
                vf.pict_type = 1  # forced IDR at every segment-grid boundary
            for pkt in self._enc.encode(vf):
                self._mux_routed(pkt)
            self._seg_next_index = frame_index + 1
        except Exception:
            log.exception("encode failed on frame %d — dropping frame, "
                          "resetting segment + encoder", frame_index)
            try:
                if self._container is not None:
                    self._container.close()
            except Exception:
                pass
            try:
                if self._path is not None:
                    self._path.unlink(missing_ok=True)
            except Exception:
                pass
            self._container = self._stream = None
            self._enc = None  # recreate on next frame

    def _drain_encoder(self) -> None:
        """Flush remaining delayed packets into the current container and
        retire the encoder (gap/dim-change/close paths only — costs a
        ~110 ms re-open next frame, acceptable for these rare events)."""
        if self._enc is None:
            return
        try:
            for pkt in self._enc.encode(None):
                self._mux_routed(pkt)
        except Exception:
            log.exception("encoder drain failed")
        self._enc = None

    def _mux_routed(self, pkt) -> None:
        """Route an encoder packet into its segment's container, rotating
        containers when a packet crosses the segment-grid boundary —
        PACKET-driven because the encoder emits packets ~2 frames behind
        the submitted frames. Rebases run-global pts/dts to segment-local
        so each file starts at pts 0 (the extractor contract)."""
        if self._container is None or pkt.pts is None:
            return
        while pkt.pts >= self._seg_base_pts + self._frames_per_seg:
            next_global = (self._run_first_index + self._seg_base_pts
                           + self._frames_per_seg)
            self._close_video_segment()
            self._open_video_segment(next_global, *self._enc_dims)
        pkt.pts -= self._seg_base_pts
        if pkt.dts is not None:
            pkt.dts = pkt.pts
        pkt.stream = self._stream
        self._container.mux(pkt)
        self._seg_pkts += 1

    def _open_video_segment(self, first_index: int, w: int, h: int) -> None:
        self._seg_n += 1
        path = self._dir / f"video_{self._seg_n:06d}.ts"
        self._container = av.open(str(path), "w", format="mpegts")
        self._stream = self._container.add_stream(self._codec, rate=self._cfg.fps)
        self._stream.width, self._stream.height = w, h
        self._stream.pix_fmt = "yuv420p"
        self._stream.codec_context.time_base = Fraction(1, self._cfg.fps)
        if self._enc is not None and self._enc.extradata:
            self._stream.codec_context.extradata = self._enc.extradata
        self._seg_first_index = first_index
        if self._seg_next_index is None or first_index >= self._seg_next_index:
            self._seg_next_index = first_index + 1
        self._seg_base_pts = (first_index - self._run_first_index
                              if self._run_first_index is not None else 0)
        self._seg_pkts = 0
        self._dims = (w, h)
        self._path = path

    def _close_video_segment(self) -> None:
        if self._container is None:
            return
        container = self._container
        path, first_index, seg_frames = self._path, self._seg_first_index, self._seg_pkts
        if seg_frames == 0:
            # nothing was ever muxed (e.g. gap immediately after open):
            # discard the empty file rather than report a zero-length segment
            self._container = self._stream = None
            try:
                container.close()
            except Exception:
                pass
            path.unlink(missing_ok=True)
            return
        self._container = self._stream = None
        try:
            container.close()  # container only — the encoder lives on
        except Exception:
            log.exception("close failed for segment %s — discarding", path)
            try:
                path.unlink(missing_ok=True)
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
        self._drain_encoder()  # end-of-run flush routes delayed packets
        self._close_video_segment()
        if self._audio_t0 is not None and self._pcm_buffered:
            self._flush_audio_chunk(self._pcm_buffered)
