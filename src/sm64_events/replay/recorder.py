"""Orchestrator: window attach-retry (mirrors server/poller.py's pattern),
capture sources -> video sink / SegmentWriter -> SegmentRing, status
surface.

Two video paths:
- ffmpeg sink (PRIMARY when ffmpeg.exe is on PATH — main.py probes):
  _on_frame applies queue/ledger selection, prepares accepted plugin pixels,
  and submits owned BGRA. The sink muxes explicit timestamps into NUT;
  the child handles compression and segmentation. Legacy CFR remains an
  explicit configuration fallback, not the plugin picture feed.
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
recording proceeds video-only. BOTH take the target window's pid — the
fallback needs it as much as the primary does, to target the endpoint
hosting that app's session. The chain is config wiring, not policy —
main.py decides the factories (currently: per-process tap PRIMARY so only
the game is recorded, device loopback as the fallback; see audio.py)."""
import logging
from sm64_events.core.profiling import measured
import shutil
import threading
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol
from uuid import uuid4

import numpy as np

from sm64_events.core.recorder_lock import acquire_recorder_lock
from sm64_events.replay.clock import CaptureClock, qpc_100ns
from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.encoder import SegmentWriter, pick_video_codec
from sm64_events.replay.ledger import PictureLedger
from sm64_events.replay.fragmentmedia import FragmentMedia
from sm64_events.replay.pixels import BgrPicture, as_bgra
from sm64_events.replay.ring import SegmentRing
from sm64_events.replay.scratch import OwnedScratch
from sm64_events.replay.window import WindowInfo

log = logging.getLogger("sm64.replay")

# Minimum inactivity threshold avoids rapid retention-mode changes at tiny pads.
_IDLE_FLOOR_S = 3.0


class VideoSource(Protocol):
    """A camera. `on_frame(bgra, ts_100ns)` for a desktop grab the recorder
    must place in game time itself; the capture layer's source
    (replay/pluginsource.py) calls `on_frame(bgra, ts_100ns, stamp)` with the
    game's own frame counter and pad for that picture, and carries
    `frame_source = "plugin"` so status can say which camera is live."""
    def start(self, on_frame: Callable[..., None],
              on_stopped: Callable[[], None]) -> None: ...
    def stop(self) -> None: ...


class AudioSource(Protocol):
    mode: str  # "process" | "system"
    def start(self, on_pcm: Callable[[np.ndarray], None]) -> None: ...
    def stop(self) -> None: ...


def _sink_has_room(sink) -> bool:
    """Whether the sink can still encode a picture. A sink that does not
    answer (the CFR fallback, a test's stand-in) is always willing."""
    ask = getattr(sink, "has_room", None)
    return True if ask is None else bool(ask())


