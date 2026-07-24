"""Cut one scrub-ready MP4 out of the ring (single-mux architecture).

The ring holds combined audio+video MPEG-TS segments — the FfmpegAvSink
encoded ONE continuous A/V stream on a single wall-clock and the segment muxer
sliced it, so audio and video are already locked together inside every
segment. Extraction is therefore a pure CUT, not a re-mux: concatenate the
covering segments, accurate-seek to the span start, re-encode the video for
dense keyframes (0.5 s GOP) + faststart so the browser can scrub, and
stream-copy the already-synced audio. No PCM assembly, no per-frame interleave,
no timestamp reconstruction — those (and the whole two-clock drift class they
caused) are gone with the PCM-sidecar design.

The re-encode exists ONLY to move the keyframes, so it runs at a constant-
quality target (`config.py::video_quality_args`): a saved clip must never look
softer than the ring segments it was cut from.

Why re-encode video but copy audio: `-c copy` can only cut on keyframes (our
2 s segment boundaries), so frame-accurate edges need a video re-encode; audio
copies losslessly and the cut lands on the nearest AAC frame (<~21 ms, a fixed
sub-frame offset, never drift). MPEG-TS is self-framing, so `concat:` across
segment files needs no moov and preserves A/V sync across boundaries.

Coverage holes (idle-discarded footage) are honoured: the extractor uses only
the maximal contiguous run of segments containing the span start and marks the
clip truncated if a hole clips it — concatenating across a hole would silently
collapse wall-clock time and shear the result. A FRAME-SIZE change (the player
resized the emulator window, so the encoder restarted) breaks a run the same
way, for the same reason: ffmpeg would rescale the whole clip to the first
segment's size and squash it if the aspect changed.
"""
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sm64_events.core.paths import bundled_ffmpeg
from sm64_events.replay.config import (CLIP_MAXRATE, ReplayConfig,
                                       video_quality_args)
from sm64_events.replay.ring import SegmentRing

_EDGE_TOLERANCE_S = 0.5   # clamping beyond this marks the clip truncated
_GAP_TOLERANCE_S = 0.25   # segment join wider than this is a coverage hole
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


@dataclass(frozen=True)
class ClipResult:
    path: Path
    duration_s: float
    truncated: bool


def _joinable(prev, seg) -> bool:
    """Can `seg` be concatenated onto `prev`? Two ways it cannot:

    TIME — a join wider than _GAP_TOLERANCE_S is a coverage hole (idle-
    discarded footage); crossing it would collapse wall-clock time.

    SIZE — the player resized the emulator window, so the encoder restarted at
    a new frame size. ffmpeg would happily concatenate those and rescale
    everything to the FIRST segment's size (measured: a clip spanning a
    640x480 -> 1280x960 resize came out entirely 640x480), squashing the
    picture outright if the aspect changed. Unknown dims (audio chunks, the
    in-process fallback writer) never force a break."""
    if (seg.utc_start - prev.utc_end).total_seconds() > _GAP_TOLERANCE_S:
        return False
    return not (prev.dims and seg.dims and prev.dims != seg.dims)


def contiguous_run(segments, s: datetime):
    """The maximal run of JOINABLE segments that contains `s` (see _joinable:
    time-contiguous AND one frame size).

    Segments arrive sorted by utc_start; we keep only the run covering the span
    start. Returns (run, hole_before, hole_after): the segment list plus
    whether a break bounds it on either side (→ the clip is truncated). Pure —
    unit-tested."""
    runs, cur = [], []
    for seg in segments:
        if cur and not _joinable(cur[-1], seg):
            runs.append(cur)
            cur = []
        cur.append(seg)
    if cur:
        runs.append(cur)
    for i, run in enumerate(runs):
        if run[0].utc_start <= s < run[-1].utc_end or (i == 0 and s < run[0].utc_start):
            hole_before = i > 0
            hole_after = i < len(runs) - 1
            return run, hole_before, hole_after
    # s falls in a hole after the last run start — use the last run
    return runs[-1], len(runs) > 1, False


