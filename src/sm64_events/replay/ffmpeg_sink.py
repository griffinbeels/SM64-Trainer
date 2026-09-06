r"""One encoder process owns the replay's video, audio and media clock.

The picture feed muxes rawvideo and PCM into one NUT input. A MediaRun retains
its first-picture UTC origin and unique encoder identity; video is assigned
monotonic 90 kHz PTS before encoding, audio retains its capture timing on a
microsecond clock. Audio samples before the run origin are trimmed so NUT
cannot shift both streams. FFmpeg preserves those timestamps through the TS
segment muxer (copyts, mpegts_copyts, disabled negative-timestamp adjustment).
Each segment carries its own run, including a late final segment after restart.

Every accepted video write records the actual assigned source PTS and captured
row in the feed log. The extractor cuts on that same clock and the frame map
looks up (run, PTS), without fitting an offset. Extremely close or delayed
pictures get successive transport ticks when their composition timestamps
cannot remain monotonic; their picture identity is retained explicitly. A
heartbeat repeats the last picture, with the same captured identity.

The legacy CFR mode sends rawvideo and a Windows named audio pipe to one
wall-clock-stamping ffmpeg. Async audio resampling prevents independent-clock
drift. It has no retained source-PTS contract and cannot claim an exact map.

Children use quiet spawn flags and a kill-on-close Windows job. See the input
frame chain rule for measured failures and tests/test_replay_picture_identity.py
for independent pixel identities through the actual sink, segments and cut.
"""
import ctypes
import ctypes.wintypes as wt
import logging
import os
import queue
from collections import deque
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path

import numpy as np

from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.core.timefmt import GAME_FPS
from sm64_events.replay.config import RING_MAXRATE, video_quality_args
from sm64_events.replay.ring import SegmentInfo
from sm64_events.replay.media import MEDIA_HZ, MEDIA_TIME_BASE, MediaRun, picture_duration_filter

log = logging.getLogger("sm64.replay")

# THE PICTURE FEED (config.picture_feed, item 38): one video frame per
# DISTINCT captured picture, carrying its assigned source timestamp through
# ffmpeg unchanged (`-fps_mode passthrough`, VFR). A
# picture that stays on screen feeds nothing, so after this much silence
# the last picture is written again -- untagged, a repeat -- to keep the
# segment muxer and the ring's coverage rolling (a segment closes on the
# first keyframe past segment_s, and keyframes are forced by TIME here).
PICTURE_HEARTBEAT_S = 1.0
# Pictures waiting for the feeder, bounded by BYTES rather than count: one
# grab of his window is 1600x1224 BGRA = 7.8 MB, so a count-based bound is a
# memory bound in disguise. 512 MB is ~65 of his frames, over two seconds of
# slack at the game's ~30 pictures/s.
#
# NOTHING IN HERE DROPS A QUEUED PICTURE. The old queue was 16 deep and shed
# its OLDEST entry on overflow -- a picture the ledger had already recorded,
# so "captured" and "encoded" silently diverged and the clip's map described
# frames the video did not contain. His rule, 2026-09-02: "We should always
# be encoding frames we captured... If I see a frame in my replay, as a user,
# I would expect to see the input capture for that frame as well." The
# recorder now asks `has_room()` BEFORE it records a picture at all, so a
# loaded machine captures fewer pictures and encodes every one of them.
PICTURE_QUEUE_BYTES = 512 * 1024 * 1024
# The picture feed's video reaches ffmpeg as a NUT stream, muxed in this
# process with EVERY FRAME CARRYING ITS OWN TIMESTAMP (90 kHz video ticks;
# microseconds for audio). `-use_wallclock_as_timestamps` stamps a frame when ffmpeg's
# demuxer happens to read it, and ffmpeg's scheduler holds a demuxer back
# whenever the OTHER input is behind: measured 2026-09-02, a 30 pictures/s
# feed came out with 108 frames in one segment mostly ONE 90 kHz TICK
# apart (read in bursts, stamped together, nudged apart by the muxer),
# while the same pictures through NUT reproduced their write times to
# 6 us and kept a deliberate 200 ms stall as 201 ms (scratch nut_probe).
# AUDIO RIDES THE SAME STREAM, stamped by us on the same clock: with the
# audio on its own wall-clock-stamped pipe, ffmpeg's scheduler held the
# video demuxer to 26 pictures/s of a 30/s feed whatever offset the two
# carried (measured 2026-09-02, scratch hang_probe: lag +-2 s, no filter,
# null output -- all 26/s; no audio -- 30/s), and one NUT input with
# both streams ran 30.0/s with 0.17 ms writes (scratch nut_av_probe).
PICTURE_TIME_BASE = Fraction(1, 1_000_000)

_JOB_KILL_ON_CLOSE = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
_JOB_EXTENDED_LIMIT_INFO_CLASS = 9   # JobObjectExtendedLimitInformation

# -- Windows named pipe (audio transport) ------------------------------------
_PIPE_ACCESS_OUTBOUND = 0x00000002
_PIPE_TYPE_BYTE = 0x0
_PIPE_WAIT = 0x0
_INVALID_HANDLE = wt.HANDLE(-1).value
_pipe_seq = 0  # process-unique pipe names

