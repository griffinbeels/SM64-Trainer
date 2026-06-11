"""Orchestrator: window attach-retry (mirrors server/poller.py's pattern),
capture sources -> SegmentWriter -> SegmentRing, status surface.

Threading: capture callbacks arrive on library threads (windows-capture's
Rust thread, proc-tap's audio thread). One lock serialises writer access;
it is taken PER FRAME (not around whole CFR fill loops) so a large fill
after a stale-window gap can't starve the audio callback, and the
writer-None re-check happens UNDER the lock (teardown can run between a
naked check and the write).

CFR conform happens here: frame_index = round(seconds_since_anchor * fps).
Delivery gaps (WGC only sends frames on change — pause menus, occlusion)
are filled by re-encoding the last frame at each missing index; backwards/
duplicate indexes are dropped by the writer. This wall-clock-locks the
video stream, which is what makes utc <-> frame mapping exact.

Audio fallback chain: audio_factory (per-process tap) is tried first; if
its start() fails and a fallback_audio_factory was provided (device-wide
loopback), that is tried; otherwise recording proceeds video-only. The
chain is config wiring, not policy — main.py decides what the factories
are."""
import logging
import threading
from typing import Callable, Protocol

import numpy as np

from sm64_events.replay.clock import CaptureClock, qpc_100ns
from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.encoder import SegmentWriter, pick_video_codec
from sm64_events.replay.ring import SegmentRing
from sm64_events.replay.window import WindowInfo

log = logging.getLogger("sm64.replay")


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
                 codec: str | None = None):
        self._cfg = cfg
        self._window_finder = window_finder
        self._video_factory = video_factory
        self._audio_factory = audio_factory
        self._fallback_audio_factory = fallback_audio_factory
        self._clock_factory = clock_factory
        self._codec: str | None = codec

        self.ring = SegmentRing(cfg.retention_s, cfg.max_buffer_bytes)

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._window_lost = threading.Event()
        self._thread: threading.Thread | None = None

        # protected by _lock
        self._writer: SegmentWriter | None = None
        self._video_source: VideoSource | None = None
        self._audio_source: AudioSource | None = None
        self._clock: CaptureClock | None = None
        self._last_frame: np.ndarray | None = None
        self._last_index: int = -1

        # status fields (written under _lock or from attach thread only)
        self._recording: bool = False
        self._window_found: bool = False
        self._audio_mode: str = "none"

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Wipe scratch dir, pick codec if needed, start attach loop."""
        scratch = self._cfg.scratch_dir
        if scratch.exists():
            for p in scratch.iterdir():
                if p.is_file():
                    p.unlink(missing_ok=True)
        scratch.mkdir(parents=True, exist_ok=True)

        if self._codec is None:
            self._codec = pick_video_codec()

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._attach_loop, name="replay-attach", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Signal attach loop to exit and tear down any active capture."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
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

            self._stop_event.wait(self._cfg.attach_poll_s)

    # -- capture setup / teardown --------------------------------------------

    def _begin_capture(self, win: WindowInfo) -> None:
        clock = self._clock_factory()
        writer = SegmentWriter(
            self._cfg, clock, self._cfg.scratch_dir, self._codec, self.ring.add)

        with self._lock:
            self._clock = clock
            self._writer = writer
            self._last_frame = None
            self._last_index = -1

        # Start video — on_stopped signals window loss back to attach loop
        video = self._video_factory(win)
        video.start(self._on_frame, self._window_lost.set)

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

        with self._lock:
            self._video_source = video
            self._audio_source = audio
            self._audio_mode = audio_mode
            writer.start_audio(t0_utc=clock.utc_of(qpc_100ns()))

        self._recording = True
        log.info("capture started — window=%r audio=%s codec=%s",
                 win.title, audio_mode, self._codec)

    def _teardown_capture(self) -> None:
        """Stop sources and close writer. Safe to call when already idle."""
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
        # _audio_mode intentionally kept as last-known value so status() can
        # report which mode was active even after stop; cleared only on
        # fresh _begin_capture (set to new mode) or explicit reset.

    # -- frame callback (library thread) -------------------------------------

    def _on_frame(self, bgra: np.ndarray, ts_100ns: int) -> None:
        with self._lock:
            if self._writer is None:
                return
            clock = self._clock

        target = round(clock.seconds_since_anchor(ts_100ns) * self._cfg.fps)

        # Drop backwards/duplicate (encoder will also guard, but be explicit)
        if target <= self._last_index:
            return

        # CFR fill: re-encode last known frame for every missing index
        if self._last_frame is not None:
            for idx in range(self._last_index + 1, target):
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
        with self._lock:
            if self._writer is None:
                return
            self._writer.write_audio(pcm_s16)

    # -- status --------------------------------------------------------------

    def status(self) -> dict:
        cov = self.ring.coverage("video")
        return {
            "recording": self._recording,
            "window_found": self._window_found,
            "audio_mode": self._audio_mode,
            "encoder": self._codec,
            "buffer_start_utc": cov[0].isoformat() if cov else None,
            "buffer_end_utc": cov[1].isoformat() if cov else None,
            "disk_bytes": self.ring.total_bytes,
        }