class ClipExtractor:
    def __init__(self, cfg: ReplayConfig, codec: str, ffmpeg: str | None = None):
        self._cfg = cfg
        self._codec = codec
        self._ffmpeg = ffmpeg or bundled_ffmpeg() or shutil.which("ffmpeg")

    def extract(self, ring: SegmentRing, start: datetime, end: datetime,
                out_path: Path) -> ClipResult:
        """Slice [start, end) from the ring into a browser-scrubbable MP4.

        Clamps to available coverage and to the contiguous run containing the
        span start; marks truncated if either edge moved more than
        _EDGE_TOLERANCE_S or a coverage hole clipped the run. Raises ValueError
        when no footage overlaps or the span is sub-frame.

        Partial-file safety: any ffmpeg failure unlinks out_path before
        raising, so a cached-by-existence lookup never serves a broken file.
        """
        if not self._ffmpeg:
            raise RuntimeError("ffmpeg binary not available for extraction")
        cov = ring.coverage("video")
        if cov is None:
            raise ValueError("no footage in the replay buffer")
        s = max(start, cov[0])
        e = min(end, cov[1])
        if e <= s:
            raise ValueError("no footage overlaps the requested span")

        segs = ring.covering("video", s, e)
        if not segs:
            raise ValueError("no footage overlaps the requested span")
        segs = sorted(segs, key=lambda x: x.utc_start)
        run, hole_before, hole_after = contiguous_run(segs, s)
        # clamp the span to the contiguous run (a hole inside the requested
        # window truncates the clip rather than shearing A/V across it)
        rs, re = run[0].utc_start, run[-1].utc_end
        s, e = max(s, rs), min(e, re)
        if e <= s:
            raise ValueError("no footage overlaps the requested span")

        truncated = ((s - start).total_seconds() > _EDGE_TOLERANCE_S
                     or (end - e).total_seconds() > _EDGE_TOLERANCE_S
                     or hole_before or hole_after)

        ss = max(0.0, (s - rs).total_seconds())
        dur = (e - s).total_seconds()
        if dur * self._cfg.fps < 1:
            raise ValueError("span too short to extract")

        concat = "concat:" + "|".join(p.path.as_posix() for p in run)
        fps = self._cfg.fps
        out_path.parent.mkdir(parents=True, exist_ok=True)
        args = [
            self._ffmpeg, "-hide_banner", "-loglevel", "error",
            "-i", concat, "-ss", f"{ss:.6f}", "-t", f"{dur:.6f}",
            "-map", "0:v:0", "-map", "0:a:0",
            "-c:v", self._codec,
            "-g", str(max(1, fps // 2)),
            "-force_key_frames", "expr:gte(t,n_forced*0.5)",
            *self._codec_opts(),
            "-c:a", "copy",
            "-fflags", "+genpts", "-avoid_negative_ts", "make_zero",
            "-movflags", "+faststart", "-y", str(out_path),
        ]
        try:
            subprocess.run(args, check=True, capture_output=True,
                           creationflags=_NO_WINDOW)
        except subprocess.CalledProcessError as exc:
            out_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"ffmpeg extract failed: {exc.stderr.decode('utf-8', 'replace')[-500:]}"
            ) from exc

        return ClipResult(path=out_path, duration_s=dur, truncated=truncated)

    def _codec_opts(self) -> list[str]:
        """Quality settings for the cut, from the ONE registry in config.py.

        These used to be bare presets with no rate control, which handed the
        encoder ffmpeg's ~2 Mbps default and threw away the ring segment's
        detail on the way out (see config.py for the measurement). The cut must
        be transparent w.r.t. its source: the segment holds all the detail a
        clip can ever contain."""
        opts = video_quality_args(self._codec, "offline", CLIP_MAXRATE)
        if self._codec == "h264_nvenc":
            opts += ["-bf", "0"]  # keep the cut's pts contract frame-0 aligned
        return opts
