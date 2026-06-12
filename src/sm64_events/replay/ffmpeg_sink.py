"""ffmpeg-subprocess video sink — encoding OUT of the Python process.

WHY: every remaining replay glitch class (scattered missed slots, rare
100-200 ms gaps, audio hiccups correlated with them) traced to threads
sharing one interpreter lock with the in-process PyAV/NVENC encoder.
Capture callbacks, the audio pump, and the encoder all paid each other's
latency through the GIL. A separate process is the structural end of that
coupling: ffmpeg.exe receives raw BGRA frames over a pipe (the write is a
syscall — GIL released), encodes with NVENC, and rotates MPEG-TS segments
with its own battle-tested segment muxer.

Design:
- FEEDER thread paces EXACTLY fps frames/s (high-resolution waitable
  timer): each tick writes the latest submitted frame to ffmpeg's stdin.
  CFR by construction — no index math, no fill logic; a capture gap simply
  re-sends the last frame (frozen image = honest content for "nothing new
  was presented").
- submit() (called from the capture thread) just swaps an ndarray
  reference: lock-free, O(1).
- ffmpeg writes a CSV segment list to stdout; a reader thread maps each
  completed segment (start/end in fed-frame seconds) onto wall time via
  the anchor captured at the FIRST fed frame, and hands SegmentInfo to the
  ring. stderr drains to the log.
- -muxdelay 0 -muxpreload 0 -reset_timestamps 1: each segment starts at
  pts 0 with no MPEG-TS preload offset (the extractor contract:
  frame i of a segment is at utc_start + i/fps EXACTLY).
- -force_key_frames at the segment period: every segment opens on an IDR.
- Window resize: the rawvideo pipe is fixed-size; the sink restarts the
  process with new dimensions (rare; logged; a brief coverage hole).
"""
import logging
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from sm64_events.replay.ring import SegmentInfo

log = logging.getLogger("sm64.replay")


def parse_segment_csv(line: str, anchor_utc: datetime,
                      scratch: Path) -> SegmentInfo | None:
    """One line of ffmpeg's -segment_list_type csv: 'file,start,end'
    (seconds on the fed-frame timeline). Pure — unit-tested."""
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
    return SegmentInfo(path=path, kind="video",
                       utc_start=anchor_utc + timedelta(seconds=start),
                       utc_end=anchor_utc + timedelta(seconds=end),
                       size_bytes=size)


class FfmpegVideoSink:
    """Drop-in video half of the recorder pipeline. submit() frames from
    any thread; segments arrive at on_segment with wall-true utc spans."""

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
        self._seg_n_base = 0  # filename numbering across restarts
        self._restarts = 0

    # -- capture-thread surface (lock-free) -----------------------------------
    def submit(self, bgra: np.ndarray) -> None:
        h, w = bgra.shape[:2]
        if (h & 1) or (w & 1):
            bgra = bgra[:h & ~1, :w & ~1]
        self._latest = bgra if bgra.flags["C_CONTIGUOUS"] \
            else np.ascontiguousarray(bgra)

    # -- lifecycle -------------------------------------------------------------
    def start(self) -> None:
        self._stop.clear()
        self._feeder = threading.Thread(target=self._feed_loop,
                                        name="ffmpeg-feeder", daemon=True)
        self._feeder.start()

    def stop(self) -> None:
        # Order matters: feeder first (stops stdin writes), then close stdin
        # so ffmpeg flushes its final segment and exits, THEN the readers —
        # they exit on stdout/stderr EOF, which requires process exit.
        self._stop.set()
        if self._feeder is not None:
            self._feeder.join(timeout=10)
            self._feeder = None
        self._stop_proc()
        for t in self._readers:
            t.join(timeout=10)
        self._readers.clear()

    # -- process management ----------------------------------------------------
    def _spawn(self, w: int, h: int) -> None:
        fps = self._cfg.fps
        seg_s = self._cfg.segment_s
        pattern = str(self._cfg.scratch_dir / f"video_{self._seg_n_base:02d}_%06d.ts")
        args = [
            self._ffmpeg, "-hide_banner", "-loglevel", "warning",
            "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{w}x{h}",
            "-framerate", str(fps), "-i", "pipe:0",
            "-c:v", "h264_nvenc", "-preset", "p1", "-tune", "ull",
            # exact periodic IDR every segment: the segment muxer can only
            # split at keyframes — without -g, NVENC's default GOP (~250)
            # produced one giant unsplittable segment (live-tested)
            "-bf", "0", "-b:v", "12M",
            "-g", str(int(fps * seg_s)), "-forced-idr", "1",
            "-muxdelay", "0", "-muxpreload", "0",
            "-f", "segment", "-segment_time", str(seg_s),
            "-segment_format", "mpegts", "-reset_timestamps", "1",
            "-segment_list", "pipe:1", "-segment_list_type", "csv",
            pattern,
        ]
        self._proc = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, bufsize=0,
            creationflags=subprocess.CREATE_NO_WINDOW)
        self._dims = (w, h)
        self._seg_n_base += 1
        for target, name in ((self._segment_list_loop, "ffmpeg-segments"),
                             (self._stderr_loop, "ffmpeg-stderr")):
            t = threading.Thread(target=target, args=(self._proc,),
                                 name=name, daemon=True)
            t.start()
            self._readers.append(t)
        log.info("ffmpeg sink: spawned %dx%d@%d nvenc (run %d)",
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

    # -- threads ---------------------------------------------------------------
    def _feed_loop(self) -> None:
        import ctypes
        import time as _time

        from sm64_events.replay.clock import qpc_100ns  # noqa: F401 (timebase doc)

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
                        log.info("ffmpeg sink: dims %s -> %s, restarting",
                                 self._dims, (w, h))
                        self._restarts += 1
                        self._stop_proc()
                    self._anchor_segbase_utc = None
                    self._spawn(w, h)
                if self._anchor_utc is None:
                    self._anchor_utc = datetime.now(timezone.utc)
                if getattr(self, "_anchor_segbase_utc", None) is None:
                    # per-process anchor: this run's frame 0 is NOW
                    self._anchor_segbase_utc = datetime.now(timezone.utc)
                t0 = _time.perf_counter()
                try:
                    self._proc.stdin.write(frame)  # raw pipe: GIL released
                except Exception:
                    log.exception("ffmpeg stdin write failed - restarting")
                    self._restarts += 1
                    self._stop_proc()
                    next_t = _time.perf_counter()
                    continue
                wms = (_time.perf_counter() - t0) * 1000
                stall_max = max(stall_max, wms)
                self._fed += 1
                fed_window += 1
                now = _time.monotonic()
                if now - last_report > 30 and fed_window:
                    log.info("ffmpeg sink: %.1f fed/s, max write %.0f ms, "
                             "%d restarts", fed_window / (now - last_report),
                             stall_max, self._restarts)
                    fed_window = 0
                    stall_max = 0.0
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
                    next_t = _time.perf_counter()  # resync after long stall
        except Exception:
            log.exception("ffmpeg feeder died")
        finally:
            if htimer:
                kernel32.CloseHandle(htimer)

    def _segment_list_loop(self, proc) -> None:
        for raw in iter(proc.stdout.readline, b""):
            anchor = getattr(self, "_anchor_segbase_utc", None)
            if anchor is None:
                continue
            seg = parse_segment_csv(raw.decode("utf-8", "replace"), anchor,
                                    self._cfg.scratch_dir)
            if seg is not None:
                self._on_segment(seg)

    def _stderr_loop(self, proc) -> None:
        for raw in iter(proc.stderr.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            if line:
                log.warning("ffmpeg: %s", line)
