"""Cut one scrub-ready MP4 out of the ring.

Decode-and-re-encode, not stream-copy concat (spec decision): frame-accurate
edges, absorbs mid-session window resizes (everything scales to the first
frame's dims), and the clip gets dense keyframes (0.5 s GOP) + faststart —
the two properties browser scrubbing actually needs. NVENC makes this far
faster than realtime at 480p.

Video frame times are reconstructed as seg.utc_start + pts * time_base —
exact because MPEG-TS segments have pts origin 0 (encoder contract; the 90 kHz
time_base is read from the decoded stream so we never hardcode it). Gaps in
capture are segment boundaries, which simply skip ahead here.

Audio is sliced sample-exactly from the PCM chunks and AAC-encoded once,
here (the buffer keeps PCM precisely so no AAC priming gap ever lands
inside a clip).

A/V alignment: both streams are anchored to clamped span start `s`.
Video first frame >= s; audio sample 0 = s.

PyAV 17 adjustments vs. the original sketch
--------------------------------------------
- Frame timing: use `frame.pts * float(stream.time_base)` not `i / fps`.
  MPEG-TS rescales PTS to 90 kHz (1/90000), so enumerate-index i would be
  correct by coincidence but the PTS-based approach is explicit and handles
  any MPEG-TS time_base.
- Collect then interleave: collect all video frames (reformatted to yuv420p)
  and the full PCM buffer first, then create both streams upfront and
  interleave encoding. Encoding all video first then starting audio at pts=0
  triggers PyAV's "Cannot rebase to zero time" error because the muxer sees
  audio timestamps going backward relative to the already-written video wall
  clock. Interleaving avoids this entirely without requiring packet-level
  DTS sorting.
- Layout: `astream.codec_context.layout = "stereo"` (codec_context.channels
  is read-only in PyAV 17).
- AudioFrame: `from_ndarray(block.reshape(1, -1), format="s16", layout="stereo")`
  — packed s16 interleaved stereo expects shape (1, n_samples*channels).
- Resampler flush: call `resampler.resample(None)` before `astream.encode(None)`
  to drain the SwrContext internal buffer.
- faststart: `options={"movflags": "+faststart"}` on `av.open(..., "w")` works
  correctly in PyAV 17.0.1; no need for container_options workaround.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction
from pathlib import Path

import av
import numpy as np

from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.ring import SegmentRing

_EDGE_TOLERANCE_S = 0.5  # clamping beyond this marks the clip truncated
_AAC_FRAME_SIZE = 1024   # AAC encoder expects exactly 1024 samples per frame


@dataclass(frozen=True)
class ClipResult:
    path: Path
    duration_s: float
    truncated: bool


class ClipExtractor:
    def __init__(self, cfg: ReplayConfig, codec: str):
        self._cfg = cfg
        self._codec = codec

    def extract(self, ring: SegmentRing, start: datetime, end: datetime,
                out_path: Path) -> ClipResult:
        """Slice [start, end) from the ring and write a browser-scrubbable MP4.

        Clamps to available coverage; marks truncated if either edge moved by
        more than _EDGE_TOLERANCE_S. Raises ValueError if no footage overlaps.

        A/V alignment invariant: video first frame >= s, audio sample 0 = s.
        Both anchored to clamped span start `s`.
        """
        cov = ring.coverage("video")
        if cov is None:
            raise ValueError("no footage in the replay buffer")
        s = max(start, cov[0])
        e = min(end, cov[1])
        if e <= s:
            raise ValueError("no footage overlaps the requested span")
        truncated = ((s - start).total_seconds() > _EDGE_TOLERANCE_S
                     or (end - e).total_seconds() > _EDGE_TOLERANCE_S)

        fps = self._cfg.fps
        rate = self._cfg.audio_rate

        # -- Pass 1: collect video frames (reformatted) ----------------------
        # Decode each overlapping segment; keep only frames whose wall-clock
        # time falls in [s, e).  Frame time = seg.utc_start + pts*time_base
        # (MPEG-TS pts origin 0, 90 kHz time_base).
        video_frames: list[av.VideoFrame] = []
        dims: tuple[int, int] | None = None
        for seg in ring.covering("video", s, e):
            with av.open(str(seg.path)) as src:
                vstr = src.streams.video[0]
                tb = float(vstr.time_base)
                for fr in src.decode(video=0):
                    t = seg.utc_start + timedelta(seconds=fr.pts * tb)
                    if t < s or t >= e:
                        continue
                    if dims is None:
                        dims = (fr.width, fr.height)
                    video_frames.append(
                        fr.reformat(width=dims[0], height=dims[1],
                                    format="yuv420p"))

        if not video_frames:
            raise ValueError("no decodable video frames in the span")

        # -- Pass 2: assemble PCM buffer -------------------------------------
        # Contiguous s16le stereo, aligned to clamped span start `s`.
        # Coverage holes remain silent (zeros already in place).
        total_samples = int((e - s).total_seconds() * rate)
        pcm = np.zeros((total_samples, 2), dtype=np.int16)
        for chunk in ring.covering("audio", s, e):
            data = np.frombuffer(chunk.path.read_bytes(),
                                 dtype=np.int16).reshape(-1, 2)
            # offset into `data` where the window `s` begins
            src_off = max(0, int((s - chunk.utc_start).total_seconds() * rate))
            # offset into `pcm` where this chunk contributes
            dst_off = max(0, int((chunk.utc_start - s).total_seconds() * rate))
            n = min(len(data) - src_off, total_samples - dst_off)
            if n > 0:
                pcm[dst_off:dst_off + n] = data[src_off:src_off + n]

        # -- Pass 3: encode and mux (interleaved) ----------------------------
        # Create both streams upfront so the muxer sees a valid header before
        # any packets arrive.  Then interleave audio and video by sending one
        # audio block (1600 samples = one video frame at 48 kHz/30 fps) for
        # every video frame — this keeps the muxer's timeline monotone and
        # avoids the "Cannot rebase to zero time" error that occurs when audio
        # starts after a fully-flushed video stream.
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out = av.open(str(out_path), "w", options={"movflags": "+faststart"})
        try:
            vstream = out.add_stream(self._codec, rate=fps)
            vstream.width, vstream.height = dims
            vstream.pix_fmt = "yuv420p"
            vstream.codec_context.time_base = Fraction(1, fps)
            # dense keyframes (0.5 s GOP) for instant seeking
            vstream.codec_context.gop_size = max(1, fps // 2)
            if self._codec == "libx264":
                vstream.options = {"preset": "ultrafast", "tune": "zerolatency"}
            elif self._codec == "h264_nvenc":
                vstream.options = {"bf": "0"}

            astream = out.add_stream("aac", rate=rate)
            astream.codec_context.layout = "stereo"
            resampler = av.AudioResampler(format="fltp", layout="stereo",
                                          rate=rate)

            # samples per video frame (interleave granularity)
            smpv = rate // fps  # 1600 at 48 kHz / 30 fps
            audio_pos = 0

            for v_pts, vf in enumerate(video_frames):
                # encode one video frame
                vf.pts = v_pts
                for pkt in vstream.encode(vf):
                    out.mux(pkt)

                # encode one interleave-block worth of audio
                a_end = min(audio_pos + smpv, total_samples)
                if a_end > audio_pos:
                    block = pcm[audio_pos:a_end]
                    if len(block) < _AAC_FRAME_SIZE:
                        # Pad so AAC never sees a sub-1024 non-final frame
                        padded = np.zeros((_AAC_FRAME_SIZE, 2), dtype=np.int16)
                        padded[:len(block)] = block
                        block = padded
                    arr = np.ascontiguousarray(block.reshape(1, -1))
                    af = av.AudioFrame.from_ndarray(arr, format="s16",
                                                    layout="stereo")
                    af.sample_rate = rate
                    af.pts = audio_pos
                    audio_pos = a_end
                    for rf in resampler.resample(af):
                        for pkt in astream.encode(rf):
                            out.mux(pkt)

            # Encode any remaining audio (tail samples after last video frame)
            while audio_pos < total_samples:
                a_end = min(audio_pos + _AAC_FRAME_SIZE, total_samples)
                block = pcm[audio_pos:a_end]
                if len(block) < _AAC_FRAME_SIZE:
                    padded = np.zeros((_AAC_FRAME_SIZE, 2), dtype=np.int16)
                    padded[:len(block)] = block
                    block = padded
                arr = np.ascontiguousarray(block.reshape(1, -1))
                af = av.AudioFrame.from_ndarray(arr, format="s16",
                                                layout="stereo")
                af.sample_rate = rate
                af.pts = audio_pos
                audio_pos = a_end
                for rf in resampler.resample(af):
                    for pkt in astream.encode(rf):
                        out.mux(pkt)

            # Flush video encoder
            for pkt in vstream.encode(None):
                out.mux(pkt)
            # Drain resampler internal buffer
            for rf in resampler.resample(None):
                for pkt in astream.encode(rf):
                    out.mux(pkt)
            # Flush AAC encoder
            for pkt in astream.encode(None):
                out.mux(pkt)
        finally:
            out.close()

        return ClipResult(path=out_path,
                          duration_s=(e - s).total_seconds(),
                          truncated=truncated)
