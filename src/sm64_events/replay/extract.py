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

Memory: O(1) in clip length for video — exactly one decoded frame is alive at
a time (whole-attempt clips are the spec use case; minutes of buffered raw
frames would be GBs). The PCM buffer IS fully assembled up front, but s16
stereo is small (~46 MB for 4 min) and having it complete before encoding is
what lets us interleave A/V inside the single video decode pass.

PyAV 17 adjustments vs. the original sketch
--------------------------------------------
- Frame timing: use `frame.pts * float(stream.time_base)` not `i / fps`.
  MPEG-TS rescales PTS to 90 kHz (1/90000), so enumerate-index i would be
  correct by coincidence but the PTS-based approach is explicit and handles
  any MPEG-TS time_base.
- Interleave A/V encoding: encoding all video first then starting audio at
  pts=0 triggers PyAV's "Cannot rebase to zero time" mux error because the
  muxer sees audio timestamps going backward relative to the already-written
  video wall clock. Both streams are created up front (video dims peeked from
  the first in-window frame — a cheap partial re-decode of one segment), then
  one audio block (rate/fps samples) is muxed alongside each video frame so
  the timeline stays monotone.
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


def _in_window_frames(segments, s: datetime, e: datetime):
    """Yield (frame, t) tuples for decoded video frames whose wall-clock time
    falls in [s, e).

    Frame time t = seg.utc_start + pts*time_base (MPEG-TS pts origin 0,
    90 kHz time_base read from the stream). A coverage hole between segments
    simply skips ahead in wall-clock time — frames on either side still land
    in order. One frame alive at a time: O(1) memory in clip length.

    If a segment file has been evicted from the ring between covering() and
    open/read, it is silently skipped (the hole is handled by wall-clock-locked
    pts in the encoder).
    """
    for seg in segments:
        try:
            # Race: the live ring may evict seg.path between covering() and here
            src_ctx = av.open(str(seg.path))
        except (FileNotFoundError, av.error.FileNotFoundError):
            continue  # evicted segment — becomes a hole; C2 wall-clock pts handles it
        with src_ctx:
            tb = float(src_ctx.streams.video[0].time_base)
            for fr in src_ctx.decode(video=0):
                t = seg.utc_start + timedelta(seconds=fr.pts * tb)
                if s <= t < e:
                    yield fr, t


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

        Wall-clock-locked pts: every video frame is assigned
        pts = round((t - s).total_seconds() * fps) so coverage holes in the
        input produce held-last-frame gaps in the output timeline rather than
        compressing the wall clock. Audio interleave tracks the video pts so
        the two streams stay aligned across holes.

        Partial-file safety: any exception during encoding unlinks out_path
        before re-raising, so a cached-by-existence lookup (Task 11) never
        serves a broken file.
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
        segments = ring.covering("video", s, e)

        # M3: guard sub-frame spans before any I/O
        if int((e - s).total_seconds() * rate) == 0:
            raise ValueError("span too short to extract")

        # -- Peek output dims from the first in-window frame ------------------
        # Both streams must exist before any packet is muxed (interleaving
        # requirement), and the video stream needs dims. A segment can overlap
        # [s, e) without contributing a frame (covering() is utc-range overlap,
        # frames are discrete), so peek the actual first in-window frame; the
        # partial re-decode of at most a couple of segments is negligible.
        dims: tuple[int, int] | None = None
        for fr, _t in _in_window_frames(segments, s, e):
            # Race: evicted files are skipped inside _in_window_frames
            dims = (fr.width, fr.height)
            break
        if dims is None:
            raise ValueError("no decodable video frames in the span")

        # -- Assemble the PCM buffer (before any encoding) ---------------------
        # Contiguous s16le stereo, aligned to clamped span start `s`.
        # Coverage holes remain silent (zeros already in place).
        total_samples = int((e - s).total_seconds() * rate)
        pcm = np.zeros((total_samples, 2), dtype=np.int16)
        for chunk in ring.covering("audio", s, e):
            try:
                # Race: the live ring may evict chunk.path between covering() and here
                raw = chunk.path.read_bytes()
            except FileNotFoundError:
                continue  # evicted audio chunk — leave corresponding region silent
            data = np.frombuffer(raw, dtype=np.int16).reshape(-1, 2)
            # offset into `data` where the window `s` begins
            src_off = max(0, int((s - chunk.utc_start).total_seconds() * rate))
            # offset into `pcm` where this chunk contributes
            dst_off = max(0, int((chunk.utc_start - s).total_seconds() * rate))
            n = min(len(data) - src_off, total_samples - dst_off)
            if n > 0:
                pcm[dst_off:dst_off + n] = data[src_off:src_off + n]

        # -- Single streaming pass: decode -> encode video + interleave audio --
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out = av.open(str(out_path), "w", options={"movflags": "+faststart"})
        ok = False
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
            audio_pos = 0

            # samples per video frame (interleave granularity)
            smpv = rate // fps  # 1600 at 48 kHz / 30 fps

            def mux_audio(a_end: int) -> None:
                nonlocal audio_pos
                block = pcm[audio_pos:a_end]
                if len(block) < _AAC_FRAME_SIZE:
                    # Per-frame blocks are smpv=1600 >= 1024, so only the tail
                    # block can be short; pad it so the AAC encoder never sees
                    # a sub-1024 non-final frame and no mid-clip silence is added.
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

            for fr, t in _in_window_frames(segments, s, e):
                vf = fr.reformat(width=dims[0], height=dims[1],
                                 format="yuv420p")
                # C1: decoded MPEG-TS frames carry 90 kHz time_base; fix it so
                # the encoder reads pts in output (1/fps) units, not 90 kHz.
                vf.time_base = Fraction(1, fps)
                # C2: wall-clock-locked pts preserves coverage holes in the
                # output timeline — sequential pts would compress holes out.
                vf.pts = round((t - s).total_seconds() * fps)
                for pkt in vstream.encode(vf):
                    out.mux(pkt)
                # Interleave audio up to the end of this video frame's wall-clock
                # slot; keying off video pts keeps A/V aligned across holes.
                a_end = min((vf.pts + 1) * smpv, total_samples)
                if a_end > audio_pos:
                    mux_audio(a_end)

            # Remaining audio (tail samples after the last video frame)
            while audio_pos < total_samples:
                mux_audio(min(audio_pos + _AAC_FRAME_SIZE, total_samples))

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

            ok = True
        finally:
            # I1: partial-file cleanup — unlink runs even when close() itself
            # raises (e.g. disk-full during trailer write), so a
            # cached-by-existence lookup (Task 11) never serves a broken file.
            try:
                out.close()
            finally:
                if not ok:
                    out_path.unlink(missing_ok=True)

        return ClipResult(path=out_path,
                          duration_s=(e - s).total_seconds(),
                          truncated=truncated)
