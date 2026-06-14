"""Orchestrator: window attach-retry (mirrors server/poller.py's pattern),
capture sources -> video sink / SegmentWriter -> SegmentRing, status
surface.

Two video paths:
- ffmpeg sink (PRIMARY when ffmpeg.exe is on PATH — main.py probes):
  _on_frame becomes a lock-free reference swap into the sink; pacing,
  encode and segmentation run in a child process (ffmpeg_sink.py
  docstring carries why in-process encoding was structurally glitchy:
  GIL co-tenancy).
- in-process fallback: the CFR-conform path below feeds SegmentWriter.

Threading: capture callbacks arrive on library threads (the video
source's deliver thread, the audio pump). One lock serialises writer
access; it is taken PER FRAME (not around whole CFR fill loops) so a
large fill after a stale-window gap can't starve the audio callback, and
the writer-None re-check happens UNDER the lock (teardown can run between
a naked check and the write).

CFR conform (fallback path only): frame_index =
round(seconds_since_anchor * fps). Small delivery gaps (sources send
frames only on change — pause menus, occlusion) are filled by re-encoding
the last frame at each missing index, up to one segment's worth of
frames. Larger gaps are NOT filled: the writer receives the real target
index, its gap-rotation logic detects the jump and rotates segments,
converting the silence into an honest coverage hole in the ring rather
than minutes of frozen duplicate video. This wall-clock-locks the video
stream, which is what makes utc <-> frame mapping exact.

Audio fallback chain: audio_factory is tried first; if its start() fails
and a fallback_audio_factory was provided, that is tried; otherwise
recording proceeds video-only. The chain is config wiring, not policy —
main.py decides the factories (currently: system loopback with PID
endpoint targeting as PRIMARY, no fallback — per-process tap is a
false-healthy trap on this machine; see audio.py docstring)."""
import logging
import shutil
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Protocol

import numpy as np

from sm64_events.replay.clock import CaptureClock, qpc_100ns
from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.encoder import SegmentWriter, pick_video_codec
from sm64_events.replay.ring import SegmentRing
from sm64_events.replay.window import WindowInfo

log = logging.getLogger("sm64.replay")

# Minimum idle threshold even when the padding window is tiny: prevents
# pause/resume thrash (each cycle restarts the ffmpeg child).
_IDLE_FLOOR_S = 3.0


class VideoSource(Protocol):
    def start(self, on_frame: Callable[[np.ndarray, int], None],
              on_stopped: Callable[[], None]) -> None: ...
    def stop(self) -> None: ...


class AudioSource(Protocol):
    mode: str  # "process" | "system"
    def start(self, on_pcm: Callable[[np.ndarray], None]) -> None: ...
    def stop(self) -> None: ...