if hasattr(ctypes, "windll"):
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateNamedPipeW.restype = wt.HANDLE
    _k32.CreateNamedPipeW.argtypes = [
        wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.DWORD,
        wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_void_p]
    _k32.ConnectNamedPipe.argtypes = [wt.HANDLE, ctypes.c_void_p]
    _k32.WriteFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD,
                               ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
    _k32.FlushFileBuffers.argtypes = [wt.HANDLE]
    _k32.DisconnectNamedPipe.argtypes = [wt.HANDLE]
    _k32.CloseHandle.argtypes = [wt.HANDLE]


def _assign_kill_on_close(proc) -> int | None:
    """Assign `proc` to a Windows Job Object whose last-handle-close kills its
    members. We never close the returned handle: it dies WITH this process and
    the OS then terminates ffmpeg. Returns the job handle to keep alive, or
    None (logged) if assignment failed."""
    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _BASIC_LIMITS(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", wt.LARGE_INTEGER),
                    ("PerJobUserTimeLimit", wt.LARGE_INTEGER),
                    ("LimitFlags", wt.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wt.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wt.DWORD),
                    ("SchedulingClass", wt.DWORD)]

    class _EXTENDED_LIMITS(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BASIC_LIMITS),
                    ("IoInfo", _IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    try:
        k32 = ctypes.windll.kernel32
        job = k32.CreateJobObjectW(None, None)
        if not job:
            log.warning("CreateJobObject failed (%d) - no ffmpeg backstop",
                        ctypes.get_last_error())
            return None
        info = _EXTENDED_LIMITS()
        info.BasicLimitInformation.LimitFlags = _JOB_KILL_ON_CLOSE
        ok = k32.SetInformationJobObject(
            job, _JOB_EXTENDED_LIMIT_INFO_CLASS,
            ctypes.byref(info), ctypes.sizeof(info))
        if ok and k32.AssignProcessToJobObject(job, int(proc._handle)):
            return job
        log.warning("job-object assignment failed - no ffmpeg backstop")
        k32.CloseHandle(job)
        return None
    except Exception:
        log.exception("job-object setup failed - no ffmpeg backstop")
        return None


class AudioPacer:
    """Keep ffmpeg's audio pipe fed CONTINUOUSLY AT REALTIME by draining real
    PCM and padding silence up to the wall-clock-expected sample count.

    Both ffmpeg inputs are wall-clock-stamped, so the input scheduler reads
    whichever stream is behind in wall time and BLOCKS on it. If the audio pipe
    falls behind — which it does whenever the game is quiet (WASAPI loopback
    delivers no packets) — ffmpeg waits for audio and stops draining the VIDEO
    stdin, collapsing the captured frame rate (live: 16.9 fed/s, ffmpeg
    duplicating >10000 frames → choppy ~17 fps). Holding audio at realtime
    keeps the scheduler from ever waiting on it; padded silence is stamped at
    its write wall-clock and aresample reconciles it.

    Pure logic — clock and writer are injected so the no-starve invariant is
    unit-testable without ffmpeg. `feed` writes real PCM; `tick` pads silence
    to realtime. Returns samples written so callers/tests can observe."""

    def __init__(self, rate: int, now, write, write_at=None):
        self._rate = rate
        self._now = now
        self._write = write
        # Optional: (real_pcm, ends_at) for a writer that stamps chunks
        # itself (the picture feed's NUT stream); padding still goes
        # through `write`, which stamps it as ending now.
        self._write_at = write_at
        self._t0 = None
        self._delivered = 0

    def feed(self, real_pcm: bytes, ends_at: float | None = None) -> None:
        if self._t0 is None:
            self._t0 = self._now()
        if ends_at is not None and self._write_at is not None:
            self._write_at(real_pcm, ends_at)
        else:
            self._write(real_pcm)
        self._delivered += len(real_pcm) // 4  # 2ch * s16

    def tick(self) -> int:
        if self._t0 is None:
            self._t0 = self._now()
        expected = int((self._now() - self._t0) * self._rate)
        pad = expected - self._delivered
        if pad > 0:
            self._write(b"\x00" * (pad * 4))
            self._delivered += pad
            return pad
        return 0

    @property
    def delivered(self) -> int:
        return self._delivered


def parse_segment_csv(line: str, anchor_utc: datetime, origin_s: float,
                      scratch: Path,
                      dims: tuple[int, int] | None = None,
                      media_run: MediaRun | None = None) -> SegmentInfo | None:
    """One line of ffmpeg's -segment_list_type csv: 'file,start,end' (seconds).
    UTC is anchored once (anchor_utc = wall time of the first fed frame) and the
    segment offset is RELATIVE to the first segment's start (origin_s) — correct
    whether ffmpeg's pts are zero-based or wall-clock-epoch-based. Pure."""
    parts = line.strip().rsplit(",", 2)
    if len(parts) != 3:
        return None
    name, start_s, end_s = parts
    try:
        start, end = float(start_s), float(end_s)
    except ValueError:
        return None
    path = scratch / name
    try:
        size = path.stat().st_size
    except OSError:
        return None
    return SegmentInfo(
        path=path, kind="video",
        utc_start=anchor_utc + timedelta(seconds=start - origin_s),
        utc_end=anchor_utc + timedelta(seconds=end - origin_s),
        size_bytes=size, dims=dims, media_run=media_run)


def fill_plane(plane, frame: np.ndarray) -> None:
    """Copy a (H, W, 4) BGRA picture into an AVFrame plane. FFmpeg pads each
    row of a plane to a 32-byte line size, so a width that is not a
    multiple of 8 has a plane larger than the picture (1190 px: 4760 bytes
    of pixels, a 4768-byte line) and `update` refuses the raw bytes -- which
    made the sink respawn ffmpeg on EVERY picture while Project64's window
    was 1190 wide during its start-up (2026-09-05: "got 2360960 bytes; need
    2364928 bytes"), i.e. record nothing at that size. Such a picture is
    laid out row by row into a buffer of the plane's own shape first."""
    height, width = frame.shape[:2]
    row_bytes = width * 4
    line = plane.line_size
    if line == row_bytes:
        plane.update(frame if frame.flags["C_CONTIGUOUS"] else np.ascontiguousarray(frame))
        return
    rows = np.zeros((height, line), dtype=np.uint8)
    rows[:, :row_bytes] = frame.reshape(height, row_bytes)
    plane.update(rows)


class _WriteAll:
    """The child's stdin for the NUT muxer: a raw pipe's write() may take
    fewer bytes than offered under backpressure, and libavformat's custom
    IO does not retry -- a 307,200-byte picture reached ffmpeg as 131,877
    bytes once (2026-09-02), the decoder refused it, the child stopped
    reading and the feeder hung in its next write. Every write here loops
    until the whole buffer is in the pipe."""

    def __init__(self, raw):
        self._raw = raw

    def write(self, data) -> int:
        view = memoryview(data)
        total = len(view)
        while view:
            written = self._raw.write(view)
            if written is None:
                raise OSError("pipe write returned None")
            view = view[written:]
        return total

    def flush(self) -> None:
        self._raw.flush()


class FfmpegAvSink:
    """Combined-A/V video+audio sink. submit() frames and submit_audio() PCM
    from any thread; segments arrive at on_segment with wall-true UTC spans and
    audio muxed in, synced on a single clock."""

    # A child alive at least this long was genuinely encoding; one that died
    # younger is dying on arrival (encoder init failure, full disk) and gets
    # backed off instead of respawned per write attempt.
    _HEALTHY_CHILD_S = 5.0

    def __init__(self, cfg, on_segment, ffmpeg: str = "ffmpeg",
                 codec: str = "h264_nvenc", on_fed=None):
        self._cfg = cfg
        # The picture feed (item 38): submit() queues each new picture and
        # the feeder writes it once; otherwise the latest grab is re-sent
        # at fps onto the CFR grid (the pre-2026-09-02 shape).
        self._picture = bool(getattr(cfg, "picture_feed", False))
        self._queue: deque = deque()
        self._queued_bytes = 0
        self._mux = None            # the NUT container over stdin (picture feed)
        self._mux_stream = None
        self._mux_audio = None
        self._mux_frame = None      # one reusable AVFrame; planes updated in place
        self._mux_lock = threading.Lock()   # the feeder and the audio thread share it
        # The picture feed's stamps are seconds since THIS run's epoch (the
        # first picture): small numbers that never wrap MPEG-TS's 33-bit clock, on
        # one timeline for every segment of the run (-reset_timestamps 0),
        # so a cut across segments carries no per-segment rounding. The
        # anchor IS the epoch, and the segment list's times add to it.
        self._run_epoch: float | None = None
        self._media_run: MediaRun | None = None
        self._last_video_pts: int | None = None
        self._arrived = threading.Event()
        self._picture_drops = 0
        # Called after every write that reached ffmpeg: (tag, wall time
        # the write completed). The recorder points it at the picture
        # ledger's feed log, which is how a clip's frame k names its row.
        self.on_fed = on_fed
        self._on_segment = on_segment
        self._ffmpeg = ffmpeg
        # The codec is pick_video_codec()'s answer, threaded through the
        # recorder — NEVER probed or hardcoded here. Hardcoding h264_nvenc
        # here is the bug that flashed a non-NVIDIA user's mouse forever
        # (2026-08-07): the child died at birth and the respawn loop showed
        # Windows' busy cursor ~2 s per spawn.
        self._codec = codec
        self._latest: np.ndarray | None = None
        self._proc: subprocess.Popen | None = None
        self._feeder: threading.Thread | None = None
        self._readers: list[threading.Thread] = []
        self._stop = threading.Event()
        self._dims: tuple[int, int] | None = None
        self._anchor_utc: datetime | None = None
        self._fed = 0
        self._seg_n_base = 0
        self._restarts = 0
        self._fail_streak = 0
        self._spawned_at_mono = 0.0
        self._jobs: list[int] = []
        # audio named-pipe transport
        self._audio_q: queue.Queue = queue.Queue(maxsize=256)
        self._audio_dropped = 0
        self._pipe_name: str | None = None
        self._pipe_handle = None
        self._audio_thread: threading.Thread | None = None

    # -- capture-thread surface (lock-free) -----------------------------------
    def submit(self, bgra: np.ndarray,
               tag: tuple[int | None, float | None] | None = None) -> None:
        """`tag` is the RAM game frame current when this picture was
        CAPTURED (recorder._on_frame reads it off the frame clock). It rides
        the reference swap so the feeder can record, per fed frame, which
        game frame's picture went to the encoder -- the clip's frame map."""
        h, w = bgra.shape[:2]
        if (h & 1) or (w & 1):
            bgra = bgra[:h & ~1, :w & ~1]
        array = bgra if bgra.flags["C_CONTIGUOUS"] \
            else np.ascontiguousarray(bgra)
        if self._picture:
            # A caller that honoured has_room() always fits; one that did not
            # is still never dropped, because a dropped picture is a lie in
            # the ledger. The counter says the budget was exceeded.
            if self._queued_bytes >= PICTURE_QUEUE_BYTES:
                self._picture_drops += 1
            self._queue.append((array, tag))
            self._queued_bytes += array.nbytes
            self._arrived.set()
            return
        self._latest = (array, tag)

    def has_room(self) -> bool:
        """Is there budget to encode one more picture? The recorder asks this
        BEFORE recording a grab, so a picture it cannot encode is never
        recorded as captured -- captured and encoded stay in lockstep."""
        return self._queued_bytes < PICTURE_QUEUE_BYTES

    def queue_depth(self) -> tuple[int, int]:
        """(pictures waiting, bytes waiting) -- the recorder's status reads it
        so a degraded capture is a number rather than a feeling."""
        return len(self._queue), self._queued_bytes

    def submit_audio(self, pcm_bytes: bytes) -> None:
        """Enqueue interleaved s16le stereo PCM for the audio pipe. Non-blocking
        and drop-on-overflow: the writer thread absorbs pipe backpressure, but a
        wedged ffmpeg must never stall the audio producer."""
        try:
            # The chunk's arrival time rides with it: the picture feed
            # stamps real audio by when it was captured, not by when the
            # audio thread got round to it (a burst after a spawn would
            # otherwise all stamp "now" and land out of order).
            self._audio_q.put_nowait((pcm_bytes, time.time()))
        except queue.Full:
            self._audio_dropped += 1

    # -- lifecycle -------------------------------------------------------------
    def start(self) -> None:
        self._stop.clear()
        self._feeder = threading.Thread(
            target=self._picture_feed_loop if self._picture else self._feed_loop,
            name="ffmpeg-feeder", daemon=True)
        self._feeder.start()

    def stop(self) -> None:
        # feeder first (stops stdin writes), then close stdin so ffmpeg flushes
        # its final segment and exits, THEN tear down audio pipe + readers.
        self._stop.set()
        if self._feeder is not None:
            self._feeder.join(timeout=10)
            self._feeder = None
        self._teardown_audio_pipe()
        self._stop_proc()
        for t in self._readers:
            t.join(timeout=10)
        self._readers.clear()

    # -- process management ----------------------------------------------------
    def _spawn(self, w: int, h: int, first_stamp: float | None = None) -> None:
        global _pipe_seq
        self._readers = [t for t in self._readers if t.is_alive()]
        fps = self._cfg.fps
        seg_s = self._cfg.segment_s
        rate = self._cfg.audio_rate
        _pipe_seq += 1
        self._pipe_name = rf"\\.\pipe\sm64av_{os.getpid()}_{_pipe_seq}"
        if not self._picture:
            self._open_audio_pipe()
        pattern = str(self._cfg.scratch_dir / f"av_{self._seg_n_base:02d}_%06d.ts")
        args = [
            self._ffmpeg, "-hide_banner", "-loglevel", "warning",
            *(["-copyts"] if self._picture else []),
            # nobuffer DISCARDS the packets stream analysis reads: 23 of the
            # first 83 pictures of a picture-feed run never reached the
            # encoder with it (measured 2026-09-02, scratch loss_probe). The
            # CFR feed keeps it -- its re-sent frames hid the loss.
            *([] if self._picture else ["-fflags", "+nobuffer"]),
            # video input: stdin, wall-clock stamped
            # The picture feed: a NUT stream whose frames carry their own
            # wall-clock stamps (PICTURE_TIME_BASE); the CFR feed: raw
            # frames stamped by ffmpeg at its read.
            *(["-thread_queue_size", "1024", "-f", "nut", "-i", "pipe:0"]
              if self._picture else
              ["-use_wallclock_as_timestamps", "1", "-thread_queue_size", "1024",
               "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{w}x{h}",
               "-i", "pipe:0"]),
            # audio input: in the NUT stream (picture feed) or a named
            # pipe, wall-clock stamped (CFR)
            *(["-map", "0:v:0", "-map", "0:a:0"] if self._picture else
              ["-use_wallclock_as_timestamps", "1", "-thread_queue_size", "1024",
               "-f", "s16le", "-ar", str(rate), "-ac", "2", "-i", self._pipe_name,
               "-map", "0:v:0", "-map", "1:a:0"]),
            # video: the machine's picked codec, CFR locked to the wall clock.
            # QUALITY comes from the one registry in config.py — the ring is
            # the ceiling on every clip ever cut from it, so it targets a
            # picture quality (cq/crf), not a bitrate that over/undershoots
            # with scene difficulty.
            "-c:v", self._codec,
            *video_quality_args(self._codec, "realtime", RING_MAXRATE),
            # bf=0: B-frames shift a segment's start_time off frame 0, breaking
            # the pts contract the extractor cuts against.
            "-bf", "0",
            # GOP: in frames. The picture feed runs at the GAME's ~30
            # pictures/s, and a paused picture feeds none, so its keyframes
            # are forced by TIME as well -- the segment muxer cuts on them.
            "-g", str(int((GAME_FPS if self._picture else fps) * seg_s)),
            *(["-force_key_frames", f"expr:gte(t,n_forced*{seg_s})"]
              if self._picture else []),
            # forced-idr is an NVENC knob; x264 already emits IDR at every
            # -g boundary (closed GOP is its default), which is all the
            # segment muxer needs to cut on.
            *(["-forced-idr", "1"] if self._codec == "h264_nvenc" else []),
            # CFR locks every frame to the wall-clock grid; the picture
            # feed keeps each frame at the time it was written instead.
            # ... and keeps the INPUT's microsecond time base through the
            # encoder: left to ffmpeg, the encoder's time base becomes
            # 1/r_frame_rate (its own guess of the input rate) and every
            # stamp is rounded onto that grid -- 33.7 ms buckets, two
            # pictures a bucket, the muxer nudging duplicates by one tick
            # (measured 2026-09-02, scratch tb_probe; `demux` keeps them at
            # the writes, `-1` is its deprecated spelling).
            *(["-fps_mode", "passthrough", "-enc_time_base", "demux"] if self._picture
              else ["-fps_mode", "cfr", "-r", str(fps)]),
            *(["-bsf:v", picture_duration_filter()] if self._picture else []),
            # audio: AAC, async-resampled to LOCK to the master (kills drift)
            "-c:a", "aac", "-b:a", "160k", "-ar", str(rate),
            "-af", "aresample=async=1:first_pts=0:min_hard_comp=0.1",
            # combined A+V MPEG-TS segments
            "-f", "segment", "-segment_time", str(seg_s),
            "-segment_format", "mpegts",
            # Both the outer muxer and the TS child must preserve PTS. TS's
            # default transport offset and AAC priming otherwise move video
            # away from the composition clock while retaining its cadence.
            *(["-avoid_negative_ts", "disabled", "-segment_format_options",
               "mpegts_copyts=1:avoid_negative_ts=disabled"]
              if self._picture else []),
            # One timeline across the run's segments for the picture feed
            # (its stamps are small already); a per-segment reset for CFR.
            "-reset_timestamps", "0" if self._picture else "1",
            "-segment_list", "pipe:1", "-segment_list_type", "csv",
            "-segment_list_flags", "+live",
            pattern,
        ]
        self._proc = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, bufsize=0, **quiet_spawn_kwargs())
        self._spawned_at_mono = time.monotonic()
        if self._picture:
            self._media_run = MediaRun.starting_at(
                time.time() if first_stamp is None else first_stamp)
            self._run_epoch = self._media_run.origin_ts
            self._last_video_pts = None
            self._anchor_utc = datetime.fromtimestamp(self._run_epoch, timezone.utc)
            self._open_mux(w, h)
        job = _assign_kill_on_close(self._proc)
        if job is not None:
            self._jobs.append(job)
        self._dims = (w, h)
        self._seg_n_base += 1
        # audio thread: into the NUT stream (picture feed), or connect the
        # named pipe (ffmpeg is the client) and drain into it (CFR)
        self._audio_thread = threading.Thread(
            target=(self._audio_mux_loop if self._picture
                    else self._audio_writer_loop),
            name="ffmpeg-audio", daemon=True)
        self._audio_thread.start()
        # Each reader belongs to ONE child, so it stamps THAT child's frame
        # size onto its segments — a later resize respawns ffmpeg and its
        # reader with the new size, and the extractor can tell the two apart.
        for target, name, extra in (
                (self._segment_list_loop, "ffmpeg-segments",
                 ((w, h), self._media_run)),
                (self._stderr_loop, "ffmpeg-stderr", ())):
            t = threading.Thread(target=target, args=(self._proc, *extra),
                                 name=name, daemon=True)
            t.start()
            self._readers.append(t)
        log.info("ffmpeg AV sink: spawned %dx%d@%d %s + audio %s (run %d)",
                 w, h, fps, self._codec,
                 "in the picture feed" if self._picture else "pipe",
                 self._seg_n_base)

    def _respawn_delay(self) -> float:
        """How long to wait before replacing a dead child. A child that died
        young is dying on arrival — respawning per write attempt produced 331
        restarts in one sitting (disk-full, 2026-06-21) and a permanently
        flashing busy cursor on a machine whose encoder cannot start
        (2026-08-07). A healthy run resets the streak so a one-off death
        still recovers instantly."""
        alive_s = time.monotonic() - self._spawned_at_mono
        if alive_s >= self._HEALTHY_CHILD_S:
            self._fail_streak = 0
            return 0.0
        self._fail_streak = min(self._fail_streak + 1, 5)
        return min(2.0 ** self._fail_streak, 30.0)

    def _stop_proc(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        self._close_mux()
        try:
            if proc.stdin:
                proc.stdin.close()  # EOF -> ffmpeg flushes final segment
            proc.wait(timeout=10)
        except Exception:
            log.exception("ffmpeg shutdown failed - killing")
            proc.kill()

    # -- audio named pipe ------------------------------------------------------
    def _open_audio_pipe(self) -> None:
        h = _k32.CreateNamedPipeW(
            self._pipe_name, _PIPE_ACCESS_OUTBOUND,
            _PIPE_TYPE_BYTE | _PIPE_WAIT, 1,
            8 * 1024 * 1024, 8 * 1024 * 1024, 0, None)
        if h == _INVALID_HANDLE:
            raise OSError(f"CreateNamedPipe failed: {ctypes.get_last_error()}")
        self._pipe_handle = h

    def _audio_writer_loop(self) -> None:
        """Block until ffmpeg connects to the named pipe, then keep it fed
        CONTINUOUSLY AT REALTIME: drain whatever real PCM has arrived and pad
        silence to the wall-clock-expected sample count.

        WHY pad (the choppy-video fix, 2026-06-18): both pipes are
        wall-clock-stamped, so ffmpeg's input scheduler reads whichever stream
        is behind in wall time and BLOCKS on it. If the audio pipe starves —
        which it does whenever the game is quiet (WASAPI loopback delivers no
        packets) — ffmpeg waits for audio and stops draining the VIDEO stdin,
        so the feeder's writes block and the captured frame rate collapses to a
        fraction of fps (live: 16.9 fed/s, ffmpeg duplicating >10000 frames →
        ~17 fps of unique content). Holding the audio input at realtime keeps
        the scheduler from ever waiting on it. The padded silence is stamped at
        its write wall-clock and aresample reconciles it; bisect-verified that
        continuous realtime audio sustains full fps while gappy audio does not.

        WriteFile may block on pipe backpressure — harmless (off the RT path);
        on ffmpeg exit the read end closes and WriteFile errors, ending loop."""
        import time as _time
        h = self._pipe_handle
        if h is None:
            return
        _k32.ConnectNamedPipe(h, None)  # returns when ffmpeg opens the pipe
        written = wt.DWORD(0)
        broken = [False]

        def _put(buf: bytes) -> None:
            if not _k32.WriteFile(h, buf, len(buf), ctypes.byref(written), None):
                if not broken[0]:
                    log.warning("audio pipe write failed (error %d): ffmpeg closed "
                                "its read end -- the audio feed stops here",
                                ctypes.get_last_error())
                broken[0] = True  # read end closed (ffmpeg gone)

        pacer = AudioPacer(self._cfg.audio_rate, _time.perf_counter, _put)
        while not self._stop.is_set():
            drained = False
            while True:
                try:
                    buf = self._audio_q.get_nowait()
                except queue.Empty:
                    break
                if buf is None:
                    return
                pacer.feed(buf[0])
                if broken[0]:
                    return
                drained = True
            pacer.tick()  # pad silence to realtime so audio never starves video
            if broken[0]:
                return
            if not drained:
                _time.sleep(0.005)  # fine tick: smooth, low-latency pacing

    def _teardown_audio_pipe(self) -> None:
        # Wake the writer WITHOUT blocking: when the ffmpeg child died young
        # the writer is already gone and the queue may be full, and a
        # blocking put here parked the recorder's attach thread forever --
        # no window re-found, no recording, for the rest of the session
        # (2026-09-05, the capture layer's first install). Make room, then
        # wake whoever is left.
        try:
            self._audio_q.put_nowait(None)
        except queue.Full:
            try:
                self._audio_q.get_nowait()
            except queue.Empty:
                pass
            try:
                self._audio_q.put_nowait(None)
            except queue.Full:
                pass
        if self._audio_thread is not None:
            self._audio_thread.join(timeout=5)
            self._audio_thread = None
        h, self._pipe_handle = self._pipe_handle, None
        if h is not None:
            try:
                _k32.FlushFileBuffers(h)
                _k32.DisconnectNamedPipe(h)
            except Exception:
                pass
            _k32.CloseHandle(h)
        # drain any residual queued PCM so a restart starts clean
        try:
            while True:
                self._audio_q.get_nowait()
        except queue.Empty:
            pass

    # -- threads ---------------------------------------------------------------
    def _feed_loop(self) -> None:
        import time as _time

        kernel32 = ctypes.windll.kernel32
        htimer = kernel32.CreateWaitableTimerExW(None, None, 0x2, 0x1F0003)
        period = 1.0 / self._cfg.fps
        next_t = _time.perf_counter()
        fed_window = 0
        stall_max = 0.0
        last_report = _time.monotonic()
        try:
            while not self._stop.is_set():
                latest = self._latest
                if latest is None:
                    _time.sleep(0.05)
                    next_t = _time.perf_counter()
                    continue
                frame, tag = latest
                wms = self._write_frame(frame, tag)
                if wms is None:
                    next_t = _time.perf_counter()
                    continue
                stall_max = max(stall_max, wms)
                fed_window += 1
                now = _time.monotonic()
                if now - last_report > 30 and fed_window:
                    log.info("ffmpeg AV sink: %.1f fed/s, max write %.0f ms, "
                             "%d restarts, %d audio drops",
                             fed_window / (now - last_report), stall_max,
                             self._restarts, self._audio_dropped)
                    fed_window = 0
                    stall_max = 0.0
                    self._audio_dropped = 0
                    last_report = now
                next_t += period
                delay = next_t - _time.perf_counter()
                if delay > 0.001 and htimer:
                    due = ctypes.c_longlong(-int((delay - 0.0005) * 1e7))
                    if kernel32.SetWaitableTimer(htimer, ctypes.byref(due),
                                                 0, None, None, False):
                        kernel32.WaitForSingleObject(htimer, 0xFFFFFFFF)
                while _time.perf_counter() < next_t:
                    pass
                if next_t < _time.perf_counter() - period:
                    next_t = _time.perf_counter()
        except Exception:
            log.exception("ffmpeg feeder died")
        finally:
            if htimer:
                kernel32.CloseHandle(htimer)

    def _write_frame(self, frame, tag) -> float | None:
        """One frame to ffmpeg's stdin, spawning or re-spawning the child
        as its size demands. Returns the write's duration in ms, or None
        when the write failed (the child is torn down and the respawn
        backoff has been waited). Bookkeeping that must follow a write
        that REACHED ffmpeg lives here, once, for both feeders."""
        import time as _time

        stamped = (tag[1] if tag is not None and len(tag) > 1
                   and tag[1] is not None else None)
        wrote_at = stamped if stamped is not None else _time.time()
        h, w = frame.shape[:2]
        pts = None
        t0 = _time.perf_counter()
        # The picture feed stamps a picture with its COMPOSITION time (the
        # tag's second field, WGC's own clock through the run's capture
        # clock) -- what the feed log files it under too, so a clip's frame
        # and its row carry one number. A heartbeat repeat, untagged, is
        # stamped now. Pictures queued through a spawn keep their true
        # times this way instead of the burst's.
        try:
            if self._proc is None or (w, h) != self._dims:
                if self._proc is not None:
                    log.info("ffmpeg AV sink: dims %s -> %s, restarting",
                             self._dims, (w, h))
                    self._restarts += 1
                    self._teardown_audio_pipe()
                    self._stop_proc()
                self._anchor_utc = None
                self._spawn(w, h, first_stamp=wrote_at)
            if self._picture:
                pts = self._mux_picture(frame, wrote_at)
                wrote_at = self._media_run.origin_ts + pts / MEDIA_HZ
            else:
                self._proc.stdin.write(frame)  # raw pipe: GIL released
        except Exception:
            log.exception("ffmpeg stdin write failed - restarting")
            self._restarts += 1
            self._teardown_audio_pipe()
            self._stop_proc()
            delay = self._respawn_delay()
            if delay:
                log.warning("ffmpeg child died young - waiting %.0f s "
                            "before respawn (streak %d)",
                            delay, self._fail_streak)
                self._stop.wait(delay)
            return None
        if not self._picture:
            # ffmpeg stamps a raw frame at the read this write satisfied:
            # the completion time, not the start (the first write blocks
            # through the child's own start-up).
            wrote_at = _time.time()
        if self._anchor_utc is None:
            # This run's frame 0: the stamp its first frame carries (the
            # picture feed) or the moment its first write completed (CFR).
            # An anchor taken BEFORE the first write sat a child start-up
            # behind every segment time (measured 2026-09-02).
            self._anchor_utc = datetime.fromtimestamp(wrote_at, timezone.utc)
        wms = (_time.perf_counter() - t0) * 1000
        if self.on_fed is not None:
            try:
                self.on_fed(tag, wrote_at, media_run=self._media_run, pts=pts)
            except Exception:
                log.exception("on_fed failed; the feed log misses a frame")
        self._fed += 1
        return wms

    def _open_mux(self, w: int, h: int) -> None:
        """A NUT container over the child's stdin carrying one rawvideo
        stream whose frames carry the assigned 90 kHz transport timestamps."""
        import av

        self._mux = av.open(_WriteAll(self._proc.stdin), mode="w", format="nut")
        stream = self._mux.add_stream("rawvideo", rate=1000)
        stream.width, stream.height = w, h
        stream.pix_fmt = "bgra"
        stream.time_base = MEDIA_TIME_BASE
        stream.codec_context.time_base = MEDIA_TIME_BASE
        self._mux_stream = stream
        audio = self._mux.add_stream("pcm_s16le", rate=self._cfg.audio_rate)
        audio.layout = "stereo"
        audio.time_base = PICTURE_TIME_BASE
        audio.codec_context.time_base = PICTURE_TIME_BASE
        self._mux_audio = audio
        self._mux_frame = av.VideoFrame(w, h, "bgra")
        self._mux_frame.time_base = MEDIA_TIME_BASE

    def _close_mux(self) -> None:
        with self._mux_lock:
            mux, self._mux = self._mux, None
            self._mux_stream = self._mux_audio = self._mux_frame = None
            if mux is None:
                return
            try:
                mux.close()
            except Exception:
                log.debug("NUT mux close failed (child gone?)", exc_info=True)

    def _mux_picture(self, frame: np.ndarray, stamp: float) -> int:
        """One picture into the NUT stream at wall time `stamp`. The
        reusable AVFrame's plane is updated in place (one copy) and the
        rawvideo 'encode' is the second; the mux write blocks on the
        pipe's backpressure exactly as the raw write did."""
        with self._mux_lock:
            if self._mux is None:
                raise OSError("no NUT mux open")
            picture = self._mux_frame
            fill_plane(picture.planes[0], frame)
            # Allocate the actual transport tick here, before encoding. Two
            # catch-up pictures can quantize to the same tick; a delayed grab
            # can even predate a heartbeat already written. FFmpeg must not
            # resolve those collisions invisibly. Keep every picture in feed
            # order and file its assigned PTS alongside its capture identity.
            pts = max(self._media_run.ticks_at(stamp),
                      self._last_video_pts + 1 if self._last_video_pts is not None else 0)
            picture.pts = pts
            for packet in self._mux_stream.encode(picture):
                self._mux.mux(packet)
            self._last_video_pts = pts
            return pts

    def _mux_audio_chunk(self, pcm: bytes, pts_us: int) -> None:
        """One chunk of interleaved s16le stereo into the NUT stream at
        `pts_us` (its first sample's wall time, PICTURE_TIME_BASE). Raises
        when the child is gone, like a pipe write."""
        import av

        samples = len(pcm) // 4
        if samples <= 0:
            return
        relative = int(pts_us) - int(round(self._run_epoch * 1_000_000))
        if relative < 0:
            # The tap may have queued audio before the first picture. NUT
            # shifts *both* streams when it sees a negative packet. Trim only
            # samples outside this run so video PTS zero remains picture zero.
            skip = min(samples, (-relative * self._cfg.audio_rate + 999_999) // 1_000_000)
            pcm = pcm[skip * 4:]
            if not pcm:
                return
            relative += round(skip * 1_000_000 / self._cfg.audio_rate)
        block = np.frombuffer(pcm, dtype=np.int16).reshape(1, -1)
        chunk = av.AudioFrame.from_ndarray(block, format="s16", layout="stereo")
        chunk.sample_rate = self._cfg.audio_rate
        chunk.time_base = PICTURE_TIME_BASE
        chunk.pts = relative
        with self._mux_lock:
            if self._mux is None:
                raise OSError("no NUT mux open")
            for packet in self._mux_audio.encode(chunk):
                self._mux.mux(packet)

    def _audio_mux_loop(self) -> None:
        """The picture feed's audio thread: drain submitted PCM into the NUT
        stream and pad silence up to realtime, exactly as the pipe writer
        does -- the muxer still interleaves video against audio, so a
        quiet game must not stall the video (the pacer's reason stands).
        Every chunk carries its own wall-clock stamp."""
        import time as _time

        broken = [False]
        rate = self._cfg.audio_rate
        next_pts = [None]           # us: where the next sample must start

        def _put_at(buf: bytes, ends_at: float) -> None:
            """Stamp a chunk by the wall time it ended at, never earlier
            than the previous chunk's end: a real chunk that arrived late
            is nudged forward by the few ms it overlaps, and aresample's
            async mode absorbs that; a wall clock keeps the stream from
            drifting the way a pure sample count did (the drift memory)."""
            samples = len(buf) // 4
            if samples <= 0:
                return
            wanted = int(round((ends_at - samples / rate) * 1_000_000))
            pts = wanted if next_pts[0] is None else max(wanted, next_pts[0])
            try:
                self._mux_audio_chunk(buf, pts)
            except Exception:
                if not broken[0]:
                    log.warning("audio mux failed: the child is gone -- the "
                                "audio feed stops here", exc_info=True)
                broken[0] = True
                return
            next_pts[0] = pts + int(round(samples * 1_000_000 / rate))

        pacer = AudioPacer(rate, _time.perf_counter,
                           write=lambda buf: _put_at(buf, _time.time()),
                           write_at=_put_at)
        while not self._stop.is_set():
            drained = False
            while True:
                try:
                    item = self._audio_q.get_nowait()
                except queue.Empty:
                    break
                if item is None:
                    return
                pacer.feed(item[0], ends_at=item[1])
                if broken[0]:
                    return
                drained = True
            pacer.tick()
            if broken[0]:
                return
            if not drained:
                _time.sleep(0.005)

    def _picture_feed_loop(self) -> None:
        """THE PICTURE FEED: every submitted picture is written once, in
        order, as soon as it arrives; after PICTURE_HEARTBEAT_S with no new
        picture the last one is written again, untagged."""
        import time as _time

        last_frame = None
        last_write = _time.monotonic()
        fed_window = 0
        stall_max = 0.0
        last_report = _time.monotonic()
        try:
            while not self._stop.is_set():
                self._arrived.wait(timeout=PICTURE_HEARTBEAT_S)
                self._arrived.clear()
                while not self._stop.is_set():
                    try:
                        frame, tag = self._queue.popleft()
                    except IndexError:
                        break
                    self._queued_bytes = max(0, self._queued_bytes - frame.nbytes)
                    wms = self._write_frame(frame, tag)
                    if wms is None:
                        break
                    last_frame = frame
                    last_write = _time.monotonic()
                    stall_max = max(stall_max, wms)
                    fed_window += 1
                if (last_frame is not None
                        and _time.monotonic() - last_write >= PICTURE_HEARTBEAT_S):
                    wms = self._write_frame(last_frame, None)
                    if wms is not None:
                        last_write = _time.monotonic()
                        fed_window += 1
                now = _time.monotonic()
                if now - last_report > 30 and fed_window:
                    log.info("ffmpeg AV sink (picture feed): %.1f fed/s, max "
                             "write %.0f ms, %d restarts, %d audio drops, "
                             "%d pictures dropped",
                             fed_window / (now - last_report), stall_max,
                             self._restarts, self._audio_dropped,
                             self._picture_drops)
                    fed_window = 0
                    stall_max = 0.0
                    self._audio_dropped = 0
                    last_report = now
        except Exception:
            log.exception("ffmpeg picture feeder died")

    def _segment_list_loop(self, proc, dims=None, media_run=None) -> None:
        # This run's first segment start (pts origin to subtract). The picture
        # feed's timeline already counts from the anchor, so nothing is.
        origin = 0.0 if self._picture else None
        for raw in iter(proc.stdout.readline, b""):
            # This reader belongs to its child even after a resize/restart.
            # Never let an old child's final CSV line use the new origin.
            anchor = (datetime.fromtimestamp(media_run.origin_ts, timezone.utc)
                      if media_run else self._anchor_utc)
            if anchor is None:
                continue
            line = raw.decode("utf-8", "replace")
            parts = line.strip().rsplit(",", 2)
            if len(parts) == 3:
                try:
                    if origin is None:
                        origin = float(parts[1])
                except ValueError:
                    pass
            seg = parse_segment_csv(line, anchor, origin or 0.0,
                                    self._cfg.scratch_dir, dims, media_run)
            if seg is not None:
                self._on_segment(seg)

    def _stderr_loop(self, proc) -> None:
        for raw in iter(proc.stderr.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            if line:
                log.warning("ffmpeg: %s", line)
