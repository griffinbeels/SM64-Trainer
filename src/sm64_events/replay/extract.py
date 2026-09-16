"""Explicit fragment export and legacy segment-to-MP4 extraction.

Normal View uses native headers over shared fragments in service.py, without
calling this extractor. Explicit compilation exports stream that same selection
to one standalone file. The remaining implementation supports legacy sources:

the segment ring holds combined audio+video MPEG-TS segments — the FfmpegAvSink
encoded ONE continuous A/V stream on a single wall-clock and the segment muxer
sliced it, so audio and video are already locked together inside every
segment. Verified native H264 is copied into MP4 without another encode. The
preceding keyframe remains as decoder pre-roll; a 90 kHz edit list hides its
leading pictures and trims audio on the same clock. The visible first picture,
VFR holds and source identities remain unchanged. Packet probing ignores only
explicitly discarded negative-time pre-roll on this known output path.

Unknown/reordered sources retain accurate-seek video re-encoding (0.5 s GOP)
and audio copying. Its constant-quality target lives in
`config.py::video_quality_args`. Both paths publish atomically with faststart;
neither reconstructs timestamps or assembles separate audio clocks.

Coverage holes (idle-discarded footage) are honoured: the extractor uses only
the maximal contiguous run of segments containing the span start and marks the
clip truncated if a hole clips it — concatenating across a hole would silently
collapse wall-clock time and shear the result. A FRAME-SIZE change (the player
resized the emulator window, so the encoder restarted) breaks a run the same
way, for the same reason: ffmpeg would rescale the whole clip to the first
segment's size and squash it if the aspect changed.
"""
import os
from contextlib import nullcontext, closing
from sm64_events.core.profiling import measured
import math
import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.core.paths import bundled_ffmpeg
from sm64_events.replay.config import (CLIP_MAXRATE, ReplayConfig,
                                       video_quality_args)
from sm64_events.replay.ring import SegmentRing
from sm64_events.replay.media import MEDIA_HZ, MediaRun, picture_duration_filter

_EDGE_TOLERANCE_S = 0.5   # clamping beyond this marks the clip truncated
_GAP_TOLERANCE_S = 0.25   # segment join wider than this is a coverage hole


@dataclass(frozen=True)
class ClipResult:
    path: Path
    duration_s: float
    truncated: bool
    # The wall time of media time zero, not necessarily its first picture.
    # The requested start moves back to the picture already displayed then,
    # including a long VFR hold. What lets anything cut on the frame
    # counter (the input track) line up with the clip: the attempt's anchor
    # sits at `started_utc - start_utc` seconds into the video.
    start_utc: datetime | None = None
    # The clip's OWN first video timestamp, in its media timeline. An
    # accurate cut leaves the sub-frame remainder on the first picture
    # (5782: frames at k/60 + 0.011003 s), and a seek to (k + 0.5)/60 then
    # lands before frame k begins, so the browser presents k - 1 -- every
    # step one picture early (measured in Chromium, 2026-09-01). Everything
    # that turns a time into a slot counts from this number.
    video_start_s: float = 0.0
    # Every video frame's own timestamp, in the clip's media timeline --
    # present when the ring was fed one frame per picture (the picture
    # feed, config.picture_feed): the clip is then VFR and NOTHING may
    # count slots as k / fps. None for a CFR clip.
    frame_times: list[float] | None = None
    media_run: MediaRun | None = None
    # Source MPEG-TS timestamps, in MEDIA_HZ ticks, for these exact slots.
    # Unknown for older/CFR sources whose media origin was not retained.
    source_pts: list[int] | None = None