class ReplayRecorder:
    def __init__(self, cfg: ReplayConfig,
                 window_finder: Callable[[str], WindowInfo | None],
                 video_factory: Callable[[WindowInfo], VideoSource],
                 audio_factory: Callable[[int], AudioSource],
                 fallback_audio_factory: Callable[[int], AudioSource] | None = None,
                 clock_factory: Callable[[], CaptureClock] = CaptureClock.now,
                 codec: str | None = None,
                 video_sink_factory=None):
        self._cfg = cfg
        self._window_finder = window_finder
        self._video_factory = video_factory
        self._audio_factory = audio_factory
        self._fallback_audio_factory = fallback_audio_factory
        self._clock_factory = clock_factory
        self._codec: str | None = codec
        # ffmpeg-subprocess video path: when set, frames bypass the in-process
        # writer entirely (sink.submit is a lock-free reference swap; pacing,
        # encoding and segmentation happen in the child process — the GIL
        # decoupling that ended the scattered-missed-slot glitch class)
        self._video_sink_factory = video_sink_factory
        self._video_sink = None

        # free-disk-gated cap: the configured byte cap is a ceiling, but the
        # buffer never grows so large that the scratch volume's free space
        # drops below the ring's margin (a near-full disk thrashes the whole
        # machine — same symptom as a RAM leak).
        self.ring = SegmentRing(
            cfg.retention_s, cfg.max_buffer_bytes,
            free_bytes_fn=lambda: shutil.disk_usage(cfg.scratch_dir).free)

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._window_lost = threading.Event()
        self._thread: threading.Thread | None = None
        self._stopping: bool = False  # M2: prevents zombie _begin_capture post-stop

        # protected by _lock
        self._writer: SegmentWriter | None = None
        self._video_source: VideoSource | None = None
        self._audio_source: AudioSource | None = None
        self._clock: CaptureClock | None = None
        self._last_frame: np.ndarray | None = None
        self._last_index: int = -1
        # observability: what the EYE sees is slots filled vs duplicated
        self._stat_written = 0   # real frames written to slots
        self._stat_fills = 0     # CFR duplicates (a missed slot each)
        self._stat_report_t = 0.0

        # status fields (written under _lock or from attach thread only)
        self._recording: bool = False
        self._window_found: bool = False
        self._audio_mode: str = "none"

        # idle gating: no player input for idle_after_s -> completed
        # segments are DISCARDED instead of retained (_on_segment); the
        # encoder keeps running so the timeline never breaks. First active
        # tick resumes instantly. These are plain attrs flipped from the
        # poll thread (resume) and attach thread (pause) — ref/bool/float
        # swaps are atomic in CPython, and both transitions are idempotent.
        self.set_idle_after(cfg.pre_pad_s + cfg.post_pad_s)
        self._last_player_active = time.monotonic()
        self._idle = False
        self._idle_since = None   # utc datetime while idle (the discard rule)
        self._idle_dropped = 0
        self._session_paused = False  # manual pause: outranks the input tap

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Wipe scratch dir, pick codec if needed, start attach loop."""
        scratch = self._cfg.scratch_dir
        # Recursive: the clip cache (clips/ subdir, owned by ReplayService)
        # must die with the buffer — a file-only wipe would leave stale clips
        # that view() then serves against an empty ring after a restart.
        shutil.rmtree(scratch, ignore_errors=True)
        scratch.mkdir(parents=True, exist_ok=True)

        if self._codec is None:
            self._codec = pick_video_codec()

        self._stop_event.clear()
        self._stopping = False  # restart is supported; stop() sets this (M2 guard)
        self._thread = threading.Thread(
            target=self._attach_loop, name="replay-attach", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Signal attach loop to exit and tear down any active capture."""
        self._stopping = True  # M2: block any in-flight _begin_capture from racing post-stop
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            if self._thread.is_alive():
                log.warning("replay attach thread did not stop within timeout")
            self._thread = None
        self._teardown_capture()

    # -- attach loop ---------------------------------------------------------

    def _attach_loop(self) -> None:
        while not self._stop_event.is_set():
            win = self._window_finder(self._cfg.window_title)
            self._window_found = win is not None

            if win is not None and not self._recording:
                try:
                    self._begin_capture(win)
                except Exception:
                    log.exception("begin_capture failed — will retry")
                    self._teardown_capture()

            if self._window_lost.is_set():
                self._window_lost.clear()
                log.info("window lost — tearing down capture")
                self._teardown_capture()

            self._maybe_idle_pause()
            self._stop_event.wait(self._cfg.attach_poll_s)

    # -- capture setup / teardown --------------------------------------------

    def _begin_capture(self, win: WindowInfo) -> None:
        # M2: bail immediately if stop() already ran — prevents a zombie begin
        # from racing ahead and resurrecting recording state post-stop.
        if self._stopping:
            return

        clock = self._clock_factory()
        writer = SegmentWriter(
            self._cfg, clock, self._cfg.scratch_dir, self._codec,
            self._on_segment)

        with self._lock:
            self._clock = clock
            self._writer = writer
            self._last_frame = None
            self._last_index = -1

        if self._video_sink_factory is not None:
            self._video_sink = self._video_sink_factory(self._cfg,
                                                        self._on_segment)
            self._video_sink.start()

        # Start video — on_stopped signals window loss back to attach loop.
        # C1: assign _video_source the instant start() succeeds so teardown
        # can always reclaim it, even if something below raises.
        video = self._video_factory(win)
        # Idle throttle: while the recorder is idle (AFK / manual pause) the
        # capture source drops to a trickle grab rate — every segment is
        # discarded anyway, so the dominant cost (the per-grab ~8 MB surface
        # read+copy, ~2 GB/s at full rate) is pure waste. The ffmpeg feeder is
        # untouched, so resume stays seamless (no child respawn hole).
        if hasattr(video, "set_idle_check"):
            video.set_idle_check(self.is_idle)
        video.start(self._on_frame, self._window_lost.set)
        with self._lock:
            self._video_source = video

        # I1: set the writer's audio t0 BEFORE wiring any audio callback so a
        # PCM packet arriving between audio.start() and start_audio() doesn't
        # hit writer.write_audio()'s "start_audio() not called" guard.
        # t0 = "writer ready", which is at most one attach-poll interval
        # earlier than "audio started" — irrelevant vs the ±2-3 s clip padding.
        with self._lock:
            writer.start_audio(t0_utc=clock.utc_of(qpc_100ns()))

        # Audio fallback chain
        audio: AudioSource | None = None
        audio_mode = "none"
        try:
            audio = self._audio_factory(win.pid)
            audio.start(self._on_pcm)
            audio_mode = audio.mode
        except Exception:
            log.exception("primary audio source failed")
            if self._fallback_audio_factory is not None:
                try:
                    audio = self._fallback_audio_factory(self._cfg.audio_rate)
                    audio.start(self._on_pcm)
                    audio_mode = audio.mode
                except Exception:
                    log.exception("fallback audio source also failed — video-only")
                    audio = None
                    audio_mode = "none"
            else:
                audio = None
                audio_mode = "none"

        # C1 continued: assign _audio_source immediately after its start()
        # succeeds so teardown can reclaim it if anything after this raises.
        with self._lock:
            self._audio_source = audio
            self._audio_mode = audio_mode

        self._last_player_active = time.monotonic()  # fresh grace period
        self._idle = False
        self._idle_since = None
        if self._session_paused:  # window (re)appeared mid-pause: stay idle
            self._set_idle(True)
        self._recording = True
        log.info("capture started — window=%r audio=%s codec=%s",
                 win.title, audio_mode, self._codec)

    def _teardown_capture(self) -> None:
        """Stop sources and close writer. Safe to call when already idle."""
        sink = self._video_sink
        self._video_sink = None
        if sink is not None:
            try:
                sink.stop()  # closes stdin -> ffmpeg flushes final segment
            except Exception:
                log.exception("ffmpeg sink stop failed")
        with self._lock:
            video = self._video_source
            audio = self._audio_source
            writer = self._writer
            self._video_source = None
            self._audio_source = None
            self._writer = None
            self._clock = None

        for src in (video, audio):
            if src is not None:
                try:
                    src.stop()
                except Exception:
                    log.exception("error stopping source %r", src)

        if writer is not None:
            try:
                writer.close()
            except Exception:
                log.exception("error closing writer")

        self._recording = False
        self._idle = False
        self._idle_since = None
        # _audio_mode intentionally kept as last-known value so status() can
        # report which mode was active even after stop; cleared only on
        # fresh _begin_capture (set to new mode) or explicit reset.

    # -- idle gating (no player input -> discard footage, don't retain) -------

    def set_idle_after(self, window_s: float) -> None:
        """The idle threshold tracks the user's padding window (pre+post):
        footage further from any input than the padding can never appear
        in a clip, so recording it is pure disk churn. Floored at
        _IDLE_FLOOR_S to prevent thrash at tiny pads."""
        self.idle_after_s = max(_IDLE_FLOOR_S, float(window_s))

    def set_player_active(self) -> None:
        """Poll-thread tap (replay/activity.py): called on every tick where
        the player is providing input. Resume happens HERE, instantly — the
        next attempt's pre-pad starts at the first input. Pause lives in
        the attach loop (2 s cadence; a couple of extra recorded idle
        seconds is harmless)."""
        if self._session_paused:
            return  # manual session pause outranks the input signal
        self._last_player_active = time.monotonic()
        if self._idle:
            self._set_idle(False)

    def set_session_paused(self, paused: bool) -> None:
        """Manual session pause (POST /api/pause via server/app.py): rides
        the idle-discard machinery — the encoder timeline stays unbroken,
        completed segments are dropped, the buffer gains nothing. Unlike
        auto-idle, resume is NOT input-driven (the poller pauses too, so
        the activity tap goes silent); unpausing restores recording
        immediately and refreshes the activity clock so auto-idle doesn't
        instantly re-trigger."""
        self._session_paused = paused
        if paused:
            if not self._idle:
                self._set_idle(True)
        else:
            self._last_player_active = time.monotonic()
            if self._idle:
                self._set_idle(False)

    def is_idle(self) -> bool:
        """True while footage is being discarded — auto-idle (AFK) OR manual
        session pause (both set _idle). The capture source reads this to
        throttle its grab rate; the gen-2 GC collector reads it to pick a
        free moment to run (a stop-the-world pause is invisible while idle)."""
        return self._idle

    def _maybe_idle_pause(self) -> None:
        if (self._recording and not self._idle
                and time.monotonic() - self._last_player_active
                > self.idle_after_s):
            self._set_idle(True)

    def _set_idle(self, idle: bool) -> None:
        self._idle = idle
        if idle:
            self._idle_since = datetime.now(timezone.utc)
            log.info("replay idle: no player input for %.0f s — new "
                     "segments will be discarded until input",
                     self.idle_after_s)
        else:
            self._idle_since = None
            log.info("replay idle: input detected — buffer resumes "
                     "(%d idle segments discarded)", self._idle_dropped)
            self._idle_dropped = 0

    def _on_segment(self, seg) -> None:
        """Ring gate — BOTH video segments and audio chunks arrive here.
        While idle, segments born ENTIRELY inside the idle window are
        deleted instead of retained; the encoder keeps running so the
        timeline never breaks. (Pausing the ffmpeg child was shipped first
        and reverted: every resume respawned it, leaving a ~0.2 s startup
        hole exactly where a 0-pre-pad clip begins — user-reported as a
        frozen clip opening.) Segments STRADDLING the idle boundary are
        kept: at pause they carry the last active footage; at resume they
        carry the anchor lead-up/fade-in, which is why a 0 s pre-pad clip
        opens exactly at the anchor."""
        idle_since = self._idle_since
        if idle_since is not None and seg.utc_start >= idle_since:
            self._idle_dropped += 1
            try:
                seg.path.unlink(missing_ok=True)
            except OSError:
                log.exception("idle-discard unlink failed for %s", seg.path)
            return
        self.ring.add(seg)

    # -- frame callback (library thread) -------------------------------------

    def _on_frame(self, bgra: np.ndarray, ts_100ns: int) -> None:
        # NO idle gate here: frames keep flowing so the sink's timeline and
        # `_latest` stay fresh; idle discard happens per completed segment
        # in _on_segment.
        # ffmpeg-sink path: a lock-free reference swap, nothing else — the
        # sink's feeder paces CFR and the child process encodes. The entire
        # in-process CFR/dedup/encode machinery below is bypassed.
        sink = self._video_sink
        if sink is not None:
            sink.submit(bgra)
            return
        # M1: _last_frame and _last_index are written here only; WGC guarantees
        # a single callback thread, so they need no lock — if that ever changes,
        # move both inside _lock.
        with self._lock:
            if self._writer is None:
                return
            clock = self._clock

        target = round(clock.seconds_since_anchor(ts_100ns) * self._cfg.fps)

        # Drop backwards/duplicate (encoder will also guard, but be explicit)
        if target <= self._last_index:
            return

        # Fill small delivery gaps (WGC sends frames only on change) by
        # re-encoding the last frame; beyond one segment's worth, stop
        # pretending — hand the writer the real index and its gap-rotation
        # turns the silence into an honest coverage hole in the ring.
        max_fill = int(self._cfg.fps * self._cfg.segment_s)
        fill_from = (self._last_index + 1
                     if self._last_frame is not None
                     and target - self._last_index <= max_fill
                     else target)
        self._stat_fills += max(0, target - fill_from)
        self._stat_written += 1
        import time as _t
        now = _t.monotonic()
        if now - self._stat_report_t > 30:
            if self._stat_report_t:
                log.info("recorder video: %d slots written, %d CFR fills "
                         "(missed slots) in last 30s",
                         self._stat_written, self._stat_fills)
            self._stat_written = self._stat_fills = 0
            self._stat_report_t = now

        for idx in range(fill_from, target):
            with self._lock:
                if self._writer is None:
                    return
                self._writer.write_video(self._last_frame, idx)

        with self._lock:
            if self._writer is None:
                return
            self._writer.write_video(bgra, target)

        self._last_frame = bgra
        self._last_index = target

    # -- audio callback (library thread) -------------------------------------

    def _on_pcm(self, pcm_s16: np.ndarray) -> None:
        # NO idle gate here: audio chunk timestamps are COUNT-based
        # (t0 + samples_written/rate — encoder.py); dropping PCM would
        # shift every post-resume chunk earlier and desync A/V. The sample
        # cursor must keep advancing; idle discard happens per completed
        # chunk in _on_segment.
        with self._lock:
            if self._writer is None:
                return
            self._writer.write_audio(pcm_s16)

    # -- status --------------------------------------------------------------

    def status(self) -> dict:
        # M3: reads of _recording/_window_found/_audio_mode/_codec are unlocked;
        # CPython attribute reads are atomic, so these are stale-but-never-torn —
        # acceptable for a polling status surface.
        cov = self.ring.coverage("video")
        return {
            "recording": self._recording,
            "window_found": self._window_found,
            "audio_mode": self._audio_mode,
            "encoder": self._codec,
            "buffer_start_utc": cov[0].isoformat() if cov else None,
            "buffer_end_utc": cov[1].isoformat() if cov else None,
            "disk_bytes": self.ring.total_bytes,
            "retention_s": self.ring.retention_s,
            "max_buffer_bytes": self.ring.max_bytes,
            "idle": self._idle,
        }