class ReplayRecorder:
    # Capture gate (set_capture_gate): no gate until main.py installs one.
    _capture_gate: Callable[[], bool] | None = None
    _capture_gated = False

    def __init__(self, cfg: ReplayConfig,
                 window_finder: Callable[[str], WindowInfo | None],
                 video_factory: Callable[[WindowInfo], VideoSource],
                 audio_factory: Callable[[int], AudioSource],
                 fallback_audio_factory: Callable[[int], AudioSource] | None = None,
                 clock_factory: Callable[[], CaptureClock] = CaptureClock.now,
                 codec: str | None = None,
                 video_sink_factory=None,
                 recorder_lock_factory=acquire_recorder_lock,
                 release_capture: Callable[[], None] | None = None,
                 scratch_protection: Callable[[], Iterable[Path]] | None = None):
        self._cfg = cfg
        # machine-wide single-recorder guard (injectable for tests): only the
        # instance holding this lock actually captures; others run viewer-only.
        self._recorder_lock_factory = recorder_lock_factory
        self._rec_lock = None        # held handle while WE are the recorder
        self._release_capture = release_capture
        self._capture_lock = threading.RLock()  # serialize attach vs teardown
        self._scratch_ready = False
        self._configure_scratch(cfg, scratch_protection)
        self._lock_warned = False    # log the viewer-only notice once
        self._window_finder = window_finder
        self._video_factory = video_factory
        self._audio_factory = audio_factory
        self._fallback_audio_factory = fallback_audio_factory
        self._clock_factory = clock_factory
        # The picture ledger (replay/ledger.py, item 40): one row per
        # DISTINCT captured picture -- its composition time, the RAM frame,
        # and every registered stamp. Extraction reads it back through
        # ReplayService._map_from_ledger.
        self.ledger = PictureLedger()
        self._ledger_name = f"picture-identity-{uuid4().hex}.sqlite3"
        # The picture feed (config.picture_feed, item 38): with the ffmpeg
        # sink, a grab reaches the encoder only when the ledger says it is
        # a NEW picture, and every write the sink completes lands in the
        # ledger's feed log -- one video frame, one row.
        self._picture_feed = bool(getattr(cfg, "picture_feed", False))
        # Grabs the sink had no budget to encode, so the ledger never saw
        # them either. status() reports it: a degraded capture must be a
        # number he can see, not a clip that merely looks thinner.
        self._grabs_skipped = 0
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
            free_bytes_fn=lambda: shutil.disk_usage(cfg.scratch_dir).free,
            on_evict=self.ledger.discard_segment,
            scratch_root=cfg.scratch_dir,
            deletion_guard=self._scratch.deletion_guard,
            on_temp_evict=lambda *args: self.fragments.evicted(*args))
        self.fragments = FragmentMedia(cfg.scratch_dir, self.ring, self.ledger,
            idle_window=self._fragment_idle_window, discard=self._fragment_paused)

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._window_lost = threading.Event()
        self._thread: threading.Thread | None = None
        self._stopping: bool = False  # M2: prevents zombie _begin_capture post-stop
        self._configure_recovery()

        # protected by _lock
        self._writer: SegmentWriter | None = None
        self._video_source: VideoSource | None = None
        self._frame_source = "desktop"
        self._frame_source_note = None
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
        self._capture_window: tuple[int, int] | None = None
        self._audio_mode: str = "none"

        # Automatic idle changes retention, not the paired capture lifetime.
        # Shared fragments retain bounded pre-roll; legacy segments use their
        # existing discard gate. These are plain attrs flipped from the
        # poll thread (resume) and attach thread (pause) — ref/bool/float
        # swaps are atomic in CPython, and both transitions are idempotent.
        self.set_idle_after(cfg.pre_pad_s + cfg.post_pad_s)
        self._last_player_active = time.monotonic()
        self._idle = False
        self._idle_since = None   # utc datetime while idle (the discard rule)
        self._idle_dropped = 0
        self._session_paused = False  # manual pause: outranks the input tap
        self._session_paused_since = None

    # -- lifecycle -----------------------------------------------------------

    def _configure_recovery(self) -> None:
        self.maintain_history = lambda: None
        self._capture_cleanup_failed = False
        self._recovery_failures = 0
        self._recovery_error = None
        self._recovery_retry_at = 0.0
        self._audio_error = None
        self._audio_failures = 0
        self._audio_retry_at = 0.0
        self._audio_started_at = 0.0

    def start(self) -> None:
        """Start attach retries; only the recorder owner may reset scratch."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._scratch_ready = False
        self._stop_event.clear()
        self._stopping = False  # restart is supported; stop() sets this (M2 guard)
        self._thread = threading.Thread(
            target=self._attach_loop, name="replay-attach", daemon=True)
        self._thread.start()

    def stop(self, *, cleanup: bool = True) -> None:
        """End this session after capture closes; pending saves may retain it.

        Services drain preservation first, or pass cleanup=False when it needs
        source media for recovery. Window loss and pause never invoke cleanup.
        """
        self._cleanup_on_stop = cleanup
        self._stopping = True  # M2: block any in-flight _begin_capture from racing post-stop
        self._stop_event.set()
        self._request_capture_stop()
        if self._thread is not None:
            self._thread.join(timeout=10)
            if self._thread.is_alive():
                log.warning("replay attach thread did not stop within timeout")
                self._recovery_error = "replay attach worker still owns cleanup"
                return  # Keep its identity; start() cannot create a second owner.
            self._thread = None
        self._teardown_capture()
        if cleanup:
            self.cleanup_scratch()

    # -- attach loop ---------------------------------------------------------

    def _attach_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    self._run_attach_loop()
                except Exception as exc:
                    # Discovery/maintenance can fail independently of capture.
                    # Partial startup already retains and closes its own owners.
                    delay = self._recovery_delay(str(exc))
                    log.exception("replay attach failed; retrying in %.1fs", delay)
                    self._stop_event.wait(delay)
        finally:
            self._teardown_capture()

    def _recovery_delay(self, error):
        self._recovery_failures = min(self._recovery_failures + 1, 32)
        delay = min(30.0, 2 ** min(self._recovery_failures - 1, 5))
        self._recovery_error = str(error)[:512]
        self._recovery_retry_at = time.monotonic() + delay
        return delay

    def _run_attach_loop(self) -> None:
        while not self._stop_event.is_set():
            if self._capture_cleanup_failed:
                # Retry the retained objects, not their constructors. The
                # machine lease and scratch stay owned until each close passes.
                self._teardown_capture()
                if self._capture_cleanup_failed:
                    delay = self._recovery_delay(self._recovery_error or "capture cleanup pending")
                    self._stop_event.wait(delay)
                    continue
            if self._rec_lock is not None and time.monotonic() >= self._next_storage_maintenance:
                self._next_storage_maintenance = time.monotonic() + 1.0
                with self._capture_lock:
                    self.ring.maintain()
                    self.fragments.maintain()
                    self.maintain_history()
                    # The GPU path never rotates a legacy segment, so its
                    # picture rows sat in one open SQLite transaction until
                    # the first eviction (round 48). Commit them here.
                    self.ledger.flush()
            if self.ring.storage_pressure and self._recording:
                log.warning("replay capture paused: disk free-space floor cannot be restored")
                self._teardown_capture(keep_owner=True)
            win = self._window_finder(self._cfg.window_title)
            self._window_found = win is not None
            win = self._gated(win)

            identity = (win.pid, win.hwnd) if win is not None else None
            if (self._window_lost.is_set()
                    or (self._recording and identity != self._capture_window)):
                # A fast process restart can replace the window before the old
                # video source notices. Audio is process-bound: replace BOTH.
                log.info("capture source ended or window changed; reconnecting")
                self._teardown_capture()

            if (win is not None and not self._recording
                    and (not self.ring.storage_pressure or self._rec_lock is None)):
                self._begin_capture(win)

            if self._recording:
                self._maybe_recover_audio(win)

            self._maybe_idle_pause()
            if not self._capture_cleanup_failed:
                self._recovery_failures = 0
                self._recovery_error = None
                self._recovery_retry_at = 0.0
            self._stop_event.wait(self._cfg.attach_poll_s)

    # -- capture setup / teardown --------------------------------------------

    def _prepare_scratch(self) -> None:
        if self._scratch_ready:
            if self._scratch.owns():
                self._scratch.resume_deletion()
                return
            # A different recorder owner reset shared scratch while we were
            # detached. Start a new owned lifetime instead of retaining ring
            # entries whose files and picture identities no longer exist.
            self._scratch_ready = False
        self.fragments.reset()
        self.ledger.reset()
        # The machine-wide lock is already held. A viewer-only boot must
        # neither remove the owner's footage nor run this recorder's own
        # fallback codec probe (main may already have supplied a codec).
        self._scratch.prepare(self._protected_scratch())
        self.ring.reset()
        if self._codec is None:
            self._codec = pick_video_codec()
        self._scratch_ready = True
        self._scratch.resume_deletion()

    def _configure_scratch(self, cfg, protection) -> None:
        self._scratch = OwnedScratch(cfg.scratch_dir)
        self.scratch_protection = protection
        self._cleanup_on_stop = True
        self._capture_closed = True
        self._next_storage_maintenance = 0.0

    def _protected_scratch(self) -> set[Path]:
        protected = self.ring.protected_paths()
        if protected:
            # A source lease spans metadata projection as well as ffmpeg's
            # file read. Detached extraction still needs its identity archive.
            archive = self._cfg.scratch_dir / self._ledger_name
            protected.update(Path(str(archive) + suffix)
                             for suffix in ("", "-journal", "-wal", "-shm"))
        if self.scratch_protection is not None:
            protected.update(self.scratch_protection())
        return protected

    def cleanup_scratch(self) -> bool:
        """Retry cleanup after stop, acquiring the lock and checking ownership.

        Losing viewers and old recorders whose token was replaced write nothing.
        The protection callback is also honored during startup crash recovery.
        """
        with self._capture_lock:
            if self._recording or not self._capture_closed or not self._scratch.owns():
                return False
            held = self._rec_lock
            if held is None:
                held = self._recorder_lock_factory()
                if held is None:
                    return False
            try:
                cleaned = self._scratch.cleanup(self._protected_scratch())
                if cleaned:
                    self.ring.prune_missing()
                return cleaned
            except Exception:
                log.exception("replay scratch cleanup deferred")
                return False
            finally:
                if held is not self._rec_lock:
                    held.close()

    def reset_session_scratch(self) -> bool:
        """Rotate owned temporary media while retaining machine ownership.

        The service must drain/serialize extraction and preservation first:
        those operations use the active picture ledger, which this replaces.
        HTTP group leases may continue; their files are deleted on release.
        A viewer or an old owner of replaced scratch cannot rotate it.
        """
        with self._capture_lock:
            if self._stopping or not self._scratch.owns():
                return False
            if self._rec_lock is None:
                self._rec_lock = self._recorder_lock_factory()
                if self._rec_lock is None:
                    return False
                if not self._scratch.owns():  # ownership could change before acquisition
                    self._release_recorder()
                    return False
            resume = self._recording
            self._close_capture(keep_owner=True)
            if not self._capture_closed:
                return False
            self._scratch.resume_deletion()
            self.ring.clear()
            self.fragments.reset()
            self.ledger.reset()
            self._ledger_name = f"picture-identity-{uuid4().hex}.sqlite3"
            self._scratch.prepare(self._protected_scratch())
            self.ring.prune_missing()
            self._scratch_ready = True
            self._window_lost.clear()
            if resume and not self._stopping:
                win = self._window_finder(self._cfg.window_title)
                if win is not None:
                    self._start_capture(win)
            return True

    def _begin_capture(self, win: WindowInfo) -> None:
        with self._capture_lock:
            try:
                self._start_capture(win)
            except BaseException:
                self._teardown_capture()
                raise
            if self._stopping:
                self._teardown_capture()

    def _start_capture(self, win: WindowInfo) -> None:
        # M2: bail immediately if stop() already ran — prevents a zombie begin
        # from racing ahead and resurrecting recording state post-stop.
        if self._stopping or self._capture_cleanup_failed:
            return

        # Machine-wide single-recorder guard: if another tracker instance (a
        # second exe, a dev server in another worktree) already owns the
        # recorder lock, do NOT start a redundant capture of the same window —
        # that doubles GPU/encode/audio load and collides on the shared replay
        # buffer. Retried every attach cycle, so this instance takes over the
        # moment the owner exits (the OS frees the lock). Released in teardown.
        if self._rec_lock is None:
            self._rec_lock = self._recorder_lock_factory()
            if self._rec_lock is None:
                if not self._lock_warned:
                    log.warning("another tracker instance is already recording "
                                "this machine — running VIEWER-ONLY (no "
                                "capture); will take over if it exits")
                    self._lock_warned = True
                return
            self._lock_warned = False

        self._prepare_scratch()
        self.ring.maintain()
        if self.ring.storage_pressure:
            return  # retain ownership, retry after maintenance sees free space
        self._capture_closed = False
        # Only the picture feed retains source-PTS identities. Legacy CFR
        # observations stay in the bounded diagnostic cache; persisting
        # them would imply a retention join its media never supplied.
        if self._picture_feed:
            self.ledger.open_archive(self._cfg.scratch_dir / self._ledger_name)

        clock = self._clock_factory()
        with self._lock:
            self._clock = clock
            self._writer = None
            self._last_frame = None
            self._last_index = -1

        # Resolve capture first: GPU source owns a compressed-packet sink and
        # must not also start the raw-picture FFmpeg pipeline.
        video = self._video_factory(win)
        with self._lock:
            self._video_source = video
        make_sink = getattr(video, "create_sink", None)

        # PRIMARY: one ffmpeg muxes video+audio on a single wall-clock (the AV
        # sink owns the timeline + audio sync). FALLBACK (no ffmpeg binary):
        # the in-process SegmentWriter — video plus the legacy count-based
        # audio sidecar, the only path that still has the old two-clock
        # behaviour, reached solely when no ffmpeg is present.
        if make_sink is not None:
            self._video_sink = make_sink(self._cfg, clock, self.ledger,
                                         self.fragments.create)
            self.fragments.enabled = True
            self._video_sink.start()
        elif self._video_sink_factory is not None:
            # codec is supplied by main or picked on first owned capture:
            # the sink must encode with what THIS machine has, not with the
            # nvenc it used to hardcode — a non-NVIDIA machine's child died at
            # birth and the respawn loop flashed the user's cursor (2026-08-07).
            self._video_sink = self._video_sink_factory(self._cfg,
                                                        self._on_segment,
                                                        self._codec)
            publish = getattr(self._video_sink, "publish_fragments", None)
            self.fragments.enabled = bool(publish and publish(self.fragments.create))
            self._video_sink.on_fed = self._on_fed
            self._video_sink.start()
        else:
            with self._lock:
                self._writer = SegmentWriter(
                    self._cfg, clock, self._cfg.scratch_dir, self._codec,
                    self._on_segment)

        # Start video — on_stopped signals window loss back to attach loop.
        # Publish before start: it may enable capture then fail, or overlap
        # stop(). Teardown must own even that partially started source.
        if self._stopping:
            return
        self._frame_source = getattr(video, "frame_source", "desktop")
        self._frame_source_note = getattr(video, "frame_source_note", None)
        # The paired source records through automatic idle. Explicit pause is
        # a separate stop signal; legacy cameras keep their throttle policy.
        if hasattr(video, "set_idle_check"):
            video.set_idle_check(self.is_idle)
        if hasattr(video, "set_pause_check"):
            video.set_pause_check(lambda: self._session_paused)
        if not self._start_sources(win, clock, video, paired=make_sink is not None):
            return
        self._last_player_active = time.monotonic()  # fresh grace period
        # Startup can overlap a pause/unpause before the source is published.
        # Reconcile its actual demand after publication, including resume.
        self._set_idle(self._session_paused)
        self._capture_window = (win.pid, win.hwnd)
        self._recording = True
        log.info("capture started — window=%r audio=%s codec=%s",
                 win.title, self._audio_mode, self._codec)

    def _start_sources(self, win, clock, video, *, paired: bool) -> bool:
        if paired:
            # Cold audio initialization can block. Finish it before GPU demand
            # starts its bounded handshake; the paired sink discards PCM until
            # its media run opens. Source timestamps still own the media origin.
            self._start_audio(win, clock)
            if self._stopping:
                return False
            self._set_idle(self._session_paused)
        video.start(self._on_frame, lambda: self._source_stopped(video))
        if self._stopping:
            return False

        if not paired:
            self._start_audio(win, clock)  # preserve the raw sink's legacy order
        return not self._stopping

    def _start_audio(self, win: WindowInfo, clock: CaptureClock, *, initializing=True) -> None:
        started_at = time.monotonic()
        # Fallback-writer only: set its audio t0 BEFORE wiring any audio
        # callback so a PCM packet arriving between audio.start() and
        # start_audio() doesn't hit write_audio()'s "start_audio() not called"
        # guard. The AV sink needs none of this — ffmpeg stamps audio itself
        # by wall-clock, so there is no separate audio origin to reconcile (the
        # whole point: one clock, no two-stream A/V drift).
        if initializing and self._writer is not None:
            with self._lock:
                audio_t0 = clock.utc_of(qpc_100ns())
                self._writer.start_audio(t0_utc=audio_t0)
            log.info("AV-SYNC audio_t0=%s (in-process fallback)",
                     audio_t0.isoformat())

        # Own even a partially started source. A fallback is safe only after
        # stop() succeeds; otherwise teardown must retain and retry that source.
        with self._lock:
            if self._audio_source is not None:
                raise RuntimeError("audio source is still owned")
        audio_mode = "none"
        for label, factory in (("primary", self._audio_factory),
                               ("fallback", self._fallback_audio_factory)):
            if self._stopping or factory is None:
                break
            audio = None
            try:
                audio = factory(win.pid)
                with self._lock:
                    self._audio_source = audio
                audio.start(self._on_pcm)
                audio_mode = audio.mode
            except Exception as exc:
                self._audio_error = f"{label} audio source failed: {exc}"[:512]
                log.exception("%s audio source failed", label)
                if audio is not None:
                    try:
                        audio.stop()
                    except Exception as exc:
                        raise RuntimeError(f"{label} audio cleanup failed; source remains owned") from exc
                    with self._lock:
                        self._audio_source = None
            else:
                break

        with self._lock:
            self._audio_mode = audio_mode
        if audio_mode == "none" and not self._stopping:
            self._schedule_audio_retry(self._audio_error or "audio source unavailable")
        elif audio_mode != "none":
            self._audio_error = None
            self._audio_retry_at = 0.0
            self._audio_started_at = time.monotonic()
        log.info("audio initialization finished: pid=%d mode=%s elapsed_s=%.3f",
                 win.pid, audio_mode, time.monotonic() - started_at)

    def _schedule_audio_retry(self, error) -> None:
        self._audio_failures = min(self._audio_failures + 1, 32)
        delay = min(30.0, 2 ** min(self._audio_failures - 1, 5))
        self._audio_error = str(error)[:512]
        self._audio_retry_at = time.monotonic() + delay
        log.warning("%s; audio retry in %.1fs", self._audio_error, delay)

    def _maybe_recover_audio(self, win) -> None:
        # Discovery owns restart. Audio/device callbacks never block to heal a
        # worker, and a replacement cannot overlap failed source disposal.
        with self._capture_lock:
            if (self._stopping or not self._recording or win is None
                    or time.monotonic() < self._audio_retry_at):
                return
            audio = self._audio_source
            if audio is not None:
                check = getattr(audio, "check_health", None)
                if check is None:
                    return  # Third-party/injected legacy sources retain their contract.
                try:
                    check()
                except Exception as exc:  # noqa: BLE001 - arbitrary source health callbacks report through owned recovery.
                    self._schedule_audio_retry(exc)
                    self._audio_mode = "none"
                    try:
                        audio.stop()
                    except Exception:
                        self._teardown_capture()  # Retains any owner that cannot close.
                        raise
                    with self._lock:
                        self._audio_source = None
                    return
                if time.monotonic() - self._audio_started_at >= 10.0:
                    self._audio_failures = 0
                return
            if self._writer is not None:
                # The legacy count-based PCM writer cannot represent a live
                # audio gap. Retire its run instead of shifting old samples.
                self._teardown_capture()
                return
            self._start_audio(win, self._clock, initializing=False)

    def _source_stopped(self, source: VideoSource) -> None:
        with self._lock:
            # Old source callbacks may arrive after a replacement has started.
            if self._video_source is source:
                self._window_lost.set()

    def _teardown_capture(self, *, keep_owner: bool = False) -> None:
        with self._capture_lock:
            self._close_capture(keep_owner=keep_owner)

    def _request_capture_stop(self) -> None:
        source = self._video_source
        if source is not None and hasattr(source, "request_stop"):
            try:
                source.request_stop()
            except Exception:
                log.exception("capture demand revocation failed")

    def _close_capture(self, *, keep_owner: bool = False) -> None:
        """Stop sources and close writer. Safe to call when already idle."""
        self._request_capture_stop()
        # Failed owners are separate from active callback routes. The fixed
        # five keys retain actual objects for a later, explicit close attempt.
        owners = dict(getattr(self, "_capture_retained", {}))
        with self._lock:
            for name, owner in (("video", self._video_source), ("audio", self._audio_source),
                                ("sink", self._video_sink), ("writer", self._writer),
                                ("ledger", self.ledger)):
                if owner is not None:
                    owners[name] = owner
            self._capture_retained = owners
            self._video_source = None
            self._window_lost.clear()
            self._capture_window = None
            self._writer = None
            self._clock = None

        self._close_capture_owner(owners, "video", "stop")
        self._close_capture_owner(owners, "audio", "stop")
        if "audio" not in owners:
            with self._lock:
                self._audio_source = None

        # Stop-time PCM can reach the old sink until the source close attempts
        # finish. Later callbacks must never feed a retained, failed output.
        self._video_sink = None
        self._close_capture_owner(owners, "sink", "stop")
        self._close_capture_owner(owners, "writer", "close")
        # An unjoined source/sink can still be filing captured rows or feeds.
        # Detach takes SQLite's lock and would both defeat bounded teardown and
        # close a database that the retained worker still owns. Keep dependencies
        # until every producer proves it has finished.
        if not any(name != "ledger" for name in owners):
            self._close_capture_owner(owners, "ledger", "detach")

        self._capture_closed = not owners
        self._capture_cleanup_failed = not self._capture_closed
        self._recording = False
        self._idle = False
        self._idle_since = None
        if self._stopping and self._cleanup_on_stop and self._rec_lock is not None:
            self.cleanup_scratch()
        # Release the machine-wide recorder lock so another instance can take
        # over recording (e.g. PJ64 closed here, or this instance is shutting
        # down). Re-acquired on the next _begin_capture if we capture again.
        # Keep the machine lease until every retained owner proves quiet.
        if not keep_owner and self._capture_closed:
            self._release_recorder()
        # _audio_mode intentionally kept as last-known value so status() can
        # report which mode was active even after stop; cleared only on
        # fresh _begin_capture (set to new mode) or explicit reset.

    def _close_capture_owner(self, owners, name: str, method: str) -> None:
        owner = owners.get(name)
        if owner is None:
            return
        try:
            getattr(owner, method)()
        except Exception as exc:
            self._recovery_error = f"capture {name} cleanup pending: {exc}"[:512]
            log.exception("capture %s cleanup failed; owner retained", name)
        else:
            del owners[name]


    def _release_recorder(self) -> None:
        if self._rec_lock is not None:
            self._scratch.pause_deletion()
            try:
                if self._release_capture is not None:
                    self._release_capture()
                self._rec_lock.close()
            except Exception:
                self._capture_closed = False
                self._capture_cleanup_failed = True
                log.exception("capture ownership release failed; machine lease retained")
            else:
                self._rec_lock = None

    def set_capture_gate(self, allowed: Callable[[], bool] | None) -> None:
        """Capture only while `allowed()` holds: main.py passes the poller's
        practice ROM state, so a real run on another ROM records nothing --
        no GPU request, no desktop grab, no audio (his ruling, 2026-09-16)."""
        self._capture_gate = allowed

    def _gated(self, win):
        gated = self._capture_gate is not None and not self._capture_gate()
        if gated and not self._capture_gated:
            log.info("replay capture off: the loaded ROM is not a practice ROM")
        self._capture_gated = gated
        if not gated:
            return win
        if self._recording:
            self._teardown_capture(keep_owner=True)
        return None

    # -- automatic idle retention and explicit capture pause -----------------

    def set_idle_after(self, window_s: float) -> None:
        """The idle threshold tracks the user's padding window (pre+post):
        footage further from any input than the padding can never appear
        in a clip, so recording it is pure disk churn. Floored at
        _IDLE_FLOOR_S to prevent thrash at tiny pads."""
        self.idle_after_s = max(_IDLE_FLOOR_S, float(window_s))

    def set_player_active(self) -> None:
        """Poll-thread tap (replay/activity.py): called on every tick where
        the player is providing input. Preserve the already-recorded idle
        tail here; starting a fresh recorder at this point loses the anchor.
        Automatic idle detection runs in the attach loop (2 s cadence)."""
        if self._session_paused:
            return  # manual session pause outranks the input signal
        self._last_player_active = time.monotonic()
        if self._idle:
            self._set_idle(False)

    def set_session_paused(self, paused: bool) -> None:
        """Explicit pause retires paired GPU capture, even if already idle.

        It outranks input-driven resume. Unpause prepares a new GPU run and
        refreshes the activity clock; automatic inactivity never does that.
        """
        if paused and not self._session_paused:
            self._session_paused_since = datetime.now(timezone.utc)
        elif not paused:
            self._session_paused_since = None
        self._session_paused = paused
        if paused:
            self._set_idle(True)
        else:
            self._last_player_active = time.monotonic()
            self._set_idle(False)

    def _fragment_idle_window(self):
        """One immutable retention snapshot; reading it never blocks polling."""
        since = self._idle_since
        return (since, self._cfg.pre_pad_s) if since is not None and not self._session_paused else None

    def _fragment_paused(self, start):
        # Legacy sinks keep producing while paused. Drop only extents born
        # after the explicit pause, never the preceding automatic-idle tail.
        since = self._session_paused_since
        return since is not None and start >= since

    def is_idle(self) -> bool:
        """Automatic inactivity or explicit pause; not proof capture stopped."""
        return self._idle

    def can_collect(self) -> bool:
        """Do not treat retained GPU lead-in as disposable GC time."""
        if getattr(self, "_capture_retained", None):
            return False
        source = self._video_source
        if source is None and not self._capture_closed:
            return False
        retired = getattr(source, "capture_retired", None)
        if retired is not None:
            return bool(retired())
        if self._recording and self.fragments.enabled and not self._session_paused:
            return False  # Legacy fragment sinks retain the same idle lead-in.
        return not self._recording or self._idle

    def _maybe_idle_pause(self) -> None:
        if (self._recording and not self._idle
                and time.monotonic() - self._last_player_active
                > self.idle_after_s):
            self._set_idle(True)

    def _set_idle(self, idle: bool) -> None:
        self._idle = idle
        if idle:
            if self._idle_since is None:
                self._idle_since = datetime.now(timezone.utc)
            log.info("replay %s: %s", "paused" if self._session_paused else "idle",
                     "capture pause requested" if self._session_paused else "retaining rolling lead-in")
        else:
            self._idle_since = None
            log.info("replay idle: input detected — buffer resumes "
                     "(%d idle segments discarded)", self._idle_dropped)
            self._idle_dropped = 0

        source = self._video_source
        if source is not None and hasattr(source, "refresh_demand"):
            try:
                source.refresh_demand()
            except Exception:
                # A source closing during resume cannot interrupt game event
                # polling or leave the recorder halfway through its state change.
                log.exception("replay frame demand notification failed")

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
            self.ring.discard(seg)
            return
        self.ring.add(seg)
        self.ledger.flush()

    # -- frame callback (library thread) -------------------------------------

    def _observe_picture(self, bgra, tag, capture_ts, stamp) -> bool:
        """One grab into the picture ledger. A picture from THE CAPTURE
        LAYER carries its own stamp -- the game frame the plugin read
        inside Project64 at the display list that drew it -- and that row
        is what the frame map is made of. A grab with no stamp (the
        desktop camera, before the layer publishes) is recorded by TIME
        only: it names no frame, so the clip gets no map and the timeline
        says frame-exact capture is off rather than showing a guess."""
        preparation = {"prepare": bgra.as_bgra} if isinstance(bgra, BgrPicture) else {}
        if stamp is not None and tag is not None:
            return self.ledger.observe(bgra, tag[1], tag[0], stamp.extras(), **preparation)
        if capture_ts is not None:
            return self.ledger.observe(bgra, capture_ts, None, **preparation)
        return False

    @measured("replay.on_frame", interval=True)
    def _on_frame(self, bgra: np.ndarray | BgrPicture, ts_100ns: int, stamp=None) -> None:
        if self.ring.storage_pressure:
            self._grabs_skipped += 1
            return
        # NO idle gate here: frames keep flowing so the sink's timeline and
        # `_latest` stay fresh; idle discard happens per completed segment
        # in _on_segment.
        # The sink owns queued VFR delivery or the configured legacy CFR
        # feeder. The in-process writer below is bypassed when it is present.
        sink = self._video_sink
        if sink is not None:
            if not self._picture_feed:
                # CFR submits every grab. Prepare before observing, including
                # a folded grab, so allocation failure cannot alter its ledger.
                bgra = as_bgra(bgra)
            # Tag the picture AT CAPTURE (round 32 items 17 + 30): the RAM
            # frame current right now (map v2's key) and this picture's own
            # composition time -- WGC's SystemRelativeTime through the run's
            # CaptureClock, not the moment this callback happened to run --
            # which is what the frame map's present series keys on (v4).
            # A picture from the CAPTURE LAYER (item 95) arrives with its
            # own stamp -- the frame the game submitted it as, read inside
            # the emulator -- so the frame clock is not consulted for it:
            # the tag's frame IS the stamp's, and the row says `exact`.
            tag = None
            clock = self._clock
            capture_ts = (clock.utc_of(ts_100ns).timestamp()
                          if clock is not None else None)
            if stamp is not None and capture_ts is not None:
                tag = (stamp.frame, capture_ts)
            if self._picture_feed and not _sink_has_room(sink):
                # LOCKSTEP (item 88): no budget to encode this picture, so it
                # is not recorded as captured either. The ledger keeps its
                # promise -- every row it holds became a video frame -- and a
                # loaded machine yields a sparser clip rather than a clip
                # whose map describes frames the video does not contain. His
                # rule: "If I see a frame in my replay ... I would expect to
                # see the input capture for that frame as well."
                self._grabs_skipped += 1
                return
            # The picture ledger notices each NEW picture among the grabs
            # (item 40) -- before submit so the sample reads the buffer this
            # callback was handed. observe() never raises.
            new_picture = self._observe_picture(bgra, tag, capture_ts, stamp)
            if self._picture_feed:
                # ONE frame per DISTINCT picture (item 38): a grab that
                # changed nothing feeds nothing. The tag's second field is
                # the row's own ts, which the feed log keys on.
                if new_picture:
                    sink.submit(as_bgra(bgra), tag if tag is not None
                                else (None, capture_ts))
                return
            sink.submit(as_bgra(bgra), tag)
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

        # Legacy writer also receives owned BGRA, prepared before any fills.
        bgra = as_bgra(bgra)

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

    def _on_fed(self, tag, wrote_at: float, *, media_run=None, pts=None) -> None:
        """The sink completed one write (feeder thread): file it under the
        row the tag names, or as a heartbeat repeat when there is none."""
        self.ledger.mark_fed(tag[1] if tag is not None else None, wrote_at,
                             media_run=media_run, pts=pts)

    # -- audio callback (library thread) -------------------------------------

    @measured("replay.on_pcm", interval=True)
    def _on_pcm(self, pcm_s16: np.ndarray) -> None:
        if self.ring.storage_pressure:
            return
        # PRIMARY: hand raw interleaved s16le PCM to the AV sink's audio pipe;
        # ffmpeg wall-clock-stamps it and aresample-locks it to the video
        # master, so there is no count-based cursor to keep advancing and no
        # idle gate (idle footage is discarded per completed segment, audio
        # included, in _on_segment). FALLBACK: the in-process count-based
        # writer (no ffmpeg).
        sink = self._video_sink
        if sink is not None:
            sink.submit_audio(pcm_s16.tobytes())
            return
        with self._lock:
            if self._writer is None:
                return
            self._writer.write_audio(pcm_s16)

    # -- status --------------------------------------------------------------

    def perf_gauges(self) -> dict:
        """Cached scalars only: never wait on media/storage from the poller loop.

        Values are individually atomic, eventually consistent diagnostics;
        the ordinary status API owns detailed locked coverage queries.
        """
        return {"ring_bytes": self.ring.total_bytes, "idle": self._idle,
                "recording": self._recording_active(), "audio_mode": self._audio_mode}

    def _recording_active(self):
        return (self._recording and not self._stopping
                and getattr(self._video_source, "recording_active", True))

    def status(self) -> dict:
        # M3: reads of _recording/_window_found/_audio_mode/_codec are unlocked;
        # CPython attribute reads are atomic, so these are stale-but-never-torn —
        # acceptable for a polling status surface.
        cov = self.fragments.coverage() if self.fragments.enabled else self.ring.coverage("video")
        return {
            "recording": self._recording_active(),
            "window_found": self._window_found,
            "audio_mode": self._audio_mode,
            "audio_health": {"error": self._audio_error,
                             "retry_in_s": max(0.0, self._audio_retry_at - time.monotonic()),
                             "failures": self._audio_failures},
            "recovery": {
                "state": ("cleanup_pending" if self._capture_cleanup_failed else
                          "retrying" if self._recovery_error else "ready"),
                "error": self._recovery_error,
                "attempts": self._recovery_failures,
                "retry_in_s": max(0.0, self._recovery_retry_at - time.monotonic()),
                "attach_alive": self._thread is not None and self._thread.is_alive(),
            },
            "encoder": self._codec,
            "storage_backend": "fragments" if self.fragments.enabled else "segments",
            "publication_error": getattr(self._video_sink, "publication_error", None),
            "buffer_start_utc": cov[0].isoformat() if cov else None,
            "buffer_end_utc": cov[1].isoformat() if cov else None,
            "disk_bytes": self.ring.total_bytes,
            "storage_pressure": self.ring.storage_pressure,
            "retention_s": self.ring.retention_s,
            "max_buffer_bytes": self.ring.max_bytes,
            "idle": self._idle,
            # True while the loaded ROM is not a practice ROM (a real run).
            "capture_gated": self._capture_gated,
            # A degraded capture is a NUMBER, not a thinner-looking clip:
            # grabs the sink had no budget for (so the ledger never recorded
            # them either) and how deep its queue is right now.
            "grabs_skipped": self._grabs_skipped,
            "encode_backlog": (self._video_sink.queue_depth()[0]
                               if hasattr(self._video_sink, "queue_depth")
                               else 0),
            # Which camera is live: "plugin" = the capture layer inside
            # Project64 (every picture stamped by the game), "desktop" = the
            # window grab the frame clock places in game time afterwards.
            "frame_source": self._frame_source,
            # ...and WHY it is the desktop when the capture layer is installed
            # (the layer refused every picture, presented none) -- None
            # when nothing fell back.
            "frame_source_note": getattr(self._video_source, "frame_source_note",
                                         self._frame_source_note),
            "frame_source_health": (self._video_source.status()
                                    if hasattr(self._video_source, "status")
                                    else None),
        }