@measured("replay.probe_frames")
def frame_times_of(ffmpeg: str | None, clip: Path, *,
                   input_format: str | None = None,
                   native_packets: bool = False,
                   native_preroll: bool = False) -> list[float] | None:
    """Every video frame's PTS; None when it cannot be read.

    Native encoder callers can use guarded packet timestamps. Other media
    retains decoded-frame semantics, including presentation reordering.
    """
    ffprobe = ffprobe_beside(ffmpeg)
    if not ffprobe:
        return None
    if native_packets:
        times = _native_packet_times(ffprobe, clip, input_format,
                                     allow_preroll=native_preroll)
        if times is not None:
            return times
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             *(["-f", input_format] if input_format else []),
             "-show_entries", "frame=pts_time", "-of", "csv=p=0", str(clip)],
            capture_output=True, text=True, timeout=120, check=False,
            **quiet_spawn_kwargs())
        if out.returncode:
            return None
        times = [float(line.split(",")[0]) for line in out.stdout.split()
                 if line.strip()]
        return times or None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _native_packet_times(ffprobe: str, clip: Path,
                         input_format: str | None, *,
                         allow_preroll: bool = False) -> list[float] | None:
    packets = _native_packet_index(ffprobe, clip, input_format,
                                   allow_preroll=allow_preroll)
    if not packets:
        return None
    return [round(p["pts"] / MEDIA_HZ, 6) for p in packets if "D" not in p["flags"]] or None


@measured("replay.probe_native_packets")
def _native_packet_index(ffprobe: str, clip: Path,
                         input_format: str | None, *,
                         allow_preroll: bool = False) -> list[dict] | None:
    """Read timestamps without decoding pixels, only for our native encoder.

    Our H264 mux writes one picture per packet with no B frames. Arbitrary
    downloads do not have that contract and must keep the decoded-frame path.
    Refuse reordered, discarded, corrupt or incomplete packets rather than
    sorting them into an apparently plausible picture map.
    """
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             *(["-f", input_format] if input_format else []),
             "-show_entries",
             "stream=codec_name,has_b_frames,time_base:packet=pts,dts,flags",
             "-of", "json", str(clip)],
            capture_output=True, text=True, timeout=120, check=False,
            **quiet_spawn_kwargs())
        if out.returncode or out.stderr.strip():
            return None
        data = json.loads(out.stdout)
        streams = data.get("streams", [])
        if len(streams) != 1:
            return None
        stream = streams[0]
        if (stream.get("codec_name") != "h264" or stream.get("has_b_frames") != 0
                or stream.get("time_base") != "1/90000"):
            return None
        ticks = []
        for packet in data.get("packets", []):
            pts = packet.get("pts")
            flags = packet.get("flags")
            if (type(pts) is not int or packet.get("dts") != pts
                    or not isinstance(flags, str) or "C" in flags
                    or ("D" in flags and not (allow_preroll and pts < 0))
                    or (allow_preroll and pts < 0 and "D" not in flags)
                    or (ticks and pts <= ticks[-1])):
                return None
            ticks.append(pts)
        return data.get("packets") or None
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, AttributeError):
        return None


def ffprobe_beside(ffmpeg: str | None) -> str | None:
    """The ffprobe that ships beside `ffmpeg` -- by FILE name only. A plain
    replace on the whole path turned D:/ffmpeg/bin/ffmpeg.EXE into
    D:/ffprobe/bin/ffprobe.EXE (the standard install layout), so the first
    version of this read 0.0 on the very clip it was written for and the
    shipped fix would have done nothing; tools/probe_clip_seek.py caught it
    on its first run (2026-09-01). PATH's ffprobe is the fallback."""
    if not ffmpeg:
        return None
    binary = Path(ffmpeg)
    sibling = binary.with_name(binary.name.replace("ffmpeg", "ffprobe"))
    if binary.parent != Path(".") or sibling.exists():
        if sibling.exists():
            return str(sibling)
    return shutil.which("ffprobe")


