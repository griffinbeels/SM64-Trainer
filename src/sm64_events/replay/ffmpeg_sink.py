r"""ffmpeg-subprocess A+V sink — ONE ffmpeg owns both streams and ONE clock.

WHY this shape (the whole point — read the drift memory): video and audio used
to be two streams on two independent clocks (count-based audio vs
fed-frame-count video), reconciled by hand only at extraction. Their rates
diverged ~150 ppm and the clip A/V offset grew to seconds over a long session.
The cure is structural: feed BOTH raw streams into one ffmpeg that stamps each
by SYSTEM WALL-CLOCK at read time (`-use_wallclock_as_timestamps`), CFR-locks
the video to that clock (`-fps_mode cfr -r fps`), and continuously resamples
the audio onto the same timeline (`-af aresample=async=1`). The segment muxer
then slices an already-continuous, already-synced encode, so each MPEG-TS
segment carries A+V locked together — and the per-segment AAC priming gap that
forced the old PCM-sidecar design does NOT return (priming is applied once at
stream start, not per segment).

Transport:
- VIDEO over stdin (`pipe:0`): the feeder thread re-sends the latest submitted
  frame at fps. Exact pacing is no longer load-bearing — ffmpeg's wallclock
  stamping owns the timeline, so feeder jitter cannot accumulate drift (this is
  precisely the bug the old fed-frame-count timeline had: a stall's resync
  dropped owed frames and the video clock fell permanently behind).
- AUDIO over a Windows named pipe (`\\.\pipe\...`): the producer (recorder
  _on_pcm) calls submit_audio(); a writer thread connects the pipe and drains a
  queue into it. Inherited-fd `pipe:N` does NOT work on Windows — a named pipe
  is the only second-input mechanism. Pitfall: ffmpeg opens inputs in order and
  a pipe open BLOCKS until a writer connects, so the writer thread must connect
  promptly and independently of the video feeder.

Timeline -> UTC: anchor once at the first fed frame (wall time); each segment's
UTC offset is its CSV start MINUS the first segment's CSV start (relative), so
the mapping is correct whether ffmpeg reports zero-based or wall-clock-epoch
pts. `-reset_timestamps 1` applies ONE shared offset to both streams per
segment (verified in libavformat/segment.c), so files open at pts~0 and A/V
sync is preserved across segment boundaries (the extractor contract).

Backstop: every spawned child is assigned to a kill-on-close Job Object, so a
hung/hard-killed parent can never orphan an ffmpeg recording into a dead
terminal (live incident 2026-06-12).
"""
import ctypes
import ctypes.wintypes as wt
import logging
import os
import queue
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from sm64_events.replay.ring import SegmentInfo

log = logging.getLogger("sm64.replay")

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


def parse_segment_csv(line: str, anchor_utc: datetime, origin_s: float,
                      scratch: Path) -> SegmentInfo | None:
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
        size_bytes=size)


class FfmpegAvSink:
    """Combined-A/V video+audio sink. submit() frames and submit_audio() PCM
    from any thread; segments arrive at on_segment with wall-true UTC spans and
    audio muxed in, synced on a single clock."""

    def __init__(self, cfg, on_segment, ffmpeg: str = "ffmpeg"):
        self._cfg = cfg
        self._on_segment = on_segment
        self._ffmpeg = ffmpeg
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
        self._jobs: list[int] = []
        # audio named-pipe transport
        self._audio_q: queue.Queue = queue.Queue(maxsize=256)
        self._audio_dropped = 0
        self._pipe_name: str | None = None
        self._pipe_handle = None
        self._audio_thread: threading.Thread | None = None

    # -- capture-thread surface (lock-free) -----------------------------------
    def submit(self, bgra: np.ndarray) -> None:
        h, w = bgra.shape[:2]
        if (h & 1) or (w & 1):
            bgra = bgra[:h & ~1, :w & ~1]
        self._latest = bgra if bgra.flags["C_CONTIGUOUS"] \
            else np.ascontiguousarray(bgra)

    def submit_audio(self, pcm_bytes: bytes) -> None:
        """Enqueue interleaved s16le stereo PCM for the audio pipe. Non-blocking
        and drop-on-overflow: the writer thread absorbs pipe backpressure, but a
        wedged ffmpeg must never stall the audio producer."""
        try:
            self._audio_q.put_nowait(pcm_bytes)
        except queue.Full:
            self._audio_dropped += 1

    # -- lifecycle -------------------------------------------------------------
    def start(self) -> None:
        self._stop.clear()
        self._feeder = threading.Thread(target=self._feed_loop,
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
    def _spawn(self, w: int, h: int) -> None:
        global _pipe_seq
        self._readers = [t for t in self._readers if t.is_alive()]
        fps = self._cfg.fps
        seg_s = self._cfg.segment_s
        rate = self._cfg.audio_rate
        _pipe_seq += 1
        self._pipe_name = rf"\\.\pipe\sm64av_{os.getpid()}_{_pipe_seq}"
        self._open_audio_pipe()
        pattern = str(self._cfg.scratch_dir / f"av_{self._seg_n_base:02d}_%06d.ts")
        args = [
            self._ffmpeg, "-hide_banner", "-loglevel", "warning",
            "-fflags", "+nobuffer",
            # video input: stdin, wall-clock stamped
            "-use_wallclock_as_timestamps", "1", "-thread_queue_size", "1024",
            "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{w}x{h}", "-i", "pipe:0",
            # audio input: named pipe, wall-clock stamped
            "-use_wallclock_as_timestamps", "1", "-thread_queue_size", "1024",
            "-f", "s16le", "-ar", str(rate), "-ac", "2", "-i", self._pipe_name,
            "-map", "0:v:0", "-map", "1:a:0",
            # video: NVENC, CFR locked to the wall clock
            "-c:v", "h264_nvenc", "-preset", "p1", "-tune", "ull", "-bf", "0",
            "-b:v", "12M", "-g", str(int(fps * seg_s)), "-forced-idr", "1",
            "-fps_mode", "cfr", "-r", str(fps),
            # audio: AAC, async-resampled to LOCK to the master (kills drift)
            "-c:a", "aac", "-b:a", "160k", "-ar", str(rate),
            "-af", "aresample=async=1:first_pts=0:min_hard_comp=0.1",
            # combined A+V MPEG-TS segments
            "-f", "segment", "-segment_time", str(seg_s),
            "-segment_format", "mpegts", "-reset_timestamps", "1",
            "-segment_list", "pipe:1", "-segment_list_type", "csv",
            "-segment_list_flags", "+live",
            pattern,
        ]
        self._proc = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, bufsize=0,
            creationflags=subprocess.CREATE_NO_WINDOW)
        job = _assign_kill_on_close(self._proc)
        if job is not None:
            self._jobs.append(job)
        self._dims = (w, h)
        self._seg_n_base += 1
        # audio writer thread: connect the pipe (ffmpeg is the client) + drain
        self._audio_thread = threading.Thread(
            target=self._audio_writer_loop, name="ffmpeg-audio", daemon=True)
        self._audio_thread.start()
        for target, name in ((self._segment_list_loop, "ffmpeg-segments"),
                             (self._stderr_loop, "ffmpeg-stderr")):
            t = threading.Thread(target=target, args=(self._proc,),
                                 name=name, daemon=True)
            t.start()
            self._readers.append(t)
        log.info("ffmpeg AV sink: spawned %dx%d@%d nvenc + audio pipe (run %d)",
                 w, h, fps, self._seg_n_base)

    def _stop_proc(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
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
        """Block until ffmpeg connects to the named pipe, then drain the queue
        into it. WriteFile may block on pipe backpressure — harmless here (off
        the RT path); on ffmpeg exit the read end closes and WriteFile errors,
        ending the loop."""
        h = self._pipe_handle
        if h is None:
            return
        _k32.ConnectNamedPipe(h, None)  # returns when ffmpeg opens the pipe
        written = wt.DWORD(0)
        while not self._stop.is_set():
            try:
                buf = self._audio_q.get(timeout=0.5)
            except queue.Empty:
                continue
            if buf is None:
                break
            ok = _k32.WriteFile(h, buf, len(buf), ctypes.byref(written), None)
            if not ok:
                break  # broken pipe (ffmpeg gone)

    def _teardown_audio_pipe(self) -> None:
        self._audio_q.put(None)  # wake the writer
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
                frame = self._latest
                if frame is None:
                    _time.sleep(0.05)
                    next_t = _time.perf_counter()
                    continue
                h, w = frame.shape[:2]
                if self._proc is None or (w, h) != self._dims:
                    if self._proc is not None:
                        log.info("ffmpeg AV sink: dims %s -> %s, restarting",
                                 self._dims, (w, h))
                        self._restarts += 1
                        self._teardown_audio_pipe()
                        self._stop_proc()
                    self._anchor_utc = None
                    self._spawn(w, h)
                if self._anchor_utc is None:
                    # this run's frame 0 is NOW (the segment-time anchor)
                    self._anchor_utc = datetime.now(timezone.utc)
                t0 = _time.perf_counter()
                try:
                    self._proc.stdin.write(frame)  # raw pipe: GIL released
                except Exception:
                    log.exception("ffmpeg stdin write failed - restarting")
                    self._restarts += 1
                    self._teardown_audio_pipe()
                    self._stop_proc()
                    next_t = _time.perf_counter()
                    continue
                wms = (_time.perf_counter() - t0) * 1000
                stall_max = max(stall_max, wms)
                self._fed += 1
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

    def _segment_list_loop(self, proc) -> None:
        origin = None  # this run's first segment start (pts origin to subtract)
        for raw in iter(proc.stdout.readline, b""):
            anchor = self._anchor_utc
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
                                    self._cfg.scratch_dir)
            if seg is not None:
                self._on_segment(seg)

    def _stderr_loop(self, proc) -> None:
        for raw in iter(proc.stderr.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            if line:
                log.warning("ffmpeg: %s", line)