def video_start_of(ffmpeg: str | None, clip: Path) -> float:
    """The video stream's first pts, in seconds; 0.0 when it cannot be read
    (no ffprobe, an unreadable file) -- the pre-2026-09-01 assumption."""
    ffprobe = ffprobe_beside(ffmpeg)
    if not ffprobe:
        return 0.0
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=start_time", "-of", "csv=p=0", str(clip)],
            capture_output=True, text=True, timeout=30, check=False,
            **quiet_spawn_kwargs())
        if out.returncode:
            return 0.0
        first = out.stdout.strip().splitlines()[0]
        value = float(first.split(",")[0])
        return value if value >= 0 else 0.0
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        # no ffprobe, a timeout, an unreadable file, an empty answer
        return 0.0


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
    if prev.media_run != seg.media_run:
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
    def __init__(self, cfg: ReplayConfig, codec: str, ffmpeg: str | None = None, fragments=None):
        self.fragments = fragments
        self._cfg = cfg
        self._codec = codec
        self._ffmpeg = ffmpeg or bundled_ffmpeg() or shutil.which("ffmpeg")
        self._picture_feed = bool(getattr(cfg, "picture_feed", False))

    @property
    def ffmpeg(self) -> str | None:
        """The binary this extractor cuts with -- the service reads a cached
        clip's first pts with the ffprobe beside it."""
        return self._ffmpeg

    @measured("replay.extract")
    def extract(self, ring: SegmentRing, start: datetime, end: datetime,
                out_path: Path) -> ClipResult:
        """Retain source files for all consumers, including compilations."""
        if self.fragments is not None and self.fragments.enabled:
            return self._export_fragments(start, end, out_path)
        pin = getattr(ring, "pin", None)
        with pin("video", start, end) if pin else nullcontext():
            return self._extract(ring, start, end, out_path)

    def _export_fragments(self, start, end, out_path):
        """Explicit compilation export; View serves these bytes without a file."""
        out_path.parent.mkdir(parents=True, exist_ok=True)
        partial = out_path.with_suffix(".copying")
        try:
            with self.fragments.open(start, end) as (media, result, _):
                with partial.open("wb") as target, closing(media.chunks()) as chunks:
                    for chunk in chunks:
                        target.write(chunk)
                if partial.stat().st_size != media.size:
                    raise OSError("incomplete fragment export")
                os.replace(partial, out_path)
                return ClipResult(path=out_path, **vars(result))
        except LookupError as error:
            raise ValueError(str(error)) from error
        finally:
            partial.unlink(missing_ok=True)

    def _extract(self, ring: SegmentRing, start: datetime, end: datetime,
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

        media_run = run[0].media_run
        ss = (s.timestamp() - media_run.origin_ts if media_run
              else max(0.0, (s - rs).total_seconds()))
        # Select one representable source tick and use it for both ffmpeg's
        # cut and the reverse map. Adding an unrounded float seek to ffprobe's
        # rounded seconds can otherwise recover a neighboring timestamp.
        seek_pts = math.ceil(ss * MEDIA_HZ) if media_run else None
        copy_pts = None
        if seek_pts is not None:
            # An output -ss drops every picture preceding the seek, including
            # the one still displayed during a VFR hold. Start on that actual
            # source picture and report its UTC origin, keeping the source
            # identity and A/V offset unchanged. Never fabricate a new PTS for
            # a duplicate leading picture.
            # A tiny held-picture TS can be misdetected as MPEG program
            # stream with audio only. The ring's format is already known.
            seek_pts, copy_pts = self._source_seek(run[0].path, seek_pts)
            ss = seek_pts / MEDIA_HZ
            s = datetime.fromtimestamp(media_run.origin_ts + ss, timezone.utc)
        dur = (e - s).total_seconds()
        if dur * self._cfg.fps < 1:
            raise ValueError("span too short to extract")

        concat = "concat:" + "|".join(p.path.as_posix() for p in run)
        fps = self._cfg.fps
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Cut BESIDE the target and rename when the file is whole. Writing
        # out_path directly means any second writer -- or any interruption --
        # leaves a half-file that `view()`'s exists() check happily serves
        # forever. His 100-coin clip came back as an unplayable black video
        # whose H.264 stream was full of invalid NAL units, and whose sidecar
        # counted 1921 frames where the file held 1380: two cuts wrote one
        # path (2026-09-02). os.replace is atomic on Windows and POSIX.
        # The suffix stays LAST: ffmpeg picks its muxer from the extension
        # and refuses "clip.mp4.cut123" outright.
        cut_path = out_path.with_name(
            f"{out_path.stem}.cut{os.getpid()}{out_path.suffix}")
        # Native H264 already exists. Keep the prior keyframe as decode-only
        # pre-roll; an exact 90 kHz MP4 edit list hides it. Visible timestamps,
        # the requested first held picture and audio all retain the same origin.
        # Unknown/reordered sources keep the established transcode path.
        preroll = (seek_pts - copy_pts) / MEDIA_HZ if copy_pts is not None else 0.0
        cut_ss, cut_duration = ss - preroll, dur + preroll
        video_args = (["-c:v", "copy"] if copy_pts is not None else [
            "-c:v", self._codec, "-g", str(max(1, fps // 2)),
            "-force_key_frames", "expr:gte(t,n_forced*0.5)", *self._codec_opts()])
        args = [
            self._ffmpeg, "-hide_banner", "-loglevel", "error",
            *(["-copyts"] if media_run else []),
            "-f", "mpegts", "-i", concat, "-ss", f"{cut_ss:.6f}", "-t", f"{cut_duration:.6f}",
            "-map", "0:v:0", "-map", "0:a:0",
            *video_args,
            "-c:a", "copy",
            # The picture feed's ring is VFR -- one frame per picture -- and
            # the cut must keep every frame at its own time: a CFR conform
            # here would put the 60 Hz grid's jitter straight back.
            # ... in the segment's own 90 kHz time base: the encoder would
            # otherwise round every stamp onto 1/r_frame_rate (see the sink).
            *(["-fps_mode", "passthrough", "-enc_time_base", "demux"]
              if self._picture_feed else []),
            *(["-bsf:v", picture_duration_filter(round(cut_duration * MEDIA_HZ))]
              if media_run else []),
            "-fflags", "+genpts", "-avoid_negative_ts",
            "disabled" if media_run else "make_zero",
            # MP4's default 1 kHz edit-list clock discards sub-millisecond
            # origin precision even when the video track remains 90 kHz.
            *(["-movie_timescale", str(MEDIA_HZ)] if media_run else []),
            *(["-output_ts_offset", f"{-preroll:.6f}", "-use_editlist", "1"]
              if copy_pts is not None else []),
            "-movflags", "+faststart", "-y", str(cut_path),
        ]
        try:
            subprocess.run(args, check=True, capture_output=True,
                           **quiet_spawn_kwargs())
        except subprocess.CalledProcessError as exc:
            cut_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"ffmpeg extract failed: {exc.stderr.decode('utf-8', 'replace')[-500:]}"
            ) from exc

        # Probe the file we just wrote, THEN publish it. Probing after the
        # rename let a racing writer change the file between the two, which is
        # how a sidecar came to describe 1921 frames of a 1380-frame clip.
        times = (frame_times_of(self._ffmpeg, cut_path, native_packets=True,
                                native_preroll=copy_pts is not None)
                 if self._picture_feed else None)
        start_s = (times[0] if times
                   else video_start_of(self._ffmpeg, cut_path))
        os.replace(cut_path, out_path)
        return ClipResult(path=out_path, duration_s=dur, truncated=truncated,
                          start_utc=s, video_start_s=start_s,
                          frame_times=times, media_run=media_run,
                          source_pts=([round(t * MEDIA_HZ) + seek_pts for t in times]
                                      if times and media_run else None))

    def _source_seek(self, path: Path, requested_pts: int) -> tuple[int, int | None]:
        """Resolve the held source picture and its decode-only keyframe pre-roll."""
        probe = ffprobe_beside(self._ffmpeg)
        packets = _native_packet_index(probe, path, "mpegts") if probe else None
        source_times = ([round(p["pts"] / MEDIA_HZ, 6) for p in packets] if packets else
                        frame_times_of(self._ffmpeg, path, input_format="mpegts"))
        if not source_times:
            raise ValueError("no readable pictures at the requested start")
        source_ticks = [round(t * MEDIA_HZ) for t in source_times]
        seek_pts = max((t for t in source_ticks if t <= requested_pts),
                       default=source_ticks[0])
        copy_pts = None
        if packets and self._picture_feed:
            copy_pts = max((p["pts"] for p in packets
                            if "K" in p["flags"] and p["pts"] <= seek_pts), default=None)
        return seek_pts, copy_pts

    def _codec_opts(self) -> list[str]:
        """Quality settings for the cut, from the ONE registry in config.py.

        These used to be bare presets with no rate control, which handed the
        encoder ffmpeg's ~2 Mbps default and threw away the ring segment's
        detail on the way out (see config.py for the measurement). The cut must
        be transparent w.r.t. its source: the segment holds all the detail a
        clip can ever contain."""
        opts = video_quality_args(self._codec, "offline", CLIP_MAXRATE)
        opts += ["-bf", "0"]  # preserve PTS with every supported encoder
        return opts
